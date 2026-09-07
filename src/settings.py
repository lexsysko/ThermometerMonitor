from os import environ
from pathlib import Path

import asyncio
import logging
import time
from dotenv import load_dotenv

BASE_PATH = Path(__file__).parent.parent
if (BASE_PATH / ".env").exists():
    load_dotenv()
DB_PATH = BASE_PATH / "data/ble_data.db"

SCANNING_MODE: str = environ.get("SCANNING_MODE", "auto")
WATCHDOG_TIMEOUT: int = int(environ.get("WATCHDOG_TIMEOUT", 600))
LOG_LEVEL = getattr(logging, environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
DEVICE_PREFIX_DEFAULT: str = environ.get("DEVICE_PREFIX_DEFAULT", "ATC_")
DEBUG_EVENTS: bool = environ.get("DEBUG_EVENTS", "f").strip()[0].lower() == "t"
NAME_PREFIXES: tuple[str] = tuple(s.strip().lower() for s in environ.get("NAME_PREFIXES", "").split(","))
ADDRESS_PREFIXES: tuple[str] = tuple(s.strip().lower() for s in environ.get("ADDRESS_PREFIXES", "").split(","))

UUID_ENVIRONMENTAL_SENSING: str = environ.get("UUID_ENVIRONMENTAL_SENSING", "0000181a-0000-1000-8000-00805f9b34fb")


shutdown_event = asyncio.Event()
db_queue = asyncio.Queue()

last_frame_counter = {}
last_packet_time = time.time()
last_counter_data = {"last_packet_time": time.time()}
