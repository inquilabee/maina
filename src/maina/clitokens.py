"""``maina run`` token parsing: RunInvocation, RunArgParser, RawArgv, RunOpts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import typer


def split_at_dashdash(tokens: list[str]) -> tuple[list[str], list[str] | None]:
    """Split tokens on the first ``--``; return (head, tail_without_sep).

    When ``--`` is absent, returns ``(tokens, None)``. When present, the
    separator itself is omitted from both sides (tail may be empty).
    """
    if "--" not in tokens:
        return tokens, None
    sep = tokens.index("--")
    return tokens[:sep], tokens[sep + 1 :]


@dataclass(frozen=True, slots=True)
class RunOpts:
    """maina-side options for ``maina run`` (not tool kwargs)."""

    as_json: bool = False
    dry_run: bool = False
    timeout: float | None = None
    cwd: str | None = None


class RawArgv:
    """Capture process argv before Click drops the ``--`` separator."""

    def __init__(self) -> None:
        self._argv: list[str] | None = None

    def capture(self, argv: list[str]) -> None:
        """Store argv for later ``run`` token recovery."""
        self._argv = list(argv)

    def clear(self) -> None:
        """Drop the captured argv."""
        self._argv = None

    def after_command(self, command: str) -> list[str] | None:
        """Return tokens after ``command``, or None when unset / missing."""
        if self._argv is None:
            return None
        try:
            idx = self._argv.index(command)
        except ValueError:
            return None
        return self._argv[idx + 1 :]


RAW_ARGV = RawArgv()


class RunInvocation:
    """Normalize ``run`` argv: tool id, payload tokens, and help intent."""

    HELP_FLAGS = frozenset({"--help", "-h"})

    @classmethod
    def resolve(
        cls,
        tool_id: str | None,
        ctx_args: list[str],
    ) -> tuple[str | None, list[str], bool]:
        """Prefer raw argv (keeps ``--``); fall back to Click ``ctx.args``."""
        raw_after = RAW_ARGV.after_command("run")
        return cls.parse_raw(raw_after) if raw_after is not None else cls.parse(tool_id, ctx_args)

    JSON_FLAG = "--json"
    DRY_RUN_FLAG = "--dry-run"
    MAINA_FLAGS = frozenset({JSON_FLAG, DRY_RUN_FLAG})
    TIMEOUT_OPT = "--timeout="
    CWD_OPT = "--cwd="

    @classmethod
    def split_maina_opts(cls, head: list[str]) -> tuple[RunOpts, list[str]]:
        """Pull the maina-side options (``--json``, ``--dry-run``, ``--timeout=``,
        ``--cwd=``) out of *head*; the rest are the tool's ``key=value`` tokens."""
        rest: list[str] = []
        as_json = dry_run = False
        timeout: float | None = None
        cwd: str | None = None
        for tok in head:
            if tok == cls.JSON_FLAG:
                as_json = True
            elif tok == cls.DRY_RUN_FLAG:
                dry_run = True
            elif tok.startswith(cls.TIMEOUT_OPT):
                timeout = cls._opt_float(tok)
            elif tok.startswith(cls.CWD_OPT):
                cwd = tok.removeprefix(cls.CWD_OPT)
            else:
                rest.append(tok)
        return RunOpts(as_json=as_json, dry_run=dry_run, timeout=timeout, cwd=cwd), rest

    @classmethod
    def _opt_float(cls, tok: str) -> float:
        try:
            return float(tok.removeprefix(cls.TIMEOUT_OPT))
        except ValueError as exc:
            raise typer.BadParameter(f"{tok!r}: --timeout needs a number of seconds") from exc

    @classmethod
    def _lift_lead_flag(
        cls, tool_id: str | None, tokens: list[str]
    ) -> tuple[str | None, list[str]]:
        """`maina run --json <id> …` — move a leading maina flag past the id."""
        return (
            (tokens[0], [str(tool_id), *tokens[1:]])
            if tool_id in cls.MAINA_FLAGS and tokens
            else (tool_id, tokens)
        )

    @classmethod
    def parse_raw(cls, after_run: list[str]) -> tuple[str | None, list[str], bool]:
        """Parse tokens captured after the ``run`` command name.

        A maina flag (``--json`` / ``--dry-run``) written *before* the tool id
        is relocated into the token stream so it is not read as the id.
        """
        split = next(
            (i for i, tok in enumerate(after_run) if tok not in cls.MAINA_FLAGS), len(after_run)
        )
        lead, rest = after_run[:split], after_run[split:]
        if not rest:
            return None, [], False
        tid, tokens, wants_help = cls.parse(rest[0], rest[1:])
        return tid, [*lead, *tokens], wants_help

    @classmethod
    def parse(
        cls,
        tool_id: str | None,
        tokens: list[str],
    ) -> tuple[str | None, list[str], bool]:
        """Return ``(tool_id, tokens, wants_help)`` with help flags stripped from the head."""
        if tool_id in cls.HELP_FLAGS:
            tool_id = None
        tool_id, tokens = cls._lift_lead_flag(tool_id, tokens)
        head, tail = split_at_dashdash(tokens)
        if tail is not None:
            wants_help = any(t in cls.HELP_FLAGS for t in head)
            head = [t for t in head if t not in cls.HELP_FLAGS]
            # Preserve ``--`` in the token stream for RunArgParser / is_empty.
            return tool_id, [*head, "--", *tail], wants_help
        # Click drops ``--``; only treat sole help flags as help requests.
        wants_help = all(t in cls.HELP_FLAGS for t in tokens) if tokens else False
        return tool_id, ([] if wants_help else tokens), wants_help

    @staticmethod
    def is_empty(tokens: list[str]) -> bool:
        """True only when nothing was passed at all.

        A bare ``maina run <id>`` prints help; ``maina run <id> --`` explicitly
        runs the tool with no arguments (for surfaces with no required args).
        """
        return not tokens


