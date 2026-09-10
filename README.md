# TermometerMonitor

[![Python Version](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**TermometerMonitor** is an asynchronous Bluetooth Low Energy (BLE) environmental monitor and telemetry logger. It continuously
listens for BLE advertising packets broadcast by smart thermometers and hygrometers (such as Xiaomi Mijia / LYWSD03MMC flashed
with custom **ATC** or **PVVX** firmware), decodes sensor payloads, deduplicates readings, and stores them in a local SQLite
database.

It supports **cross-platform operation** (Linux, macOS, Windows) out of the box via **Bleak**, while featuring an **automatic
Linux raw HCI socket fallback** for legacy adapters or specialized container setups.

It includes ready-to-use **Docker Compose** configurations along with a **Grafana** dashboard integration for real-time
visualization.

---

## Features

- **Cross-Platform BLE Scanning**:
    - Primary driver powered by **Bleak** for native support across **Linux**, **macOS**, and **Windows**.
    - **Smart Linux Fallback**: On Linux systems where native passive scanning via BlueZ / BT 5.0 is unavailable or unsupported by
      the hardware (HCI version < 9), it automatically falls back to a custom **low-level HCI raw socket scanner (`aioblescan` +
      `HCIPassiveScannerProtocol`)**.
- **Multi-Format Sensor Parsing**: Supports popular custom firmware advertisement formats:
    - **PVVX 15-byte** (`pvvx_15b`)
    - **PVVX 18-byte** (`pvvx_18b`)
    - **ATC1441 13-byte** (`atc1441`)
- **Metrics Captured**:
    - Temperature (°C)
    - Relative Humidity (%)
    - Battery Level (% and mV)
    - Signal Strength (RSSI in dBm)
    - Frame Counter & Payload Format
    - Device MAC Address & Name
- **Deduplication**: Filters out redundant packets using frame counters to minimize database bloat.
- **Async Batch Storage**: Queues incoming sensor events and writes them in batches to SQLite with **WAL (Write-Ahead Logging)**
  mode and indexed queries.
- **Hardware Watchdog**: Monitors packet freshness and initiates graceful recovery if the Bluetooth stack or adapter stalls.
- **Graceful Shutdown**: Intercepts standard termination signals (`SIGINT`, `SIGTERM`), flushing buffered telemetry to disk
  cleanly.
- **Grafana Integration**: Pre-configured `compose.yaml` with the SQLite datasource plugin (`frser-sqlite-datasource`) to
  visualize trends instantly.

---

## Architecture & Scanning Modes

```text
               +----------------------------------+
               |      start_scanning(mode)        |
               +----------------------------------+
                                |
               +----------------------------------+
               |  Is Linux AND Passive Scanning?  |
               +----------------------------------+
                     /                      \
               [ No / Other OS ]          [ Yes ]
                    /                        \
                   v                          v
        +--------------------+      +--------------------+
        | Standard Bleak     |      | Check BT 5.0      |
        | Scanner            |      | Capabilities       |
        | (macOS / Win /     |      +--------------------+
        | Linux D-Bus)       |         /              \
        +--------------------+     [Supported]    [Not Supported]
                                       /              \
                                      v                v
                             +------------------+  +--------------------+
                             | Bleak + BlueZ    |  | Raw HCI Socket     |
                             | Passive Patterns |  | Fallback Scanner   |
                             +------------------+  | (aioblescan)       |
                                                   +--------------------+
```

---

## Project Structure

```text
.
├── compose.yaml                      # Docker Compose setup (BLE Monitor + Grafana)
├── Dockerfile                        # Multi-stage container build with uv
├── dot.env.example                   # Sample environment configuration
├── entrypoint.sh                     # Container entrypoint script
├── pyproject.toml                    # Project metadata and dependencies
├── data/
│   └── ble_data.db                   # SQLite database (generated at runtime)
└── src/
    ├── main.py                       # Application entrypoint & scanner lifecycle
    ├── settings.py                   # Configuration and environment variables
    ├── hci_passive_scanner_protocol.py # Low-level HCI socket protocol handler (Linux fallback)
    ├── parser.py                     # BLE payload decoders (PVVX, ATC1441)
    ├── db_writer.py                  # Async SQLite batch writer & table schema
    ├── watchdog.py                   # Bluetooth stall watchdog worker
    └── handler_signal.py             # Cross-platform signal handlers
```

---

## Database Schema

Telemetry is recorded into the SQLite database at `data/ble_data.db` under the table `atc_sensor_data`:

| Column           | Type                  | Description                                        |
|------------------|-----------------------|----------------------------------------------------|
| `id`             | `INTEGER PRIMARY KEY` | Auto-incrementing record ID                        |
| `timestamp`      | `REAL`                | Unix epoch timestamp (seconds)                     |
| `mac_address`    | `TEXT`                | Device Bluetooth MAC address (`XX:XX:XX:XX:XX:XX`) |
| `device_name`    | `TEXT`                | Local advertised name or generated ID              |
| `rssi`           | `INTEGER`             | Received Signal Strength Indicator (dBm)           |
| `temperature_c`  | `REAL`                | Temperature in Celsius                             |
| `humidity_pct`   | `REAL`                | Relative Humidity percentage (0–100%)              |
| `battery_pct`    | `INTEGER`             | Battery level percentage (0–100%)                  |
| `battery_mv`     | `INTEGER`             | Battery voltage in millivolts (mV)                 |
| `frame_counter`  | `INTEGER`             | Sensor advertisement sequence counter              |
| `payload_format` | `TEXT`                | Decoded format (`pvvx_15b`, `pvvx_18b`, `atc1441`) |

---

## Configuration

Configuration is managed via environment variables or a `.env` file in the project root. Copy the template to get started:

```bash
cp dot.env.example .env
```

### Environment Variables

| Variable                     | Default                                | Description                                                                            |
|------------------------------|----------------------------------------|----------------------------------------------------------------------------------------|
| `NAME_PREFIXES`              | `""` *(empty / all)*                   | Comma-separated list of device name prefixes to filter (e.g. `ATC_112233,ATC_`)        |
| `ADDRESS_PREFIXES`           | `""` *(empty / all)*                   | Comma-separated list of device addresses prefixes to filter (e.g. `A4:C1:38,A1:C1:18`) |
| `DEVICE_PREFIX_DEFAULT`      | `ATC_`                                 | Prefix prepended to device names when an explicit name is absent                       |
| `LOG_LEVEL`                  | `INFO`                                 | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)                                    |
| `SCANNING_MODE`              | `auto`                                 | BLE scanning mode (`auto`, `active`, `passive`)                                        |
| `WATCHDOG_TIMEOUT`           | `600`                                  | Inactivity threshold in seconds before watchdog flags a stall                          |
| `UUID_ENVIRONMENTAL_SENSING` | `0000181a-0000-1000-8000-00805f9b34fb` | BLE Service Data UUID for Environmental Sensing (181A)                                 |

---

## Getting Started

### Local Setup (macOS / Windows / Linux)

You can run TermometerMonitor directly on your local machine without Docker or elevated root permissions in most OS environments.

1. **Clone the repository**:

```bash
git clone [https://github.com/lexsysko/TermometerMonitor.git](https://github.com/lexsysko/TermometerMonitor.git)
cd TermometerMonitor
```

2. **Install dependencies**:
   Using `uv`:

```bash
uv sync

```

Or standard `pip`:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install .
```

3. **Run the monitor**:

```bash
uv run src/main.py
```

or

```bash
python src/main.py
```

*(Note: On Linux, if using the raw HCI fallback mode, ensure your user account has access to raw socket capabilities or run with
`sudo setcap cap_net_raw,cap_net_admin+eip $(readlink -f $(which python))`)*

---

### Running with Docker Compose

Running via Docker Compose is ideal for dedicated monitoring servers (e.g., Raspberry Pi, home server) and includes Grafana
visualization out of the box.

1. **Start services**:

```bash
docker compose up -d
```

2. **Check monitor logs**:

```bash
docker compose logs -f ble-monitor
```

3. **Open Grafana**:

* Access Grafana at: [http://localhost:3000](http://localhost:3000)
* Default login: `admin` / `admin`
* Pre-configured datasource path: `/var/lib/grafana/sqlite_data/ble_data.db`.

*Note: For Docker on Linux utilizing the HCI socket fallback, the container config uses `network_mode: host` and
`cap_add: [NET_RAW, NET_ADMIN]`.*

4. **SQL for Grafana**

```sql
SELECT
  timestamp AS time,
  COALESCE(NULLIF(device_name, ''), mac_address) AS metric,
  temperature_c AS value
FROM atc_sensor_data
WHERE timestamp >= ($__from / 1000)
  AND timestamp <= ($__to / 1000)
  AND COALESCE(NULLIF(device_name, ''), mac_address) IN (${device:singlequote})
ORDER BY timestamp ASC;
```

---

## Linux Host Troubleshooting

If running on Linux and the system drops down to the HCI socket fallback:

```bash
# 1. Install bluez/rfkill utilities if missing
sudo apt update && sudo apt install -y bluez rfkill

# 2. Unblock the Bluetooth radio
sudo rfkill unblock bluetooth

# 3. Ensure the HCI interface is active
sudo hciconfig hci0 up

```

---

## Supported Hardware & Firmware

* **Xiaomi Mijia Bluetooth Thermometer 2 (LYWSD03MMC)**
* Custom firmwares:
* [pvvx/ATC_MiThermometer](https://github.com/pvvx/ATC_MiThermometer)
* [atc1441/ATC_MiThermometer](https://github.com/atc1441/ATC_MiThermometer)


* Any BLE broadcaster sending standard `0x181A` environmental service advertisement payloads.

---

## License

This project is licensed under the [MIT License](https://www.google.com/search?q=LICENSE).

