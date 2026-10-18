"""vkforge plan — invariants × binding → the typed plan a human approves (stage `plan`).

The planner is the linker of the pipeline: it takes what the miner proved the firm does
(invariants.json) and what the peripheral implements (binding-<app>.json) and emits one action, an
ordered list of ontology verbs — open, the steps that produce each invariant, save, the export, then
one verify step per invariant — with the post-conditions a qualification replay must reach. Free
text never crosses into the plan: a footer or a header is named `$secret.<id>` and the generator
resolves it from the vault. An ambiguity is applied only when an arch chose one of the miner's own
candidates; otherwise it is purged (spec §6: never applied in part). What cannot be driven goes to
plan-excluded.json, with why.

`validate` mirrors `DriverPlan::validate` (crates/vk-contracts/src/plan.rs): same refusals, same
sentences, same order, so a plan this stage writes is one the kernel parses.
"""
import argparse
import copy
import re
from pathlib import Path

from . import canon, inventory, ontology
from .errors import Refusal


def step(verb: str, **args) -> dict:
    return {"verb": verb, "args": args}


# (invariant id, steps that produce it, the verify step that checks it), in step order
RULES = [
    ("title_style", lambda v: [step("apply_paragraph_style", selector="first", style=v["builtin"])],
                    lambda v: step("verify_paragraph_style", selector="first", style=v["builtin"])),
    ("body_font",   lambda v: [step("set_font_run", selector="body", font=v)],
                    lambda v: step("verify_font_run", selector="body", font=v)),
    ("title_font",  lambda v: [step("set_font_run", selector="title", font=v)],
                    lambda v: step("verify_font_run", selector="title", font=v)),
    ("header_text", lambda v: [step("set_header", text="$secret.header_text")],
                    lambda v: step("verify_header", text="$secret.header_text")),
    ("footer_text", lambda v: [step("set_footer", text="$secret.footer_text")],
                    lambda v: step("verify_footer", text="$secret.footer_text")),
]
EXPORT = ("export_format", lambda v: [step("export", format=v["format"], path="$arg.pdf")],
                           lambda v: step("verify_pdf", path="$arg.pdf"))   # post id: pdf_exists
EMITTED_VERBS = frozenset({"open", "apply_paragraph_style", "set_font_run", "set_header", "set_footer", "save", "export",
                           "verify_paragraph_style", "verify_font_run", "verify_header", "verify_footer", "verify_pdf"})
ACTION = {"word": "appliquer_style_maison", "powerpoint": "appliquer_style_presentation"}
QUALIFICATION = {"min_passes": 10, "min_rate": 1.0}

_DRIVEN = ("bound", "partial")
_ONTOLOGY_REMEDY = "fix its projection in drivers/vkforge/ontology/office-document.json"


def _unbound_why(entry: dict | None) -> str | None:
    """None when the binding drives the verb; else what the binding says about it."""
    if entry is None:
        return "absent from the binding"
    if entry.get("status") in _DRIVEN:
        return None
    return entry.get("why") or "unbound"


def _resolved(amb: dict, resolve):
    """The candidate an arch chose for `amb`, as the miner wrote it; None when there is no answer or
    the answer is not one of the candidates (compared in canonical form: 11 is not 11.0)."""
    answer = resolve(amb) if resolve is not None else None
    if not isinstance(answer, dict) or "value" not in answer:
        return None, None
    wanted = canon.dumps(answer["value"])
    chosen = next((c["value"] for c in amb["candidates"] if canon.dumps(c["value"]) == wanted), None)
    return (None, None) if chosen is None else (chosen, answer.get("resolved_by"))


