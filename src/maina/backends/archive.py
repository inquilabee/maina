"""Shared archive download/extract helpers for installer backends."""

from __future__ import annotations

import hashlib
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import requests

from maina.backends.unpack import UNPACK_ERRORS, ArchiveUnpacker
from maina.core.errors import InstallError

if TYPE_CHECKING:
    from collections.abc import Iterable

# Soft cap for streamed release assets (GitHub binaries, etc.).
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
# Wall-clock cap on one download; the per-read timeout alone lets a slow-drip server hold it open.
DOWNLOAD_MAX_SECONDS = 30 * 60
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECT_HOPS = 5


class ArchiveFetcher(ArchiveUnpacker):
    """Download https assets and extract binaries into a destination directory.

    When ``allowed_redirect_hosts`` is given, redirects are resolved hop by hop
    and every hop host must match one of those names (exact or ``.suffix``);
    this keeps a github download from being bounced to an arbitrary host. With
    ``None`` (default) redirects are followed normally.
    """

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        allowed_redirect_hosts: Iterable[str] | None = None,
    ) -> None:
        self._session = session or requests.Session()
        self.max_bytes = max_bytes
        self._allowed_hosts = (
            None if allowed_redirect_hosts is None else frozenset(allowed_redirect_hosts)
        )

    def close(self) -> None:
        """Close the underlying :class:`requests.Session`."""
        self._session.close()

    def download_and_extract(
        self,
        url: str,
        dest_dir: Path,
        *,
        binary: str,
        asset_name: str,
        sha256: str | None = None,
        allow_any_executable: bool = False,
    ) -> None:
        """Download an asset and extract/copy the binary into dest_dir.

        When *sha256* is given, the downloaded asset must match that hex digest
        before it is extracted; a mismatch raises :class:`InstallError`.
        """
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                # asset_name is validated at manifest load, but never let it escape.
                archive = tmp_path / Path(asset_name).name
                self.fetch_https(url, archive)
                if sha256:
                    self._verify_sha256(archive, expected=sha256, url=url)
                self.extract_archive(
                    archive,
                    tmp_path,
                    dest_dir,
                    binary=binary,
                    asset_name=asset_name,
                    allow_any_executable=allow_any_executable,
                )
        except UNPACK_ERRORS as exc:
            raise InstallError(f"could not install {asset_name} from {url}: {exc}") from exc

    @staticmethod
    def _verify_sha256(archive: Path, *, expected: str, url: str) -> None:
        """Raise unless *archive* hashes to *expected* (case-insensitive hex)."""
        with archive.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual.lower() != expected.strip().lower():
            raise InstallError(
                f"sha256 mismatch for {url}: expected {expected.strip().lower()}, got {actual}"
            )

    def fetch_https(self, url: str, dest: Path) -> None:
        """Stream a URL that must use the https scheme, enforcing a size cap."""
        if urlparse(url).scheme != "https" or not urlparse(url).netloc:
            raise InstallError(f"refusing non-https download URL: {url}")
        if self._allowed_hosts is None:
            self._stream_get(url, dest, allow_redirects=True)
            return
        current = url
        for _ in range(MAX_REDIRECT_HOPS):
            self._require_allowed_host(current)
            location = self._stream_get(current, dest, allow_redirects=False)
            if location is None:
                return
            current = urljoin(current, location)
        raise InstallError(f"too many redirects fetching {url}")

    def _stream_get(self, url: str, dest: Path, *, allow_redirects: bool) -> str | None:
        """GET *url*; write the body, or return a redirect Location to follow."""
        try:
            with self._session.get(
                url, timeout=120, stream=True, allow_redirects=allow_redirects
            ) as response:
                if not allow_redirects and response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise InstallError(f"redirect missing Location: {url}")
                    return location
                response.raise_for_status()
                self._write_capped_stream(response, dest, url=url)
        except requests.RequestException as exc:
            raise InstallError(f"download failed for {url}: {exc}") from exc
        return None

    def _require_allowed_host(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        listed = self._host_in_allowlist(host)
        if parsed.scheme != "https" or parsed.username is not None or not host or not listed:
            raise InstallError(f"refusing redirect off allowed hosts: {url}")

    def _host_in_allowlist(self, host: str) -> bool:
        allowed = self._allowed_hosts or frozenset()
        return any(host == name or host.endswith(f".{name}") for name in allowed)

    def _write_capped_stream(self, response: requests.Response, dest: Path, *, url: str) -> None:
        """Write response body to dest, rejecting Content-Length or body over max_bytes."""
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            with suppress(ValueError):
                if int(content_length) > self.max_bytes:
                    raise InstallError(f"download exceeds size cap ({self.max_bytes} bytes): {url}")
        total = 0
        deadline = time.monotonic() + DOWNLOAD_MAX_SECONDS
        with dest.open("wb") as out:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                if time.monotonic() > deadline:
                    raise InstallError(f"download took over {DOWNLOAD_MAX_SECONDS}s: {url}")
                total += len(chunk)
                if total > self.max_bytes:
                    raise InstallError(f"download exceeds size cap ({self.max_bytes} bytes): {url}")
                out.write(chunk)
