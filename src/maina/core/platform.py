"""Host platform / architecture helpers for asset selection."""

from __future__ import annotations

import platform
import re
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

# The ``install.assets`` / ``install.sha256`` keys most catalogs use — the six
# combinations :func:`current_arch` has an alias for. Not the whole valid set:
# see :func:`is_platform_key`.
KNOWN_PLATFORM_KEYS = frozenset(
    f"{os_}-{arch}" for os_ in ("linux", "macos", "windows") for arch in ("x86_64", "arm64")
)

# ``current_arch`` returns the raw ``platform.machine()`` for anything it has no
# alias for, so ``platform_key()`` on a 32-bit Pi is exactly ``linux-armv7l``.
# A manifest that carries such a key installs fine and must lint clean too.
PLATFORM_KEY_RE = re.compile(r"^(linux|macos|windows)-([a-z0-9_]+)$")


def platform_key_problem(key: str) -> str | None:
    """Why *key* is not a usable ``{os}-{arch}`` asset key, or ``None``.

    Any architecture segment is accepted, not just the six aliased ones: an
    armv7l / ppc64le / s390x / riscv64 host reaches the installer with exactly
    that key, so a catalog carrying it must lint clean. What is still rejected
    is an *unnormalised* spelling of an arch maina does alias (``linux-amd64``
    would never be looked up, because the host asks for ``linux-x86_64``) and an
    OS segment maina never emits (``darwin-arm64``).
    """
    match = PLATFORM_KEY_RE.match(key)
    if match is None:
        allowed = ", ".join(sorted(PLATFORM_BY_PREFIX.values()))
        return f"{key!r} is not a '<os>-<arch>' key ({allowed} + a lowercase arch)"
    arch = match[2]
    canonical = ARCH_ALIASES.get(arch, arch)
    return (
        None
        if canonical == arch
        else f"{key!r} should be {match[1]}-{canonical} (the name this host reports)"
    )


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
