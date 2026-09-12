import asyncio
import logging
import time
from os import environ
from pathlib import Path

from dotenv import load_dotenv

from ThermometerMonitor import __version__

BASE_PATH = Path(__file__).parent.parent.parent

for folder in (Path.cwd(), BASE_PATH):
    if (folder / ".env").exists():
        load_dotenv()
        break


db_env_path: str | None = environ.get("DB_PATH")

if db_env_path:
    DB_PATH = Path(db_env_path).expanduser().resolve()
else:
    DB_PATH = BASE_PATH / "data/ble_data.db"
    if not DB_PATH.parent.exists():
        # Safe for both CLI package execution and local development
        DB_PATH = Path.cwd() / "data/ble_data.db"

DB_PATH.parent.mkdir(exist_ok=True, parents=True)
APP_VERSION = __version__
SCANNING_MODE: str = environ.get("SCANNING_MODE", "auto")
WATCHDOG_TIMEOUT: int = int(environ.get("WATCHDOG_TIMEOUT", 600))
CLEANUP_TIMEOUT: int = int(environ.get("CLEANUP_TIMEOUT", 60 * 60 * 24))
CLEANUP_PERIOD_DAYS: int = int(environ.get("CLEANUP_PERIOD_DAYS", 30))
LOG_LEVEL = getattr(logging, environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
DEVICE_PREFIX_DEFAULT: str = environ.get("DEVICE_PREFIX_DEFAULT", "ATC_")
DEBUG_EVENTS: bool = environ.get("DEBUG_EVENTS", "f").strip()[0].lower() == "t"
NAME_PREFIXES: tuple[str] = tuple(s.strip().lower() for s in environ.get("NAME_PREFIXES", "").split(","))
ADDRESS_PREFIXES: tuple[str] = tuple(s.strip().lower() for s in environ.get("ADDRESS_PREFIXES", "").split(","))

UUID_ENVIRONMENTAL_SENSING: str = environ.get("UUID_ENVIRONMENTAL_SENSING", "0000181a-0000-1000-8000-00805f9b34fb")

_shutdown_event = None
_db_queue = None

last_frame_counter = {}
last_packet_time = time.time()
last_counter_data = {"last_packet_time": time.time()}


def get_shutdown_event() -> asyncio.Event:
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    return _shutdown_event


def get_db_queue() -> asyncio.Queue:
    global _db_queue
    if _db_queue is None:
        _db_queue = asyncio.Queue()
    return _db_queue
