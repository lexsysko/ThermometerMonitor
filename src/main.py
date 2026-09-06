from os import environ
from pathlib import Path

import asyncio
import logging
import signal
import sqlite3
import time
from bleak import BleakScanner, BleakError
from bleak.exc import BleakBluetoothNotAvailableError
from dotenv import load_dotenv
from typing import Callable

from parser import parse_atc_payload

BASE_PATH = Path(__file__).parent.parent

if (BASE_PATH / ".env").exists():
    load_dotenv()

# Global Configuration
SCANNING_MODE = environ.get("SCANNING_MODE", "auto")
WATCHDOG_TIMEOUT = int(environ.get("WATCHDOG_TIMEOUT", 300))
LOG_LEVEL = getattr(logging, environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
DEVICE_PREFIX_DEFAULT = environ.get("DEVICE_PREFIX_DEFAULT", "ATC_")
NAME_PREFIXES: tuple[str] = tuple(s.strip() for s in environ.get("NAME_PREFIXES", "").split(","))
DB_PATH = BASE_PATH / "data/ble_data.db"
UUID_ENVIRONMENTAL_SENSING = environ.get("UUID_ENVIRONMENTAL_SENSING", "0000181a-0000-1000-8000-00805f9b34fb")

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


def generate_device_name(device):
    """Generate a default name if none is provided."""
    if ":" in device.address:
        uuid = "".join(device.address.split(":")[-3:])
    else:
        uuid = device.address.split("-")[-1][-6:]
    if uuid:
        return DEVICE_PREFIX_DEFAULT + uuid
    return None


async def ble_callback(device, advertising_data):
    if shutdown_event.is_set():
        return
    global last_packet_time
    last_packet_time = time.time()
    await asyncio.sleep(0.01)

    name = advertising_data.local_name or device.name or generate_device_name(device) or device.address or ""
    # logger.debug(f"NAME: {name}")

    # # Filter for target prefix (e.g., 'atc')
    if NAME_PREFIXES and not name.lower().startswith(NAME_PREFIXES):
        return

    # logger.debug(f"FILTERED NAME: {name}, {device=}")

    # Decode advertisement payload
    # logger.debug(f"{advertising_data=}")
    raw_data = advertising_data.service_data.get(UUID_ENVIRONMENTAL_SENSING)

    if not raw_data:
        return

    parsed = parse_atc_payload(raw_data)

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

    logger.debug(parsed)

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


async def bluetooth_watchdog(timeout_seconds=60):
    """Monitors packet freshness to catch dead Bluetooth hardware/stack freezes."""
    global last_packet_time
    logger.debug(f"[Watchdog] Watchdog active. Packet timeout: {timeout_seconds}s.")

    # Warm-up grace period so initial scanning starts before watchdog checks
    await asyncio.sleep(timeout_seconds // 3)

    while not shutdown_event.is_set():
        await asyncio.sleep(timeout_seconds // 4)

        time_since_last_packet = time.time() - last_packet_time

        logger.debug(f"[Watchdog] Heartbeat check | Secs since last packet: {time_since_last_packet:.1f}s")

        # Catch hardware disconnects, stack stalls, and disabled Bluetooth
        if time_since_last_packet > timeout_seconds:
            logger.error(
                f"[Watchdog] BLE stall detected! No packets for {time_since_last_packet:.0f}s. "
                "Initiating system recovery/shutdown..."
            )
            shutdown_event.set()
            break

    logger.debug("[Watchdog] Watchdog loop exited.")


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


async def start_scanning(mode: str, callback: Callable) -> BleakScanner | None:
    """Start scanning for BLE devices."""
    modes = ("passive", "active") if mode.lower() == "auto" else (mode,)
    for mode in modes:
        logger.info(f"[BLE] Attempting scan for sensors matching prefix '{','.join(NAME_PREFIXES)}' in {mode} mode...")
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
    watchdog_task = asyncio.create_task(bluetooth_watchdog(timeout_seconds=WATCHDOG_TIMEOUT))  # noqa

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
