import asyncio
import logging
import sqlite3

from ThermometerMonitor import settings

logger = logging.getLogger(__name__)


async def db_writer_worker(db_path=settings.DB_PATH):
    logger.info(f"Used SQLite database on file: {str(settings.DB_PATH)}")
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
        while not (settings.get_shutdown_event().is_set() and settings.get_db_queue().empty()):
            try:
                item = await asyncio.wait_for(settings.get_db_queue().get(), timeout=1.0)
                batch.append(item)
                settings.get_db_queue().task_done()

                if len(batch) >= 10:
                    flush_batch()
            except asyncio.TimeoutError:
                flush_batch()
    finally:
        while not settings.get_db_queue().empty():
            batch.append(settings.get_db_queue().get_nowait())
            settings.get_db_queue().task_done()

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
