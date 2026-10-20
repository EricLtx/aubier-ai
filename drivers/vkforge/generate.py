"""vkforge generate — plan.json and the binding become a self-contained protocol-1 driver (stage `generate`).

The generator is the pipeline's last link: it composes driver.py from fixed text and one line per plan
step (no template engine), writes the binding the COM adapter reads (L-P31: the application; L-P22: the
type library the probe checks), the clear secrets the steps reference, and vendors vkdriver and the
adapter (L-P6, L-P19), so the driver runs from its own directory with no repository around it. Every
file is written with `\\n` line endings and no timestamp: two generations from the same artefacts are
byte-identical, and manifest.json, written last, carries hash_dir of the directory (files_sha256).
The fake application is never vendored; no pack.json is written (L-P9).
"""
import argparse
import json
import keyword
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from vkdriver import protocol as vkdriver_protocol
from vkdriver.hash_dir import hash_dir

from . import canon, inventory, mine, ontology
from . import plan as planner
from .errors import Refusal
from .runtime import com

VKFORGE_VENV = Path(os.environ["LOCALAPPDATA"]) / "vk" / "venvs" / "vkforge" if os.environ.get("LOCALAPPDATA") else None
RUNTIME = Path(__file__).resolve().parent / "runtime"
COMMAND = [".venv/Scripts/python.exe", "-I", "driver.py"]           # L-P12: the development command
MAX_ACTION_MS = 120000
_NAME = re.compile(r"[a-z0-9-]{1,32}")
_ARG, _SECRET = "$arg.", "$secret."
# Names driver.py defines at module level, and the parameters of Application.call: an action or an
# argument of the same name would break the code the generator writes.
_DRIVER_NAMES = frozenset({"atexit", "hashlib", "json", "os", "sys", "Path", "DRIVER_DIR", "VENDOR_DIR",
                           "VKDRIVER_DIR", "DriverRefusal", "serve", "Application", "NAME", "VERSION", "BINDING",
                           "SECRETS", "ACTIONS", "app", "_app", "_quit", "_sha", "_first_dir"})
_CALL_PARAMETERS = frozenset({"self", "verb", "doc"})

_HEAD = '''"""@NAME@: driver learned by vkforge, no timestamp.

ontology_sha256   @ONTOLOGY@
catalogue_sha256  @CATALOGUE@
invariants_sha256 @INVARIANTS@
plan_sha256       @PLAN@
"""
import atexit
import hashlib
import json
import os
import sys
from pathlib import Path

DRIVER_DIR = Path(__file__).resolve().parent
VENDOR_DIR = DRIVER_DIR / "vendor"


def _first_dir(candidates: tuple, needs: tuple) -> Path:
    """The first directory holding every file of `needs`; else the last (the repository), so the
    import error names the development path."""
    for d in candidates:
        if all((d / n).is_file() for n in needs):
            return d
    return candidates[-1]


# vkdriver vendor-first (L-P6): the generator copied drivers/vkdriver into vendor/, so the driver runs
# alone wherever it was written; the repository layout (<repo>/drivers/vkdriver, the driver at
# drivers/office-com/<name>/) only without vendor/. Never one directory up. The COM adapter is always
# vendored (L-P19).
VKDRIVER_DIR = _first_dir((VENDOR_DIR / "vkdriver", DRIVER_DIR.parents[1] / "vkdriver"), ("protocol.py", "hash_dir.py"))
sys.path[:0] = [str(VKDRIVER_DIR.parent), str(VENDOR_DIR)]
from vkdriver.protocol import DriverRefusal, serve  # noqa: E402
from vkforge_runtime.com import Application  # noqa: E402

NAME = @NAME_REPR@
VERSION = @VERSION_REPR@
BINDING = json.loads((DRIVER_DIR / "binding.json").read_text(encoding="utf-8"))
SECRETS = (json.loads((DRIVER_DIR / "secrets.json").read_text(encoding="utf-8"))
           if (DRIVER_DIR / "secrets.json").is_file() else {})
_app = None


def app():
    """The COM adapter; its fake twin only when `vkforge qualify --fake` put fake.json in this run's
    work directory (L-P19): never an environment switch, never in a mount's work directory."""
    global _app
    if _app is None:
        fake = Path(os.environ.get("VK_DRIVER_WORK_DIR") or os.getcwd()) / "fake.json"
        if fake.is_file():
            config = json.loads(fake.read_text(encoding="utf-8"))
            sys.path.insert(0, config["vkforge_root"])
            from vkforge.runtime.fake import FakeApplication
            _app = FakeApplication(BINDING, config)
        else:
            _app = Application(BINDING)
        # Once the application exists: atexit runs last-registered first, so _quit releases its COM
        # references before the exit hook comtypes registered when Application imported it.
        atexit.register(_quit)
    return _app


def _quit():
    """The end of the session (L-P46), once, whether serve returned or the interpreter is exiting: the
    adapter ends the instance it started within founder decision D-35's bounds — quit, handed to the
    user, or ended by its Job (D-04) — and puts DisplayAlerts back on an instance it leaves to the user
    (L-P47)."""
    global _app
    if _app is not None:
        ending, _app = _app, None
        ending.quit()


def _sha(path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
'''

