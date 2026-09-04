"""Regression tests: install-path safety, env trimming, argv/pairs, and output.from_file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from helpers import write_yaml
from maina.backends import PathBackend
from maina.core.argparser import ArgParser
from maina.core.errors import InstallError, ManifestError
from maina.core.parsers import ParserResolver, builtin_generals
from maina.core.registry import Registry
from maina.installer import Installer
from maina.runner import ToolRunner
from maina.runtime import Runtime

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class FakeLanguage:
    cache_root: Path

    def ensure(self, runtime: str, version: str, **_kw: object) -> Path:  # pragma: no cover
        del runtime, version
        return self.cache_root

    def env(self, runtime: str, version: str, **_kw: object) -> dict[str, str]:  # pragma: no cover
        del runtime, version
        return {"PATH": str(self.cache_root)}


def make_reg(tmp_path: Path, files: dict[str, dict[str, Any]]) -> Registry:
    root = tmp_path / "reg"
    for rel, data in files.items():
        write_yaml(root / rel, data)
    return Registry(root)


def path_installer(tmp_path: Path, reg: Registry) -> Installer:
    return Installer(cache_root=tmp_path / "cache", registry=reg, backends=[PathBackend()])


# --- review C: install-path security (B1, B2, B3) -------------------------------


def test_install_asset_name_traversal_rejected(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {
                    "backend": "github",
                    "repo": "acme/t",
                    "assets": {"linux-x86_64": "../../../etc/evil"},
                },
            }
        },
    )
    with pytest.raises(ManifestError, match="not a safe path segment"):
        reg.load("t")


def test_install_repo_shape_validated(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "t", "install": {"backend": "github", "repo": "not-a-repo"}}},
    )
    with pytest.raises(ManifestError, match="owner/name"):
        reg.load("t")


def test_identity_key_distinguishes_specs() -> None:
    from maina.core.registry import InstallSpec

    a = InstallSpec(backend="github", repo="acme/x", tag="v1", assets={"k": "x-1.tgz"})
    b = InstallSpec(backend="github", repo="other/x", tag="v1", assets={"k": "x-1.tgz"})
    c = InstallSpec(backend="github", repo="acme/x", tag="v1", assets={"k": "x-1.tgz"})
    assert a.identity_key() != b.identity_key()
    assert a.identity_key() == c.identity_key()


def test_cache_version_includes_identity_for_github(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "version": "1.7.1",
                "install": {
                    "backend": "github",
                    "repo": "acme/t",
                    "assets": {"linux-x86_64": "t-1.7.1.tgz"},
                },
            },
            "p.yaml": {"cli": "p", "version": "1.0", "install": {"backend": "path"}},
        },
    )
    inst = Installer(cache_root=tmp_path / "c", registry=reg, backends=[PathBackend()])
    gh = inst._cache_version(reg.load("t"))
    assert gh.startswith("1.7.1-")
    assert len(gh) > len("1.7.1-")
    assert inst._cache_version(reg.load("p")) == "1.0"


def test_env_allow_reaches_install_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "leak")
    seen: dict[str, dict[str, str]] = {}

    class RecordingBackend:
        name = "rec"

        def resolve(
            self, manifest: object, *, version: str, env: dict[str, str], ctx: object
        ) -> tuple[str, dict[str, str]]:
            del manifest, version, ctx
            seen["env"] = dict(env)
            return "/bin/true", env

    reg = make_reg(tmp_path, {"t.yaml": {"cli": "t", "install": {"backend": "rec"}}})
    inst = Installer(cache_root=tmp_path / "c", registry=reg, backends=[RecordingBackend()])
    inst.resolve(reg.load("t"), env_allow=["KEEP"])
    assert "MY_SECRET" not in seen["env"]
    assert "PATH" in seen["env"]


def test_env_allow_reaches_language_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runtime pin routes the trimmed env into ``LanguageRuntimeProvider.ensure``."""
    monkeypatch.setenv("MY_SECRET", "leak")
    seen: dict[str, dict[str, str] | None] = {}

    class RecordingLanguage:
        def __init__(self) -> None:
            self.bin = tmp_path / "node-bin"
            self.bin.mkdir()

        def ensure(
            self, runtime: str, version: str, *, base_env: dict[str, str] | None = None
        ) -> Path:
            del runtime, version
            seen["base_env"] = dict(base_env) if base_env is not None else None
            return self.bin

        def env(
            self, runtime: str, version: str, *, base_env: dict[str, str] | None = None
        ) -> dict[str, str]:
            del runtime, version, base_env
            return {"PATH": str(self.bin)}

    class NoopBackend:
        name = "noop"

        def resolve(
            self, manifest: object, *, version: str, env: dict[str, str], ctx: object
        ) -> tuple[str, dict[str, str]]:
            del manifest, version, ctx
            return "/bin/true", env

    reg = make_reg(
        tmp_path,
        {
            "base.yaml": {"abstract": True, "requires": {"node": "20"}},
            "t.yaml": {"cli": "t", "install": {"backend": "noop"}},
        },
    )
    inst = Installer(
        cache_root=tmp_path / "c",
        registry=reg,
        backends=[NoopBackend()],
        language=RecordingLanguage(),
    )
    inst.resolve(reg.load("t"), env_allow=["KEEP"])
    recorded = seen["base_env"]
    assert recorded is not None
    assert "MY_SECRET" not in recorded
    assert "PATH" in recorded


