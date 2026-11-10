"""Shared archive download/extract helpers for installer backends."""

from __future__ import annotations

import gzip
import hashlib
import lzma
import shutil
import stat
import tarfile
import tempfile
import warnings
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import IO, TYPE_CHECKING, Literal, Protocol
from urllib.parse import urljoin, urlparse

import requests

from maina.core.errors import InstallError

if TYPE_CHECKING:
    from collections.abc import Iterable

# Soft cap for streamed release assets (GitHub binaries, etc.).
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
# Cap on the *decompressed* size of an archive (guards against zip/tar bombs;
# the download cap above only covers the compressed stream).
EXTRACT_MAX_BYTES = 2 * 1024 * 1024 * 1024
# Cap on archive member count: the byte cap above ignores links and empty
# members, so a tiny archive of millions of symlinks could still exhaust time/memory.
EXTRACT_MAX_MEMBERS = 100_000
# What an executable maina can exec starts with: ELF, Mach-O (thin/fat, either
# endianness), a PE header, or a shebang. A captive-portal HTML page does not.
EXECUTABLE_MAGICS = (
    b"\x7fELF",
    b"#!",
    b"MZ",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
)
# Failures a corrupt, truncated, or unwritable download raises from the stdlib.
UNPACK_ERRORS = (
    tarfile.TarError,
    zipfile.BadZipFile,
    gzip.BadGzipFile,
    lzma.LZMAError,
    EOFError,
    OSError,
)
CHUNK_SIZE = 64 * 1024
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECT_HOPS = 5
TarReadMode = Literal["r:gz", "r:xz"]


class DecompressingReader(Protocol):
    """A ``gzip.GzipFile`` / ``lzma.LZMAFile``-shaped chunked byte reader.

    The two don't share a common typed base in ``typing``, so ``_capped_copy``
    names the one method it actually calls.
    """

    def read(self, size: int = ..., /) -> bytes: ...


