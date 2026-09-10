import asyncio
import logging
import time
from inspect import iscoroutinefunction
from typing import Callable

from bleak import BleakScanner

from db_writer import db_writer_worker
from db_writer import init_db
from handler_signal import setup_signal_handlers
from parser import parse_atc_payload, Payload
from settings import (
    SCANNING_MODE,
    WATCHDOG_TIMEOUT,
    DEVICE_PREFIX_DEFAULT,
    NAME_PREFIXES,
    UUID_ENVIRONMENTAL_SENSING,
    shutdown_event,
    db_queue,
    last_frame_counter,
    last_counter_data,
    ADDRESS_PREFIXES,
    DEBUG_EVENTS,
)
from start_scanning import start_scanning
from watchdog import bluetooth_watchdog

logger = logging.getLogger("Monitor")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
logger.setLevel(logging.DEBUG)
logger.debug("DEBUG Started")


# Track timestamp of the last received advertisement packet


def generate_device_name(device):
    """Generate a default name if none is provided."""
    if ":" in device.address:
        uuid = "".join(device.address.split(":")[-3:])
    else:
        uuid = device.address.split("-")[-1][-6:]
    if uuid:
        return DEVICE_PREFIX_DEFAULT + uuid
    return None


def ble_callback(device, advertising_data):
    if shutdown_event.is_set():
        return
    last_counter_data["last_packet_time"] = time.time()

    name = advertising_data.local_name or device.name or generate_device_name(device) or device.address or ""
    if DEBUG_EVENTS:
        logger.debug(f"NAME: {name}, {device.address=}")

    # # Filter for target prefix (e.g., 'atc')
    if NAME_PREFIXES[0] and (not name.lower().startswith(NAME_PREFIXES)):
        return
    # # Filter for addresses prefix (e.g., 'A4:')
    if ADDRESS_PREFIXES[0] and (not device.address.lower().startswith(ADDRESS_PREFIXES)):
        return

    # Decode advertisement payload
    # logger.debug(f"{advertising_data=}")
    raw_data = advertising_data.service_data.get(UUID_ENVIRONMENTAL_SENSING)

    if not raw_data:
        return

    parsed: Payload | None = parse_atc_payload(raw_data)

    if not parsed:
        return

    # Deduplication Logic
    frame_counter = parsed.frame_counter
    if frame_counter is not None:
        # Skip if frame_counter matches the last seen frame
        if last_frame_counter.get(name) == frame_counter:
            return

        # Update last seen frame counter and return payload
        last_frame_counter[name] = frame_counter

    logger.debug(f"{name}: {parsed}")

    record = (
        time.time(),
        device.address,
        name,
        advertising_data.rssi,
        parsed.temperature_c,
        parsed.humidity_pct,
        parsed.battery_pct,
        parsed.battery_mv,
        parsed.frame_counter,
        parsed.format,
    )

    db_queue.put_nowait(record)


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

    logger.info("[BLE] Scanner and Watchdog are running. Waiting for events...")

    await shutdown_event.wait()

    logger.info("[BLE] Stopping scanner...")
    watchdog_task.cancel()

    try:
        if isinstance(scanner, BleakScanner):
            await scanner.stop()
        elif hasattr(scanner, "stop") and callable(scanner.stop):
            # Calls the stop() method on HCIPassiveScannerProtocol

            if iscoroutinefunction(scanner.stop):
                await scanner.stop()
            else:
                scanner.stop()
        elif (
            hasattr(scanner, "stop_scan_request")
            and callable(scanner.stop_scan_request)
            and iscoroutinefunction(scanner.stop_scan_request)
        ):
            await scanner.stop_scan_request()
    except Exception as e:
        logger.warning(f"[BLE] Exception while stopping scanner: {e}")

    logger.info("[DB] Flushing remaining queue items...")
    await db_queue.join()
    await writer_task
    logger.info("[System] Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
