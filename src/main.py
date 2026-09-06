from bleak.exc import BleakBluetoothNotAvailableError
from typing import Literal, Callable

import asyncio
import logging
import signal
import sqlite3
import struct
import time
from pathlib import Path

from bleak import BleakScanner, BleakError

# Global Configuration
NAME_PREFIXES = ("atc",)
BASE_PATH = Path(__file__).parent.parent
DB_PATH = BASE_PATH / "data/ble_data.db"
UUID_ENVIRONMENTAL_SENSING = "0000181a-0000-1000-8000-00805f9b34fb"
SCANNING_MODE = "auto"

logger = logging.getLogger("Monitor")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
logger.setLevel(logging.DEBUG)
logger.debug("DEBUG Started")

shutdown_event = asyncio.Event()
db_queue = asyncio.Queue()

last_frame_counter = {}
# Track timestamp of the last received advertisement packet
last_packet_time = time.time()


def init_db(db_path=DB_PATH):
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


def parse_atc_payload(service_data):
    raw_bytes = service_data.get(UUID_ENVIRONMENTAL_SENSING)
    if not raw_bytes:
        logger.debug("parse_atc_payload empty")
        return None

    length = len(raw_bytes)
    logger.debug(f"{length=}, {raw_bytes=}")
    #
    # """Update the data of a registered BLE device."""
    # frame_cnt = int.from_bytes(raw_bytes[13:14], byteorder="little", signed=False)
    # temp_raw = int.from_bytes(raw_bytes[6:8], byteorder="little", signed=True)
    # hum_raw = (
    #         int.from_bytes(raw_bytes[8:10], byteorder="little", signed=True)
    # )
    # battery_mv = (
    #         int.from_bytes(raw_bytes[10:12], byteorder="little", signed=False)
    # )
    # batt_pct = int.from_bytes(raw_bytes[12:13], byteorder="little", signed=False)
    #
    # payload = {
    #     "format": "pvvx",
    #     "temperature_c": temp_raw / 100.0,
    #     "humidity_pct": hum_raw / 100.0,
    #     "battery_mv": battery_mv,
    #     "battery_pct": batt_pct,
    #     "frame_counter": frame_cnt,
    # }

    # if payload:
    #     return  payload

    # 1. Custom / pvvx Format (18 bytes, Little-Endian)
    if length == 18:
        _, temp_raw, hum_raw, batt_mv, batt_pct, frame_cnt, _ = struct.unpack("<6shHHBBB", raw_bytes)
        payload = {
            "format": "pvvx",
            "temperature_c": temp_raw / 100.0,
            "humidity_pct": hum_raw / 100.0,
            "battery_mv": batt_mv,
            "battery_pct": batt_pct,
            "frame_counter": frame_cnt,
        }

    # 2. ATC1441 Format (13 bytes, Big-Endian)
    elif length == 13:
        _, temp_raw, hum_raw, batt_pct, batt_mv, frame_cnt = struct.unpack(">6shBBHB", raw_bytes)
        payload = {
            "format": "atc1441",
            "temperature_c": temp_raw / 10.0,
            "humidity_pct": float(hum_raw),
            "battery_mv": batt_mv,
            "battery_pct": batt_pct,
            "frame_counter": frame_cnt,
        }
    else:
        return None
    return payload


def ble_callback(device, advertising_data):
    if shutdown_event.is_set():
        return
    global last_packet_time
    last_packet_time = time.time()

    name = advertising_data.local_name or device.name or device.address or ""
    logger.debug(f"NAME: {name}")

    # # Filter for target prefix (e.g., 'atc')
    # if not name.lower().startswith(NAME_PREFIXES):
    #     return

    logger.debug(f"FILTERED NAME: {name}, {device.address=}")

    # Decode advertisement payload
    parsed = parse_atc_payload(advertising_data.service_data)

    if not parsed:
        return

    # Deduplication Logic
    frame_counter = parsed.get("frame_counter")
    if frame_counter is not None:
        # Skip if frame_counter matches the last seen frame
        if last_frame_counter.get(name) == frame_counter:
            return

        # Update last seen frame counter and return payload
        last_frame_counter[name] = frame_counter

    print(parsed)

    record = (
        time.time(),
        device.address,
        name,
        advertising_data.rssi,
        parsed["temperature_c"] if parsed else None,
        parsed["humidity_pct"] if parsed else None,
        parsed["battery_pct"] if parsed else None,
        parsed["battery_mv"] if parsed else None,
        parsed["frame_counter"] if parsed else None,
        parsed["format"] if parsed else None,
    )

    db_queue.put_nowait(record)


