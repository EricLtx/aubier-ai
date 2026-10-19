"""vkforge ooxml — read an Office Open XML package with zipfile and lxml, nothing else.

A .docx or .pptx is a zip of XML parts. The miner reads them as a datasheet, not as a document:
no Office, no automation, no external entity, no network. A package is untrusted input, so parts
are parsed without entity resolution and a part above MAX_PART bytes is refused (zip bomb).
`write_parts` is the reader's twin, for the fake application's `save` (Task 7): parts in, package out.
"""
import fnmatch
import os
import tempfile
import zipfile
import zlib
from pathlib import Path

from lxml import etree

from .errors import Refusal

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"      # the OLE compound-file signature
MAX_PART = 64 * 1024 * 1024
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


def q(prefix: str, local: str) -> str:
    """Clark notation of `prefix:local`, for lxml attribute and tag names."""
    return "{%s}%s" % (NS[prefix], local)


def is_encrypted(path: Path) -> bool:
    """Starts with the OLE signature: an encrypted OOXML package or a binary .doc/.ppt, not a zip."""
    try:
        with open(path, "rb") as f:
            return f.read(len(OLE)) == OLE
    except OSError:
        return False


def open_package(path: Path) -> zipfile.ZipFile:
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, EOFError, ValueError):
        raise Refusal(f"{path.name} is not an OOXML package", "a .docx or .pptx saved by Office")
    if "[Content_Types].xml" not in zf.namelist():
        zf.close()
        raise Refusal(f"{path.name} is not an OOXML package", "a .docx or .pptx saved by Office")
    return zf


def kind_of(zf: zipfile.ZipFile) -> str:
    names = set(zf.namelist())
    name = Path(zf.filename or "package").name
    if "word/document.xml" in names:
        return "docx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    raise Refusal(f"{name} is not OOXML", "a .docx or .pptx saved by Office")


def part(zf: zipfile.ZipFile, name: str):
    """The lxml root of a member, or None when the package has no such part."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART:
        raise Refusal(f"{name} is larger than {MAX_PART} bytes", "a document saved by Office")
    try:
        return etree.fromstring(zf.read(name), _PARSER)
    except (etree.XMLSyntaxError, zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, zlib.error,
            EOFError, ValueError):
        raise Refusal(f"{name} cannot be read as XML", "a document saved by Office")


def parts(zf: zipfile.ZipFile, pattern: str) -> list[str]:
    return sorted(n for n in zf.namelist() if fnmatch.fnmatchcase(n, pattern))


_EPOCH = (1980, 1, 1, 0, 0, 0)        # the zip epoch: an added member carries no date of its own


def _member(name: str, like: zipfile.ZipInfo | None = None) -> zipfile.ZipInfo:
    """A fresh header for `name`: the date, compression and mode of the member it replaces, else the
    epoch, deflated, 0o644 (never a stale size or extra field copied from the old header)."""
    info = zipfile.ZipInfo(name, like.date_time if like is not None else _EPOCH)
    info.compress_type = like.compress_type if like is not None else zipfile.ZIP_DEFLATED
    info.external_attr = like.external_attr if like is not None else 0o644 << 16
    info.create_system = like.create_system if like is not None else 3
    return info


def write_parts(package: Path, parts: dict[str, bytes]) -> None:
    """Each member of `parts` written (added when the package has none), every other member copied
    byte for byte in the package's order, then the package replaced with one os.replace: a reader
    sees the old document or the new one, never half of one."""
    package = Path(package)
    fd, tmp = tempfile.mkstemp(prefix=".~vkforge-", suffix=".tmp", dir=package.parent)
    os.close(fd)
    try:
        with open_package(package) as src, zipfile.ZipFile(tmp, "w") as dst:
            seen = set()
            for info in src.infolist():
                if info.filename in seen:
                    continue
                seen.add(info.filename)
                data = parts[info.filename] if info.filename in parts else src.read(info)
                dst.writestr(_member(info.filename, info), data)
            for name in sorted(set(parts) - seen):
                dst.writestr(_member(name), parts[name])
        os.replace(tmp, package)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
