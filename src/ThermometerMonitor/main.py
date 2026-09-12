import asyncio
import logging
import time
from inspect import iscoroutinefunction
from pathlib import Path

from bleak import BleakScanner

from ThermometerMonitor import settings
from ThermometerMonitor.db_writer import db_writer_worker, init_db, db_cleanup_worker
from ThermometerMonitor.handler_signal import setup_signal_handlers
from ThermometerMonitor.parser import parse_atc_payload, Payload
from ThermometerMonitor.start_scanning import start_scanning
from ThermometerMonitor.watchdog import bluetooth_watchdog

logger = logging.getLogger(Path(__file__).parent.stem)

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
        return settings.DEVICE_PREFIX_DEFAULT + uuid
    return None


def ble_callback(device, advertising_data):
    if settings.get_shutdown_event().is_set():
        return
    settings.last_counter_data["last_packet_time"] = time.monotonic()

    name = advertising_data.local_name or device.name or generate_device_name(device) or device.address or ""
    if settings.DEBUG_EVENTS:
        logger.debug(f"NAME: {name}, {device.address=}")

    # # Filter for target prefix (e.g., 'atc')
    if settings.NAME_PREFIXES[0] and (not name.lower().startswith(settings.NAME_PREFIXES)):
        return
    # # Filter for addresses prefix (e.g., 'A4:')
    if settings.ADDRESS_PREFIXES[0] and (not device.address.lower().startswith(settings.ADDRESS_PREFIXES)):
        return

    # Decode advertisement payload
    # logger.debug(f"{advertising_data=}")
    raw_data = advertising_data.service_data.get(settings.UUID_ENVIRONMENTAL_SENSING)

    if not raw_data:
        return

    parsed: Payload | None = parse_atc_payload(raw_data)

    if not parsed:
        return

    # Deduplication Logic
    frame_counter = parsed.frame_counter
    if frame_counter is not None:
        # Skip if frame_counter matches the last seen frame
        if settings.last_frame_counter.get(name) == frame_counter:
            return

        # Update last seen frame counter and return payload
        settings.last_frame_counter[name] = frame_counter

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

    settings.get_db_queue().put_nowait(record)


async def main():
    logger.info(f"Version of app: {settings.APP_VERSION}")
    shutdown_event = settings.get_shutdown_event()
    db_queue = settings.get_db_queue()

    await init_db()
    loop = asyncio.get_running_loop()
    setup_signal_handlers(loop)

    writer_task = asyncio.create_task(db_writer_worker())
    cleanup_task = asyncio.create_task(db_cleanup_worker())

    scanner = await start_scanning(mode=settings.SCANNING_MODE, callback=ble_callback)

    if scanner is None:
        logger.error(f"[BLE] Bluetooth device not found or it disabled. Sleep 10 seconds")
        await asyncio.sleep(10)
        return

    # Start the watchdog task
    watchdog_task = asyncio.create_task(bluetooth_watchdog(timeout_seconds=settings.WATCHDOG_TIMEOUT))  # noqa

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
    await cleanup_task
    logger.info("[System] Shutdown complete.")


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
