# The potential value of information in the learning pipeline — design

Date: 2026-10-18. Status: design validated section by section with the founder (decisions D-26 of
the open-decisions register); Task 12 of the learning chantier held on 2026-10-19 and the chantier
merged (`22962c7`, tag `driver-learning`); the implementation is scheduled by the founder (register,
D-34), and its plan brings the optional contract fields of §3.2 to the founder's review. Extends `2026-10-17-driver-learning-office-design.md` (vkforge: inventory,
bind, mine, plan, generate, qualify, debrief), which stays binding; this document adds one quantity
and the places where it acts. Thesis it serves (research note of 2026-10-18, private): the relevance
of information and the reduction of its entropy as it crosses compliant layers.

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| V1 | The potential value of information is attached to the **corpus invariant**, not to an orchestration step and not to a ledger field. | The invariant is where the information exists as an object, before any processing; the three places that discard information (purge, plan exclusion, failed repair) all sit in the pipeline; a value computed locally is deterministic, costs no token and is versioned by `ontology_sha256`. The ledger stays mechanism; value is userland policy. |
| V2 | The value is **declared in the ontology, per verb** (`value`, a number in [0, 1]); a per-firm override file and learning from usage are later horizons. | One source of truth for the family, reviewed with the ontology; the corpus weights it. |
| V3 | The value **acts in the plan and in qualification**, not in the miner's purge (v1). | The purge decides the operating point of the miner; changing it changes what is learned. Plan and gate only make the existing choices legible in value. |
| V4 | The mount gate is **soft**: a critical invariant that is not deterministic is written to the `learned` record; `min_value_rate` decides. | More drivers mount; the guarantee is read, not enforced, at this stage (founder's choice). |

## 2. The quantity

- **Ontology.** Each verb carries `value ∈ [0, 1]`. Base values for `office-document` v2: `apply_paragraph_style` 1.0, `export` 0.9, `set_font_run` 0.8, `set_footer` and `set_header` 0.7, `set_margins`/`set_orientation`/`set_page_size` 0.3, `verify_*` the value of the verb they verify, `open`/`save`/`close` none (they realise the others). `ontology.validate` refuses a verb whose `value` is outside [0, 1] or missing on an emitted verb.
- **Invariant.** The invariant inherits the value of the verb the planner's rule maps it to (`title_style` → `apply_paragraph_style`, `body_font`/`title_font` → `set_font_run`, `footer_text` → `set_footer`, `header_text` → `set_header`, `export_format` → `export`), weighted by the corpus: `potential = value(verb) × support`. An invariant with no rule has `potential = null` and goes to `plan-excluded.json` as today, with its cause.
- **Ambiguity.** For a miner ambiguity with candidates `(value_i, support_i)`, the planner computes `H = −Σ p_i log₂ p_i` over the normalised supports (a 50/50 tie is 1 bit; 90/10 is 0.47 bit) and the **value of information** of resolving it, `voi = value(verb) × H`.
- **Pass.** In qualification, each pass holds or fails each post id; the **realised value** of a pass is the sum of the `potential` of the post ids held; per invariant, the success frequency `q` over `n` copies gives the binary entropy `h(q)` (0 bit at `q ∈ {0, 1}`, 1 bit at `q = 0.5`): the driver's reliability on that invariant.

## 3. Where it acts

### 3.1 Plan (`vkforge plan`)

1. Actions carry `potential` (the invariant's) and the plan carries `potential_total` (their sum); actions are ordered by `potential` descending, the verify steps in the same order.
2. An ambiguity is submitted to the arch only when `voi ≥ VOI_MIN` (default 0.25 bit, a plan ruling); below, it is purged without a call. Each ambiguity in `plan.json` carries `H`, `voi` and `resolved_by` as today (`arch:<id>` | `human` | `purged`), so a purge is explainable by a number.
3. `plan-excluded.json` lists every lost invariant with its `potential` and the cause (`no rule`, `verb unbound`, `custom style`, `voi below threshold`); the plan carries `entropy_residual` = Σ `voi` of the ambiguities purged (the information the driver does not have).
4. `DriverPlan` (Rust, `crates/vk-contracts/src/plan.rs`) gains `potential` on `PlanAction`, `potential_total` and `entropy_residual` on the plan, `h` and `voi` on `Ambiguity` — all optional, so every existing plan still validates; `plan_sha256` stays the hash of the bytes.

### 3.2 Qualification (`vkforge qualify`) and the gate

1. `qualification.json` gains, per run: `realised` (Σ potential held), `potential` (Σ potential of the plan's post ids), `value_rate = realised / potential`; per invariant: `q`, `h`, `potential`; and the **loss by layer** block: `purged_by_mine` (Σ potential of invariants under the support threshold — the miner writes it in `invariants.json` and `qualify` copies it), `excluded_by_plan` (Σ potential of `plan-excluded.json`), `failed_by_qualify` (Σ potential of the post ids failed, averaged over passes). The three losses added are the learn's entropy budget.
2. The plan's `qualification` block gains `min_value_rate` (default 0.9) and `critical_value` (default 0.9).
3. The gate at mount (`check_qualified`, kernel) keeps `passes ≥ min_passes` and `rate ≥ min_rate`, adds `value_rate ≥ min_value_rate`, and **writes** on the `learned` row the list of critical invariants (`potential ≥ critical_value`) whose `h > 0`, without refusing on them (V4). `vk driver show` prints that list; the debrief reads it.
4. `DriverQualified` (ledger payload) gains `value_rate` (optional); the `learned` row gains `non_deterministic_critical: Vec<String>`.

### 3.3 Debrief

`vkforge debrief` reads the loss-by-layer block and the critical list and proposes in value: "the plan lost 0.4 of potential to an unbound verb" ranks above "a selector changed".

## 4. What this gives

Fewer model calls (VOI-gated), a plan legible in value and in missing bits, a gate that protects what matters while letting more drivers mount, a debrief that says where information was lost, and one quantity, realised over potential, that the third MaskAI recette will reuse per pipeline layer.

## 5. Limits and non-goals

- No value on the miner's purge in v1 (V3); the support threshold stays the operating point.
- No orchestration or ledger measurement of value beyond the two optional payload fields; a recette's layers are measured by its own oracles.
- Values are the ontology author's judgement; a per-firm override and usage-based learning are explicitly deferred.
- Entropy here is Shannon entropy over the miner's candidate supports and the qualification's pass frequencies — no semantic entropy of model answers (that belongs to the recette, not to the pipeline).

## 6. Plan rulings expected at implementation

`VOI_MIN = 0.25`, `min_value_rate = 0.9`, `critical_value = 0.9` are defaults to confirm on the first real corpus; the optional contract fields follow the frozen-contract rule (a founder decision at the implementation plan's review); `ontology_sha256` changes with the new `value` fields, so every binding fixture is regenerated.

Notes from the learning chantier's final review (2026-10-19), for that plan:

- `LearnedRow.qualification` gains `min_value_rate` with `#[serde(default)]`, so a `learned` row written before the field still reads; `DriverQualified.value_rate` is optional for the same reason.
- The ontology's `VERB_FIELDS` must accept `value`; every binding fixture is regenerated with the new `ontology_sha256`.
- `min_passes = min(10, corpus files)` (final review I-3) and `min_rate` 1.0 for Word and PowerPoint (founder decision D-30) stay; `value_rate ≥ min_value_rate` is checked beside them, not instead of them.
- The gate runs before every spawn of a learned driver once the follow-ups chantier lands (founder decision D-31, plan `2026-10-19-learning-follow-ups.md`, ruling LF-9), so the value threshold will too, not only at mount.
