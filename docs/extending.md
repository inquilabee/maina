# Extending maina

Add local tools, parsers, and install backends without forking. Built-ins use
the same `register(...)` path.

## The Runtime

`Runtime` is the composition root. It owns the registry overlay roots, the
general-parser map, the installer backend map, and the language provider.
`Runtime.preferred()` builds one with the builtin parsers, install backends, and
mise registered — but **no tool catalog** (add one below).

For an embedding app, pass everything explicitly and ignore the ambient
environment:

```python
rt = maina.Runtime.preferred(
    cache_root=Path("/var/cache/myapp/tools"),
    registry_dirs=[importlib.resources.files("myapp") / "tools"],
    use_env=False,   # ignore MAINA_REGISTRY / MAINA_CACHE
)
...
rt.close()           # or: with maina.Runtime.preferred(...) as rt: ...
```

```python
from pathlib import Path
import maina

rt = maina.Runtime.preferred()
maina.register(
    rt,
    registry_dirs=[Path("tools")],   # a later root overrides an earlier id
    parsers={"csv": parse_csv},
    parsers_dirs=[Path("parsers")],
    installers=[MyBackend()],
    language=MyRuntimeProvider(),
)
maina.Tool("mytool", runtime=rt).run(...)
```

`register(rt, registry_dirs=[...], replace=True)` clears the catalog first;
duplicate roots are ignored.

## Introspection

Build your own help, form UI, or CLI from a tool's schema without parsing
`help()` text:

```python
tool = maina.Tool("mytool", runtime=rt)
tool.manifest            # the resolved Manifest
tool.args                # read-only Mapping[str, ArgSpec]
tool.describe()          # JSON-safe dict: id, cli, args, install, output, ...
tool.argv(path=["src"])  # the argv a run would build (argv[0] = bare cli name)
```

`tool.argv(**kwargs)` maps kwargs the same way `run` does but stops before
executing — use it for a dry run, a preview, or to hand the argv to your own
subprocess call. It binds the same run-only parameter names `run()` does
(`parser`, `cwd`, `timeout`, `env_allow`, `env`, `stdin`, `text`) and ignores
them, so a manifest arg whose name collides with one never disagrees between
the two — see [reserved names](manifests.md#args).

`Manifest`, `ArgSpec`, `InstallSpec`, `OutputSpec`, `Registry`, and `ToolRunner`
are importable straight from `maina`.

## Sharing a catalog as a package

Publish a catalog directory through an entry point:

```toml
[project.entry-points."maina.registries"]
acme = "acme_tools:registry_dir"   # a str/Path, or a callable returning one
```

Consumers pick it up with `Runtime.preferred(discover=True)` — it registers
every installed `maina.registries` catalog (env `MAINA_REGISTRY` first, then
discovered, then explicit `registry_dirs`; later wins).

`register(...)` with no Runtime mutates the process-wide session instead:

```python
maina.register(registry_dirs=["tools"])
maina.Tool("mytool").run(...)
```

## Isolation and threads

The process-wide session is **not thread-safe**. For concurrent or nested use:

- pass an explicit `Tool(..., runtime=rt)` (preferred), or
- scope a swap to a block:

```python
with maina.isolated(rt):
    maina.Tool("mytool").run(...)
# prior Runtime restored here
```

## Overlay registries

A `registry_dirs` root is just a directory of manifest YAML in the
[standard layout](manifests.md#file-layout-and-ids). A later root overriding an
existing id replaces that tool; new ids are added. Use it to pin
different versions, add private tools, or tweak `args` defaults.

## Custom install backends

A backend is any object with a `name` property and a `resolve(...)` method
matching the `InstallerBackend` protocol:

```python
class MyBackend:
    @property
    def name(self) -> str:
        return "mybackend"

    def resolve(self, manifest, *, version, env, ctx):
        exe = ...  # obtain / locate the executable
        return str(exe), ctx.prepend_path(env, exe.parent)
```

`ctx` is an `InstallContext` (cache root, language provider,
`tool_bin_dir(...)`, `prepend_path(...)`). Optionally add a side-effect-free
`probe(self, manifest, *, version, ctx) -> bool` so `maina prepare --check` can
report your backend's tools.

## Custom language provider

Implement `LanguageRuntimeProvider` (`ensure(runtime, version) -> Path` and
`env(runtime, version) -> dict`) and pass it as `language=`. `MiseRuntime` is
the default.
