"""vkforge inventory — the catalogue of an application's type library (stage `inventory`).

The type library is the datasheet of the peripheral: every type, member, signature and constant the
application exposes through COM, read from the file the registry names, never from a running
application. Only the Windows-only imports (`winreg`, `comtypes`) live inside functions, so this
module imports everywhere and CI reads the recorded fixtures on Linux.
"""
import argparse
import json
import sys
from pathlib import Path

from . import canon
from .errors import Refusal

# No ProgID here: the adapter's ProgID is the ontology's `applications` block's (L-P31).
APPS = {
    "word":       {"exe": "WINWORD.EXE",  "kind": "docx", "typelib": "{00020905-0000-0000-C000-000000000046}"},
    "powerpoint": {"exe": "POWERPNT.EXE", "kind": "pptx", "typelib": "{91493440-5A91-11CF-8700-00AA0060263B}"},
}

# oleaut32's VARENUM: the names the patterns read; any other type is rendered `vt<number>`.
_VT_NAMES = {2: "short", 3: "long", 4: "float", 5: "double", 8: "BSTR", 9: "IDispatch", 11: "bool", 12: "VARIANT",
             24: "void"}
_VT_PTR, _VT_SAFEARRAY, _VT_USERDEFINED = 26, 27, 29

_TKIND_ENUM, _TKIND_INTERFACE, _TKIND_DISPATCH, _TKIND_COCLASS = 0, 3, 4, 5
_INVOKE_KINDS = {1: "method", 2: "property_get", 4: "property_put"}   # INVOKE_FUNC, _PROPERTYGET, _PROPERTYPUT
_IMPLTYPE_FDEFAULT, _IMPLTYPE_FSOURCE = 1, 2                           # IMPLTYPEFLAG_*: default; an event interface
_MEMID_INHERITED = 0x60000000                                          # IUnknown / IDispatch plumbing


def _app(app: str) -> dict:
    if app not in APPS:
        raise Refusal(f"unknown app {app}", "--app word or --app powerpoint")
    return APPS[app]


def _not_registered(guid: str) -> Refusal:
    app = next((a for a, v in APPS.items() if v["typelib"].upper() == guid.upper()), "the application")
    return Refusal(f"type library of {app} {guid} is not registered", "install Microsoft 365 desktop")


def _version_key(name: str):
    major, _, minor = name.partition(".")
    return int(major, 16), int(minor or "0", 16)          # registry versions are hexadecimal `major.minor`


def typelib_path(guid: str) -> str:
    """HKCR\\TypeLib\\{guid}\\<highest version>\\0\\win64, then \\win32; a Refusal if the library is absent."""
    if sys.platform != "win32":
        raise _not_registered(guid)
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"TypeLib\{guid}") as key:
            versions = []
            try:
                while True:
                    versions.append(winreg.EnumKey(key, len(versions)))
            except OSError:                                 # the end of the enumeration
                pass
        parsable = []
        for version in versions:
            try:
                parsable.append((_version_key(version), version))
            except ValueError:                              # a subkey that is not `major.minor`: not a version
                continue
        for _, version in sorted(parsable, reverse=True):
            for bitness in ("win64", "win32"):
                try:
                    return winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf"TypeLib\{guid}\{version}\0\{bitness}")
                except OSError:
                    continue
    except OSError:
        pass
    raise _not_registered(guid)


