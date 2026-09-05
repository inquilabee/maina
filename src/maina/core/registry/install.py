"""The typed ``install:`` block — where an executable comes from.

Split from :mod:`maina.core.registry.specs` because this one block carries the
most rules: a repo, an asset name per platform (or one pattern for all of them),
a digest, a package name, and the opt-ins that let an install run code.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from string import Formatter
from typing import Any, ClassVar

from maina.core.platform import PLATFORM_BY_PREFIX

from .common import (
    is_unsafe_segment,
    manifest_error,
    require_bool,
    safe_segment,
    scalar_str,
    unknown_keys,
)
from .keys import INSTALL_KEYS

# The os_map (not arch_map) key space is closed: maina only ever emits
# linux/macos/windows as the {os} half of a platform key. arch_map keys stay
# open (any platform.machine() string is a legitimate, if exotic, arch).
KNOWN_OS_TOKENS = frozenset(PLATFORM_BY_PREFIX.values())


@dataclass(slots=True, frozen=True)
class InstallSpec:
    """Typed ``install:`` block from a resolved manifest."""

    backend: str = "path"
    repo: str | None = None
    assets: dict[str, str] = field(default_factory=dict)
    asset_template: str | None = None
    os_map: dict[str, str] = field(default_factory=dict)
    arch_map: dict[str, str] = field(default_factory=dict)
    binary: str | None = None
    tag: str | None = None
    package: str | None = None
    entry: str | None = None
    sha256: dict[str, str] = field(default_factory=dict)
    allow_scripts: bool = False
    allow_sdist: bool = False
    allow_any_executable: bool = False

    @classmethod
    def from_mapping(cls, raw: Any, *, tool_id: str) -> InstallSpec:
        """Parse install map; fail closed on non-maps and unknown keys."""
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise manifest_error(tool_id, "install must be a map")
        if bad := unknown_keys(raw, INSTALL_KEYS):
            keys = ", ".join(bad)
            raise manifest_error(tool_id, f"install unknown keys: {keys}")
        return cls(
            backend=(
                "path"
                if raw.get("backend") is None
                else scalar_str(raw["backend"], field_name="install.backend", tool_id=tool_id)
            ),
            repo=cls._parse_repo(raw.get("repo"), tool_id=tool_id),
            assets=cls._parse_assets(raw.get("assets"), tool_id=tool_id),
            asset_template=cls._parse_asset_template(raw.get("asset_template"), tool_id=tool_id),
            os_map=cls._parse_token_map(
                raw.get("os_map"),
                field_name="os_map",
                tool_id=tool_id,
                valid_keys=KNOWN_OS_TOKENS,
            ),
            arch_map=cls._parse_token_map(
                raw.get("arch_map"), field_name="arch_map", tool_id=tool_id
            ),
            binary=safe_segment(
                raw.get("binary"), field_name="install.binary", tool_id=tool_id, allow_none=True
            ),
            tag=safe_segment(
                raw.get("tag"), field_name="install.tag", tool_id=tool_id, allow_none=True
            ),
            package=cls._parse_package(raw.get("package"), tool_id=tool_id),
            entry=cls._parse_entry(raw.get("entry"), tool_id=tool_id),
            sha256=cls._parse_sha256(raw.get("sha256"), tool_id=tool_id),
            allow_scripts=require_bool(
                raw.get("allow_scripts"), field_name="install.allow_scripts", tool_id=tool_id
            ),
            allow_sdist=require_bool(
                raw.get("allow_sdist"), field_name="install.allow_sdist", tool_id=tool_id
            ),
            allow_any_executable=require_bool(
                raw.get("allow_any_executable"),
                field_name="install.allow_any_executable",
                tool_id=tool_id,
            ),
        )

    # 8 bytes (64 bits): wide enough that a collision between two distinct
    # specs sharing a cache directory is not a realistic catalog-scale risk
    # (5 bytes/40 bits was the original width).
    IDENTITY_DIGEST_SIZE: ClassVar[int] = 8

    def identity_key(self, *, extra: tuple[Any, ...] = ()) -> str:
        """Short stable hash of what this spec fetches (repo/tag/asset/sha256).

        Two manifests that share ``cli`` + ``version`` but differ here must not
        share a cache directory. *extra* lets the caller fold in context the
        spec does not own (e.g. the ``requires:`` runtime pins for ``npm``).
        """
        payload = repr(
            (
                self.backend,
                self.repo,
                self.tag,
                sorted(self.assets.items()),
                self.asset_template,
                sorted(self.os_map.items()),
                sorted(self.arch_map.items()),
                sorted(self.sha256.items()),
                self.package,
                self.binary,
                self.allow_scripts,
                self.allow_sdist,
                self.allow_any_executable,
                extra,
            )
        )
        return hashlib.blake2s(
            payload.encode("utf-8"), digest_size=self.IDENTITY_DIGEST_SIZE
        ).hexdigest()

    # npm package: optional @scope/, then a name of url-safe lowercase chars.
    _NPM_NAME_RE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")

    @classmethod
    def _parse_package(cls, raw: Any, *, tool_id: str) -> str | None:
        """Validate ``install.package`` as an npm package name (scoped ok)."""
        if raw is None:
            return None
        text = scalar_str(raw, field_name="install.package", tool_id=tool_id)
        if not cls._NPM_NAME_RE.match(text):
            raise manifest_error(tool_id, f"install.package {text!r} is not a valid npm name")
        return text

    @staticmethod
    def _parse_entry(raw: Any, *, tool_id: str) -> str | None:
        """Parse ``install.entry`` (the ``script`` backend's file/dir ref).

        Just a well-formed string here — path-safety (no ``..``/absolute, must
        resolve inside the registry root) is checked at load time once the
        declaring layer's root is known; see ``Registry._resolve_entry_path``.
        """
        return None if raw is None else scalar_str(raw, field_name="install.entry", tool_id=tool_id)

    @staticmethod
    def _parse_repo(raw: Any, *, tool_id: str) -> str | None:
        if raw is None:
            return None
        text = scalar_str(raw, field_name="install.repo", tool_id=tool_id)
        parts = text.split("/")
        if len(parts) != 2 or not all(parts) or any(is_unsafe_segment(p) for p in parts):
            raise manifest_error(tool_id, f"install.repo {text!r} must be 'owner/name'")
        return text

    # Placeholders ``asset_template`` may name. ``{os}`` / ``{arch}`` are the
    # host's own platform-key halves (after ``os_map`` / ``arch_map``), never
    # manifest-controlled path text.
    ASSET_TEMPLATE_FIELDS: ClassVar[frozenset[str]] = frozenset({"version", "os", "arch"})

    def asset_for(self, platform_key: str, version: str | None) -> str | None:
        """The release asset name for *platform_key*, or ``None``.

        An explicit ``assets`` entry wins, so a one-off name for a single
        platform still overrides the pattern. Otherwise ``asset_template`` is
        expanded — most projects name every asset to one pattern, and writing it
        once beats six near-identical lines that can drift apart.
        """
        if (explicit := self.assets.get(platform_key)) is not None:
            return str(explicit)
        if self.asset_template is None:
            return None
        os_token, _, arch_token = platform_key.partition("-")
        return self.asset_template.format(
            version=version or "",
            os=self.os_map.get(os_token, os_token),
            arch=self.arch_map.get(arch_token, arch_token),
        )

    def has_assets(self) -> bool:
        """Whether this spec can name an asset at all (explicit map or template)."""
        return any((self.assets, self.asset_template))

    @classmethod
    def _parse_asset_template(cls, raw: Any, *, tool_id: str) -> str | None:
        """Validate ``asset_template``: bare ``{version}`` / ``{os}`` / ``{arch}``.

        Anything else is a typo that would otherwise surface as a 404 mid-install.
        Attribute / item access and format specs are rejected for the same reason
        ``output.from_file`` rejects them.
        """
        if raw is None:
            return None
        text = scalar_str(raw, field_name="install.asset_template", tool_id=tool_id)
        if not text:
            raise manifest_error(tool_id, "install.asset_template must be a non-empty pattern")
        for _literal, name, spec, conv in Formatter().parse(text):
            if name is None:
                continue
            if spec or conv:
                raise manifest_error(
                    tool_id,
                    f"install.asset_template placeholder {name!r} may not carry a "
                    "conversion or format spec",
                )
            if name not in cls.ASSET_TEMPLATE_FIELDS:
                allowed = ", ".join(sorted(cls.ASSET_TEMPLATE_FIELDS))
                raise manifest_error(
                    tool_id,
                    f"install.asset_template placeholder {name!r} is not one of {{{allowed}}}",
                )
        return text

    @staticmethod
    def _parse_token_map(
        raw: Any, *, field_name: str, tool_id: str, valid_keys: frozenset[str] | None = None
    ) -> dict[str, str]:
        """Parse ``os_map`` / ``arch_map`` — maina's token to the project's spelling.

        *valid_keys*, when given, closes the key space (``os_map``'s keys can
        only be ``linux``/``macos``/``windows`` — a typo like ``linvx`` would
        otherwise silently no-op instead of failing at load).
        """
        prefix = f"install.{field_name}"
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise manifest_error(tool_id, f"{prefix} must be a map")
        if valid_keys is not None and (bad := sorted(str(k) for k in raw if k not in valid_keys)):
            allowed = ", ".join(sorted(valid_keys))
            raise manifest_error(tool_id, f"{prefix} keys {bad} not in {{{allowed}}}")
        return {
            str(key): safe_segment(value, field_name=f"{prefix}.{key}", tool_id=tool_id)
            for key, value in raw.items()
        }

    @staticmethod
    def _parse_assets(raw: Any, *, tool_id: str) -> dict[str, str]:
        assets = raw or {}
        if not isinstance(assets, dict):
            raise manifest_error(tool_id, "install.assets must be a map")
        return {
            str(key): safe_segment(value, field_name=f"install.assets.{key}", tool_id=tool_id)
            for key, value in assets.items()
        }

    @staticmethod
    def _parse_sha256(raw: Any, *, tool_id: str) -> dict[str, str]:
        """Parse the optional ``install.sha256`` map (platform key → 64-hex digest)."""
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise manifest_error(
                tool_id, "install.sha256 must be a map of platform key to hex digest"
            )
        out: dict[str, str] = {}
        for key, value in raw.items():
            digest = str(value).strip().lower()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise manifest_error(
                    tool_id, f"install.sha256.{key} {value!r} is not a 64-char hex digest"
                )
            out[str(key)] = digest
        return out
