"""vkforge mine — the firm's documents read as a datasheet: invariants with their support, no content (stage `mine`).

The miner is deterministic and reads structure only: styles, fonts, footers and headers, page
setup, as OOXML declares them. It never opens Office and never reads the body text of a document.
What it does read of the text — a footer, a header, a custom style id — is free text that can carry
a name or an address: it leaves `invariants.json` as a `<redacted:sha256:…>` token and its clear
value goes to the `.vault.json` sidecar (0o600), which stays on the node.

A value is an invariant when the share of mined files carrying it (its support) reaches the
threshold; below it, two values or more are an ambiguity listed with their candidates, and a lone
value is purged (spec §6: what the firm does not do consistently is not a rule).
"""
import argparse
import hashlib
import os
from pathlib import Path

from . import canon, ooxml
from .errors import Refusal
from .ooxml import NS, q

MIN_FILES = 5
BUILTIN_STYLES = {"normal": "Normal", "title": "Title", "subtitle": "Subtitle", "quote": "Quote",
                  **{f"heading {n}": f"Heading{n}" for n in range(1, 10)}}   # w:name (lower-case) → built-in
INVARIANT_IDS = {("paragraph_style", "first"): "title_style", ("paragraph_style", "all"): "body_style",
                 ("font_run", "body"): "body_font", ("font_run", "title"): "title_font",
                 ("footer", None): "footer_text", ("header", None): "header_text",
                 ("export_format", None): "export_format", ("page_size", None): "page_size",
                 ("margins", None): "margins", ("orientation", None): "orientation",
                 ("numbering_present", None): "numbering_present"}
REMEDY = "more documents of one kind"
_TOKEN = "<redacted:sha256:"
_CHAIN = 32          # a basedOn chain is followed this far at most (a cycle in styles.xml)


def redact(text: str) -> str:
    return _TOKEN + hashlib.sha256(text.encode("utf-8")).hexdigest() + ">"


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _half_points(value):
    """w:sz is in half-points: 22 → 11, 21 → 10.5."""
    n = _int(value)
    if n is None:
        return None
    return n // 2 if n % 2 == 0 else n / 2


def _pdf_of(path: Path) -> Path | None:
    for suffix in (".pdf", ".PDF"):
        sibling = path.with_suffix(suffix)
        if sibling.is_file():
            return sibling
    return None


class _Styles:
    """styles.xml as a lookup: name, parent and font of a style, the default paragraph style, docDefaults."""

    def __init__(self, root):
        self.by_id, self.default = {}, None
        self.font, self.size = None, None
        if root is None:
            return
        for s in root.findall("w:style", NS):
            sid = s.get(q("w", "styleId"))
            if sid is None or s.get(q("w", "type")) != "paragraph":
                continue
            name, based = s.find("w:name", NS), s.find("w:basedOn", NS)
            font, size = self.rpr(s.find("w:rPr", NS))
            self.by_id[sid] = {"name": None if name is None else name.get(q("w", "val")),
                               "based": None if based is None else based.get(q("w", "val")),
                               "font": font, "size": size}
            if s.get(q("w", "default")) in ("1", "true") and self.default is None:
                self.default = sid
        self.font, self.size = self.rpr(root.find("w:docDefaults/w:rPrDefault/w:rPr", NS))

    @staticmethod
    def rpr(rpr):
        if rpr is None:
            return None, None
        fonts, sz = rpr.find("w:rFonts", NS), rpr.find("w:sz", NS)
        return (None if fonts is None else fonts.get(q("w", "ascii")),
                None if sz is None else _half_points(sz.get(q("w", "val"))))

    def name(self, sid):
        return self.by_id.get(sid, {}).get("name")

    def font_of(self, sid):
        """(name, size) a paragraph style gives, following basedOn; None where it gives nothing."""
        font = size = None
        seen = 0
        while sid in self.by_id and seen < _CHAIN:
            entry = self.by_id[sid]
            font = font or entry["font"]
            size = size if size is not None else entry["size"]
            sid, seen = entry["based"], seen + 1
        return font, size


def _style_value(sid: str, styles: _Styles, vault: dict) -> dict | None:
    """The built-in anchor of a style, found by its English w:name and never by its styleId. A custom
    style is its w:name (what Word's Styles(name) takes) as a token, the name going to the vault; the
    styleId is never stored. A style that styles.xml does not declare has no name: None."""
    name = styles.name(sid)
    if not name:
        return None
    if name.lower() in BUILTIN_STYLES:
        return {"builtin": BUILTIN_STYLES[name.lower()]}
    token = redact(name)
    vault[token] = name
    return {"custom": token}


def _majority(pairs: list):
    if not pairs:
        return None
    counts = {}
    for pair in pairs:
        counts.setdefault(canon.dumps(list(pair)), [pair, 0])[1] += 1
    return sorted(counts.items(), key=lambda kv: (-kv[1][1], kv[0]))[0][1][0]


def _joined_text(roots, p_tag: str, t_tag: str) -> str:
    """The text of footers and headers (the one free text the miner reads): paragraphs joined by a newline."""
    lines = []
    for root in roots:
        for p in root.iter(p_tag):
            line = "".join(t.text or "" for t in p.iter(t_tag))
            if line.strip():
                lines.append(line)
    return "\n".join(lines)


