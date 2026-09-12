import asyncio
import datetime
import logging
import sqlite3
import time

from ThermometerMonitor import settings

logger = logging.getLogger(__name__)


async def db_writer_worker(db_path=settings.DB_PATH):
    logger.info(f"Used SQLite database on file: {str(settings.DB_PATH)}")
    queue = settings.get_db_queue()
    shutdown_event = settings.get_shutdown_event()

    conn = sqlite3.connect(db_path)
    batch = []

    def flush_batch():
        if not batch:
            return
        with conn:
            conn.executemany(
                """
                INSERT INTO atc_sensor_data (
                    timestamp, mac_address, device_name, rssi,
                    temperature_c, humidity_pct, battery_pct, battery_mv,
                    frame_counter, payload_format
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                batch,
            )
        logger.debug(f"[DB] Saved {len(batch)} sensor readings.")
        batch.clear()

    try:
        while not (shutdown_event.is_set() and queue.empty()):
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15.0)
                batch.append(item)
                queue.task_done()

                if len(batch) >= 10:
                    flush_batch()
            except asyncio.TimeoutError:
                flush_batch()
    finally:
        while not queue.empty():
            batch.append(queue.get_nowait())
            queue.task_done()

        flush_batch()
        conn.close()
        logger.info("[DB] Connection closed cleanly.")


def init_db(db_path=settings.DB_PATH):
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute("""
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
        # Indexes for fast querying by MAC address and time range
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mac ON atc_sensor_data(mac_address)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_time ON atc_sensor_data(timestamp)")
        conn.execute("PRAGMA journal_mode = WAL;")
    conn.close()


def sync_cleanup(db_path: str, cutoff_timestamp: float) -> int:
    """Synchronous cleanup executed in a separate worker thread."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM atc_sensor_data WHERE timestamp < ?", (cutoff_timestamp,))
        return cursor.rowcount


async def db_cleanup_worker(db_path=None, cleanup_timeout=None):
    db_path = db_path or str(settings.DB_PATH)
    shutdown_event = settings.get_shutdown_event()
    cleanup_timeout = cleanup_timeout or settings.CLEANUP_TIMEOUT
    cleanup_period = datetime.timedelta(days=settings.CLEANUP_PERIOD_DAYS).total_seconds()
    if not cleanup_period:
        logger.info("DB WORKER FOR CLEANUP IS DISABLED")
        return
    logger.info(f"DB CLEANUP initialized for run check every {cleanup_timeout} seconds.")

    while not shutdown_event.is_set():
        try:
            cutoff_timestamp = time.time() - cleanup_period

            # Run blocking SQLite query safely off the main event loop thread
            deleted_count = await asyncio.to_thread(sync_cleanup, db_path, cutoff_timestamp)
            if deleted_count:
                logger.info(
                    f"[DB] Cleanup finished. Deleted {deleted_count} old sensor records. Next check after {cleanup_timeout} seconds."
                )

        except Exception as e:
            logger.error(f"[DB] Error during database cleanup: {e}", exc_info=True)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=cleanup_timeout)
        except asyncio.TimeoutError:
            ...

    logger.info("[DB] Cleanup worker shut down cleanly.")
