"""The fake application: the COM adapter's twin on the OOXML parts (L-P19). Repository only, never
vendored: a generated driver reaches it only when `vkforge qualify --fake` (or a test) put fake.json
in its work directory. Word .docx only (L-P31). Its verbs are com.VERBS' keys: they edit the
package's parts in memory, `save` writes them with ooxml.write_parts, and every check re-reads the
saved file through the miner, the reading the qualification makes. Its dialog box (fake.json's
"dialog_on") is closed by com.guarded, the guard the real driver runs (L-P33). Its refusals are the
adapter's: a failure while opening is `precondition`, a failure inside a verb `internal`."""
import os
import posixpath
import threading
from pathlib import Path

from lxml import etree

from vkdriver.protocol import DriverRefusal
from vkforge import mine, ooxml
from vkforge.errors import Refusal
from vkforge.ooxml import NS, q
from vkforge.runtime import com    # the repository's adapter: its guard is the one the real driver runs

FAKE_CALL_DEADLINE_S = 1.0   # the guard's deadline when the fake raises its dialog box: a test waits 1 s, not 30
FAKE_PDF = b"%PDF-1.4 fake\n" + b"%" * 1185 + b"\n"      # 1 200 bytes: a header and one comment line
READ_ONLY = "document opened read-only (locked elsewhere)"

_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_WML = "application/vnd.openxmlformats-officedocument.wordprocessingml."
_DOCUMENT, _STYLES, _RELS, _TYPES = ("word/document.xml", "word/styles.xml", "word/_rels/document.xml.rels",
                                     "[Content_Types].xml")
# The schema's order of the children the fake writes (CT_RPr's sequence, up to w:sz): a new one is put
# after its predecessors, so the part stays valid for Word.
_RPR_ORDER = ("rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
              "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
              "w", "kern", "position", "sz")
_BANDS = {"footer": "ftr", "header": "hdr"}
_STYLE_WHERE = {"first": ("first",), "title": ("first",), "body": ("all",), "all": ("first", "all")}  # the miner's places


class _Document:
    """An opened .docx: its path and the parts the verbs loaded, edited in memory until `save`."""

    def __init__(self, path: Path):
        self.path, self.parts, self.dirty = path, {}, set()

    def part(self, name: str):
        """The part's root, read from the package once; None when the package has no such part."""
        if name not in self.parts:
            try:
                with ooxml.open_package(self.path) as zf:
                    root = ooxml.part(zf, name)
            except (Refusal, OSError) as e:                 # inside a verb: the adapter's COMError
                raise DriverRefusal("internal", str(e)) from e
            if root is None:
                return None
            self.parts[name] = root
        return self.parts[name]

    def put(self, name: str, root) -> None:
        self.parts[name] = root
        self.dirty.add(name)


class FakeApplication:
    def __init__(self, binding: dict, config: dict | None = None):
        if binding["app"] != "word":
            raise DriverRefusal("precondition", "the fake application implements Word .docx only; "
                                                "remedy: qualify on the real application")
        self.binding = binding
        self.verbs = {v["verb"]: v for v in binding["verbs"]}
        self.dialog_on = (config or {}).get("dialog_on")   # <work>/fake.json's, handed over by the driver (L-P33)

    def probe(self) -> None:
        """No type library behind the fake: nothing to compare (L-P22)."""

    def open(self, path: str):
        """The document, as the adapter's `open` gives it: a file the OS says is read-only refuses as
        Word's doc.ReadOnly does (a document the user holds open elsewhere)."""
        path = Path(path)
        if not path.is_file():
            raise DriverRefusal("precondition", "no document at the path given")
        if not os.access(path, os.W_OK):
            raise DriverRefusal("precondition", READ_ONLY)
        try:
            with ooxml.open_package(path) as zf:
                kind = ooxml.kind_of(zf)
        except Refusal as e:
            raise DriverRefusal("precondition", str(e)) from e
        if kind != "docx":
            raise DriverRefusal("precondition", "the fake application implements Word .docx only; "
                                                "remedy: qualify on the real application")
        return _Document(path)

    def call(self, verb: str, doc, **args):
        if verb == self.dialog_on:
            return self.__dialog__(verb)
        return VERBS[verb](self, doc, **args)

    def __dialog__(self, verb: str):
        """fake.json's `dialog_on` (L-P33): the call blocks behind a modal box, as Word's does behind a
        sign-in or an overwrite prompt, until the adapter's own guard closes the box — so the refusal
        `dialog` comes from com.guarded, the code the real driver runs."""
        closed = threading.Event()
        return com.guarded(lambda: closed.wait(60), verb, deadline_s=FAKE_CALL_DEADLINE_S,
                           find_dialogs=lambda: [] if closed.is_set() else ["the fake's dialog box"],
                           close=lambda hwnd: closed.set())

    def close(self, doc) -> None:
        """Closed without saving, as the adapter's Close(**close): what `save` did not write is gone."""
        doc.parts.clear()
        doc.dirty.clear()

    def quit(self) -> None:
        """No instance behind the fake."""


