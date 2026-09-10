import asyncio
import logging
import platform
import re
import subprocess
from types import SimpleNamespace
from typing import Callable, Any

from bleak import BleakScanner, BlueZScannerArgs
from bleak.args.bluez import OrPattern
from bleak.assigned_numbers import AdvertisementDataType
from bleak.exc import BleakBluetoothNotAvailableError

from ThermometerMonitor.hci_passive_scanner_protocol import HCIPassiveScannerProtocol
from ThermometerMonitor.settings import NAME_PREFIXES, ADDRESS_PREFIXES

logger = logging.getLogger(__name__)


def is_linux():
    return platform.system() == "Linux"


async def aio_bt_scanner(callback: Callable, scanning_mode: str = "passive", **kwargs):
    if not is_linux():
        logger.warning(f"Only for Linux OS. Now is: {platform.system()}")
        return None

    logger.info("Load 'aioblescan' library for access to HCI Event")

    import aioblescan as aiobs

    def _hci_process(data):
        ev = aiobs.HCI_Event()
        raw_packet = ev.decode(data)

        # 1. Extract raw fields from aioblescan
        mac_list = ev.retrieve("peer")
        rssi_list = ev.retrieve("rssi")
        local_name_list = ev.retrieve("COMPLETE LOCAL NAME") or ev.retrieve("SHORTENED LOCAL NAME")

        mac_address = str(mac_list[0]).upper() if mac_list else ""
        rssi = rssi_list[0] if rssi_list else 0
        local_name = local_name_list[0] if local_name_list else None

        # 2. Extract Service Data (16-bit UUIDs e.g., 0x181A for Environmental Sensing)
        service_data = {}

        # aioblescan returns raw AD payload structures
        for ad_struct in getattr(ev, "adv_payload", []):
            # Check for Service Data - 16-bit UUID (Type 0x16)
            if hasattr(ad_struct, "type") and ad_struct.type == 0x16:
                raw_payload = ad_struct.payload
                if len(raw_payload) >= 2:
                    # First 2 bytes are the 16-bit UUID in little-endian format
                    uuid_16 = (raw_payload[1] << 8) | raw_payload[0]
                    uuid_str = f"{uuid_16:08x}-0000-1000-8000-00805f9b34fb"
                    payload_bytes = bytes(raw_payload[2:])
                    service_data[uuid_str] = payload_bytes

        # 3. Construct Bleak-compatible mock objects
        device = SimpleNamespace(address=mac_address, name=local_name)

        advertising_data = SimpleNamespace(local_name=local_name, service_data=service_data, rssi=rssi)

        # 4. Invoke your existing callback
        if callable(callback):
            callback(device, advertising_data)

    event_loop = asyncio.get_running_loop()
    bt_socket = aiobs.create_bt_socket(0)
    bt_socket.setblocking(False)
    is_active = scanning_mode == "active"

    # Use DatagramProtocol wrapper compatible with Python 3.10 - 3.14+
    transport, protocol = await event_loop.create_datagram_endpoint(
        lambda: HCIPassiveScannerProtocol(
            process_callback=callback, is_active=is_active, filter_addresses=ADDRESS_PREFIXES
        ),
        sock=bt_socket,
    )

    return transport


def is_linux_bt5_supported():
    """Checks if the system is Linux AND has a BT 5.0+ (HCI version >= 9) controller."""
    if not is_linux():
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


async def start_scanning(mode: str, callback: Callable) -> Any | None:
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
            if mode == "passive" and is_linux():
                if is_linux_bt5_supported():
                    options = {"bluez": bluez_passive_args}
                else:
                    # fallback to use aioblescan with HCI Event access
                    scanner = await aio_bt_scanner(callback, scanning_mode=mode)
                    if scanner is not None:
                        return scanner
            scanner = BleakScanner(callback, scanning_mode=mode, **options)
            await scanner.start()
            return scanner
        except BleakBluetoothNotAvailableError as e:
            logger.error(f"Error in {mode} mode: {e}")
            return None
        except Exception as e:
            logger.error(f"Error in {mode} mode: {e}")
    return None