def office_present(app: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        typelib_path(_app(app)["typelib"])
    except Refusal:
        return False
    return True


def _type_name(info, tdesc) -> str:
    vt = tdesc.vt
    if vt == _VT_PTR:
        return _type_name(info, tdesc._.lptdesc[0]) + "*"
    if vt == _VT_SAFEARRAY:
        return "SAFEARRAY(" + _type_name(info, tdesc._.lptdesc[0]) + ")"
    if vt == _VT_USERDEFINED:
        return info.GetRefTypeInfo(tdesc._.hreftype).GetDocumentation(-1)[0]
    return _VT_NAMES.get(vt, f"vt{vt}")


def _members(info, count: int) -> list:
    members = []
    for j in range(count):
        fd = info.GetFuncDesc(j)
        kind = _INVOKE_KINDS.get(fd.invkind)
        if kind is None or fd.memid >= _MEMID_INHERITED:
            continue
        names = info.GetNames(fd.memid, fd.cParams + 1)
        params = [{"name": names[k + 1] if k + 1 < len(names) else f"arg{k}",
                   "type": _type_name(info, fd.lprgelemdescParam[k].tdesc)} for k in range(fd.cParams)]
        members.append({"name": names[0], "kind": kind, "params": params,
                        "returns": _type_name(info, fd.elemdescFunc.tdesc)})
    return sorted(members, key=lambda m: (m["name"], m["kind"]))


def _default_interface(info, attr):
    """The interface a coclass is used through: the one flagged default and not an event source, else the first
    implemented interface that is not an event source."""
    candidates = [i for i in range(attr.cImplTypes) if not info.GetImplTypeFlags(i) & _IMPLTYPE_FSOURCE]
    chosen = next((i for i in candidates if info.GetImplTypeFlags(i) & _IMPLTYPE_FDEFAULT),
                  candidates[0] if candidates else None)
    return None if chosen is None else info.GetRefTypeInfo(info.GetRefTypeOfImplType(chosen))


def catalogue_from_typelib(app: str, path: str) -> dict:
    """The catalogue (spec §4.1) of the type library file at `path`; its hash is that file's bytes (L-P22)."""
    guid = _app(app)["typelib"]
    try:
        from comtypes import COMError, typeinfo
    except ImportError as e:
        raise Refusal(f"comtypes is not available ({e})", "install comtypes (drivers/vkforge/requirements.txt)")
    try:
        tlib = typeinfo.LoadTypeLibEx(str(path))
    except (COMError, OSError) as e:
        raise Refusal(f"type library {path} did not load ({e})", "repair Microsoft 365 desktop")
    lib = tlib.GetLibAttr()
    if str(lib.guid).upper() != guid.upper():
        raise Refusal(f"{path} is the type library {lib.guid}, not that of {app} {guid}",
                      "the file the registry names")
    types, enums = [], {}
    for i in range(tlib.GetTypeInfoCount()):
        info = tlib.GetTypeInfo(i)
        attr = info.GetTypeAttr()
        name = info.GetDocumentation(-1)[0]
        if attr.typekind == _TKIND_ENUM:
            constants = {}
            for j in range(attr.cVars):
                vd = info.GetVarDesc(j)
                constants[info.GetDocumentation(vd.memid)[0]] = int(vd._.lpvarValue[0].value)
            enums[name] = dict(sorted(constants.items()))
            types.append({"name": name, "kind": "enum", "members": []})
        elif attr.typekind in (_TKIND_DISPATCH, _TKIND_INTERFACE):
            types.append({"name": name, "kind": "dispatch", "members": _members(info, attr.cFuncs)})
        elif attr.typekind == _TKIND_COCLASS:
            # A coclass carries the members of its default interface: `Document` is the name the ontology
            # walks, `_Document` the interface behind it.
            default = _default_interface(info, attr)
            members = _members(default, default.GetTypeAttr().cFuncs) if default is not None else []
            types.append({"name": name, "kind": "coclass", "members": members})
    return {"app": app,
            "typelib": {"guid": guid, "version": f"{lib.wMajorVerNum:x}.{lib.wMinorVerNum:x}"},
            "typelib_sha256": canon.sha256_file(path),
            "types": sorted(types, key=lambda t: (t["name"], t["kind"])),
            "enums": dict(sorted(enums.items()))}


def catalogue_from_fixture(app: str, fixture: Path) -> dict:
    _app(app)                                               # before any file is read
    remedy = f"record it with inventory --app {app} --out DIR on a node with Office"
    fixture = Path(fixture)
    if not fixture.is_file():
        raise Refusal(f"fixture {fixture} not found", remedy)
    try:
        cat = json.loads(fixture.read_text(encoding="utf-8"))
    except (OSError, ValueError):                           # unreadable, not UTF-8, not JSON
        raise Refusal(f"fixture {fixture} is not a catalogue", remedy)
    if not isinstance(cat, dict):
        raise Refusal(f"fixture {fixture} is not a catalogue", remedy)
    if cat.get("app") != app:
        raise Refusal(f"{fixture} is the catalogue of {cat.get('app')}, not of {app}",
                      f"--app {cat.get('app')} or the fixture of {app}")
    return cat


def restrict(cat: dict, types: list[str]) -> dict:
    """Only the named types, in the catalogue's order; every enum and every other key kept."""
    wanted = set(types)
    return {**cat, "types": [t for t in cat["types"] if t["name"] in wanted]}


def catalogue_sha256(cat: dict) -> str:
    return canon.sha256(cat)


def write_catalogue(cat: dict, out: Path) -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "catalogue.json"
    canon.write(cat, path)
    return path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--app", required=True, choices=sorted(APPS))
    parser.add_argument("--out", required=True, type=Path, help="directory that receives catalogue.json")
    parser.add_argument("--fixture", type=Path, help="a recorded catalogue, instead of the installed type library")
    parser.add_argument("--only-types", help="comma-separated type names to keep (every enum is kept)")


def run(ns: argparse.Namespace) -> int:
    if ns.fixture is not None:
        cat = catalogue_from_fixture(ns.app, ns.fixture)
    else:
        cat = catalogue_from_typelib(ns.app, typelib_path(_app(ns.app)["typelib"]))
    if ns.only_types:
        names = [n.strip() for n in ns.only_types.split(",") if n.strip()]
        known = {t["name"] for t in cat["types"]}
        unknown = sorted(set(names) - known)
        if unknown:
            raise Refusal(f"unknown types in --only-types: {', '.join(unknown)}",
                          f"names from the catalogue of {ns.app} (types: {len(known)})")
        cat = restrict(cat, names)
    path = write_catalogue(cat, ns.out)
    print(f"inventory: {len(cat['types'])} types, {len(cat['enums'])} enums -> {path}")
    return 0
