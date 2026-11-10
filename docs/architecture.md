# Architecture

## Registry-first tool runtime

The registry — a tree of YAML manifests — is the single source of truth for
tools. The runtime loads a manifest into a `Tool` on demand; the `Tool`
installs or resolves the executable, maps keyword arguments to argv, runs the
process, and returns a structured `ToolResult`.

No hand-written Python wrapper per CLI. Convenience names are thin bindings:
`kubectl_get = Tool("kubectl", subcommand="get")`.

Language runtimes (Node, and any future ones) are declared per manifest with
`requires:` and provisioned through [mise](https://mise.jdx.dev/). Static
binaries skip that path entirely. mise is an implementation backend, never the
user-facing API.

Layers:

```text
Tool (public)
  -> Registry      load + merge manifests, resolve ids
  -> Installer     dispatch to an InstallerBackend, compose env
       -> PathBackend / GithubBackend / NpmBackend / PipBackend / ScriptBackend
       -> LanguageRuntimeProvider (MiseRuntime)
  -> ArgParser     kwargs -> argv from the manifest schema
  -> subprocess
  -> ParserResolver  stdout -> ToolResult.parsed
```

## Runtime and register extendibility

The composition root is `Runtime` — it owns registry overlay roots, the parser
map, the installer backend map, the language provider, and the cache root.
Everything registers through the **same** `register(rt, ...)` call.
`Runtime.preferred()` registers the builtin parsers, install backends, and mise
— but **no tool catalog**. maina ships the engine and the manifest contract;
catalogs belong to the consumer.

`LanguageRuntimeProvider` and `Parser` are structural `Protocol`s — a plugin
satisfies them by shape alone, no subclassing. `InstallerBackend` is a real
`ABC`: a backend declares conformance by explicit inheritance, and Python
enforces it at instantiation (a subclass missing `resolve`/`name` fails with a
clear `TypeError` instead of an `AttributeError` the first time a manifest
dispatches to it). Either way, a plugin is just passed to `register(...)` — no
maina source edit.

The process-wide session (`use`, bare `register`, `runtime()`) is a convenience
for scripts and is not thread-safe. Libraries and concurrent code pass an
explicit `Runtime` or use `maina.isolated(rt)`.
