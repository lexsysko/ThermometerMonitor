import logging

import struct


logger = logging.getLogger("Monitor.{__name__}")


def parse_atc_payload(raw_bytes) -> dict | None:
    if not raw_bytes:
        logger.debug("parse_atc_payload empty")
        return None

    length = len(raw_bytes)
    hex_array = " ".join(f"{b:02x}" for b in raw_bytes)
    logger.debug(f"{length=}, {raw_bytes=}")
    logger.debug(f"Raw bytes (hex): [{hex_array}]")

    try:
        if length == 15:
            """Update the data of a registered BLE device."""
            # temp_raw = int.from_bytes(raw_bytes[6:8], byteorder="little", signed=True)
            # hum_raw = int.from_bytes(raw_bytes[8:10], byteorder="little", signed=True)
            # batt_mv = int.from_bytes(raw_bytes[10:12], byteorder="little", signed=False)
            # batt_pct = int.from_bytes(raw_bytes[12:13], byteorder="little", signed=False)
            # frame_cnt = int.from_bytes(raw_bytes[13:14], byteorder="little", signed=False)
            _, temp_raw, hum_raw, batt_mv, batt_pct, frame_cnt, _ = struct.unpack("<6shHHBBB", raw_bytes)

            payload = {
                "format": "pvvx_15b",
                "temperature_c": temp_raw / 100.0,
                "humidity_pct": hum_raw / 100.0,
                "battery_mv": batt_mv,
                "battery_pct": batt_pct,
                "frame_counter": frame_cnt,
            }

        # 1. Custom / pvvx Format (18 bytes, Little-Endian)
        elif length == 18:
            _, temp_raw, hum_raw, batt_mv, batt_pct, frame_cnt, _ = struct.unpack("<6shHHBBB", raw_bytes)
            payload = {
                "format": "pvvx_18b",
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
        battery_mv = payload.get("battery_mv", None)
        if not battery_mv or (not isinstance(battery_mv, float)) or (not (1500 < battery_mv < 4000)):
            logger.error("Parse error: battery_mv")
            return None
        return payload
    except ValueError as e:
        logger.error(f"Parse error: {s}")
        return None