class RunArgParser:
    """Parse CLI tokens into Tool.run kwargs and optional extra argv."""

    @classmethod
    def parse(
        cls,
        tokens: list[str],
        *,
        flag_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[str] | None]:
        """Split tokens into kwargs and optional extra passthrough after ``--``.

        ``true``/``yes``/``1``/``0``/… are coerced to booleans only for args the
        manifest declares as ``type: flag``; every other value stays a string.
        ``key:=value`` forces the literal string regardless.
        """
        kw_tokens, extra = split_at_dashdash(tokens)
        kwargs: dict[str, Any] = {}
        for token in kw_tokens:
            if token:
                cls._parse_token(kwargs, token, flag_names=flag_names)
        return kwargs, extra

    @classmethod
    def _parse_token(
        cls, kwargs: dict[str, Any], token: str, *, flag_names: frozenset[str]
    ) -> None:
        """Fold one ``key=value`` / ``key:=value`` / bare-flag token into *kwargs*."""
        eq, colon_eq = token.find("="), token.find(":=")
        if colon_eq != -1 and (eq == -1 or colon_eq < eq):  # key:=value forces a literal string
            key, value = token[:colon_eq], token[colon_eq + 2 :]
            cls._require_key(token, key)
            cls._assign(kwargs, key, value=value)
            return
        if eq != -1:
            key, value = token[:eq], token[eq + 1 :]
            cls._require_key(token, key)
            if "." in key:  # arg.subkey=value -> a pairs dict
                arg, _, sub = key.partition(".")
                cls._require_key(token, arg)
                bucket = kwargs.setdefault(arg, {})
                if not isinstance(bucket, dict):
                    raise typer.BadParameter(
                        f"{arg!r} was already given a plain value; use only {arg}.key=value tokens"
                    )
                bucket[sub] = value
                return
            cls._assign(kwargs, key, value=cls._coerce(value) if key in flag_names else value)
            return
        if token not in flag_names:
            raise typer.BadParameter(f"{token!r} is not a flag; pass it as {token}=<value>")
        cls._assign(kwargs, token, value=True)

    @staticmethod
    def _require_key(token: str, key: str) -> None:
        if not key:
            raise typer.BadParameter(f"invalid run token: {token!r}")

    @staticmethod
    def _assign(kwargs: dict[str, Any], key: str, *, value: Any) -> None:
        """Set or append a kwarg; repeated keys become lists."""
        if key not in kwargs:
            kwargs[key] = value
            return
        existing = kwargs[key]
        if isinstance(existing, list):
            existing.append(value)
        else:
            kwargs[key] = [existing, value]

    @staticmethod
    def _coerce(value: str) -> Any:
        """Coerce common boolean spellings; leave other values as strings."""
        lowered = value.lower()
        return (
            True
            if lowered in {"true", "yes", "1"}
            else (False if lowered in {"false", "no", "0"} else value)
        )
