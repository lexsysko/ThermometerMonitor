# TermometerMonitor

[![Python Version](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**TermometerMonitor** is an asynchronous Bluetooth Low Energy (BLE) environmental monitor and telemetry logger. It continuously
listens for BLE advertising packets broadcast by smart thermometers and hygrometers (such as Xiaomi Mijia / LYWSD03MMC flashed
with custom **ATC** or **PVVX** firmware), decodes the sensor payloads, deduplicates readings, and stores them in a local SQLite
database.

It comes with ready-to-use **Docker Compose** configurations including a **Grafana** dashboard integration for real-time
visualization.

---

## Features

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
- **Graceful Shutdown**: Intercepts `SIGINT` (Ctrl+C) and `SIGTERM` signals, flushing buffered telemetry to disk cleanly.
- **Grafana Integration**: Pre-configured `compose.yaml` with the SQLite datasource plugin (`frser-sqlite-datasource`) to
  visualize trends instantly.

---

## Project Structure

```text
.
├── compose.yaml          # Docker Compose setup (BLE Monitor + Grafana)
├── Dockerfile            # Multi-stage container build with uv
├── dot.env.example       # Sample environment configuration
├── entrypoint.sh         # Container entrypoint script
├── pyproject.toml        # Project metadata and dependencies
├── data/
│   └── ble_data.db       # SQLite database (generated at runtime)
└── src/
    ├── main.py           # Application entrypoint & scanner lifecycle
    ├── settings.py       # Configuration and environment variables
    ├── parser.py         # BLE payload decoders (PVVX, ATC1441)
    ├── db_writer.py      # Async SQLite batch writer & table schema
    ├── watchdog.py       # Bluetooth stall watchdog worker
    └── handler_signal.py # Cross-platform signal handlers
```

---

## Database Schema

Telemetry is recorded into the SQLite database at `data/ble_data.db` under the table `atc_sensor_data`:

| Column           | Type                  | Description                                        |
|:-----------------|:----------------------|:---------------------------------------------------|
| `id`             | `INTEGER PRIMARY KEY` | Auto-incrementing record ID                        |
| `timestamp`      | `REAL`                | Unix epoch timestamp (seconds)                     |
| `mac_address`    | `TEXT`                | Device Bluetooth MAC address                       |
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

| Variable                     | Default                                | Description                                                                     |
|:-----------------------------|:---------------------------------------|:--------------------------------------------------------------------------------|
| `NAME_PREFIXES`              | `""` *(empty / all)*                   | Comma-separated list of device name prefixes to filter (e.g. `ATC_112233,ATC_`) |
| `DEVICE_PREFIX_DEFAULT`      | `ATC_`                                 | Prefix prepended to device names when an explicit name is absent                |
| `LOG_LEVEL`                  | `INFO`                                 | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)                             |
| `SCANNING_MODE`              | `auto`                                 | BLE scanning mode (`auto`, `active`, `passive`)                                 |
| `WATCHDOG_TIMEOUT`           | `300`                                  | Inactivity threshold in seconds before watchdog flags a stall                   |
| `UUID_ENVIRONMENTAL_SENSING` | `0000181a-0000-1000-8000-00805f9b34fb` | BLE Service Data UUID for Environmental Sensing (181A)                          |

---

## Getting Started

### Prerequisites

- **Bluetooth Adapter**: BLE-compatible Bluetooth 4.0+ hardware.
- **Operating System**:
    - Linux with **BlueZ** and DBus (recommended for production / 24x7 monitoring).
    - Windows or macOS (for development and local testing).
- **Python**: Python 3.13 or higher (or [uv](https://github.com/astral-sh/uv)).

---

### Local Installation & Running

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-username/TermometerMonitor.git
   cd TermometerMonitor
   ```

2. **Install dependencies**:
   Using `uv`:
   ```bash
   uv sync
   ```
   Or standard `pip` / `venv`:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install .
   ```

3. **Configure environment**:
   ```bash
   cp dot.env.example .env
   # Edit .env to set prefixes or logging level
   ```

4. **Run the monitor**:
   ```bash
   python src/main.py
   ```

---

### Running with Docker Compose

Running via Docker Compose is recommended on Linux hosts to ensure persistent background monitoring and integrated Grafana
dashboarding.

> **Note (Linux / BlueZ)**: Host network mode (`network_mode: host`) and access to `/var/run/dbus/system_bus_socket` along with
> `NET_ADMIN` and `NET_RAW` capabilities are configured in `compose.yaml` to enable direct Bluetooth hardware access.

1. **Start services**:
   ```bash
   docker compose up -d
   ```

2. **Check monitor logs**:
   ```bash
   docker compose logs -f ble-monitor
   ```

3. **Open Grafana**:
    - Access Grafana at: [http://localhost:3000](http://localhost:3000)
    - Default login: `admin` / `admin` (or configured `GF_SECURITY_ADMIN_PASSWORD`)
    - The SQLite plugin `frser-sqlite-datasource` is automatically installed.
    - Set up SQLite datasource pointing to `/var/lib/grafana/sqlite_data/ble_data.db`.

---

## Supported Hardware & Firmware

This project is tested and compatible with:

- **Xiaomi Mijia Bluetooth Thermometer 2 (LYWSD03MMC)**
- Custom firmwares:
    - [pvvx/ATC_MiThermometer](https://github.com/pvvx/ATC_MiThermometer)
    - [atc1441/ATC_MiThermometer](https://github.com/atc1441/ATC_MiThermometer)
- Any BLE broadcaster sending standard `0x181A` environmental service advertisement payloads.

## Ubuntu server headless

```bash
# 1. Install bluez on the host if missing
sudo apt update && sudo apt install -y bluez rfkill

# 2. Check if bluetooth is blocked by rfkill
sudo rfkill unblock bluetooth

# 3. Enable and start the Bluetooth service
sudo systemctl enable --now bluetooth

# 4. Verify BlueZ is active and registered on D-Bus
sudo systemctl status bluetooth
```

### Resolve problems with passive mode

BlueZ passive scanning with advertisement pattern filtering requires both Kernel >= 5.10 and the BlueZ experimental interface flag
enabled on the host machine.

Without --experimental turned on in the host's bluetooth.service, BlueZ refuses to expose the AdvertisementMonitor1 D-Bus
interface that Bleak relies on for passive pattern filtering.

#### Solution: Enable BlueZ Experimental Features on Host

1. Edit the host's systemd service for Bluetooth:

```bash
sudo systemctl edit bluetooth.service
```

2. Add the experimental flag:
   Paste the following configuration into the file override and save:

```toml
[Service]
ExecStart =
ExecStart = /usr/libexec/bluetooth/bluetoothd --experimental
```

> (Note: On older Ubuntu versions, the binary path might be /usr/lib/bluetooth/bluetoothd).

3. Reload systemd and restart the service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart bluetooth
 
```

4. Verify --experimental is active:

```bash
systemctl status bluetooth
```

Look for `bluetoothd --experimental` in the active process line.

---

## License

This project is licensed under the [MIT License](LICENSE).
