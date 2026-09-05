"""Security tests: archive-extraction path/link-escape and size-cap enforcement."""

from __future__ import annotations

import io
import struct
import tarfile
import zipfile
from typing import TYPE_CHECKING

import pytest

from maina.backends.archive import ArchiveFetcher
from maina.core.errors import InstallError

if TYPE_CHECKING:
    from pathlib import Path


def test_assert_safe_member_rejects_prefix_sibling(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    (tmp_path / "destevil").mkdir()
    # Classic startswith false positive: .../destevil starts with .../dest.
    with pytest.raises(InstallError, match="unsafe path"):
        ArchiveFetcher.assert_safe_member(dest, "../destevil/bin")


def test_assert_safe_member_rejects_dotdot(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(InstallError, match="unsafe path"):
        ArchiveFetcher.assert_safe_member(dest, "../outside")


def test_assert_safe_member_allows_nested(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    ArchiveFetcher.assert_safe_member(dest, "bin/tool")


def test_safe_extract_tar_rejects_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name="../escape")
        payload = b"x"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(InstallError, match="unsafe path"):
        ArchiveFetcher.safe_extract_tar(archive, dest, mode="r:gz")


def test_safe_extract_tar_rejects_symlink_escape(tmp_path: Path) -> None:
    archive = tmp_path / "evil-symlink.tar.gz"
    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../outside"
        tar.addfile(info)
    with pytest.raises(InstallError, match="unsafe link target"):
        ArchiveFetcher.safe_extract_tar(archive, dest, mode="r:gz")


def test_safe_extract_tar_rejects_hardlink_escape(tmp_path: Path) -> None:
    archive = tmp_path / "evil-hardlink.tar.gz"
    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name="hardlink")
        info.type = tarfile.LNKTYPE
        info.linkname = "../outside"
        tar.addfile(info)
    with pytest.raises(InstallError, match="unsafe link target"):
        ArchiveFetcher.safe_extract_tar(archive, dest, mode="r:gz")


def test_safe_extract_tar_rejects_oversized_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap must be enforced by the streaming member walk (``tar.next()``),
    not only reachable via ``getmembers()``."""
    monkeypatch.setattr("maina.backends.archive.EXTRACT_MAX_BYTES", 4)
    archive = tmp_path / "big.tar.gz"
    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"0123456789"
        info = tarfile.TarInfo(name="big")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(InstallError, match="over the"):
        ArchiveFetcher.safe_extract_tar(archive, dest, mode="r:gz")


def patch_zip_declared_size(path: Path, *, real_size: int, lie_size: int) -> None:
    """Rewrite the uncompressed-size field in both the local file header and
    the central directory record of the (single) entry in *path*, without
    touching the compressed data — simulating a zip entry whose declared
    ``file_size`` understates what it actually decompresses to. Deflate
    decompression is bounded by the compressed byte count, not by this field,
    so the real payload still comes out at *real_size* bytes.
    """
    data = bytearray(path.read_bytes())
    real_le = struct.pack("<I", real_size)
    lie_le = struct.pack("<I", lie_size)
    local_sig = data.find(b"PK\x03\x04")
    assert local_sig != -1
    assert data[local_sig + 22 : local_sig + 26] == real_le
    data[local_sig + 22 : local_sig + 26] = lie_le
    central_sig = data.find(b"PK\x01\x02")
    assert central_sig != -1
    assert data[central_sig + 24 : central_sig + 28] == real_le
    data[central_sig + 24 : central_sig + 28] = lie_le
    path.write_bytes(bytes(data))


def test_safe_extract_zip_rejects_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape", "x")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(InstallError, match="unsafe path"):
        ArchiveFetcher.safe_extract_zip(archive, dest)


def test_safe_extract_zip_extracts_a_normal_entry(tmp_path: Path) -> None:
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("bin/demo", "hello")
    dest = tmp_path / "out"
    dest.mkdir()
    ArchiveFetcher.safe_extract_zip(archive, dest)
    assert (dest / "bin" / "demo").read_text() == "hello"


def test_safe_extract_zip_rejects_tampered_entry_size(tmp_path: Path) -> None:
    """A zip entry whose declared size does not match its real content (the
    CRC-32 zipfile itself validates) must fail as a clean ``InstallError``,
    not a raw ``zipfile.BadZipFile`` escaping the extraction boundary.

    (``zipfile.ZipExtFile`` already bounds decompressed *output* to the
    declared size and independently checks CRC-32, so understating the size
    does not let more bytes reach disk than declared — it surfaces as
    corruption instead. That corruption must still be a domain error.)
    """
    real_size = 100_000
    archive = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("payload.bin", b"A" * real_size)
    patch_zip_declared_size(archive, real_size=real_size, lie_size=4)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(InstallError, match="corrupt or tampered zip"):
        ArchiveFetcher.safe_extract_zip(archive, dest)


def test_safe_extract_zip_rejects_oversized_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("maina.backends.archive.EXTRACT_MAX_BYTES", 10)
    archive = tmp_path / "big.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("payload.bin", b"x" * 20)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(InstallError, match="over the"):
        ArchiveFetcher.safe_extract_zip(archive, dest)
