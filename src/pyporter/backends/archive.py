"""Shared archive download/extract helpers for installer backends."""

from __future__ import annotations

import gzip
import lzma
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from pyporter.errors import InstallError

# Soft cap for streamed release assets (GitHub binaries, etc.).
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024


class ArchiveFetcher:
    """Download https assets and extract binaries into a destination directory."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._session = session or requests.Session()
        self.max_bytes = max_bytes

    def download_and_extract(
        self,
        url: str,
        dest_dir: Path,
        *,
        binary: str,
        asset_name: str,
    ) -> None:
        """Download an asset and extract/copy the binary into dest_dir."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / asset_name
            self.fetch_https(url, archive)
            self.extract_archive(
                archive,
                tmp_path,
                dest_dir,
                binary=binary,
                asset_name=asset_name,
            )

    def fetch_https(self, url: str, dest: Path) -> None:
        """Stream a URL that must use the https scheme, enforcing a size cap."""
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise InstallError(f"refusing non-https download URL: {url}")
        try:
            with self._session.get(url, timeout=120, stream=True) as response:
                response.raise_for_status()
                self._write_capped_stream(response, dest, url=url)
        except requests.RequestException as exc:
            raise InstallError(f"download failed for {url}: {exc}") from exc

    def _write_capped_stream(self, response: requests.Response, dest: Path, *, url: str) -> None:
        """Write response body to dest, rejecting Content-Length or body over max_bytes."""
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    raise InstallError(
                        f"download exceeds size cap ({self.max_bytes} bytes): {url}"
                    )
            except ValueError:
                pass
        total = 0
        with dest.open("wb") as out:
            for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.max_bytes:
                    raise InstallError(
                        f"download exceeds size cap ({self.max_bytes} bytes): {url}"
                    )
                out.write(chunk)

    def extract_archive(
        self,
        archive: Path,
        tmp_path: Path,
        dest_dir: Path,
        *,
        binary: str,
        asset_name: str,
    ) -> None:
        """Extract or copy a downloaded asset into dest_dir."""
        name = asset_name.lower()
        if name.endswith((".tar.gz", ".tgz")):
            self.safe_extract_tar(archive, tmp_path, mode="r:gz")
            self.find_and_copy_binary(tmp_path, dest_dir, binary)
            return
        if name.endswith(".tar.xz"):
            self.safe_extract_tar(archive, tmp_path, mode="r:xz")
            self.find_and_copy_binary(tmp_path, dest_dir, binary)
            return
        if name.endswith(".zip"):
            self.safe_extract_zip(archive, tmp_path)
            self.find_and_copy_binary(tmp_path, dest_dir, binary)
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
    def safe_extract_tar(cls, archive: Path, dest: Path, *, mode: str) -> None:
        """Extract a tar archive (``r:gz`` / ``r:xz``) after rejecting path-escape members."""
        dest = dest.resolve()
        with tarfile.open(archive, mode) as tar:
            cls._extract_tar_members(tar, dest)

    # Back-compat aliases for callers/tests that named the old helpers.
    @classmethod
    def safe_extract_tar_gz(cls, archive: Path, dest: Path) -> None:
        """Extract a .tar.gz archive after rejecting path-escape members."""
        cls.safe_extract_tar(archive, dest, mode="r:gz")

    @classmethod
    def safe_extract_tar_xz(cls, archive: Path, dest: Path) -> None:
        """Extract a .tar.xz archive after rejecting path-escape members."""
        cls.safe_extract_tar(archive, dest, mode="r:xz")

    @classmethod
    def _extract_tar_members(cls, tar: tarfile.TarFile, dest: Path) -> None:
        """Validate then extract every member of an open tar archive."""
        members = tar.getmembers()
        for member in members:
            cls.assert_safe_member(dest, member.name)
        try:
            tar.extractall(dest, members=members, filter="data")
        except TypeError:
            # Python 3.11 without filter= support.
            for member in members:
                tar.extract(member, dest)

    @classmethod
    def safe_extract_zip(cls, archive: Path, dest: Path) -> None:
        """Extract a zip archive after rejecting path-escape members."""
        dest = dest.resolve()
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                cls.assert_safe_member(dest, info.filename)
            for info in zf.infolist():
                zf.extract(info, dest)

    @classmethod
    def copy_raw_asset(cls, archive: Path, out: Path, *, name: str) -> None:
        """Copy a non-archive (possibly compressed) asset to out."""
        if name.endswith(".gz"):
            with gzip.open(archive, "rb") as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        elif name.endswith(".xz"):
            with lzma.open(archive, "rb") as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        else:
            shutil.copy2(archive, out)
        cls.make_executable(out)

    @classmethod
    def find_and_copy_binary(cls, tree: Path, dest_dir: Path, binary: str) -> None:
        """Locate binary in an extracted tree and copy it to dest_dir."""
        matches = [p for p in tree.rglob(binary) if p.is_file()]
        if not matches:
            files = [p for p in tree.rglob("*") if p.is_file()]
            if len(files) != 1:
                raise InstallError(f"binary {binary!r} not found in archive")
            matches = files
        dest = dest_dir / binary
        shutil.copy2(matches[0], dest)
        cls.make_executable(dest)

    @staticmethod
    def make_executable(path: Path) -> None:
        """Set user/group/other execute bits on path."""
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