_ACTION_TAIL = '''    finally:
        app().close(doc)
    ev = Path(evidence_dir, "post.json")
    ev.write_text(json.dumps({"after_sha256": _sha(doc_path), "before_sha256": before, "post": post}, sort_keys=True),
                  encoding="utf-8")
    files = [{"name": "post.json", "mime": "application/json", "path": str(ev)}]
    if not all(post.values()):
        raise DriverRefusal("precondition", "postconditions not all held: " + ",".join(k for k, v in post.items() if not v),
                            evidence=files)
    return {"post": post, "text": "@ACTION@ ok"}, files
'''

_FOOT = '''

if __name__ == "__main__":
    serve(NAME, VERSION, ACTIONS)
    _quit()
'''


def _value(v) -> str:
    """A plan value as Python source: `$arg.x` → args["x"], `$secret.id` → SECRETS["id"], dict keys
    sorted, anything else its repr."""
    if isinstance(v, str) and v.startswith(_ARG):
        return f'args["{v[len(_ARG):]}"]'
    if isinstance(v, str) and v.startswith(_SECRET):
        return f'SECRETS["{v[len(_SECRET):]}"]'
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k!r}: {_value(v[k])}" for k in sorted(v)) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    return repr(v)


def _call(step: dict) -> str:
    args = step.get("args") or {}
    return f"app().call({step['verb']!r}, doc" + "".join(f", {k}={_value(args[k])}" for k in sorted(args)) + ")"


def _references(v, prefix: str) -> set:
    """The keys of every `<prefix><key>` string at any depth of `v`."""
    if isinstance(v, str):
        return {v[len(prefix):]} if v.startswith(prefix) else set()
    if isinstance(v, dict):
        return set().union(*(_references(x, prefix) for x in v.values()))
    if isinstance(v, list):
        return set().union(*(_references(x, prefix) for x in v))
    return set()


def _action(action: dict, kind: str) -> str:
    name, steps = action["name"], action["steps"]
    checks = [s for s in steps[1:] if s["verb"].startswith("verify_")]
    lines = [f"def {name}(args: dict, evidence_dir: str):",
             f"    doc_path = {_value(steps[0]['args']['path'])}",
             f'    if not Path(doc_path).is_file() or Path(doc_path).suffix.lower() != ".{kind}":',
             f'        raise DriverRefusal("precondition", "not a .{kind} file")',
             '    app().probe()   # the type library this driver was learned on (L-P22), else "office version changed"',
             "    before = _sha(doc_path)",
             "    doc = app().open(doc_path)",
             "    try:"]
    lines += [f"        {_call(s)}" for s in steps[1:] if not s["verb"].startswith("verify_")]
    lines.append("        post = {")
    lines += [f"            {post!r}: bool({_call(s)})," for post, s in zip(action["post"], checks)]
    lines.append("        }")
    return "\n".join(lines) + "\n" + _ACTION_TAIL.replace("@ACTION@", name)


def render_driver(plan: dict, binding: dict, name: str) -> str:
    """driver.py for `plan`: fixed text, one line per step. The ProgID is not in it: the adapter reads
    it from binding.json's `application` (L-P31), with the typelib_sha256 its probe checks (L-P22)."""
    kind = inventory.APPS[plan["app"]]["kind"]
    plan_sha256 = canon.sha256(plan)
    head = (_HEAD.replace("@NAME_REPR@", repr(name)).replace("@VERSION_REPR@", repr(plan_sha256))
            .replace("@NAME@", name).replace("@ONTOLOGY@", plan["ontology_sha256"])
            .replace("@CATALOGUE@", plan["catalogue_sha256"]).replace("@INVARIANTS@", plan["invariants_sha256"])
            .replace("@PLAN@", plan_sha256))
    actions = [_action(a, kind) for a in plan["actions"]]
    table = "ACTIONS = {" + ", ".join(f"{a['name']!r}: {a['name']}" for a in plan["actions"]) + "}"
    return head + "".join("\n\n" + a for a in actions) + "\n\n" + table + _FOOT