class ArchiveFetcher:
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
        with dest.open("wb") as out:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.max_bytes:
                    raise InstallError(f"download exceeds size cap ({self.max_bytes} bytes): {url}")
                out.write(chunk)

    def extract_archive(
        self,
        archive: Path,
        tmp_path: Path,
        dest_dir: Path,
        *,
        binary: str,
        asset_name: str,
        allow_any_executable: bool = False,
    ) -> None:
        """Extract or copy a downloaded asset into dest_dir."""
        name = asset_name.lower()
        extractors = {
            (".tar.gz", ".tgz"): lambda: self.safe_extract_tar(archive, tmp_path, mode="r:gz"),
            (".tar.xz",): lambda: self.safe_extract_tar(archive, tmp_path, mode="r:xz"),
            (".zip",): lambda: self.safe_extract_zip(archive, tmp_path),
        }
        for suffixes, extract in extractors.items():
            if name.endswith(suffixes):
                extract()
                self.find_and_copy_binary(
                    tmp_path, dest_dir, binary, allow_any_executable=allow_any_executable
                )
                return
        self.copy_raw_asset(archive, dest_dir / binary, name=name)

    @staticmethod
    def assert_safe_member(dest: Path, member_name: str) -> None:
        """Reject archive members that escape the destination directory."""
        root = dest.resolve()
        member_path = (root / member_name).resolve()
        if not member_path.is_relative_to(root):
            raise InstallError(f"unsafe path in archive: {member_name}")

    @classmethod
    def assert_safe_link(cls, dest: Path, member: tarfile.TarInfo) -> None:
        """Reject symlink / hardlink members whose target escapes *dest*."""
        if not (member.issym() or member.islnk()):
            return
        root = dest.resolve()
        # A symlink target is relative to the link's own directory; a hardlink
        # target is relative to the archive root.
        anchor = root / Path(member.name).parent if member.issym() else root
        resolved = (anchor / member.linkname).resolve()
        if not resolved.is_relative_to(root):
            raise InstallError(f"unsafe link target in archive: {member.name} -> {member.linkname}")

    @classmethod
    def safe_extract_tar(cls, archive: Path, dest: Path, *, mode: TarReadMode) -> None:
        """Extract a tar archive (``r:gz`` / ``r:xz``) after rejecting path-escape members."""
        dest = dest.resolve()
        with tarfile.open(archive, mode) as tar:
            cls._extract_tar_members(tar, dest)

    @classmethod
    def _extract_tar_members(cls, tar: tarfile.TarFile, dest: Path) -> None:
        """Validate then extract every member of an open tar archive.

        Members are walked one at a time via :meth:`tarfile.TarFile.next`
        rather than :meth:`~tarfile.TarFile.getmembers`, so a highly
        compressible archive is rejected as soon as the running total crosses
        the cap. ``getmembers()`` must read to the end of the compressed
        stream to enumerate every header before returning, forcing full
        decompression cost regardless of where the cap is exceeded.
        """
        members: list[tarfile.TarInfo] = []
        total = 0
        while (member := tar.next()) is not None:
            if member.isreg():
                total += member.size
                if total > EXTRACT_MAX_BYTES:
                    raise InstallError(
                        f"archive expands to over {EXTRACT_MAX_BYTES} bytes, over the cap"
                    )
            if len(members) >= EXTRACT_MAX_MEMBERS:
                raise InstallError(f"archive has over {EXTRACT_MAX_MEMBERS} members, over the cap")
            cls.assert_safe_member(dest, member.name)
            cls.assert_safe_link(dest, member)
            members.append(member)
        try:
            tar.extractall(dest, members=members, filter="data")
        except TypeError as exc:
            # tarfile gained ``filter=`` in 3.11.4 / 3.12; older patch levels are
            # unsupported by CPython and would extract without a data filter.
            raise InstallError(
                "unsafe: this Python lacks tarfile data-filter support (upgrade to 3.11.4+ / 3.12+)"
            ) from exc
        except tarfile.TarError as exc:
            raise InstallError(f"failed to extract archive: {exc}") from exc

    @classmethod
    def safe_extract_zip(cls, archive: Path, dest: Path) -> None:
        """Extract a zip archive after rejecting path-escape members.

        Unlike tar (no separate index; headers are interleaved with data
        throughout the stream), a zip's central directory — what
        ``infolist()`` reads — is a compact record block at the end of the
        file; reading it costs nothing proportional to the archive's actual
        content, so the declared-size cap below runs before any
        decompression, not after it. ``zipfile``'s own reader additionally
        bounds decompressed output to each entry's declared size and
        validates its CRC-32, so a tampered entry that understates its own
        size fails extraction outright (``BadZipFile``) rather than
        overrunning silently; that failure is wrapped as ``InstallError``
        below like every other extraction failure.
        """
        dest = dest.resolve()
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            total = sum(info.file_size for info in infos)
            if total > EXTRACT_MAX_BYTES:
                raise InstallError(
                    f"archive expands to {total} bytes, over the {EXTRACT_MAX_BYTES} cap"
                )
            for info in infos:
                cls.assert_safe_member(dest, info.filename)
            try:
                for info in infos:
                    zf.extract(info, dest)
            except zipfile.BadZipFile as exc:
                raise InstallError(f"corrupt or tampered zip entry in {archive}: {exc}") from exc

    # Compressed / archive formats maina cannot unpack — never install one of
    # these verbatim as an executable.
    UNSUPPORTED_ASSET_SUFFIXES = (
        ".tar.bz2",
        ".tbz2",
        ".tar.zst",
        ".tar",
        ".bz2",
        ".zst",
        ".7z",
        ".rar",
        ".lz4",
        ".lzma",
    )

    @classmethod
    def copy_raw_asset(cls, archive: Path, out: Path, *, name: str) -> None:
        """Copy a single-file asset (bare binary, or ``.gz`` / ``.xz``) to out."""
        if name.endswith(cls.UNSUPPORTED_ASSET_SUFFIXES):
            raise InstallError(f"unsupported asset format: {name}")
        tmp = out.with_name(f".{out.name}.part")
        opener = gzip.open if name.endswith(".gz") else lzma.open if name.endswith(".xz") else None
        try:
            if opener is None:
                shutil.copy2(archive, tmp)
            else:
                with opener(archive, "rb") as src, tmp.open("wb") as dst:
                    cls._capped_copy(src, dst, name)
            cls._require_executable_format(tmp, name)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        cls.make_executable(tmp)
        tmp.replace(out)

    @staticmethod
    def _require_executable_format(path: Path, name: str) -> None:
        """Reject a download that is not an executable (e.g. a proxy's HTML page)."""
        with path.open("rb") as handle:
            head = handle.read(4)
        if not head.startswith(EXECUTABLE_MAGICS):
            raise InstallError(
                f"{name} is not an executable (ELF, Mach-O, PE or shebang script) — "
                "the server may have returned an error or login page instead of the release asset"
            )

    @staticmethod
    def _capped_copy(src: DecompressingReader, dst: IO[bytes], name: str) -> None:
        """Stream *src* (a decompressing reader) to *dst*, aborting past the cap."""
        written = 0
        while chunk := src.read(1 << 20):
            written += len(chunk)
            if written > EXTRACT_MAX_BYTES:
                raise InstallError(f"{name} decompresses past the {EXTRACT_MAX_BYTES} byte cap")
            dst.write(chunk)

    @classmethod
    def find_and_copy_binary(
        cls, tree: Path, dest_dir: Path, binary: str, *, allow_any_executable: bool = False
    ) -> None:
        """Locate binary in an extracted tree and copy it to dest_dir, atomically."""
        source = cls._locate_binary(tree, binary, allow_any_executable=allow_any_executable)
        dest = dest_dir / binary
        tmp = dest.with_name(f".{dest.name}.part")
        shutil.copy2(source, tmp)
        cls.make_executable(tmp)
        tmp.replace(dest)

    @classmethod
    def _locate_binary(cls, tree: Path, binary: str, *, allow_any_executable: bool = False) -> Path:
        """The file to install as *binary* from an extracted tree.

        Only a member literally **named** *binary* is used (shallowest path, then
        lexicographic). If none matches, the extraction fails — unless the
        manifest opts in with ``install.allow_any_executable: true``, in which
        case a lone executable member is accepted with a warning. Without the
        opt-in maina never runs a binary the manifest did not name.
        """
        if by_name := sorted(
            (p for p in tree.rglob(binary) if p.is_file()),
            key=lambda p: (len(p.relative_to(tree).parts), str(p)),
        ):
            return by_name[0]
        if not allow_any_executable:
            members = sorted(str(p.relative_to(tree)) for p in tree.rglob("*") if p.is_file())
            raise InstallError(
                f"binary {binary!r} not in archive (members: {', '.join(members) or 'none'}); "
                "set install.allow_any_executable: true to accept a lone executable"
            )
        return cls._lone_executable(tree, binary)

    @staticmethod
    def _lone_executable(tree: Path, binary: str) -> Path:
        """The single executable member of *tree* (``allow_any_executable`` opt-in)."""
        executable = [p for p in tree.rglob("*") if p.is_file() and p.stat().st_mode & 0o111]
        if len(executable) != 1:
            raise InstallError(f"binary {binary!r} not found and no single executable member")
        warnings.warn(
            f"install.allow_any_executable: using {executable[0].relative_to(tree)} as {binary!r}",
            RuntimeWarning,
            stacklevel=2,
        )
        return executable[0]

    @staticmethod
    def make_executable(path: Path) -> None:
        """Set user/group/other execute bits on path."""
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
