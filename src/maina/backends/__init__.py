"""Shipped installer backends."""

from __future__ import annotations

from maina.backends.github import GithubBackend
from maina.backends.npm import NpmBackend
from maina.backends.path import PathBackend
from maina.backends.pip import PipBackend
from maina.backends.script import ScriptBackend

__all__ = ["GithubBackend", "NpmBackend", "PathBackend", "PipBackend", "ScriptBackend"]
