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


class ArchiveFetcher:
    """Download https assets and extract binaries into a destination directory."""

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

    @staticmethod
    def fetch_https(url: str, dest: Path) -> None:
        """Download a URL that must use the https scheme."""
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise InstallError(f"refusing non-https download URL: {url}")
        try:
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            dest.write_bytes(response.content)
        except requests.RequestException as exc:
            raise InstallError(f"download failed for {url}: {exc}") from exc

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
            self.safe_extract_tar_gz(archive, tmp_path)
            self.find_and_copy_binary(tmp_path, dest_dir, binary)
            return
        if name.endswith(".tar.xz"):
            self.safe_extract_tar_xz(archive, tmp_path)
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
        member_path = (dest / member_name).resolve()
        if not str(member_path).startswith(str(dest.resolve())):
            raise InstallError(f"unsafe path in archive: {member_name}")

    @classmethod
    def safe_extract_tar_gz(cls, archive: Path, dest: Path) -> None:
        """Extract a .tar.gz archive after rejecting path-escape members."""
        dest = dest.resolve()
        with tarfile.open(archive, "r:gz") as tar:
            cls._extract_tar_members(tar, dest)

    @classmethod
    def safe_extract_tar_xz(cls, archive: Path, dest: Path) -> None:
        """Extract a .tar.xz archive after rejecting path-escape members."""
        dest = dest.resolve()
        with tarfile.open(archive, "r:xz") as tar:
            cls._extract_tar_members(tar, dest)

    @classmethod
    def _extract_tar_members(cls, tar: tarfile.TarFile, dest: Path) -> None:
        """Validate then extract every member of an open tar archive."""
        for member in tar.getmembers():
            cls.assert_safe_member(dest, member.name)
        for member in tar.getmembers():
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
