import logging

import time

import asyncio


logger = logging.getLogger(f"Monitor.{__name__}")


async def bluetooth_watchdog(timeout_seconds=60):
    from settings import last_counter_data
    from settings import shutdown_event

    """Monitors packet freshness to catch dead Bluetooth hardware/stack freezes."""
    logger.debug(f"[Watchdog] Watchdog active. Packet timeout: {timeout_seconds}s.")

    # Warm-up grace period so initial scanning starts before watchdog checks
    await asyncio.sleep(timeout_seconds // 3)

    while not shutdown_event.is_set():
        await asyncio.sleep(timeout_seconds // 4)
        last_packet_time: float = last_counter_data.get("last_packet_time", 0.0)
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