def test_npm_install_uses_ignore_scripts_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import maina.backends.npm as npm_mod
    from maina.backends.npm import NpmBackend

    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(argv: list[str], **_kw: object) -> _Result:
        calls.append(list(argv))
        return _Result()

    monkeypatch.setattr(npm_mod.subprocess, "run", fake_run)
    NpmBackend._npm_install("/usr/bin/npm", tmp_path / "x", "jscpd@1", {"PATH": "/usr/bin"})
    assert "--ignore-scripts" in calls[0]
    calls.clear()
    NpmBackend._npm_install(
        "/usr/bin/npm", tmp_path / "x", "jscpd@1", {"PATH": "/usr/bin"}, allow_scripts=True
    )
    assert "--ignore-scripts" not in calls[0]


# --- review C: expressiveness + output.from_file + validation (U5, B7, B11-B13) ---


def test_pairs_type_emits_flag_key_value() -> None:
    schema = {"arg": {"flag": "--arg", "type": "pairs"}}
    argv = ArgParser(schema).build("jq", [], {"arg": {"x": "1", "y": "2"}})
    assert argv == ["jq", "--arg", "x", "1", "--arg", "y", "2"]
    argv2 = ArgParser(schema).build("jq", [], {"arg": [["a", "b"]]})
    assert argv2 == ["jq", "--arg", "a", "b"]


def test_join_typo_rejected_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "path"},
                "args": {"a": {"flag": "--a", "type": "string", "join": "comma"}},
            }
        },
    )
    with pytest.raises(ManifestError, match="join 'comma' not in"):
        reg.load("t")


def test_top_level_timeout_validated_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path, {"t.yaml": {"cli": "t", "timeout": "soon", "install": {"backend": "path"}}}
    )
    with pytest.raises(ManifestError, match="timeout 'soon' must be a number"):
        reg.load("t")


def test_platforms_values_validated_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "t", "platforms": ["Linux"], "install": {"backend": "path"}}},
    )
    with pytest.raises(ManifestError, match=r"platforms .* not in"):
        reg.load("t")


def from_file_runner(tmp_path: Path) -> ToolRunner:
    reg = make_reg(
        tmp_path,
        {
            "j.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "parser": "json",
                "args": {"out": {"flag": "--out", "type": "string", "default": "report"}},
                "output": {"success_codes": [0], "from_file": "{out}/r.json"},
            }
        },
    )
    return ToolRunner(
        reg.load("j"),
        installer=path_installer(tmp_path, reg),
        parsers=ParserResolver(generals=builtin_generals()),
    )


