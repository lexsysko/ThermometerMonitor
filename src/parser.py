from dataclasses import dataclass

import logging

import struct


logger = logging.getLogger(f"Monitor.{__name__}")


@dataclass(frozen=True)
class Payload:
    format: str
    temperature_c: float
    humidity_pct: float
    battery_mv: int
    battery_pct: int
    frame_counter: int

    def __post_init__(self):
        """Validate payload fields after initialization."""
        if not isinstance(self.battery_mv, int):
            raise ValueError(f"battery_mv must be an integer, got {type(self.battery_mv)}")

        if not (1000 < self.battery_mv < 3800):
            raise ValueError(f"battery_mv must be between 1000 and 3800, got {self.battery_mv}")

        if not isinstance(self.battery_pct, int):
            raise ValueError(f"battery_pct must be an integer, got {type(self.battery_pct)}")

        if not (0 <= self.battery_pct <= 100):
            raise ValueError(f"battery_pct must be between 0 and 100, got {self.battery_pct}")

        if not (-40.0 <= self.temperature_c <= 85.0):
            raise ValueError(f"temperature_c out of reasonable range: {self.temperature_c}")

        if not (0.0 <= self.humidity_pct <= 100.0):
            raise ValueError(f"humidity_pct must be between 0 and 100, got {self.humidity_pct}")


def parse_atc_payload(raw_bytes) -> Payload | None:
    if not raw_bytes:
        logger.debug("parse_atc_payload empty")
        return None

    length = len(raw_bytes)
    hex_array = " ".join(f"{b:02x}" for b in raw_bytes)
    logger.debug(f"{length=}, Raw bytes (hex): [{hex_array}]")

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
        return Payload(**payload)
    except ValueError as e:
        logger.error(f"Parse error: {e}")
        return None
