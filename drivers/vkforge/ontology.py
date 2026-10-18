"""vkforge bind — the firm's ontology projected onto a catalogue (stage `bind`).

The ontology (ontology/office-document.json, hand-maintained, stored in canon.dumps form) is the
family's instruction set: abstract verbs with typed arguments, a precondition, a postcondition and,
per application, a projection written as a pattern over the catalogue — `Type.Member[.Member]`
walked through the members' return types, plus the enum whose built-in constants the verb takes.
`bind` resolves every pattern against one application's catalogue (the datasheet of the type
library) into binding-<app>.json: what this peripheral implements exactly, partly, or not at all.

Argument types: Path, a file path; ParagraphSelector, `first | body | all | title | index:<n>`;
BuiltinStyle, a language-independent built-in anchor (a paragraph, table or slide-layout style,
never a localised name); FontSpec, `{name, size}`; Text, a free string; Format, a format value
(a file format, an alignment, an orientation, a spacing or a margin).
"""
import argparse
import copy
import json
import re
from pathlib import Path

from . import canon, inventory
from .errors import Refusal

ONTOLOGY = Path(__file__).resolve().parent / "ontology" / "office-document.json"
ARG_TYPES = frozenset({"Path", "ParagraphSelector", "BuiltinStyle", "FontSpec", "Text", "Format"})
PRECONDITIONS = frozenset({"none", "document_open"})
POSTCONDITIONS = frozenset({"document_open", "paragraph_style", "font_run", "footer", "header", "pdf_exists", "saved", "none"})
APPLICATION_KEYS = ("alerts_off", "close", "collection", "open", "progid")   # the keys of each `applications` entry (L-P31)

VERB_FIELDS = ("args", "post", "pre", "projection", "reversible", "verb")
PROJECTION_KEYS = ("constants", "member", "status")
REMEDY = "fix drivers/vkforge/ontology/office-document.json, in canon.dumps form"
_SELECTOR = re.compile(r"first|body|all|title|index:[1-9][0-9]*")
_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+")
_LOOSE = frozenset({"VARIANT", "IDispatch"})   # a step that names no type: the next member is sought on every type
_OBJECT_KINDS = frozenset({"dispatch", "coclass"})


def is_selector(s: str) -> bool:
    return isinstance(s, str) and _SELECTOR.fullmatch(s) is not None


def _read_json(path: Path, what: str, remedy: str):
    if not path.is_file():
        raise Refusal(f"{what} {path} not found", remedy)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):                           # unreadable, not UTF-8, not JSON
        raise Refusal(f"{what} {path} is not JSON", remedy)


def load(path: Path | None = None) -> dict:
    """The ontology at `path` (default ONTOLOGY), validated and in canon.dumps form, so that
    ontology_sha256 of what it returns is the hash of the file's bytes."""
    path = ONTOLOGY if path is None else Path(path)
    ont = _read_json(path, "ontology",
                     "--ontology names an ontology file, else drivers/vkforge/ontology/office-document.json")
    validate(ont)
    if path.read_bytes() != canon.dumps(ont):
        raise Refusal(f"ontology {path} is not in canonical form",
                      "rewrite it with vkforge.canon.dumps (sorted keys, indent 1, UTF-8, final newline)")
    return ont


def _refuse(what: str) -> Refusal:
    return Refusal(what, REMEDY)


def _validate_applications(apps: dict) -> None:
    for app in sorted(set(apps) | set(inventory.APPS)):
        if app not in inventory.APPS:
            raise _refuse(f"ontology application {app} is not one of {', '.join(sorted(inventory.APPS))}")
        if not isinstance(apps.get(app), dict):
            raise _refuse(f"ontology application {app} is missing or not an object")
        entry = apps[app]
        for key in sorted(set(entry) | set(APPLICATION_KEYS)):
            if key not in APPLICATION_KEYS:
                raise _refuse(f"ontology application {app}: {key} is not one of {', '.join(APPLICATION_KEYS)}")
            if key not in entry:
                raise _refuse(f"ontology application {app}: {key} is missing")


