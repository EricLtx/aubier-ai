"""Minimal valid .docx / .pptx packages built with zipfile — no Office, no template.

Used by test_mine.py and, as a script, to build the corpus the Rust tests and CI mine:

    python -I drivers/vkforge/tests/ooxml_fixtures.py DIR GARAMOND [CALIBRI]
    python -I drivers/vkforge/tests/ooxml_fixtures.py DIR footers [N]

Stdlib only and no import from vkforge, so that it runs under -I as a script. The archives are
byte-identical on every platform (members stored, not deflated, with a fixed date, creator system
and mode), so a corpus built twice has the same content hash. The packages are meant to open in
the real Word and PowerPoint (Task 12).
"""
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

FOOTER = "Cabinet Exemple – Confidentiel"
SALT = bytes(range(32))             # a learn's salt for the tests (D-01): `vk driver learn` draws its own
OTHER_SALT = bytes(range(1, 33))    # another learn's

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_DATE = (1980, 1, 1, 0, 0, 0)         # the zip epoch
_WORD_NAMES = {"Normal": "Normal", "Title": "Title", "Subtitle": "Subtitle"}
_BODY = ("Premier paragraphe du corps.", "Deuxième paragraphe du corps.", "Troisième paragraphe du corps.")


def _write(path: Path, members: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members.items():
            info = zipfile.ZipInfo(name, _DATE)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3                  # Unix, whatever the host: zipfile writes 0 on Windows
            info.external_attr = 0o644 << 16
            zf.writestr(info, (_XML + text).encode("utf-8"))
    return path


def _content_types(overrides: dict) -> str:
    items = "".join(f'<Override PartName="/{n}" ContentType="{t}"/>' for n, t in overrides.items())
    return (f'<Types xmlns="{_CT}"><Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>{items}</Types>')


def _rels(items: list) -> str:
    body = "".join(f'<Relationship Id="{i}" Type="{t}" Target="{g}"/>' for i, t, g in items)
    return f'<Relationships xmlns="{_REL}">{body}</Relationships>'


def _word_style_name(style: str) -> str:
    if style in _WORD_NAMES:
        return _WORD_NAMES[style]
    if style.startswith("Heading") and style[7:].isdigit():
        return "heading " + style[7:]
    return style


def docx(path, title_style="Heading1", body_font=("Garamond", 11), footer=None, header=None,
         personal=None, style_id=None, pdf=False, body_style=None) -> Path:
    """One title paragraph in `title_style` (declared under `style_id` when given, as French Word
    does: styleId Titre1 for the name "heading 1"), three body paragraphs in `body_font` — each in
    the paragraph style named `body_style` when given (declared under that name, letters and digits
    of it as its styleId), in none otherwise."""
    path = Path(path)
    style_id = style_id or title_style
    name, size = body_font
    body_id = None if body_style is None else "".join(c for c in body_style if c.isalnum())
    body_ppr = "" if body_id is None else f'<w:pPr><w:pStyle w:val={quoteattr(body_id)}/></w:pPr>'
    body = [f'<w:p><w:pPr><w:pStyle w:val={quoteattr(style_id)}/></w:pPr><w:r><w:t>Titre du document</w:t></w:r></w:p>']
    for i, text in enumerate(_BODY):
        text = personal if (personal and i == 0) else text
        body.append(f'<w:p>{body_ppr}<w:r><w:rPr><w:rFonts w:ascii={quoteattr(name)} w:hAnsi={quoteattr(name)}/>'
                    f'<w:sz w:val="{int(size * 2)}"/></w:rPr><w:t>{escape(text)}</w:t></w:r></w:p>')
    refs, rels = "", [("rIdS", f"{_R}/styles", "styles.xml")]
    wml = "application/vnd.openxmlformats-officedocument.wordprocessingml."
    overrides = {"word/document.xml": wml + "document.main+xml", "word/styles.xml": wml + "styles+xml",
                 "docProps/app.xml": "application/vnd.openxmlformats-officedocument.extended-properties+xml"}
    members = {}
    for kind, text in (("header", header), ("footer", footer)):
        if text is None:
            continue
        rid, tag = f"rId{kind[0].upper()}", ("hdr" if kind == "header" else "ftr")
        refs += f'<w:{kind}Reference w:type="default" r:id="{rid}"/>'
        rels.append((rid, f"{_R}/{kind}", f"{kind}1.xml"))
        overrides[f"word/{kind}1.xml"] = wml + kind + "+xml"
        members[f"word/{kind}1.xml"] = f'<w:{tag} xmlns:w="{_W}"><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:{tag}>'
    document = (f'<w:document xmlns:w="{_W}" xmlns:r="{_R}"><w:body>{"".join(body)}'
                f'<w:sectPr>{refs}</w:sectPr></w:body></w:document>')
    title_def = ""
    if style_id != "Normal":
        title_def = (f'<w:style w:type="paragraph" w:styleId={quoteattr(style_id)}>'
                     f'<w:name w:val={quoteattr(_word_style_name(title_style))}/>'
                     '<w:basedOn w:val="Normal"/><w:qFormat/></w:style>')
    body_def = ("" if body_id is None else
                f'<w:style w:type="paragraph" w:styleId={quoteattr(body_id)}><w:name w:val={quoteattr(body_style)}/>'
                '<w:basedOn w:val="Normal"/><w:qFormat/></w:style>')
    styles = (f'<w:styles xmlns:w="{_W}"><w:docDefaults><w:rPrDefault><w:rPr>'
              '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
              f'{title_def}{body_def}</w:styles>')
    _write(path, {
        "[Content_Types].xml": _content_types(overrides),
        "_rels/.rels": _rels([("rId1", f"{_R}/officeDocument", "word/document.xml"),
                              ("rId2", f"{_R}/extended-properties", "docProps/app.xml")]),
        "docProps/app.xml": '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                            '<Application>vkforge-fixture</Application></Properties>',
        "word/document.xml": document,
        "word/_rels/document.xml.rels": _rels(rels),
        "word/styles.xml": styles,
        **members,
    })
    if pdf:
        path.with_suffix(".pdf").write_bytes(b"%PDF-1.4 fake\n")
    return path


def _theme() -> str:
    """The smallest theme PowerPoint accepts: 12 colours, one font pair, three of each format style."""
    colours = "".join(f'<a:{n}><a:srgbClr val="{v}"/></a:{n}>' for n, v in (
        ("dk1", "000000"), ("lt1", "FFFFFF"), ("dk2", "1F1F1F"), ("lt2", "EEEEEE"), ("accent1", "4472C4"),
        ("accent2", "ED7D31"), ("accent3", "A5A5A5"), ("accent4", "FFC000"), ("accent5", "5B9BD5"),
        ("accent6", "70AD47"), ("hlink", "0563C1"), ("folHlink", "954F72")))
    fonts = '<a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/>'
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = f'<a:ln w="9525">{fill}</a:ln>'
    effect = "<a:effectStyle><a:effectLst/></a:effectStyle>"
    return (f'<a:theme xmlns:a="{_A}" name="Fixture"><a:themeElements><a:clrScheme name="Fixture">{colours}</a:clrScheme>'
            f'<a:fontScheme name="Fixture"><a:majorFont>{fonts}</a:majorFont><a:minorFont>{fonts}</a:minorFont></a:fontScheme>'
            f'<a:fmtScheme name="Fixture"><a:fillStyleLst>{fill * 3}</a:fillStyleLst><a:lnStyleLst>{line * 3}</a:lnStyleLst>'
            f'<a:effectStyleLst>{effect * 3}</a:effectStyleLst><a:bgFillStyleLst>{fill * 3}</a:bgFillStyleLst></a:fmtScheme>'
            '</a:themeElements></a:theme>')


def pptx(path, title_font=("Garamond", 32), footer=None) -> Path:
    """One master (a title placeholder in `title_font`, an `ftr` placeholder when `footer`), one layout, one slide."""
    name, size = title_font
    ns = f'xmlns:a="{_A}" xmlns:p="{_P}" xmlns:r="{_R}"'

    def shape(sid: int, label: str, ph: str, run: str) -> str:
        return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{label}"/><p:cNvSpPr/><p:nvPr><p:ph type="{ph}"/></p:nvPr></p:nvSpPr>'
                f'<p:spPr/><p:txBody><a:bodyPr/><a:p>{run}</a:p></p:txBody></p:sp>')

    title = shape(2, "Title", "title", f'<a:r><a:rPr lang="fr-FR" sz="{int(size * 100)}">'
                                       f'<a:latin typeface={quoteattr(name)}/></a:rPr><a:t>Titre</a:t></a:r>')
    foot = (shape(3, "Footer", "ftr", f'<a:r><a:rPr lang="fr-FR"/><a:t>{escape(footer)}</a:t></a:r>')
            if footer is not None else "")
    tree = ('<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr/>{}</p:spTree></p:cSld>')
    clr_map = ('<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
               'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>')
    master = (f'<p:sldMaster {ns}>{tree.format(title + foot)}{clr_map}'
              '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
              '<p:txStyles><p:titleStyle/></p:txStyles></p:sldMaster>')
    layout = f'<p:sldLayout {ns} type="title">{tree.format("")}</p:sldLayout>'
    slide = f'<p:sld {ns}>{tree.format("")}</p:sld>'
    pres = (f'<p:presentation {ns}><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            '<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
    ct = "application/vnd.openxmlformats-officedocument.presentationml."
    return _write(Path(path), {
        "[Content_Types].xml": _content_types({"ppt/presentation.xml": ct + "presentation.main+xml",
                                               "ppt/slideMasters/slideMaster1.xml": ct + "slideMaster+xml",
                                               "ppt/slideLayouts/slideLayout1.xml": ct + "slideLayout+xml",
                                               "ppt/slides/slide1.xml": ct + "slide+xml",
                                               "ppt/theme/theme1.xml": "application/vnd.openxmlformats-officedocument.theme+xml"}),
        "_rels/.rels": _rels([("rId1", f"{_R}/officeDocument", "ppt/presentation.xml")]),
        "ppt/presentation.xml": pres,
        "ppt/_rels/presentation.xml.rels": _rels([("rId1", f"{_R}/slideMaster", "slideMasters/slideMaster1.xml"),
                                                  ("rId2", f"{_R}/slide", "slides/slide1.xml"),
                                                  ("rId3", f"{_R}/theme", "theme/theme1.xml")]),
        "ppt/slideMasters/slideMaster1.xml": master,
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _rels([("rId1", f"{_R}/slideLayout",
                                                                "../slideLayouts/slideLayout1.xml"),
                                                               ("rId2", f"{_R}/theme", "../theme/theme1.xml")]),
        "ppt/theme/theme1.xml": _theme(),
        "ppt/slideLayouts/slideLayout1.xml": layout,
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels([("rId1", f"{_R}/slideMaster",
                                                                "../slideMasters/slideMaster1.xml")]),
        "ppt/slides/slide1.xml": slide,
        "ppt/slides/_rels/slide1.xml.rels": _rels([("rId1", f"{_R}/slideLayout", "../slideLayouts/slideLayout1.xml")]),
    })


def corpus(dir, garamond: int, calibri: int = 0) -> Path:
    """c0.docx … : `garamond` house documents (Garamond 11) then `calibri` strays (Calibri 12), each with its PDF."""
    dir = Path(dir)
    dir.mkdir(parents=True, exist_ok=True)
    for i in range(garamond + calibri):
        font = ("Garamond", 11) if i < garamond else ("Calibri", 12)
        docx(dir / f"c{i}.docx", title_style="Heading1", body_font=font, footer=FOOTER, pdf=True)
    return dir


def client_footer(n: int) -> str:
    """The footer of the n-th contract of the footers corpus: a client's name in each."""
    return f"Contrat client {n}"


FOOTERS = [client_footer(n) for n in range(10)]      # the footers of `footers(dir)`, written in this one place


def footers(dir, n: int = 10) -> Path:
    """c0.docx … : `n` contracts, each with its PDF, whose footers all differ — a client's name in each
    (`client_footer`): the footer is an ambiguity of `n` token candidates, founder decision D-02's case."""
    dir = Path(dir)
    dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        docx(dir / f"c{i}.docx", footer=client_footer(i), pdf=True)
    return dir


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) in (2, 3) and args[1] == "footers":
        footers(Path(args[0]), int(args[2]) if len(args) > 2 else 10)
    elif 2 <= len(args) <= 3:
        corpus(Path(args[0]), int(args[1]), int(args[2]) if len(args) > 2 else 0)
    else:
        sys.exit("usage: ooxml_fixtures.py DIR GARAMOND [CALIBRI] | ooxml_fixtures.py DIR footers [N]")
