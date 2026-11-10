"""Tests for the `maina validate` / `prepare` / `clean` CLI commands."""

from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

import maina
from helpers import write_yaml
from maina.backends import PathBackend
from maina.cli import app
from maina.runtime import Runtime

runner = CliRunner()


def test_cli_validate_reports_each_error_and_exits_nonzero(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "echo", "install": {"backend": "path"}})
    (root / "bad.yaml").write_text("cli: bad\noutputs: {}\n", encoding="utf-8")
    write_yaml(
        root / "badparser.yaml",
        {"cli": "echo", "install": {"backend": "path"}, "parser": "jsonn"},
    )
    write_yaml(root / "badbackend.yaml", {"cli": "x", "install": {"backend": "gihtub"}})
    write_yaml(
        root / "badkey.yaml",
        {
            "cli": "x",
            "install": {"backend": "github", "repo": "a/b", "assets": {"linux-amd64": "x"}},
        },
    )
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 1
    assert "ok: good" in result.stdout
    assert "error: bad:" in result.stderr
    assert "error: badparser:" in result.stderr
    assert "backend 'gihtub' is not registered" in result.stderr
    assert "'linux-amd64' should be linux-x86_64" in result.stderr


def test_cli_validate_catches_static_manifest_bugs(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "nocli.yaml", {"install": {"backend": "path"}})
    write_yaml(root / "ghnorepo.yaml", {"cli": "x", "install": {"backend": "github"}})
    write_yaml(
        root / "badtmpl.yaml",
        {
            "cli": "echo",
            "install": {"backend": "path"},
            "output": {"from_file": "{nosucharg}/r.json"},
        },
    )
    write_yaml(root / "base.yaml", {"env": {"LC_ALL": "C"}})  # cli-less base is fine
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 1
    err = result.stderr
    assert "error: nocli: no cli:" in err
    assert "ghnorepo: install.backend: github needs repo: and assets:" in err
    assert "badtmpl: output.from_file names non-arg placeholder(s): nosucharg" in err
    assert "ok: base" in result.stdout


def test_validate_ids_is_callable_as_a_library_without_the_cli(tmp_path: Any) -> None:
    """maina validate's checks are also Runtime.validate_ids(rt, ...) — a
    caller embedding maina programmatically gets the same answer without
    shelling out to the CLI and parsing its text output."""
    from maina.catalog_validate import validate_ids

    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "echo", "install": {"backend": "path"}})
    write_yaml(root / "badbackend.yaml", {"cli": "x", "install": {"backend": "gihtub"}})
    rt = Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False)
    duplicate_files, problems = validate_ids(rt)
    assert duplicate_files == {}
    assert problems["good"] is None
    bad = problems["badbackend"]
    assert bad is not None
    assert "backend 'gihtub' is not registered" in bad


def test_cli_prepare_is_batch_resilient(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "aaa.yaml", {"cli": "true", "install": {"backend": "path"}})
    write_yaml(root / "mmm.yaml", {"cli": "not-a-real-binary-xyz", "install": {"backend": "path"}})
    write_yaml(root / "zzz.yaml", {"cli": "sh", "install": {"backend": "path"}})
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)
    result = runner.invoke(app, ["prepare"])
    assert result.exit_code == 1
    assert "prepared: aaa" in result.stdout
    assert "prepared: zzz" in result.stdout
    assert "failed: mmm:" in result.stderr


def test_cli_prepare_path_tool(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "trueish.yaml",
        {"cli": "true", "install": {"backend": "path"}},
    )
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)
    result = runner.invoke(app, ["prepare", "trueish"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "prepared: trueish" in result.stdout


def test_cli_prepare_force_needs_ids_and_rejects_check(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "trueish.yaml", {"cli": "true", "install": {"backend": "path"}})
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)

    bare = runner.invoke(app, ["prepare", "--force"])
    assert bare.exit_code != 0
    assert "needs explicit tool ids" in (bare.stdout + bare.stderr)

    combo = runner.invoke(app, ["prepare", "trueish", "--force", "--check"])
    assert combo.exit_code != 0
    assert "cannot be combined with --check" in (combo.stdout + combo.stderr)


def test_cli_prepare_check_reports_state(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    # `true` is on PATH → satisfied; a bogus cli → missing.
    write_yaml(root / "trueish.yaml", {"cli": "true", "install": {"backend": "path"}})
    write_yaml(
        root / "nope.yaml",
        {"cli": "definitely-not-a-real-binary", "install": {"backend": "path"}},
    )
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)
    result = runner.invoke(app, ["prepare", "--check"])
    assert result.exit_code == 1, result.stdout + result.stderr
    assert "satisfied: trueish" in result.stdout
    assert "missing: nope" in result.stdout

    ok = runner.invoke(app, ["prepare", "trueish", "--check"])
    assert ok.exit_code == 0, ok.stdout + ok.stderr
    assert ok.stdout.strip() == "satisfied: trueish"


def test_cli_prepare_check_is_batch_resilient(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "true", "install": {"backend": "path"}})
    write_yaml(root / "bad.yaml", {"cli": "x", "install": {"backend": "mystery"}})
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)
    result = runner.invoke(app, ["prepare", "--check"])
    assert result.exit_code == 1
    assert "satisfied: good" in result.stdout
    assert "error: bad:" in result.stderr