def _validate_projection(name: str, app: str, proj) -> None:
    where = f"ontology verb {name}: projection {app}"
    if app not in inventory.APPS:
        raise _refuse(f"{where} is not an application of {', '.join(sorted(inventory.APPS))}")
    if not isinstance(proj, dict):
        raise _refuse(f"{where} is not an object")
    for key in sorted(proj):
        if key not in PROJECTION_KEYS:
            raise _refuse(f"{where}: {key} is not one of {', '.join(PROJECTION_KEYS)}")
    if not isinstance(proj.get("member"), str) or not _PATTERN.fullmatch(proj["member"]):
        raise _refuse(f"{where}: member {proj.get('member')!r} is not Type.Member[.Member]")
    if not (proj.get("constants") is None or isinstance(proj["constants"], str)):
        raise _refuse(f"{where}: constants is neither an enum name nor null")
    if proj.get("status", "partial") != "partial":
        raise _refuse(f"{where}: status {proj['status']!r} is not partial (bound and unbound are bind's to say)")


def _validate_verb(i: int, v) -> str:
    if not isinstance(v, dict) or not isinstance(v.get("verb"), str) or not v["verb"]:
        raise _refuse(f"ontology verb #{i}: verb is missing or not a name")
    name = v["verb"]
    for field in sorted(set(v) | set(VERB_FIELDS)):
        if field not in VERB_FIELDS:
            raise _refuse(f"ontology verb {name}: {field} is not one of {', '.join(VERB_FIELDS)}")
        if field not in v:
            raise _refuse(f"ontology verb {name}: {field} is missing")
    if not isinstance(v["args"], dict):
        raise _refuse(f"ontology verb {name}: args is not an object")
    for arg, kind in v["args"].items():
        if not isinstance(kind, str) or kind not in ARG_TYPES:
            raise _refuse(f"ontology verb {name}: args {arg} has type {kind!r}, "
                          f"not one of {', '.join(sorted(ARG_TYPES))}")
    if not isinstance(v["pre"], str) or v["pre"] not in PRECONDITIONS:
        raise _refuse(f"ontology verb {name}: pre {v['pre']!r} is not one of {', '.join(sorted(PRECONDITIONS))}")
    if not isinstance(v["post"], str) or v["post"] not in POSTCONDITIONS:
        raise _refuse(f"ontology verb {name}: post {v['post']!r} is not one of {', '.join(sorted(POSTCONDITIONS))}")
    if not isinstance(v["reversible"], bool):
        raise _refuse(f"ontology verb {name}: reversible is not true or false")
    if not isinstance(v["projection"], dict):
        raise _refuse(f"ontology verb {name}: projection is not an object")
    for app, proj in sorted(v["projection"].items()):
        _validate_projection(name, app, proj)
    return name


def validate(ont: dict) -> None:
    """Refusal naming the verb and the field (or the application and the key) on the first fault."""
    if not isinstance(ont, dict):
        raise _refuse("the ontology is not a JSON object")
    for key, kind, noun in (("family", str, "a name"), ("version", int, "a number"),
                            ("applications", dict, "an object"), ("verbs", list, "a list")):
        if not isinstance(ont.get(key), kind) or isinstance(ont.get(key), bool):
            raise _refuse(f"ontology {key} is missing or not {noun}")
    _validate_applications(ont["applications"])
    seen = set()
    for i, v in enumerate(ont["verbs"]):
        name = _validate_verb(i, v)
        if name in seen:
            raise _refuse(f"ontology verb {name}: verb is repeated")
        seen.add(name)


def ontology_sha256(ont: dict) -> str:
    return canon.sha256(ont)


