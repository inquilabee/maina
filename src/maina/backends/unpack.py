"""Safe extraction of downloaded release assets (tar / zip / bare binaries)."""

from __future__ import annotations

import gzip
import lzma
import shutil
import stat
import tarfile
import warnings
import zipfile
from pathlib import Path
from typing import IO, Literal, Protocol

from maina.core.errors import InstallError

TarReadMode = Literal["r:gz", "r:xz"]

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
    b"\xca\xfe\xba\xbf",
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


class DecompressingReader(Protocol):
    """A ``gzip.GzipFile`` / ``lzma.LZMAFile``-shaped chunked byte reader.

    The two don't share a common typed base in ``typing``, so ``_capped_copy``
    names the one method it actually calls.
    """

    def read(self, size: int = ..., /) -> bytes: ...


class ArchiveUnpacker:
    """Extract a downloaded asset into a directory, refusing unsafe archives."""

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
        self.copy_raw_asset(
            archive, dest_dir / binary, name=name, allow_any_executable=allow_any_executable
        )

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
            if len(infos) > EXTRACT_MAX_MEMBERS:
                raise InstallError(f"archive has over {EXTRACT_MAX_MEMBERS} members, over the cap")
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
    def copy_raw_asset(
        cls, archive: Path, out: Path, *, name: str, allow_any_executable: bool = False
    ) -> None:
        """Copy a single-file asset (bare binary, or ``.gz`` / ``.xz``) to out.

        Unless *allow_any_executable*, the file must start like an executable.
        """
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
            if not allow_any_executable:
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
