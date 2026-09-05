"""Install and run 30 real, non-Python-implemented CLIs through maina.

Unlike examples/catalog/ (which stays offline on purpose), every tool here
actually downloads (or resolves from PATH) and does real work over the
network or the local machine — this is the "does the engine work on real
CLIs, not just our own fixtures" check, not a curated, shipped catalog (see
docs/product/vision.md: maina ships no tool catalog). Every call below does
something meaningful with its own output (a real lint finding, a real diff,
a real HTTP response, a real encrypted round trip) rather than a bare
--version check.

Network access (github.com, registry.npmjs.org, api.github.com) and a Node
on PATH are required; nothing here is exercised by the test suite
(deliberately). grype's first run downloads a vulnerability database (~40s),
then caches it — the one intentionally slow step. git/docker/kubectl use the
path backend (realistically always already installed, never fetched) rather
than github/npm like every other tool here; docker's demo is read-only
(docker ps) since this may run on a shared machine with real containers;
kubectl's demo is skipped with a clear message if it isn't on PATH (no
cluster is assumed either — the demo only does a client-side dry run).
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import maina

REPO_ROOT = Path(__file__).resolve().parents[2]


def run(app: maina.MainaApp, tool_id: str, /, **kwargs: Any) -> None:
    result = app.tool(tool_id).run(**kwargs)
    first_line = next(iter((result.raw or "").strip().splitlines()), "")
    if len(first_line) > 100:
        first_line = f"{first_line[:100]}… ({len(result.raw)} bytes total)"
    print(f"{tool_id:14} ok={result.ok!s:5} {first_line}")


def public_key_from(keyfile: Path) -> str:
    for line in keyfile.read_text().splitlines():
        if match := re.match(r"# public key: (age1\w+)", line):
            return match.group(1)
    raise RuntimeError(f"no public key comment line in {keyfile}")


def main() -> None:
    here = Path(__file__).resolve().parent
    app = maina.register(maina.Runtime.preferred(use_env=False), registry_dirs=[here / "tools"])

    # --- stdin / small-file tools -------------------------------------------------
    run(app, "pandoc", from_format="markdown", to_format="plain", stdin="# Hello\n\nWorld\n")
    run(app, "dasel", selector="name", in_format="json", stdin='{"name": "maina", "ok": true}')
    run(app, "hyperfine", command_to_run="echo hi", warmup="1", min_runs="2")
    run(app, "bat", stdin='print("hi")\n', language="python", plain=True)
    run(app, "glow", source="-", stdin="# Hello\n\nA real render.\n")
    run(app, "xh", method="GET", url="https://api.github.com/repos/anchore/syft", print="b")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        old_file, new_file = tmp_path / "old.txt", tmp_path / "new.txt"
        old_file.write_text("line one\n")
        new_file.write_text("line two\n")
        run(app, "difftastic", old_file=str(old_file), new_file=str(new_file))

        (tmp_path / "a.txt").write_bytes(b"x" * 4096)
        run(app, "eza", path=str(tmp_path), long=True, all=True)
        run(app, "dust", path=str(tmp_path), depth="1")

        diff_text = (
            "diff --git a/x.txt b/x.txt\n"
            "index 0000000..1111111 100644\n"
            "--- a/x.txt\n+++ b/x.txt\n"
            "@@ -1 +1 @@\n-old line\n+new line\n"
        )
        run(app, "delta", no_gitconfig=True, stdin=diff_text)

        doc, ruleset = tmp_path / "doc.yaml", tmp_path / "ruleset.yaml"
        doc.write_text('openapi: "3.0.0"\ninfo:\n  title: demo\n  version: "1.0"\npaths: {}\n')
        ruleset.write_text("rules: {}\n")
        run(app, "spectral", documents=[str(doc)], ruleset=str(ruleset))

        css = tmp_path / "test.css"
        css.write_text("a { color: red; }\n")
        stylelint_config = tmp_path / ".stylelintrc.json"
        stylelint_config.write_text('{"rules": {}}\n')
        run(app, "stylelint", files=[str(css)], config=str(stylelint_config))
        run(app, "commitlint", default_config=True, stdin="feat: add a real thing\n")

        # --- github/gh API + this repo's own history ----------------------------
        run(app, "gh", command="api", target="repos/anchore/syft", jq=".full_name")
        run(
            app,
            "git-cliff",
            repository=str(REPO_ROOT),
            unreleased=True,
            tag="v0.0.0-demo",
        )

        # --- security chain: age-keygen -> age encrypt/decrypt, and -> sops ------
        keyfile = tmp_path / "key.txt"
        run(app, "age-keygen", output=str(keyfile))
        recipient = public_key_from(keyfile)

        encrypted = app.tool("age").run(recipient=recipient, armor=True, stdin="a real secret\n")
        print(f"{'age (encrypt)':14} ok={encrypted.ok!s:5} {len(encrypted.raw)} bytes armored")
        decrypted = app.tool("age").run(decrypt=True, identity=str(keyfile), stdin=encrypted.raw)
        print(f"{'age (decrypt)':14} ok={decrypted.ok!s:5} {decrypted.raw.strip()!r}")

        secret = tmp_path / "secret.yaml"
        secret.write_text("password: hunter2\n")
        sops_result = app.tool("sops").run(
            encrypt=True,
            age_recipient=recipient,
            file=str(secret),
            env={"SOPS_AGE_KEY_FILE": str(keyfile)},
        )
        print(f"{'sops':14} ok={sops_result.ok!s:5} {'ENC[' in sops_result.raw}")

        # cwd=tmp_path: generate-key-pair writes cosign.key/cosign.pub into the
        # current directory with no way to name the output path — must not run
        # from the repo's own cwd (and must not collide with a re-run's files).
        run(
            app,
            "cosign",
            command="generate-key-pair",
            cwd=str(tmp_path),
            env={"COSIGN_PASSWORD": ""},
        )

        # --- security scanners ---------------------------------------------------
        dockerfile_dir = tmp_path / "dockerfile_fixture"
        dockerfile_dir.mkdir()
        (dockerfile_dir / "Dockerfile").write_text("FROM alpine:3.18\n")
        run(app, "trivy", command="fs", target=str(dockerfile_dir), scanners="misconfig")
        run(app, "syft", target=f"dir:{tmp_path}", output="table")
        run(app, "grype", target=f"dir:{tmp_path}", quiet=True)  # first run: ~40s database pull

        # --- IaC / CI / protobuf linters ------------------------------------------
        tf_dir = tmp_path / "tf_fixture"
        tf_dir.mkdir()
        (tf_dir / "main.tf").write_text(
            'resource "aws_instance" "example" {\n'
            '  ami           = "ami-123456"\n'
            '  instance_type = "t2.micro"\n'
            "}\n"
        )
        run(app, "tflint", chdir=str(tf_dir))

        workflow = tmp_path / "workflow.yml"
        workflow.write_text(
            "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: echo hi\n"
        )
        run(app, "actionlint", path=[str(workflow)])

        proto_dir = tmp_path / "proto_fixture"
        proto_dir.mkdir()
        (proto_dir / "buf.yaml").write_text("version: v2\nlint:\n  use:\n    - STANDARD\n")
        (proto_dir / "example.proto").write_text(
            'syntax = "proto3";\npackage example;\nmessage Example {\n  string name = 1;\n}\n'
        )
        run(app, "buf", command="lint", target=str(proto_dir))

        run(app, "golangci-lint", command="linters")

        script = tmp_path / "script.js"
        script.write_text("export default function() { let x = 1 + 1; }\n")
        run(app, "k6", command="run", script=str(script), vus="1", iterations="1")

        # --- already-installed, path-backend tools --------------------------------
        run(app, "git", command="log", repo_path=str(REPO_ROOT), extra=["--oneline", "-n", "3"])
        run(app, "docker", command="ps", format="{{.Names}}")

        if shutil.which("kubectl"):
            k8s_manifest = tmp_path / "pod.yaml"
            k8s_manifest.write_text(
                "apiVersion: v1\nkind: Pod\nmetadata:\n  name: demo\nspec:\n"
                "  containers:\n    - name: demo\n      image: alpine:3.18\n"
            )
            run(app, "kubectl", command="apply", filename=str(k8s_manifest), dry_run="client")
        else:
            print(f"{'kubectl':14} skipped (not on PATH — no cluster assumed either)")


if __name__ == "__main__":
    main()
