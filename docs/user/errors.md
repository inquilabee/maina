# Errors

Every failure maina raises derives from `maina.MainaError`, so one `except` covers
the library. The subclasses tell you which phase failed.

```text
MainaError
├── ManifestError          a manifest is invalid, or an id has no manifest
├── AbstractToolError      you called an inheritance-only (abstract) surface
├── ArgError               bad keyword arguments or call options
├── RegisterError          a register() path is missing, or the cache cannot be created
├── InstallError           the executable could not be installed or resolved
│   ├── RuntimeProvisionError    a mise-managed language runtime failed
│   └── UnsupportedPlatformError this host is not in the manifest's platforms
├── ToolExecError          the process could not be started (bad cwd, missing exe)
├── ToolTimeoutError       the process outran its timeout
├── ToolOutputError        the process wrote more than the output cap
├── ToolExitError          raised by raise_for_status() on a non-success exit
└── ParseError             a parser could not read the output (raise it from your own parser)
```

| Error | When | Useful attributes |
| --- | --- | --- |
| `ManifestError` | A manifest fails validation at load, an id is unknown, or the registry has no roots. | `files` — the manifest file(s) the problem came from, leaf first. |
| `AbstractToolError` | The tool id names an `abstract: true` surface. | |
| `ArgError` | An unknown or mistyped keyword argument, a missing required one, or a bad call option (`timeout`, `env`, `env_allow`, `extra`, `stdin`, a null byte in a value). Raised before anything is installed. | |
| `RegisterError` | `register(...)` is given a path that does not exist or is not a directory, or the cache directory cannot be created. | |
| `InstallError` | A download fails, a digest does not match, an archive is corrupt or unsafe, the binary is not found, or the backend is unknown. | |
| `ToolExecError` | The subprocess could not be started. | `tool`, `command` |
| `ToolTimeoutError` | The run exceeded `timeout=` (or the manifest's `timeout:`). The whole process tree is killed. | `tool`, `timeout`, `command` |
| `ToolOutputError` | stdout or stderr passed the 64 MiB cap; the process is killed rather than returning truncated output. | `tool`, `command` |
| `ToolExitError` | You called `result.raise_for_status()` and the exit code is not in `success_codes`. | `exit_code`, `tool`, `command`, `raw`, `stderr` |

A tool that exits with a failing code is **not** an exception by itself:
`run()` returns a `ToolResult` with `ok=False`. Call `raise_for_status()` if you
want an exception. Likewise a parser that fails does not raise from `run()`; the
reason lands on `result.parse_error`.

```python
import maina

try:
    result = app.tool("shellcheck").run(path=["deploy.sh"], timeout=30)
    result.raise_for_status()
except maina.ToolTimeoutError as exc:
    print(f"{exc.tool} timed out after {exc.timeout}s")
except maina.ToolExitError as exc:
    print(exc.exit_code, exc.stderr)
except maina.InstallError as exc:
    print("could not install the tool:", exc)
```

The [CLI](cli.md) maps these to exit codes: `2` for usage and manifest errors,
`1` when the tool itself failed.