def build(invariants: dict, binding: dict, ontology: dict, resolve=None) -> tuple[dict, list]:
    """(plan, excluded): the plan of one action and the invariants it cannot drive, with why."""
    app = binding.get("app") if isinstance(binding, dict) else None
    if app not in inventory.APPS or not isinstance(binding.get("verbs"), list):
        raise Refusal(f"the binding names no application of {', '.join(sorted(inventory.APPS))}",
                      "bind it again with vkforge bind")
    if not (isinstance(invariants, dict) and isinstance(invariants.get("invariants"), list)
            and isinstance(invariants.get("ambiguities"), list)):
        raise Refusal("invariants.json holds no invariants and ambiguities lists", "run vkforge mine again")
    kind = inventory.APPS[app]["kind"]
    verbs = {v["verb"]: v for v in binding["verbs"]}
    for v in ("open", "save"):
        if _unbound_why(verbs.get(v)) is not None:
            raise Refusal(f"verb {v} is unbound on {app}", _ONTOLOGY_REMEDY)

    # The values: every invariant, then each ambiguity an arch resolved within its candidates.
    values = {i["id"]: i["value"] for i in invariants["invariants"]}
    ambiguities = []
    for amb in sorted(invariants["ambiguities"], key=lambda a: a["invariant"]):
        chosen, by = _resolved(amb, resolve)
        if chosen is None:
            ambiguities.append({"invariant": amb["invariant"], "resolved_by": "purged"})
            continue
        values[amb["invariant"]] = chosen
        ambiguities.append({"invariant": amb["invariant"], "resolved_by": by, "value": copy.deepcopy(chosen)})

    # The rules: what each value becomes, or why it is excluded.
    kept, excluded, ruled = [], [], set()
    for ident, produce, verify in [*RULES, EXPORT]:
        ruled.add(ident)
        if ident not in values:
            continue
        value = values[ident]
        if ident == "title_style" and isinstance(value, dict) and "custom" in value:
            excluded.append({"invariant": ident, "why": "a custom style has no built-in anchor (v1)"})
            continue
        produced, check = produce(value), verify(value)
        unbound = next(((s["verb"], why) for s in [*produced, check]
                        if (why := _unbound_why(verbs.get(s["verb"]))) is not None), None)
        if unbound is not None:
            excluded.append({"invariant": ident, "why": f"verb {unbound[0]} is unbound on {app}: {unbound[1]}"})
            continue
        kept.append((ident, produced, check))
    excluded += [{"invariant": ident, "why": f"no rule for {ident} in plan.RULES (v1)"}
                 for ident in values if ident not in ruled]
    excluded.sort(key=lambda e: e["invariant"])
    if not kept:
        ids = ", ".join(e["invariant"] for e in excluded) or "none"
        raise Refusal(f"no invariant can be driven on {app} (excluded: {ids})",
                      "read plan-excluded.json; vk driver learn --deliberate VERB proposes a binding")

    # The action: open, the produce steps, save, the export, then the verify steps in the same order.
    export = EXPORT[0]
    ordered = [step("open", path="$arg.document"),
               *(s for ident, produced, _ in kept if ident != export for s in produced),
               step("save"),
               *(s for ident, produced, _ in kept if ident == export for s in produced),
               *(check for _, _, check in kept)]
    steps = [copy.deepcopy(s) for s in ordered]      # no value shared between two steps, nor with the invariants
    reversible = {v["verb"]: v["reversible"] for v in ontology["verbs"]}
    partial = {}
    for s in steps:
        entry = verbs[s["verb"]]
        if entry["status"] == "partial":
            partial.setdefault(s["verb"], entry["member"])
    action = {"name": ACTION[app],
              "reversible": all(reversible[s["verb"]] for s in steps),
              "steps": steps,
              "pre": [f"document is .{kind}", "not open elsewhere"],
              "post": ["pdf_exists" if ident == export else ident for ident, _, _ in kept]}
    if partial:
        action["note"] = "; ".join(f"{verb} partial ({member})" for verb, member in partial.items())
    plan = {"family": ontology["family"],
            "app": app,
            "ontology_sha256": binding["ontology_sha256"],
            "catalogue_sha256": binding["catalogue_sha256"],
            "invariants_sha256": canon.sha256(invariants),
            "actions": [action],
            "ambiguities": ambiguities,
            "qualification": dict(QUALIFICATION)}
    validate(plan)
    return plan, excluded


# --- validate: DriverPlan::validate (crates/vk-contracts/src/plan.rs), rule for rule, same order ---