# --- the parts ---------------------------------------------------------------------------------------

def _xml(root) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _child(parent, local: str, order: tuple = ()):
    """`parent`'s w:<local>, created when absent: after the children `order` puts before it, else first."""
    found = parent.find(f"w:{local}", NS)
    if found is not None:
        return found
    rank = order.index(local) if local in order else 0
    at = 0
    for i, c in enumerate(parent):
        if isinstance(c.tag, str) and etree.QName(c).localname in order[:rank]:
            at = i + 1
    element = etree.Element(q("w", local))
    parent.insert(at, element)
    return element


def _need_selector(selector, verb: str) -> None:
    if not isinstance(selector, str):
        raise DriverRefusal("precondition", f"verb {verb} needs a selector")


def _paragraphs(doc: _Document, selector: str, verb: str) -> list:
    """The paragraphs a selector names, in the miner's reading of document.xml (every w:p, in order)."""
    _need_selector(selector, verb)
    root = doc.part(_DOCUMENT)
    paragraphs = [] if root is None else list(root.iter(q("w", "p")))
    count = len(paragraphs)
    if selector in ("first", "title"):
        wanted = [1]
    elif selector == "body":
        wanted = list(range(2, count + 1))
    elif selector == "all":
        wanted = list(range(1, count + 1))
    elif selector.startswith("index:") and selector[6:].isdigit():
        wanted = [int(selector[6:])]
    else:
        raise DriverRefusal("precondition", f"selector {selector} is not one of first, body, all, title, index:<n>")
    if any(not 1 <= i <= count for i in wanted):
        raise DriverRefusal("precondition", f"selector {selector} selects no paragraph of this document")
    return [paragraphs[i - 1] for i in wanted]


def _english_name(builtin: str) -> str:
    """The w:name Word writes for a built-in style whatever its language (heading 1, Title, Normal)."""
    key = next((k for k, v in mine.BUILTIN_STYLES.items() if v == builtin), None)
    if key is None:
        raise DriverRefusal("precondition", f"the fake application knows no OOXML name for the built-in style {builtin}")
    return builtin if key == builtin.lower() else key


def _style_id(doc: _Document, builtin: str) -> str:
    """The styleId of the paragraph style whose w:name is `builtin`'s, added to styles.xml with its
    English w:name when the document has none (as Word adds a built-in style on first use)."""
    name = _english_name(builtin)
    styles = doc.part(_STYLES)
    if styles is None:
        styles = etree.Element(q("w", "styles"), nsmap={"w": NS["w"]})
        _add_part(doc, _STYLES, styles, "styles", "styles.xml", _WML + "styles+xml")
    ids, default = set(), None
    for s in styles.findall("w:style", NS):
        sid = s.get(q("w", "styleId"))
        ids.add(sid)
        if s.get(q("w", "type")) != "paragraph":
            continue
        if s.get(q("w", "default")) in ("1", "true") and default is None:
            default = sid
        n = s.find("w:name", NS)
        if n is not None and (n.get(q("w", "val")) or "").lower() == name.lower():
            return sid
    sid, k = builtin, 1
    while sid in ids:
        sid, k = f"{builtin}{k}", k + 1
    style = etree.SubElement(styles, q("w", "style"), {q("w", "type"): "paragraph", q("w", "styleId"): sid})
    etree.SubElement(style, q("w", "name"), {q("w", "val"): name})
    if default is not None:
        etree.SubElement(style, q("w", "basedOn"), {q("w", "val"): default})
    etree.SubElement(style, q("w", "qFormat"))
    doc.put(_STYLES, styles)
    return sid


