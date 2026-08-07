"""Host platform / architecture helpers for asset selection."""

from __future__ import annotations

import platform
import sys

PLATFORM_BY_PREFIX: dict[str, str] = {
    "linux": "linux",
    "darwin": "macos",
    "win": "windows",
}

ARCH_ALIASES: dict[str, str] = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


def current_platform() -> str:
    """Return linux|macos|windows (or the raw ``sys.platform`` if unknown)."""
    for prefix, name in PLATFORM_BY_PREFIX.items():
        if sys.platform.startswith(prefix) or sys.platform == prefix:
            return name
    return sys.platform


def current_arch() -> str:
    """Return x86_64|arm64 (or the raw machine string if unknown)."""
    machine = platform.machine().lower()
    return ARCH_ALIASES.get(machine, machine)


def platform_key() -> str:
    """Asset key like linux-x86_64."""
    return f"{current_platform()}-{current_arch()}"
