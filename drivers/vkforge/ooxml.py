"""vkforge ooxml — read an Office Open XML package with zipfile and lxml, nothing else.

A .docx or .pptx is a zip of XML parts. The miner reads them as a datasheet, not as a document:
no Office, no automation, no external entity, no network. A package is untrusted input, so parts
are parsed without entity resolution and a part above MAX_PART bytes is refused (zip bomb).
"""
import fnmatch
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