def test_from_file_explicit_kwarg(tmp_path: Path) -> None:
    work = tmp_path / "w"
    (work / "report").mkdir(parents=True)
    (work / "report" / "r.json").write_text('{"clones": 3}', encoding="utf-8")
    result = from_file_runner(tmp_path).run({"out": "report"}, cwd=str(work))
    assert result.parsed == {"clones": 3}
    assert result.meta["parsed_from"].endswith("report/r.json")
    assert result.report is not None
    assert result.raw != result.report  # stdout preserved


def test_from_file_uses_manifest_default(tmp_path: Path) -> None:
    work = tmp_path / "w"
    (work / "report").mkdir(parents=True)
    (work / "report" / "r.json").write_text('{"clones": 9}', encoding="utf-8")
    result = from_file_runner(tmp_path).run({}, cwd=str(work))  # 'out' not passed
    assert result.parsed == {"clones": 9}
    assert result.parse_error is None


def test_from_file_missing_is_surfaced(tmp_path: Path) -> None:
    result = from_file_runner(tmp_path).run({}, cwd=str(tmp_path))
    assert result.parsed is None
    assert result.parse_error is not None
    assert "not found" in result.parse_error
    assert "parsed_from_missing" in result.meta


def test_from_file_bad_template_is_surfaced(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "j.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "output": {"success_codes": [0], "from_file": "report-{.json"},
            }
        },
    )
    result = ToolRunner(reg.load("j"), installer=path_installer(tmp_path, reg)).run(
        {}, cwd=str(tmp_path)
    )
    assert result.parse_error is not None
    assert "unresolved" in result.parse_error


def test_from_file_absolute_rejected_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "j.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "output": {"success_codes": [0], "from_file": "/etc/passwd"},
            }
        },
    )
    with pytest.raises(ManifestError, match="relative path without"):
        reg.load("j")


def test_from_file_with_cwd_arg_rejected_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "j.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {"cwd": {"flag": "--cwd", "type": "string"}},
                "output": {"success_codes": [0], "from_file": "{cwd}/r.json"},
            }
        },
    )
    with pytest.raises(ManifestError, match="cannot be named 'cwd'"):
        reg.load("j")


def test_from_file_stale_report_is_surfaced(tmp_path: Path) -> None:
    work = tmp_path / "w"
    (work / "report").mkdir(parents=True)
    stale = work / "report" / "r.json"
    stale.write_text('{"clones": 1}', encoding="utf-8")
    old = stale.stat().st_mtime - 3600
    os.utime(stale, (old, old))
    result = from_file_runner(tmp_path).run({}, cwd=str(work))
    assert result.parsed is None
    assert result.parse_error is not None
    assert "stale" in result.parse_error
    assert result.meta.get("parsed_from_mtime") == old


def test_cli_json_split_keeps_dashdash_tail() -> None:
    from maina.cli import split_at_dashdash

    head, tail = split_at_dashdash(["a=1", "--json", "--", "--json"])
    assert head == ["a=1", "--json"]
    assert tail == ["--json"]


# --- review C: robustness P2s (B4, B8, B9, B10) -------------------------------


def test_copy_raw_asset_rejects_unsupported_format(tmp_path: Path) -> None:
    from maina.backends.archive import ArchiveFetcher

    src = tmp_path / "tool.tar.bz2"
    src.write_bytes(b"BZh9fake")
    with pytest.raises(InstallError, match="unsupported asset format"):
        ArchiveFetcher.copy_raw_asset(src, tmp_path / "out", name="tool.tar.bz2")


def test_resolve_cache_root_is_absolute() -> None:
    import pathlib

    root = Runtime.resolve_cache_root(pathlib.Path("rel-cache-xyz"))
    assert root.is_absolute()
    root.rmdir()


def test_run_bad_cwd_raises_tool_exec_error(tmp_path: Path) -> None:
    from maina.core.errors import ToolExecError

    reg = make_reg(
        tmp_path,
        {
            "e.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "output": {"success_codes": [0]},
            }
        },
    )
    runner = ToolRunner(reg.load("e"), installer=path_installer(tmp_path, reg))
    with pytest.raises(ToolExecError, match="could not run"):
        runner.run({}, cwd=str(tmp_path / "nope-does-not-exist"))