def _rels(doc: _Document):
    rels = doc.part(_RELS)
    if rels is None:
        rels = etree.Element(f"{{{_REL}}}Relationships", nsmap={None: _REL})
    return rels


def _add_part(doc: _Document, name: str, root, kind: str, target: str, content_type: str) -> str:
    """A new part of the document: its relationship from document.xml and its content type. Its rId."""
    rels = _rels(doc)
    ids = {r.get("Id") for r in rels}
    rid, n = "rIdVk1", 1
    while rid in ids:
        n += 1
        rid = f"rIdVk{n}"
    etree.SubElement(rels, f"{{{_REL}}}Relationship",
                     {"Id": rid, "Type": f"{NS['r']}/{kind}", "Target": target})
    doc.put(_RELS, rels)
    types = doc.part(_TYPES)
    etree.SubElement(types, f"{{{_CT}}}Override", {"PartName": "/" + name, "ContentType": content_type})
    doc.put(_TYPES, types)
    doc.put(name, root)
    return rid


def _write_band(doc: _Document, kind: str, text: str) -> None:
    """The default header or footer of the body's section, written with `text` (one paragraph per
    line); the part, its relationship and the w:sectPr reference are added when absent."""
    tag = _BANDS[kind]
    root = etree.Element(q("w", tag), nsmap={"w": NS["w"], "r": NS["r"]})
    for line in text.split("\n"):
        t = etree.SubElement(etree.SubElement(etree.SubElement(root, q("w", "p")), q("w", "r")), q("w", "t"))
        t.text = line
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    document = doc.part(_DOCUMENT)
    body = document.find("w:body", NS)
    sect = body.find("w:sectPr", NS)
    if sect is None:
        sect = etree.SubElement(body, q("w", "sectPr"))
    ref = next((r for r in sect.findall(f"w:{kind}Reference", NS) if r.get(q("w", "type")) == "default"), None)
    if ref is not None:
        rel = next((r for r in _rels(doc) if r.get("Id") == ref.get(q("r", "id"))), None)
        if rel is not None:
            target = rel.get("Target")
            name = target[1:] if target.startswith("/") else posixpath.normpath(posixpath.join("word", target))
            doc.put(name, root)
            return
    try:
        with ooxml.open_package(doc.path) as zf:
            taken = set(zf.namelist()) | set(doc.parts)
    except (Refusal, OSError) as e:
        raise DriverRefusal("internal", str(e)) from e
    n = 1
    while f"word/{kind}{n}.xml" in taken:
        n += 1
    name = f"word/{kind}{n}.xml"
    rid = _add_part(doc, name, root, kind, f"{kind}{n}.xml", _WML + kind + "+xml")
    if ref is None:
        at = sum(1 for c in sect if isinstance(c.tag, str)
                 and etree.QName(c).localname in ("headerReference", "footerReference"))
        ref = etree.Element(q("w", f"{kind}Reference"), {q("w", "type"): "default"})
        sect.insert(at, ref)
    ref.set(q("r", "id"), rid)
    doc.put(_DOCUMENT, document)


def _observed(doc: _Document) -> list:
    """The saved file, read back by the miner: what the qualification will see."""
    try:
        return mine.mine_file(doc.path)["obs"]
    except (Refusal, OSError) as e:
        raise DriverRefusal("internal", str(e)) from e


# --- the verbs: com.VERBS' keys, over the parts --------------------------------------------------------

