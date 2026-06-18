<div align="center">

```
███╗   ██╗███████╗ ██████╗ ███████╗████████╗██████╗ ██╗██╗  ██╗███████╗
████╗  ██║██╔════╝██╔═══██╗██╔════╝╚══██╔══╝██╔══██╗██║██║ ██╔╝██╔════╝
██╔██╗ ██║█████╗  ██║   ██║███████╗   ██║   ██████╔╝██║█████╔╝ █████╗  
██║╚██╗██║██╔══╝  ██║   ██║╚════██║   ██║   ██╔══██╗██║██╔═██╗ ██╔══╝  
██║ ╚████║███████╗╚██████╔╝███████║   ██║   ██║  ██║██║██║  ██╗███████╗
╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝╚══════╝
```

**A cyberpunk network stress-testing tool with a glassmorphism desktop GUI and a full-featured CLI.**

![Python](https://img.shields.io/badge/Python-3.10%2B-cyan?style=flat-square&logo=python&logoColor=white)
![C#](https://img.shields.io/badge/C%23-.NET_8-68217A?style=flat-square&logo=dotnet&logoColor=white)
![WPF](https://img.shields.io/badge/WPF-Glassmorphism-0078D4?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

</div>

---

> **AUTHORIZED USE ONLY** — NeoStrike is built strictly for testing systems you own or have explicit written permission to test. Unauthorized use against third-party infrastructure is illegal. You're responsible for how you use this.

---

## What is this?

NeoStrike started as a Python CLI tool for load-testing web servers and raw TCP/UDP endpoints. I later added a desktop GUI because clicking buttons is nicer than remembering CLI flags.

There are two ways to use it:

- **CLI** (`neostrike.py`) — the full engine, 10 attack modes, live terminal dashboard, distributed testing, plugin system, structured logging. This is where all the power lives.
- **Desktop GUI** (`NeoStrike/`) — a C# WPF app with a glassmorphism UI. 11 attack modes, real-time bandwidth charts, preset save/load, JSON export. It wraps the same core logic in a pretty package.

Both do the same thing — pick whichever fits your workflow.

---

## Features

### CLI Engine
- **10 attack modes** — GET, POST, HEAD, PUT, DELETE, TCP, UDP, SLOWLORIS, RUDY, WEBSOCKET
- **Async HTTP engine** — asyncio + aiohttp with token-bucket rate limiting
- **Raw socket modes** — TCP/UDP flood via thread pool
- **Interactive config wizard** — guided setup with DNS resolution up front (bad targets fail before the test starts)
- **Live rich dashboard** — 4 panels: telemetry, responses, live feed, progress bar
- **Distributed testing** — master/worker architecture over TCP
- **Web dashboard** — embedded HTML+JS served via aiohttp with WebSocket for real-time stats
- **Plugin system** — drop a Python file in `plugins/` and it gets loaded automatically
- **Structured logging** — every session writes a timestamped log with per-request outcomes
- **JSON/CSV export** — save results for later analysis
- **Preset system** — save and load full attack configurations as JSON files

### Desktop GUI (WPF)
- **11 attack modes** — everything from the CLI plus SYN flood
- **Glassmorphism design** — frosted glass panels, animated gradient orbs, dark theme
- **Real-time bandwidth chart** — area chart with gradient fill and peak indicator
- **KPI tiles** — packets/sec, MB/s, active connections, errors — all live
- **Preset save/load** — export and import full configs as JSON
- **Custom HTTP path** — target specific endpoints like `/api/login`
- **Pulse mode** — burst/pause timing control
- **One-click export** — save results to JSON

---

## Getting Started

### CLI

```bash
git clone https://github.com/Mayzam-exe/Neostrike-loadtest-GUI.git
cd Neostrike-loadtest-GUI
pip install -r requirements.txt
python neostrike.py
```

### Desktop GUI

**Option A — Build from source**

1. Open `NeoStrike/NeoStrike.sln` in Visual Studio 2022 or later
2. Set build configuration to `Release | x64`
3. Build the solution

**Option B — Publish a standalone exe**

```bash
cd NeoStrike/NeoStrike
dotnet publish -c Release -r win-x64 --self-contained -p:PublishSingleFile=true
```

The output exe will be in `bin/Release/net8.0-windows/win-x64/publish/`. It's self-contained — no .NET runtime install needed on the target machine.

**Option C — Download a release**

Check the [Releases](https://github.com/Mayzam-exe/Neostrike-loadtest-GUI/releases) page for pre-built exe downloads.

---

## How the CLI Works

When you launch `neostrike.py`, you get a guided setup:

1. **Disclaimer gate** — you confirm you're authorized to test the target
2. **Mode selection** — pick from a formatted table
3. **Target input** — enter host, host:port, or full URL (DNS resolves immediately)
4. **Tuning** — concurrency, duration, request count, headers, proxy
5. **Config review** — see everything before you commit
6. **Go** — the live dashboard kicks in

You can also skip the wizard and pass everything via CLI flags:

```bash
python neostrike.py --target https://example.com --mode GET --threads 100 --duration 60s
```

---

## Dashboard Preview

```
┌─[ NEOSTRIKE v1.0 ]──────────────────────────────────────────────┐
│  ██ TELEMETRY       ██ RESPONSES      ██ LIVE FEED              │
│                                                                  │
│  Target  : https://target.local       200 OK  ████████  91.2%  │
│  Mode    : GET                        4xx     ██        5.1%   │
│  Workers : 50                         5xx     █         3.7%   │
│  Elapsed : 00:01:23                                             │
│                                                                  │
│  ██ PROGRESS ─────────────────────────────── 1,240 / 5,000 req │
│  [████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░] 24.8%              │
└──────────────────────────────────────────────────────────────────┘
```

---

## Configuration

All tunables are exposed as commented constants at the top of `neostrike.py`:

```python
DEFAULT_CONCURRENCY   = 50          # Parallel workers
DEFAULT_TIMEOUT       = 10          # Per-request timeout (seconds)
DEFAULT_DURATION      = 60          # Default test duration (seconds)
RATE_LIMIT_RPS        = None        # Token-bucket cap (None = unlimited)
TCP_UDP_PAYLOAD_SIZE  = 1024        # Bytes per raw socket packet
```

---

## Distributed Testing

NeoStrike supports splitting load across multiple machines:

- **Master** node broadcasts config and start/stop signals over TCP
- **Worker** nodes connect, receive the config, and report stats every second
- Aggregate stats are displayed on the master's dashboard

```bash
# On worker machines
python neostrike.py --worker --master-host 192.168.1.100

# On the master
python neostrike.py --target https://example.com --mode GET --distributed
```

---

## Plugin System

Drop a Python file in the `plugins/` directory and NeoStrike picks it up automatically. See `plugins/example_icmp.py` for the API:

```python
PLUGIN_INFO = {
    "name": "ICMP Flood",
    "description": "ICMP echo request flooding",
    "methods": ["ICMP"]
}

def worker_fn(cfg, stats, stopper, logger):
    # Your attack logic here
    pass
```

---

## Logging

Every session writes a structured log to `neostrike_logs/`:

```
neostrike_logs/neostrike_2026-06-18_14-30-00.log
```

Logs include session metadata, per-request outcomes (status, latency, errors), and a full session summary.

---

## Project Structure

```
Neostrike-loadtest-GUI/
├── neostrike.py                  # CLI engine (the main thing)
├── requirements.txt              # Python deps
├── plugins/
│   └── example_icmp.py           # Example plugin
└── NeoStrike/                    # C# WPF desktop GUI
    ├── NeoStrike.sln
    └── NeoStrike/
        ├── App.xaml
        ├── App.xaml.cs
        ├── MainWindow.xaml       # UI layout
        ├── MainWindow.xaml.cs    # Attack engine + UI logic
        ├── NeoStrike.csproj
        └── Themes/
            └── BrutalTheme.xaml
```

---

## Disclaimer

This tool is for **authorized stress testing and performance benchmarking only**.

- Only test systems you own or have explicit written permission to test.
- Don't use it against production systems or targets you don't own.
- The author takes no responsibility for misuse.

Test responsibly.

---

<div align="center">

*Built with asyncio, aiohttp, rich, WPF, and probably too much coffee.*

</div>
