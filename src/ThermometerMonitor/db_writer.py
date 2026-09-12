import asyncio
import datetime
import logging
import time
from contextlib import asynccontextmanager

import aiosqlite

from ThermometerMonitor import settings

logger = logging.getLogger(__name__)

INSERT_SENSOR_DATA_SQL = """
    INSERT INTO atc_sensor_data (
        timestamp, mac_address, device_name, rssi,
        temperature_c, humidity_pct, battery_pct, battery_mv,
        frame_counter, payload_format
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@asynccontextmanager
async def get_db_connection(db_path=None):
    """Centralized database connection provider with optimized PRAGMAs."""
    path = str(db_path or settings.DB_PATH)
    async with aiosqlite.connect(path) as db:
        # Standardize performance & concurrency settings across all connections
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=5000;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        yield db


async def init_db(db_path=settings.DB_PATH):
    async with get_db_connection(db_path) as db:
        await db.execute(""" 
            CREATE TABLE IF NOT EXISTS atc_sensor_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                mac_address TEXT NOT NULL,
                device_name TEXT,
                rssi INTEGER NOT NULL,
                temperature_c REAL,
                humidity_pct REAL,
                battery_pct INTEGER,
                battery_mv INTEGER,
                frame_counter INTEGER,
                payload_format TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_mac ON atc_sensor_data(mac_address)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_time ON atc_sensor_data(timestamp)")
        await db.commit()
    logger.info("[DB] Database initialized successfully.")


async def db_writer_worker(db_path=settings.DB_PATH):
    logger.info(f"Using SQLite database file: {db_path}")
    queue = settings.get_db_queue()
    shutdown_event = settings.get_shutdown_event()
    batch = []

    async with get_db_connection(db_path) as db:

        async def flush_batch():
            if not batch:
                return
            await db.executemany(INSERT_SENSOR_DATA_SQL, batch)
            await db.commit()
            logger.debug(f"[DB] Saved {len(batch)} sensor readings.")
            batch.clear()

        try:
            while not (shutdown_event.is_set() and queue.empty()):
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=settings.BATCH_FLUSH_DB_TIMEOUT)
                    batch.append(item)
                    queue.task_done()

                    if len(batch) >= 10:
                        await flush_batch()

                except asyncio.TimeoutError:
                    await flush_batch()

        finally:
            # Drain residual items from queue during app shutdown
            while not queue.empty():
                try:
                    batch.append(queue.get_nowait())
                    queue.task_done()
                except asyncio.QueueEmpty:
                    break

            await flush_batch()
            logger.info("[DB] Writer worker shut down cleanly.")


async def async_cleanup(db_path: str, cutoff_timestamp: float) -> int:
    """Deletes records older than cutoff_timestamp and returns deleted row count."""
    async with get_db_connection(db_path) as db:
        cursor = await db.execute("DELETE FROM atc_sensor_data WHERE timestamp < ?", (cutoff_timestamp,))
        await db.commit()  # Fixed missing commit
        return cursor.rowcount


async def db_cleanup_worker(db_path=None, cleanup_timeout=None):
    db_path = str(db_path or settings.DB_PATH)
    shutdown_event = settings.get_shutdown_event()
    cleanup_timeout = cleanup_timeout or settings.CLEANUP_TIMEOUT
    cleanup_period = datetime.timedelta(days=settings.CLEANUP_PERIOD_DAYS).total_seconds()

    if not cleanup_period:
        logger.info("[DB] DB WORKER FOR CLEANUP IS DISABLED")
        return
    logger.info(f"[DB] CLEANUP initialized every {cleanup_timeout} seconds.")

    while not shutdown_event.is_set():
        try:
            cutoff_timestamp = time.time() - cleanup_period
            deleted_count = await async_cleanup(db_path, cutoff_timestamp)

            if deleted_count:
                logger.info(f"[DB] Cleanup finished. Deleted {deleted_count} old sensor records.")

        except Exception as e:
            logger.error(f"[DB] Error during database cleanup: {e}", exc_info=True)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=cleanup_timeout)
        except asyncio.TimeoutError:
            pass

    logger.info("[DB] Cleanup worker shut down cleanly.")