def apply_paragraph_style(app, doc, selector: str, style: str) -> None:
    com._constant(com._entry(app, "apply_paragraph_style"), style)   # the adapter's refusal for a style it lacks
    targets = _paragraphs(doc, selector, "apply_paragraph_style")
    sid = _style_id(doc, style)
    for p in targets:
        _child(_child(p, "pPr"), "pStyle").set(q("w", "val"), sid)
    doc.put(_DOCUMENT, doc.part(_DOCUMENT))


def verify_paragraph_style(app, doc, selector: str, style: str) -> bool:
    com._constant(com._entry(app, "verify_paragraph_style"), style)
    _need_selector(selector, "verify_paragraph_style")
    obs = _observed(doc)
    wheres = _STYLE_WHERE.get(selector, ())
    return bool(wheres) and all({"kind": "paragraph_style", "where": w, "value": {"builtin": style}} in obs
                                for w in wheres)


def set_font_run(app, doc, selector: str, font: dict) -> None:
    com._entry(app, "set_font_run")
    size = font["size"] * 2
    half_points = str(int(size)) if float(size).is_integer() else None
    if half_points is None:
        raise DriverRefusal("precondition", f"font size {font['size']} is not a whole number of half-points")
    for p in _paragraphs(doc, selector, "set_font_run"):
        for run in p.iter(q("w", "r")):
            rpr = _child(run, "rPr")
            fonts = _child(rpr, "rFonts", _RPR_ORDER)
            for theme in ("asciiTheme", "hAnsiTheme"):          # a theme font overrides the named one
                fonts.attrib.pop(q("w", theme), None)
            fonts.set(q("w", "ascii"), font["name"])
            fonts.set(q("w", "hAnsi"), font["name"])
            _child(rpr, "sz", _RPR_ORDER).set(q("w", "val"), half_points)
    doc.put(_DOCUMENT, doc.part(_DOCUMENT))


def verify_font_run(app, doc, selector: str, font: dict) -> bool:
    com._entry(app, "verify_font_run")
    _need_selector(selector, "verify_font_run")
    return any(o["kind"] == "font_run" and o["where"] == selector and o["value"]["name"] == font["name"]
               and float(o["value"]["size"]) == float(font["size"]) for o in _observed(doc))


def set_header(app, doc, text: str) -> None:
    com._entry(app, "set_header")
    _write_band(doc, "header", text)


def set_footer(app, doc, text: str) -> None:
    com._entry(app, "set_footer")
    _write_band(doc, "footer", text)


def verify_header(app, doc, text: str) -> bool:
    com._entry(app, "verify_header")
    return {"kind": "header", "where": None, "value": mine.redact(text)} in _observed(doc)


def verify_footer(app, doc, text: str) -> bool:
    com._entry(app, "verify_footer")
    return {"kind": "footer", "where": None, "value": mine.redact(text)} in _observed(doc)


def save(app, doc) -> None:
    com._entry(app, "save")
    try:
        ooxml.write_parts(doc.path, {name: _xml(doc.parts[name]) for name in sorted(doc.dirty)})
    except Refusal as e:
        raise DriverRefusal("internal", f"save: {e}") from e
    except OSError as e:                                    # the adapter's COMError for the same condition
        raise DriverRefusal("internal", f"save: the document cannot be written: {e.strerror}") from e
    doc.dirty.clear()


def export(app, doc, format: str, path: str) -> None:
    com._constant(com._entry(app, "export"), format)
    if format.lower() != "pdf":
        raise DriverRefusal("precondition", f"the fake application exports pdf only, not {format}")
    try:
        Path(path).write_bytes(FAKE_PDF)
    except OSError as e:
        raise DriverRefusal("internal", f"export: {e.strerror}") from e


def verify_pdf(app, doc, path: str) -> bool:
    return com.verify_pdf(app, doc, path)


VERBS = {"apply_paragraph_style": apply_paragraph_style, "set_font_run": set_font_run,
         "set_header": set_header, "set_footer": set_footer, "save": save, "export": export,
         "verify_paragraph_style": verify_paragraph_style, "verify_font_run": verify_font_run,
         "verify_header": verify_header, "verify_footer": verify_footer, "verify_pdf": verify_pdf}