_DRIVER_NAME = re.compile(r"[a-z0-9-]{1,32}")         # vk_contracts::driver::is_driver_name
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_SNAKE_32 = re.compile(r"[a-z_]{1,32}")                # action name
_SNAKE_40 = re.compile(r"[a-z_]{1,40}")                # step verb
_POST = re.compile(r"[a-z0-9_]{1,40}")                 # post entry
_ARG_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")         # args key, and the key/id of a reference


def _is(pattern: re.Pattern, s) -> bool:
    return isinstance(s, str) and pattern.fullmatch(s) is not None


def _bad_reference(v):
    """The first string, at any depth, that starts with `$` and is neither `$arg.<key>` nor
    `$secret.<id>`; object values in key order (serde_json's map is a BTreeMap)."""
    if isinstance(v, str):
        if not v.startswith("$"):
            return None
        for prefix in ("$arg.", "$secret."):
            if v.startswith(prefix):
                return None if _is(_ARG_KEY, v[len(prefix):]) else v
        return v
    if isinstance(v, list):
        return next((bad for item in v if (bad := _bad_reference(item)) is not None), None)
    if isinstance(v, dict):
        return next((bad for k in sorted(v) if (bad := _bad_reference(v[k])) is not None), None)
    return None


def _rust_f64(x):
    """As Rust's `{}` writes an f64 that holds an integer: 2, not 2.0."""
    return int(x) if isinstance(x, float) and x.is_integer() else x


def validate(plan: dict) -> None:
    """Refusal on the first rule of DriverPlan::validate the plan breaks, in the Rust order."""
    for field in ("family", "app"):
        if not _is(_DRIVER_NAME, plan[field]):
            raise Refusal(f"{field} `{plan[field]}` must be 1 to 32 characters of a-z, 0-9 and `-`",
                          "use the plain token the ontology names")
    for field in ("ontology_sha256", "catalogue_sha256", "invariants_sha256"):
        if not _is(_SHA256, plan[field]):
            raise Refusal(f"{field} `{plan[field]}` must be `sha256:` and 64 lower-case hex digits",
                          "copy the hash of the file the plan was built from")
    if not plan["actions"]:
        raise Refusal("actions is empty: a plan holds at least one action",
                      "learn again from a corpus that yields an invariant")
    seen = []
    for action in plan["actions"]:
        name, steps, post = action["name"], action["steps"], action["post"]
        if not _is(_SNAKE_32, name):
            raise Refusal(f"action name `{name}` must be 1 to 32 characters of a-z and `_`",
                          "rename the action with lower-case words joined by `_`")
        if name in seen:
            raise Refusal(f"duplicate action name `{name}`", "give each action its own name")
        seen.append(name)
        if not steps or steps[0]["verb"] != "open":
            raise Refusal(f"action {name}: the first step is open, the step that names the document",
                          "start the action with an open step")
        for s in steps:
            verb, args = s["verb"], s.get("args", {})
            if not _is(_SNAKE_40, verb):
                raise Refusal(f"action {name}: verb `{verb}` must be 1 to 40 characters of a-z and `_`",
                              "use a verb of the ontology")
            for key in sorted(args):
                if not _is(_ARG_KEY, key):
                    raise Refusal(f"action {name}: step {verb}: args key `{key}` must be 1 to 32 characters "
                                  "of a-z, 0-9 and `_`, starting with a letter", "rename the argument")
            bad = _bad_reference(args)
            if bad is not None:
                raise Refusal(f"action {name}: step {verb}: reference `{bad}` is neither `$arg.<key>` nor "
                              "`$secret.<id>`",
                              "name an argument or a secret, or write the value without a leading `$`")
        bad = next((p for p in post if not _is(_POST, p)), None)
        if bad is not None:
            raise Refusal(f"action {name}: post entry `{bad}` must be 1 to 40 characters of a-z, 0-9 and `_`",
                          "name an invariant id or `pdf_exists`")
        verifies = sum(s["verb"].startswith("verify_") for s in steps)
        if len(post) != verifies:
            raise Refusal(f"action {name}: post has {len(post)} entries for {verifies} verify_* steps",
                          "one post entry per verify_* step, in their order")
    for a in plan.get("ambiguities", []):
        by = a["resolved_by"]
        if by == "purged":
            resolved = False
        elif by == "human" or (isinstance(by, str) and by.startswith("arch:") and by != "arch:"):
            resolved = True
        else:
            raise Refusal(f"ambiguity {a['invariant']}: resolved_by `{by}` must be `purged`, `human` or `arch:<id>`",
                          "record who settled it")
        has_value = a.get("value") is not None            # serde reads `"value": null` as absent
        if resolved and not has_value:
            raise Refusal(f"ambiguity {a['invariant']}: value is missing for a resolved ambiguity",
                          "record the chosen candidate")
        if not resolved and has_value:
            raise Refusal(f"ambiguity {a['invariant']}: value must be absent for a purged ambiguity",
                          "drop the value, or record who resolved it")
    q = plan["qualification"]
    if q["min_passes"] < 1:
        raise Refusal("qualification.min_passes must be at least 1", "a driver is qualified by at least one replay")
    if not 0.0 <= q["min_rate"] <= 1.0:
        raise Refusal(f"qualification.min_rate {_rust_f64(q['min_rate'])} must be between 0 and 1",
                      "write the rate as a fraction")