def test_cli_prepare_rejects_explicit_abstract(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "base.yaml",
        {"cli": "base", "abstract": True, "install": {"backend": "path"}},
    )
    write_yaml(
        root / "child.yaml",
        {
            "cli": "true",
            "extends": "base",
            "abstract": False,
            "install": {"backend": "path"},
        },
    )
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)
    result = runner.invoke(app, ["prepare", "base"])
    assert result.exit_code == 2
    assert "cannot prepare abstract" in (result.stdout + result.stderr)


def test_cli_prepare_skips_abstract(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "base.yaml",
        {"cli": "base", "abstract": True, "install": {"backend": "path"}},
    )
    write_yaml(
        root / "child.yaml",
        {
            "cli": "true",
            "extends": "base",
            "abstract": False,
            "install": {"backend": "path"},
        },
    )
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)
    result = runner.invoke(app, ["prepare"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "prepared: child" in result.stdout
    assert "prepared: base" not in result.stdout


def test_cli_clean_removes_cache(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "t.yaml", {"cli": "toolx", "version": "1", "install": {"backend": "path"}})
    cache = tmp_path / "c"
    (cache / "tools" / "toolx" / "1").mkdir(parents=True)
    maina.use(Runtime.preferred(cache_root=cache, registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["clean", "t"])
    assert result.exit_code == 0
    assert "cleaned: toolx/1" in result.stdout
    assert not (cache / "tools" / "toolx").exists()
    assert "nothing to clean" in runner.invoke(app, ["clean"]).stdout


def test_validate_accepts_a_platform_key_the_installer_would_actually_use(
    tmp_path: Any,
) -> None:
    """``current_arch()`` returns the raw ``platform.machine()`` when it has no
    alias, so a 32-bit Pi asks for exactly ``linux-armv7l``. A catalog that
    carries such an asset installs fine and must lint clean too."""
    root = tmp_path / "reg"
    write_yaml(
        root / "pi.yaml",
        {
            "cli": "mytool",
            "install": {
                "backend": "github",
                "repo": "a/b",
                "assets": {
                    "linux-x86_64": "mytool-x86_64.tar.gz",
                    "linux-armv7l": "mytool-armv7l.tar.gz",
                    "linux-ppc64le": "mytool-ppc64le.tar.gz",
                },
            },
        },
    )
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 0
    assert "ok: pi" in result.stdout


def test_validate_rejects_an_unnormalised_or_unknown_platform_key(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "typo.yaml",
        {
            "cli": "x",
            "install": {
                "backend": "github",
                "repo": "a/b",
                "assets": {"linux-amd64": "x.tar.gz", "darwin-arm64": "y.tar.gz"},
            },
        },
    )
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 1
    assert "'linux-amd64' should be linux-x86_64" in result.stderr
    assert "'darwin-arm64' is not a '<os>-<arch>' key" in result.stderr


def test_validate_rejects_a_from_file_placeholder_that_can_never_resolve(
    tmp_path: Any,
) -> None:
    """The runner only interpolates scalars, so a ``type: flag`` placeholder is
    statically unresolvable — ``validate`` must not bless it."""
    root = tmp_path / "reg"
    write_yaml(
        root / "rep.yaml",
        {
            "cli": "echo",
            "args": {"verbose": {"flag": "-v", "type": "flag"}},
            "output": {"from_file": "{verbose}/r.json"},
        },
    )
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 1
    assert "never interpolates: verbose" in result.stderr


def test_validate_problems_name_the_manifest_file(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "nocli.yaml", {"install": {"backend": "path"}})
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 1
    assert str(root / "nocli.yaml") in result.stderr


def test_cli_prepare_check_does_not_call_a_manifest_validate_rejects_satisfied(
    tmp_path: Any,
) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "badparser.yaml",
        {"cli": "echo", "install": {"backend": "path"}, "parser": "jsonn"},
    )
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["prepare", "--check"])
    assert result.exit_code == 1
    assert "satisfied: badparser" not in result.stdout
    assert "error: badparser:" in result.stderr


def test_cli_prepare_exits_nonzero_when_a_manifest_cannot_load(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "echo", "install": {"backend": "path"}})
    (root / "bad.yaml").write_text("cli: bad\noutputs: {}\n", encoding="utf-8")
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["prepare"])
    assert result.exit_code == 1
    assert "prepared: good" in result.stdout
    assert "failed: bad:" in result.stderr