def _docx(zf, obs: list, vault: dict) -> None:
    doc = ooxml.part(zf, "word/document.xml")
    styles = _Styles(ooxml.part(zf, "word/styles.xml"))
    paragraphs = [] if doc is None else list(doc.iter(q("w", "p")))

    def style_id(p):
        ps = p.find("w:pPr/w:pStyle", NS)
        return None if ps is None else ps.get(q("w", "val"))

    if paragraphs:
        first = style_id(paragraphs[0]) or styles.default
        value = None if first is None else _style_value(first, styles, vault)
        if value is not None:
            obs.append({"kind": "paragraph_style", "where": "first", "value": value})
        rest = paragraphs[1:]
        explicit = [style_id(p) for p in rest]
        if rest and all(explicit):
            values = {canon.dumps(_style_value(sid, styles, {})) for sid in explicit}
            value = _style_value(explicit[0], styles, vault)
            if len(values) == 1 and value is not None:
                obs.append({"kind": "paragraph_style", "where": "all", "value": value})
        pairs = []
        for p in rest:
            pfont, psize = styles.font_of(style_id(p) or styles.default)
            for run in p.iter(q("w", "r")):
                font, size = styles.rpr(run.find("w:rPr", NS))
                font = font or pfont or styles.font
                size = next((v for v in (size, psize, styles.size) if v is not None), None)
                if font is not None and size is not None:
                    pairs.append((font, size))
        best = _majority(pairs)
        if best is not None:
            obs.append({"kind": "font_run", "where": "body", "value": {"name": best[0], "size": best[1]}})
    for kind, tag, pattern in (("footer", "ftr", "word/footer*.xml"), ("header", "hdr", "word/header*.xml")):
        roots = [ooxml.part(zf, n) for n in ooxml.parts(zf, pattern)]
        text = _joined_text(roots, q("w", "p"), q("w", "t"))
        if text:
            vault[redact(text)] = text
            obs.append({"kind": kind, "where": None, "value": redact(text)})
    sect = None
    if doc is not None:
        sect = doc.find("w:body/w:sectPr", NS)
        if sect is None:
            sect = doc.find(".//w:sectPr", NS)
    if sect is not None:
        size, margins = sect.find("w:pgSz", NS), sect.find("w:pgMar", NS)
        if size is not None:
            width, height = _int(size.get(q("w", "w"))), _int(size.get(q("w", "h")))
            if width is not None and height is not None:
                obs.append({"kind": "page_size", "where": None, "value": {"height": height, "width": width}})
                orient = size.get(q("w", "orient"))
                if orient not in ("portrait", "landscape"):      # a free attribute: never copied as it stands
                    orient = "landscape" if width > height else "portrait"
                obs.append({"kind": "orientation", "where": None, "value": orient})
        if margins is not None:
            sides = {s: _int(margins.get(q("w", s))) for s in ("top", "right", "bottom", "left")}
            if all(v is not None for v in sides.values()):
                obs.append({"kind": "margins", "where": None, "value": sides})
    numbering = ooxml.part(zf, "word/numbering.xml")
    if numbering is not None and numbering.find(".//w:num", NS) is not None:
        obs.append({"kind": "numbering_present", "where": None, "value": True})


