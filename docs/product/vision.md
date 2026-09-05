# Vision

## Who

Vishal Mishra maintains this. The client is a Python developer who needs to call a
non-Python CLI (a linter, formatter, `git`, `jq`, an npm or pip-installed tool) from
Python without hand-writing install, argv, and env plumbing for each one. The same
person also builds other tools (ShipGate) that need a consistent engine to run their
tool catalogs on.

## Product

maina is a library and CLI. You describe a tool once in a YAML manifest: what to
run, its arguments, how to install it. `Tool(name)` then resolves or installs the
executable, maps keyword arguments to argv, runs the process, and returns a
structured result (raw text, parsed value, exit code, timing).

It ships the engine and the manifest contract, not a tool catalog: callers register
their own manifests, and other projects build their catalogs on top of maina rather
than depending on one bundled with it. Install backends cover path-resolved
binaries, GitHub release archives, npm packages, pip packages in per-tool
virtual environments, and repo-local scripts or script packages, with
mise-provisioned language runtimes and per-tool caching underneath.

## Not this

Not a curated registry of pre-wrapped CLIs. Not a general subprocess wrapper
unrelated to the manifest contract. Not a project-wide dependency manager: each
install backend resolves one pinned tool into its own cache directory, not a shared
environment graph.

## Success

A Python project adds a manifest for a CLI it depends on and calls it as
`Tool(id)(**kwargs)`, with no hand-written install or argv glue. A consumer like
ShipGate expresses its whole tool catalog as maina manifests and gets the same argv
its own hand-built runner produced. maina is published on PyPI with a working
GitHub Pages user guide that someone outside this project can follow unassisted.