def _secrets(plan: dict, invariants: dict, vault: dict) -> dict:
    """{id: clear} for every `$secret.<id>` the steps reference: the token is the invariant's value of
    <id>, else the resolved plan ambiguity's; the clear text is the vault's entry for that token."""
    refs = set().union(*(_references(s.get("args") or {}, _SECRET) for a in plan["actions"] for s in a["steps"]))
    values = {i.get("id"): i.get("value") for i in invariants.get("invariants", []) if isinstance(i, dict)}
    resolved = {a["invariant"]: a["value"] for a in plan.get("ambiguities", []) if a.get("value") is not None}
    clear = {}
    for ident in sorted(refs):
        token = values.get(ident, resolved.get(ident))
        if not isinstance(token, str) or not isinstance(vault.get(token), str):
            raise Refusal(f"secret {ident} has no clear value in the vault", "learn again: the vault is deleted after generation")
        clear[ident] = vault[token]
    return clear


def _check(plan: dict, binding: dict, invariants: dict, out: Path, name: str) -> None:
    """Every refusal the generator makes before it writes anything."""
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise Refusal(f"out {out} is not empty", "a new directory: generation never writes over a driver")
    if not (isinstance(name, str) and _NAME.fullmatch(name)):
        raise Refusal(f"name `{name}` must be 1 to 32 characters of a-z, 0-9 and `-`", "name the driver as vk names it")
    planner.validate(plan)                                  # the plan the kernel parses: first step open, one post per check
    if plan["app"] not in inventory.APPS or any(binding.get(k) != plan[k] for k in
                                                 ("app", "ontology_sha256", "catalogue_sha256")):
        raise Refusal("the binding is not the one the plan was built from",
                      "pass the binding-<app>.json the plan was built from")
    for action in plan["actions"]:
        if keyword.iskeyword(action["name"]) or action["name"] in _DRIVER_NAMES:
            raise Refusal(f"action name `{action['name']}` is a Python keyword or a name of driver.py",
                          "rename the action")
        for step in action["steps"][1:]:
            if step["verb"] not in com.VERBS:
                raise Refusal(f"verb {step['verb']} has no runtime implementation",
                              "implement it in drivers/vkforge/runtime/com.py and fake.py")
        for step in action["steps"]:
            for key in sorted(step.get("args") or {}):
                if not key.isidentifier() or keyword.iskeyword(key) or key in _CALL_PARAMETERS:
                    raise Refusal(f"action {action['name']}: step {step['verb']}: args key `{key}` is not a Python "
                                  "identifier the step can pass",
                                  "rename the argument in drivers/vkforge/ontology/office-document.json")
    if canon.sha256(invariants) != plan["invariants_sha256"]:
        raise Refusal("plan and invariants do not belong together", "pass the invariants.json the plan was built from")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _link(venv: Path, at: Path) -> None:
    """`.venv` → the vkforge virtualenv (L-P12): a junction on Windows, a symlink elsewhere; hash_dir
    skips it, as it skips every `.venv` and every link."""
    target = str(Path(venv).resolve())
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(target, str(at))
    else:
        os.symlink(target, at, target_is_directory=True)


def _manifest(plan: dict, name: str, files_sha256: str) -> dict:
    actions = []
    for a in plan["actions"]:
        refs = sorted(set().union(*(_references(s.get("args") or {}, _ARG) for s in a["steps"])))
        actions.append({"name": a["name"], "reversible": a["reversible"],
                        "args": {"type": "object", "properties": {r: {"type": "string"} for r in refs},
                                 "required": refs, "additionalProperties": False},
                        "evidence": ["post.json"], "to_register": False})
    return {"name": name, "kind": "com",
            "program": {"runtime": "python-com", "command": list(COMMAND), "files_sha256": files_sha256,
                        "browser_channel": None, "headless": True},
            "hosts": [], "applications": [inventory.APPS[plan["app"]]["exe"]], "actions": actions,
            "min_interval_ms": 0, "max_action_ms": MAX_ACTION_MS,
            "clearance": {"max_scope": "business", "third_party_allowed": False}, "governed": False}