def _pptx(zf, obs: list, vault: dict) -> None:
    masters = ooxml.parts(zf, "ppt/slideMasters/slideMaster*.xml")
    master = ooxml.part(zf, masters[0]) if masters else None
    if master is None:
        return

    def placeholder(kind):
        return master.xpath(f'.//p:sp[p:nvSpPr/p:nvPr/p:ph[@type="{kind}"]]', namespaces=NS)

    title = placeholder("title")
    if title:
        def first(root, xpath):
            hit = root.xpath(xpath, namespaces=NS)
            return hit[0] if hit else None

        style = first(master, "p:txStyles/p:titleStyle/a:lvl1pPr/a:defRPr")
        name = first(title[0], ".//a:latin/@typeface")
        sz = first(title[0], ".//a:rPr/@sz | .//a:defRPr/@sz")
        if name is None and style is not None:
            name = first(style, "a:latin/@typeface")
        if sz is None and style is not None:
            sz = style.get("sz")
        size = _int(sz)
        if name is not None and size is not None:
            obs.append({"kind": "font_run", "where": "title",
                        "value": {"name": str(name), "size": size // 100 if size % 100 == 0 else size / 100}})
    footers = placeholder("ftr")
    text = _joined_text(footers[:1], q("a", "p"), q("a", "t"))
    if text:
        vault[redact(text)] = text
        obs.append({"kind": "footer", "where": None, "value": redact(text)})


def mine_file(path: Path) -> dict:
    """One document's observations; `vault` maps each token it used to its clear value. Refusal when it is not OOXML."""
    path = Path(path)
    obs, vault = [], {}
    with ooxml.open_package(path) as zf:
        kind = ooxml.kind_of(zf)
        (_docx if kind == "docx" else _pptx)(zf, obs, vault)
    if _pdf_of(path) is not None:
        obs.append({"kind": "export_format", "where": None, "value": {"format": "pdf"}})
    return {"kind": kind, "obs": obs, "vault": vault}


def survey(corpus: Path) -> tuple[list, int]:
    """(the mined files with their observations, the number of files skipped). A .pdf beside a mined
    document is that document's export_format, not a skipped file."""
    corpus = Path(corpus)
    if not corpus.is_dir():
        raise Refusal(f"corpus {corpus} is not a folder", "--corpus names a folder of .docx or .pptx files")
    files = sorted(p for p in corpus.iterdir() if p.is_file())
    mined = []
    for p in files:
        if p.suffix.lower() not in (".docx", ".pptx") or p.name.startswith("~$") or ooxml.is_encrypted(p):
            continue
        try:
            mined.append((p, mine_file(p)))
        except Refusal:
            continue                                    # not a package after all: skipped, never an abort
    pdfs = {pdf for p, _ in mined if (pdf := _pdf_of(p)) is not None}
    return mined, len(files) - len(mined) - len(pdfs)


def content_sha256(corpus: Path) -> str:
    mined, _ = survey(corpus)
    return canon.sha256(sorted(canon.sha256_file(p) for p, _ in mined))


def _tokens(value):
    if isinstance(value, str):
        if value.startswith(_TOKEN):
            yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _tokens(v)
    elif isinstance(value, list):
        for v in value:
            yield from _tokens(v)


def mine_corpus(corpus: Path, threshold: float = 0.8) -> tuple[dict, dict]:
    mined, skipped = survey(corpus)
    kinds = sorted({result["kind"] for _, result in mined})
    if len(kinds) > 1:
        raise Refusal("corpus mixes .docx and .pptx files", REMEDY)
    n = len(mined)
    if n < MIN_FILES:
        raise Refusal(f"corpus has {n} files", REMEDY)
    groups, vault = {}, {}
    for _, result in mined:
        vault.update(result["vault"])
        seen = set()
        for o in result["obs"]:
            key = (o["kind"], o["where"])
            if key in seen:
                continue
            seen.add(key)
            values = groups.setdefault(key, {})
            values.setdefault(canon.dumps(o["value"]), [o["value"], 0])[1] += 1
    invariants, ambiguities = [], []
    for (kind, where), values in groups.items():
        ranked = sorted(values.items(), key=lambda kv: (-kv[1][1], kv[0]))
        ident = INVARIANT_IDS[(kind, where)]
        if ranked[0][1][1] / n >= threshold:
            entry = {"id": ident, "kind": kind, "value": ranked[0][1][0], "support": round(ranked[0][1][1] / n, 4)}
            if where is not None:
                entry["where"] = where
            invariants.append(entry)
        elif len(ranked) >= 2:
            ambiguities.append({"invariant": ident, "kind": kind, "candidates": [
                {"value": value, "support": round(count / n, 4)} for _, (value, count) in ranked]})
    if not invariants:
        raise Refusal(f"no invariant reaches the threshold {threshold} over {n} files", REMEDY)
    invariants.sort(key=lambda i: i["id"])
    ambiguities.sort(key=lambda a: a["invariant"])
    inv = {"corpus": {"files": n, "skipped": skipped, "kind": kinds[0],
                      "content_sha256": canon.sha256(sorted(canon.sha256_file(p) for p, _ in mined)),
                      "schema_sha256": canon.sha256(invariants)},
           "threshold": threshold, "invariants": invariants, "ambiguities": ambiguities}
    used = set(_tokens(invariants)) | set(_tokens(ambiguities))
    return inv, {t: vault[t] for t in sorted(used) if t in vault}


def write_invariants(inv: dict, vault: dict, out: Path) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    canon.write(inv, out / "invariants.json")
    sidecar = out / ".vault.json"
    sidecar.touch()
    try:
        os.chmod(sidecar, 0o600)                        # before the clear text is written; best effort on Windows
    except OSError:
        pass
    canon.write(vault, sidecar)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--corpus", required=True, type=Path, help="folder of .docx or .pptx files of one kind")
    parser.add_argument("--out", required=True, type=Path, help="directory that receives invariants.json and .vault.json")
    parser.add_argument("--threshold", type=float, default=0.8, help="support an invariant needs (default 0.8)")


def run(ns: argparse.Namespace) -> int:
    if not 0 < ns.threshold <= 1:
        raise Refusal(f"threshold {ns.threshold} is not in (0, 1]", "--threshold 0.8")
    corpus, out = Path(ns.corpus).resolve(), Path(ns.out).resolve()
    if out == corpus or corpus in out.parents:
        raise Refusal(f"out {ns.out} is inside the corpus", "write elsewhere")
    inv, vault = mine_corpus(ns.corpus, ns.threshold)
    write_invariants(inv, vault, ns.out)
    print(f"mine: {len(inv['invariants'])} invariants, {len(inv['ambiguities'])} ambiguities over "
          f"{inv['corpus']['files']} files ({inv['corpus']['skipped']} skipped) -> {Path(ns.out) / 'invariants.json'}")
    return 0