async def db_writer_worker(db_path=DB_PATH):
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
        logger.info(f"[DB] Saved {len(batch)} sensor readings.")
        batch.clear()

    try:
        while not (shutdown_event.is_set() and db_queue.empty()):
            try:
                item = await asyncio.wait_for(db_queue.get(), timeout=1.0)
                batch.append(item)
                db_queue.task_done()

                if len(batch) >= 10:
                    flush_batch()
            except asyncio.TimeoutError:
                flush_batch()
    finally:
        while not db_queue.empty():
            batch.append(db_queue.get_nowait())
            db_queue.task_done()

        flush_batch()
        conn.close()
        logger.info("[DB] Connection closed cleanly.")


async def bluetooth_watchdog(scanner, timeout_seconds=30):
    """Monitors scanner status and packet freshness."""
    global last_packet_time
    logger.debug(f"[Watchdog] Starting bluetooth_watchdog. Timeout set to {timeout_seconds}s.")

    while not shutdown_event.is_set():
        time_since_last_packet = time.time() - last_packet_time
        logger.debug(
            f"[Watchdog] Status check | Scanner active: {scanner.is_scanning} | "
            f"Secs since last packet: {time_since_last_packet:.1f}s"
        )

        if not scanner.is_scanning:
            logger.error("[Watchdog] BLE scanner stopped unexpectedly.")
            shutdown_event.set()
            break

        if time_since_last_packet > timeout_seconds:
            logger.error(f"[Watchdog] Stalled: No BLE packets received for {time_since_last_packet:.0f}s.")
            shutdown_event.set()
            break

        await asyncio.sleep(5)

    logger.debug("[Watchdog] Exited watchdog loop.")


def setup_signal_handlers(loop=None):
    """
    Cross-platform signal handler setup.
    Works on both Linux/macOS (Docker) and Windows natively.
    """

    def handle_signal(sig, frame=None):
        logger.info(f"\n[System] Received signal {sig}. Triggering graceful shutdown...")
        # Check if loop is running and thread-safely set the shutdown flag/event
        if loop and loop.is_running():
            loop.call_soon_threadsafe(shutdown_event.set)
        else:
            shutdown_event.set()

    # Standard signal bindings compatible with Windows and Linux
    signal.signal(signal.SIGINT, handle_signal)  # Ctrl+C
    signal.signal(signal.SIGTERM, handle_signal)  # Docker stop / termination


async def start_scanning(mode: Literal["active", "passive", "auto"], callback: Callable) -> BleakScanner | None:
    """Start scanning for BLE devices."""
    modes = ("passive", "active") if mode.lower() == "auto" else (mode,)
    mode: Literal["active", "passive"]
    for mode in modes:
        logger.info(f"[BLE] Attempting scan for sensors matching prefix {NAME_PREFIXES} in {mode} mode...")
        try:
            if mode not in ("active", "passive"):
                raise ValueError("Mode must be either 'active' or 'passive'.")
            scanner = BleakScanner(callback, scanning_mode=mode)
            await scanner.start()
            return scanner
        except BleakBluetoothNotAvailableError as e:
            logger.error(f"Error in {mode} mode: {e}")
            return None
        except BleakError as e:
            logger.error(f"Error in {mode} mode: {e}")
    return None


async def main():
    init_db()
    loop = asyncio.get_running_loop()
    setup_signal_handlers(loop)

    writer_task = asyncio.create_task(db_writer_worker())

    scanner = await start_scanning(mode=SCANNING_MODE, callback=ble_callback)

    if scanner is None:
        logger.error(f"[BLE] Bluetooth device not found or it disabled. Sleep 10 seconds")
        await asyncio.sleep(10)
        return

    # Start the watchdog task
    watchdog_task = asyncio.create_task(bluetooth_watchdog(scanner, timeout_seconds=10))  # noqa

    logger.info("[BLE] Scanner and Watchdog active. Waiting for events...")

    await shutdown_event.wait()

    logger.info("[BLE] Stopping scanner...")
    watchdog_task.cancel()

    try:
        await scanner.stop()
    except Exception as e:
        logger.warning(f"[BLE] Exception while stopping scanner: {e}")

    logger.info("[DB] Flushing remaining queue items...")
    await db_queue.join()
    await writer_task
    logger.info("[System] Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
