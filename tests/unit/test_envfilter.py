"""Unit tests for env_allow trimming (core.envfilter)."""

from __future__ import annotations

from maina.core.envfilter import filter_env


def test_none_allow_passes_env_through() -> None:
    env = {"SECRET": "x", "PATH": "/bin"}
    assert filter_env(env, None) is env


def test_allow_keeps_essentials_and_named_only() -> None:
    env = {"PATH": "/bin", "SECRET": "x", "KEEP": "y", "AWS_SECRET_ACCESS_KEY": "z"}
    out = filter_env(env, ["KEEP"])
    assert out == {"PATH": "/bin", "KEEP": "y"}


def test_essentials_match_case_insensitively() -> None:
    # Windows spellings must survive an all-caps allowlist.
    env = {"SystemRoot": r"C:\Windows", "Path": r"C:\bin", "http_proxy": "http://p"}
    out = filter_env(env, [])
    assert out == env


def test_shared_library_and_ca_paths_are_essential() -> None:
    env = {
        "LD_LIBRARY_PATH": "/usr/lib",
        "SSL_CERT_FILE": "/etc/ssl/cert.pem",
        "SECRET": "x",
    }
    out = filter_env(env, [])
    assert "LD_LIBRARY_PATH" in out
    assert "SSL_CERT_FILE" in out
    assert "SECRET" not in out