def generate(plan: dict, binding: dict, invariants: dict, vault: dict, out: Path, name: str,
             venv: Path | None = None) -> None:
    out = Path(out)
    _check(plan, binding, invariants, out, name)
    secrets = _secrets(plan, invariants, vault)
    if venv is not None and not (Path(venv) / "Scripts" / "python.exe").is_file():
        raise Refusal(f"no virtualenv at {venv}", r"powershell -ExecutionPolicy Bypass -File drivers\vkforge\setup.ps1")
    driver = render_driver(plan, binding, name)
    # Built in a sibling directory and moved into place last: a failed write leaves no partial `out`
    # (which the next run would refuse as not empty). hash_dir hashes relative paths: the same there.
    out.parent.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix=f".{out.name}.", suffix=".partial", dir=out.parent))
    try:
        _build(build, plan, binding, name, driver, secrets, venv)
        if out.exists():
            out.rmdir()                                     # empty: _check refused anything else
        os.replace(build, out)
    except BaseException:
        _discard(build)
        raise


def _build(build: Path, plan: dict, binding: dict, name: str, driver: str, secrets: dict, venv) -> None:
    _write(build / "driver.py", driver.encode("utf-8"))
    _write(build / "binding.json", canon.dumps(binding))
    if secrets:
        path = build / "secrets.json"
        path.touch()
        try:
            os.chmod(path, 0o600)                           # before the clear text is written; best effort on Windows
        except OSError:
            pass
        path.write_bytes(canon.dumps(secrets))
    vkdriver_dir = Path(vkdriver_protocol.__file__).resolve().parent
    for source in sorted(vkdriver_dir.glob("*.py")):
        if not source.name.startswith("test_"):
            _write(build / "vendor" / "vkdriver" / source.name, source.read_bytes())
    for source in ("__init__.py", "com.py"):                # never fake.py (L-P19)
        _write(build / "vendor" / "vkforge_runtime" / source, (RUNTIME / source).read_bytes())
    if venv is not None:
        _link(Path(venv), build / ".venv")
    canon.write(_manifest(plan, name, hash_dir(str(build))), build / "manifest.json")


def _discard(build: Path) -> None:
    """A failed generation's directory removed, its .venv link first and alone: never what it points to."""
    link = build / ".venv"
    if getattr(os.path, "isjunction", lambda p: False)(link):
        os.rmdir(link)
    elif link.is_symlink():
        link.unlink()
    shutil.rmtree(build, ignore_errors=True)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan", required=True, type=Path, help="plan.json written by vkforge plan")
    parser.add_argument("--binding", required=True, type=Path, help="binding-<app>.json the plan was built from")
    parser.add_argument("--invariants", required=True, type=Path, help="invariants.json the plan was built from")
    parser.add_argument("--vault", required=True, type=Path,
                        help=".vault.json written by vkforge mine and cut down by vkforge plan (read, never deleted)")
    parser.add_argument("--out", required=True, type=Path, help="new or empty directory that receives the driver")
    parser.add_argument("--name", required=True, help="the driver's name: 1 to 32 characters of a-z, 0-9 and -")
    parser.add_argument("--venv", type=Path, default=VKFORGE_VENV,
                        help="virtualenv the driver's .venv links to (default the vkforge virtualenv)")


def run(ns: argparse.Namespace) -> int:
    p, data = ontology.read_json(ns.plan, "plan", "run vkforge plan again")
    if data != canon.dumps(p):                          # so VERSION, canon.sha256(plan), is the file's hash
        raise Refusal("plan.json is not in canonical form", "run vkforge plan again")
    binding, _ = ontology.read_json(ns.binding, "binding", "run vkforge bind again")
    invariants, _ = ontology.read_json(ns.invariants, "invariants", "run vkforge mine again")
    if not all(isinstance(x, dict) for x in (p, binding, invariants)):
        raise Refusal("plan, binding and invariants must each be a JSON object", "run vkforge learn again")
    vault = mine.read_vault(ns.vault)
    generate(p, binding, invariants, vault, ns.out, ns.name, ns.venv)
    manifest = json.loads((Path(ns.out) / "manifest.json").read_text(encoding="utf-8"))
    print(f"generate: {ns.name} {manifest['program']['files_sha256']} -> {ns.out}")
    return 0
