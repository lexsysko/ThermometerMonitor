import asyncio
import logging
import time


logger = logging.getLogger(__name__)


async def bluetooth_watchdog(timeout_seconds=60):
    from ThermometerMonitor import settings

    """Monitors packet freshness to catch dead Bluetooth hardware/stack freezes."""

    logger.debug(f"[Watchdog] Watchdog activated. Packet timeout: {timeout_seconds}s.")
    # Warm-up grace period so initial scanning starts before watchdog checks
    await asyncio.sleep((timeout_seconds // 3) or 1)

    while not settings.shutdown_event.is_set():
        await asyncio.sleep((timeout_seconds // 10) or 1)
        last_packet_time: float = settings.last_counter_data.get("last_packet_time", 0.0)
        time_since_last_packet = time.time() - last_packet_time

        logger.debug(f"[Watchdog] Heartbeat check | Secs since last packet: {time_since_last_packet:.1f}s")

        # Catch hardware disconnects, stack stalls, and disabled Bluetooth
        if time_since_last_packet > timeout_seconds:
            logger.error(
                f"[Watchdog] BLE stall detected! No packets for {time_since_last_packet:.0f}s. "
                "Initiating system recovery/shutdown..."
            )
            settings.shutdown_event.set()
            break

    logger.debug("[Watchdog] Watchdog loop exited.")
