import asyncio
import io
import os
import types
import logging
from contextlib import redirect_stdout
from enum import Enum

from settings import DEBUG_EVENTS, ADDRESS_PREFIXES

logger = logging.getLogger(f"Monitor.{__name__}")


class AdvTypes(Enum):
    ADV_IND = "0"
    ADV_DIRECT_IND_HIGH = "1"
    ADV_SCAN_IND = "2"
    ADV_NONCONN_IND = "3"
    ADV_DIRECT_IND_LOW = "4"


class HCIPassiveScannerProtocol(asyncio.DatagramProtocol):
    """Clean asyncio DatagramProtocol for raw HCI Bluetooth scanning in Python 3.10+"""

    def __init__(self, process_callback, is_active: bool = False, filter_addresses: tuple[str] | None = None):
        self.process_callback = process_callback
        self.is_active = is_active
        self.transport = None
        self._sock_fd = None
        self.prefilter_event_types = (AdvTypes.ADV_IND.value,)
        self.filter_addresses = filter_addresses

    @staticmethod
    def format_str(env_obj) -> str:
        if env_obj is None:
            return ""
        return str(getattr(env_obj, "val", env_obj))  # noqa

    def _write_hci_cmd(self, cmd_bytes: bytes):
        """Writes raw HCI command bytes directly to the socket file descriptor."""
        if self._sock_fd is not None:
            try:
                os.write(self._sock_fd, cmd_bytes)
            except Exception as err:
                logger.error(f"Failed to write HCI command to socket: {err}")

    def connection_made(self, transport):
        self.transport = transport
        # Get underlying socket file descriptor to bypass DatagramTransport sendto() format checks
        sock = transport.get_extra_info("socket")
        if sock:
            self._sock_fd = sock.fileno()

        import aioblescan as aiobs

        try:
            # 1. Configure HCI scan parameters (0 = Passive, 1 = Active)
            scan_type = 1 if self.is_active else 0
            params_cmd = aiobs.HCI_Cmd_LE_Set_Scan_Params(scan_type=scan_type)
            self._write_hci_cmd(params_cmd.encode())

            # 2. Turn scanning on
            enable_cmd = aiobs.HCI_Cmd_LE_Scan_Enable(enable=True, filter_dups=False)
            self._write_hci_cmd(enable_cmd.encode())

            logger.info(f"HCI Scanning started. Passive mode is {not self.is_active}")
        except Exception as err:
            logger.error(f"Failed to initialize HCI scanner: {err}")

    async def stop(self):
        """Sends disable command to HCI controller and closes transport."""
        logger.info("Stopping HCI BLE Scanner...")
        import aioblescan as aiobs

        # Send command to disable scanning
        disable_cmd = aiobs.HCI_Cmd_LE_Scan_Enable(enable=False)
        self._write_hci_cmd(disable_cmd.encode())

        # Close transport connection
        if self.transport:
            self.transport.close()

    def datagram_received(self, data: bytes, addr):
        import aioblescan as aiobs

        ev = aiobs.HCI_Event()
        try:
            ev.decode(data)
        except Exception:
            return

        # --- PRE-FILTER BY EVENT TYPE ---
        ev_types = ev.retrieve("ev type")
        if not ev_types:
            return

        event_type = self.format_str(ev_types[0])

        # logger.debug(f"{event_type=}")
        #
        if event_type not in self.prefilter_event_types:
            return

        mac_list = ev.retrieve("peer")
        rssi_list = ev.retrieve("rssi")
        local_name_list = ev.retrieve("COMPLETE LOCAL NAME") or ev.retrieve("SHORTENED LOCAL NAME")

        mac_address = self.format_str(mac_list[0]) if mac_list else ""
        # logger.debug(f"event received: {ev}")
        if self.filter_addresses and mac_address and not mac_address.lower().startswith(self.filter_addresses):
            return
        rssi = self.format_str(rssi_list[0]) if rssi_list else 0
        local_name = local_name_list[0] if local_name_list else None

        if not (mac_address or local_name):
            # logger.error(f"HCI mac_address local_name is empty or None")
            return

        if DEBUG_EVENTS:
            logger.debug(f"{event_type=}")
            output_buffer = io.StringIO()
            with redirect_stdout(output_buffer):
                ev.show()  # Any print() inside show() writes to the buffer instead of stdout
            captured_text = output_buffer.getvalue()
            # Send the captured string to your logger instead of raw print
            logger.debug(f"\n{captured_text}")
            logger.debug(f"{mac_address=}")
            logger.debug(f"{rssi=}")
            logger.debug(f"{local_name=}")

        # logger.debug(ev.__dir__())

        service_data = {}
        svc_uuid_list = ev.retrieve("Service Data uuid")
        adv_payload_list = ev.retrieve("Adv Payload")

        if adv_payload_list:
            uuid_16_str = "181a"  # Default fallback (Environmental Sensing)

            if svc_uuid_list:
                raw_uuid = getattr(svc_uuid_list[0], "val", svc_uuid_list[0])

                # Handle raw bytes or bytearray (e.g., b'\x18\x1a' -> "181a")
                if isinstance(raw_uuid, (bytes, bytearray)):
                    # Big-endian formatting to yield "181a"
                    uuid_16_str = raw_uuid.hex().lower()
                # Handle string objects (e.g., "18:1a" or "181a")
                elif isinstance(raw_uuid, str):
                    uuid_16_str = raw_uuid.replace(":", "").replace("-", "").lower()

            # Ensure 8-character hex prefix for standard 128-bit UUID format
            uuid_str = f"{uuid_16_str.zfill(8)}-0000-1000-8000-00805f9b34fb"

            # Extract raw payload bytes directly
            raw_payload = getattr(adv_payload_list[0], "val", adv_payload_list[0])
            if isinstance(raw_payload, (bytes, bytearray)):
                service_data[uuid_str] = bytes(raw_payload)
            elif isinstance(raw_payload, str):
                service_data[uuid_str] = bytes.fromhex(raw_payload.replace(":", "").replace(" ", ""))

        if not service_data:
            return

        device = types.SimpleNamespace(address=mac_address.upper(), name=local_name)
        advertising_data = types.SimpleNamespace(local_name=local_name, service_data=service_data, rssi=rssi)

        if DEBUG_EVENTS:
            logger.debug(f"{advertising_data=}")

        if callable(self.process_callback):
            self.process_callback(device, advertising_data)

    def error_received(self, exc):
        logger.error(f"HCI Transport error: {exc}")

    def connection_lost(self, exc):
        logger.info("HCI Transport closed.")