def test_run_without_stdin_does_not_block(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "c.yaml": {
                "cli": "cat",
                "install": {"backend": "path"},
                "output": {"success_codes": [0]},
            }
        },
    )
    runner = ToolRunner(reg.load("c"), installer=path_installer(tmp_path, reg))
    result = runner.run({}, timeout=5)
    assert result.ok
    assert result.raw == ""


# --- review C: introspection & adaptability (A1, A7, A8) --------------------


def test_tool_describe_is_json_safe(tmp_path: Path) -> None:
    import json

    import maina

    reg_dir = tmp_path / "reg"
    write_yaml(
        reg_dir / "demo.yaml",
        {
            "cli": "echo",
            "version": "1.0",
            "install": {"backend": "path"},
            "args": {
                "msg": {"positional": True, "type": "string", "required": True},
                "loud": {"flag": "--loud", "type": "flag", "default": False},
            },
            "output": {"success_codes": [0]},
        },
    )
    rt = maina.Runtime.preferred(registry_dirs=[reg_dir], use_env=False)
    tool = maina.Tool("demo", runtime=rt)
    desc = tool.describe()
    json.dumps(desc)  # must not raise
    assert desc["id"] == "demo"
    assert desc["args"]["msg"]["required"] is True
    assert set(tool.args) == {"msg", "loud"}
    assert tool.manifest.cli == "echo"


def test_append_roots_dedupes(tmp_path: Path) -> None:
    reg_dir = tmp_path / "reg"
    write_yaml(reg_dir / "x.yaml", {"cli": "echo", "install": {"backend": "path"}})
    r = Registry([])
    r.append_roots([reg_dir, reg_dir, reg_dir])
    assert r.roots == [reg_dir]


def test_register_replace_resets_catalog(tmp_path: Path) -> None:
    import maina

    a = tmp_path / "a"
    b = tmp_path / "b"
    write_yaml(a / "one.yaml", {"cli": "echo", "install": {"backend": "path"}})
    write_yaml(b / "two.yaml", {"cli": "echo", "install": {"backend": "path"}})
    rt = maina.Runtime.preferred(use_env=False)
    maina.register(rt, registry_dirs=[a])
    assert "one" in rt.registry.iter_ids()
    maina.register(rt, registry_dirs=[b], replace=True)
    assert rt.registry.iter_ids() == ["two"]


# --- review C: entry-point discovery (A2) + npm version regression -----------


def test_npm_pkg_spec_uses_manifest_version_not_cache_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The identity-keyed cache segment must not leak into the npm dist-tag."""
    import maina.backends.npm as npm_mod
    from maina.backends.npm import NpmBackend
    from maina.core.protocols import InstallContext
    from maina.core.registry import InstallSpec, Manifest

    seen: dict[str, str | None] = {}

    def fake_install(cli, package, version, prefix, link, env, *, allow_scripts=False):  # ruff:ignore[unused-function-argument]
        seen["version"] = version
        return link

    monkeypatch.setattr(npm_mod.shutil, "which", lambda *_a, **_k: "/usr/bin/npm")
    monkeypatch.setattr(NpmBackend, "_install_package", staticmethod(fake_install))
    manifest = Manifest(
        id="cowsay",
        data={"cli": "cowsay", "version": "1.6.0"},
        install=InstallSpec(backend="npm", package="cowsay"),
    )

    ctx = InstallContext(
        cache_root=tmp_path / "c",
        language=FakeLanguage(tmp_path / "c"),  # node is on PATH, so ensure() is never called
    )
    NpmBackend()._resolve_npm(manifest, "1.6.0-deadbeef12", {"PATH": "/usr/bin"}, ctx)
    assert seen["version"] == "1.6.0"


def test_discover_registers_entry_point_catalogs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cat = tmp_path / "cat"
    write_yaml(cat / "epx.yaml", {"cli": "echo", "install": {"backend": "path"}})
    monkeypatch.setattr(Runtime, "_discovered_registry_dirs", staticmethod(lambda: [cat]))
    rt = Runtime.preferred(use_env=False, discover=True)
    assert "epx" in rt.registry.iter_ids()
    rt.close()
