import asyncio
import logging
import time
from os import environ
from pathlib import Path

from dotenv import load_dotenv

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