# --- the artefacts and the stage ---------------------------------------------------------------

def plan_sha256(path: Path) -> str:
    """The SHA-256 of plan.json's bytes as they are on disk (L-P13), as the kernel hashes them."""
    return canon.sha256_file(path)


def write_plan(plan: dict, excluded: list, out: Path) -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    canon.write(plan, out / "plan.json")
    canon.write(excluded, out / "plan-excluded.json")
    return out / "plan.json"


def load_resolver(path: Path):
    """resolver.json (written by the kernel) → resolve(ambiguity) -> {"value", "resolved_by"} | None."""
    path = Path(path)
    remedy = "run vk driver learn again (the kernel writes resolver.json)"
    answers = ontology._read_json(path, "resolver", remedy)
    if not isinstance(answers, dict):
        raise Refusal(f"resolver {path} is not an object", remedy)
    for ident, answer in sorted(answers.items()):
        if not (isinstance(answer, dict) and set(answer) == {"value", "resolved_by"}
                and isinstance(answer["resolved_by"], str)):
            raise Refusal(f"resolver {path}: {ident} is not {{value, resolved_by}}", remedy)

    def resolve(amb: dict):
        answer = answers.get(amb["invariant"])
        return None if answer is None else copy.deepcopy(answer)

    return resolve


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--invariants", required=True, type=Path, help="invariants.json written by vkforge mine")
    parser.add_argument("--binding", required=True, type=Path, help="binding-<app>.json written by vkforge bind")
    parser.add_argument("--out", required=True, type=Path, help="directory that receives plan.json and plan-excluded.json")
    parser.add_argument("--ontology", type=Path, help="an ontology file instead of ontology/office-document.json")
    parser.add_argument("--resolver", type=Path, help="resolver.json: the ambiguities an arch resolved")


def run(ns: argparse.Namespace) -> int:
    ont = ontology.load(ns.ontology)
    invariants = ontology._read_json(ns.invariants, "invariants", "run vkforge mine again")
    if ns.invariants.read_bytes() != canon.dumps(invariants):     # so canon.sha256(invariants) is the file's hash
        raise Refusal("invariants.json is not in canonical form", "run vkforge mine again")
    binding = ontology._read_json(ns.binding, "binding", "run vkforge bind again")
    resolve = None if ns.resolver is None else load_resolver(ns.resolver)
    p, excluded = build(invariants, binding, ont, resolve)
    path = write_plan(p, excluded, ns.out)
    a = p["actions"][0]
    purged = sum(x["resolved_by"] == "purged" for x in p["ambiguities"])
    print(f"plan: {a['name']} {len(a['steps'])} steps, post {','.join(a['post'])}; "
          f"ambiguities {len(p['ambiguities'])} ({purged} purged); excluded {len(excluded)} -> {path}")
    return 0