def _walk(pattern: str, members: dict, kinds: dict) -> tuple[str, str | None]:
    """(status, why) of `pattern` over the catalogue: the head is a type, each next segment a member of
    the type the previous step returns (`*` stripped); a step returning VARIANT or IDispatch names no
    type, so the next member is accepted on any type that has it, and the walk is `partial`."""
    head, *chain = pattern.split(".")
    if head not in members:
        return "unbound", f"{head} matched no type of the catalogue"
    current, loose = {head}, None
    for i, name in enumerate(chain):
        prefix = ".".join([head, *chain[:i + 1]])
        found = [m for t in sorted(current) for m in members[t].get(name, ())]
        if not found:
            return "unbound", f"{prefix} matched nothing"
        if i == len(chain) - 1:
            break
        following = chain[i + 1]
        returns = sorted({m["returns"].rstrip("*") for m in found if m["kind"] != "property_put"})
        current = set()
        for r in returns:
            if r in _LOOSE:
                current |= {t for t, ms in members.items() if following in ms}
                loose = loose or f"{prefix} returns {r}: {following} accepted on any type that has it"
            elif kinds.get(r) in _OBJECT_KINDS:
                current.add(r)
        if not current:
            return "unbound", f"{prefix} returns {' or '.join(returns) or 'nothing'}: no type there has {following}"
    return ("partial", loose) if loose else ("bound", None)


def _bind_verb(v: dict, app: str, members: dict, kinds: dict, enums: dict) -> dict:
    unbound = {"verb": v["verb"], "status": "unbound", "member": None, "constants": None}
    proj = v["projection"].get(app)
    if proj is None:
        return {**unbound, "why": f"no projection for {app}"}
    status, why = _walk(proj["member"], members, kinds)
    if status == "unbound":
        return {**unbound, "why": why}
    enum = proj.get("constants")
    if enum is not None and enum not in enums:
        return {**unbound, "why": f"constants {enum} matched no enum of the catalogue"}
    if proj.get("status") == "partial" and status == "bound":
        status, why = "partial", f"declared partial: {app} has no exact counterpart"
    return {"verb": v["verb"], "status": status, "member": proj["member"],
            "constants": None if enum is None else {enum: dict(enums[enum])}, "why": why}


def bind(ont: dict, catalogue: dict) -> dict:
    validate(ont)
    app = catalogue.get("app") if isinstance(catalogue, dict) else None
    if app not in inventory.APPS:
        raise Refusal(f"the catalogue names no application of {', '.join(sorted(inventory.APPS))}",
                      "record it with vkforge inventory")
    for key in ("typelib", "typelib_sha256", "types", "enums"):   # L-P22: no fingerprint, no binding
        if key not in catalogue:
            raise Refusal(f"catalogue of {app} has no {key}", "record it again with vkforge inventory")
    members, kinds = {}, {}
    for t in catalogue["types"]:
        kinds[t["name"]] = t["kind"]
        by_name = members.setdefault(t["name"], {})
        for m in t["members"]:
            by_name.setdefault(m["name"], []).append(m)
    verbs = [_bind_verb(v, app, members, kinds, catalogue["enums"]) for v in ont["verbs"]]
    return {"app": app,
            "ontology_sha256": ontology_sha256(ont),
            "catalogue_sha256": inventory.catalogue_sha256(catalogue),
            "typelib": copy.deepcopy(catalogue["typelib"]),
            "typelib_sha256": catalogue["typelib_sha256"],
            "application": copy.deepcopy(ont["applications"][app]),    # L-P31: what the adapter must not name
            "verbs": sorted(verbs, key=lambda x: x["verb"])}


def write_binding(binding: dict, out: Path) -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"binding-{binding['app']}.json"
    canon.write(binding, path)
    return path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--catalogue", required=True, type=Path, help="a catalogue.json written by vkforge inventory")
    parser.add_argument("--out", required=True, type=Path, help="directory that receives binding-<app>.json")
    parser.add_argument("--ontology", type=Path, help="an ontology file instead of ontology/office-document.json")


def run(ns: argparse.Namespace) -> int:
    ont = load(ns.ontology)
    catalogue = _read_json(ns.catalogue, "catalogue", "vkforge inventory --app APP --out DIR writes one")
    binding = bind(ont, catalogue)
    path = write_binding(binding, ns.out)
    count = {s: sum(v["status"] == s for v in binding["verbs"]) for s in ("bound", "partial", "unbound")}
    print(f"bind: {count['bound']} bound, {count['partial']} partial, {count['unbound']} unbound "
          f"on {binding['app']} -> {path}")
    for v in binding["verbs"]:
        if v["status"] == "unbound":
            print(f"unbound {v['verb']}: {v['why']}")
    return 0
