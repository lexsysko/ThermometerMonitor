import platform

import re

import subprocess

import logging
from bleak import BleakScanner, BlueZScannerArgs, BleakError
from bleak.args.bluez import OrPattern
from bleak.assigned_numbers import AdvertisementDataType
from bleak.exc import BleakBluetoothNotAvailableError
from typing import Callable

from settings import NAME_PREFIXES, ADDRESS_PREFIXES

logger = logging.getLogger(f"Monitor.{__name__}")


def is_linux_bt5_supported():
    """Checks if the system is Linux AND has a BT 5.0+ (HCI version >= 9) controller."""
    if platform.system() != "Linux":
        return False

    try:
        output = subprocess.check_output(["hciconfig", "-a", "hci0"], text=True)
        match = re.search(r"HCI Version:\s*([\d\.]+)\s*\(0x([0-9a-fA-F]+)\)", output)
        if match:
            hci_version_hex = int(match.group(2), 16)
            # 0x9 = Bluetooth 5.0 (0x8 = BT 4.2)
            bt5_supported = hci_version_hex >= 9
            if not bt5_supported:
                logger.warning("Bluetooth 5.0 capabilities for passive scanning are not supported on this host")
            return bt5_supported
    except Exception as e:
        logger.warning(f"[BLE] Could not query hciconfig on Linux: {e}")

    return False


async def start_scanning(mode: str, callback: Callable) -> BleakScanner | None:
    """Start scanning for BLE devices."""

    # For Ubuntu Docker permission for passive mode 'bluetoothd --experimental'
    # bluez_passive_args = BlueZScannerArgs(
    #     or_patterns=[
    #         # Type 0x16 = SERVICE_DATA_UUID16, matching b"\x1a\x18" at start offset 0
    #         (0, AdvertisementDataType.SERVICE_DATA_UUID16, b"\x1a\x18")
    #     ]
    # )
    bluez_passive_args = BlueZScannerArgs(
        or_patterns=[
            OrPattern(0, AdvertisementDataType.FLAGS, bytes([v]))
            for v in range(0x20)  # 0x00-0x1f covers the common flag combos
        ]
    )

    modes = ("passive", "active") if mode.lower() == "auto" else (mode,)
    options = {}
    filter_list = []
    if NAME_PREFIXES[0]:
        filter_list.append(f"Names: {','.join(NAME_PREFIXES)}")
    if ADDRESS_PREFIXES[0]:
        filter_list.append(f"Addresses: {','.join(ADDRESS_PREFIXES)}")

    for mode in modes:
        logger.info(f"[BLE] Attempting scan for sensors matching prefix '{', '.join(filter_list)}' in {mode} mode...")
        try:
            if mode not in ("active", "passive"):
                raise ValueError("Mode must be either 'active' or 'passive'.")
            if mode == "passive" and is_linux_bt5_supported():
                options = {"bluez": bluez_passive_args}
            scanner = BleakScanner(callback, scanning_mode=mode, **options)
            await scanner.start()
            return scanner
        except BleakBluetoothNotAvailableError as e:
            logger.error(f"Error in {mode} mode: {e}")
            return None
        except Exception as e:
            logger.error(f"Error in {mode} mode: {e}")
    return None
