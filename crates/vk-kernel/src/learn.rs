//! Driver learning, the kernel's side (driver-learning spec §4.10 and §6, plan Task 9): the two
//! ledger rows a learned driver earns, the table that keeps them, the one model call of the
//! pipeline and the qualification gate a `kind: com` driver passes at mount.
//!
//! The pipeline itself is not here: `vk-ipc` runs vkforge's six stages as governed one-shot
//! processes (ruling L-P2) and comes to the kernel, in short lock phases, for what only the
//! kernel does — record a human's act ([`RealKernel::driver_learned`]), record a replay
//! ([`RealKernel::driver_qualified`]), ask an arch to settle an ambiguity
//! ([`RealKernel::learn_ambiguity`]), and refuse a learned driver that was not qualified
//! ([`RealKernel::check_qualified`]).
//!
//! OS analogy: `driver.learned` is the factory's build record of a firmware image (which
//! toolchain, which inputs, which image hash), `driver.qualified` the bench test report signed
//! off against that image, and the gate the boot ROM refusing an image whose test report is
//! missing, failed, or no longer the one the factory filed.
use crate::tasks::StepKind;
use crate::{store_failed, RealKernel};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::{Path, PathBuf};
use vk_contracts::driver::{DriverKind, DriverLearned, DriverManifest, DriverQualified};
use vk_contracts::hash_bytes;
use vk_contracts::labels::{Label, Scope};
use vk_contracts::plan::Qualification;
use vk_contracts::principal::Principal;
use vk_contracts::syscalls::{Ctx, Kernel, KernelError};

/// The vk-store table of learned drivers, keyed by `driver_id`.
pub const LEARNED: &str = "learned";

/// The one constraint of the ambiguity call's register: the shape an answer must take. What
/// is read back is [`answer_choice`]'s, whatever the arch wrote around it.
pub const ANSWER_RULE: &str = "Answer with one JSON object {\"invariant\": <the invariant above>, \"value\": <one candidate's value, verbatim>}.";

/// The artefact kind of the ambiguity call's task: it attaches nothing, but every task names one.
const ANSWER_KIND: &str = "answer";

/// What every refusal of the gate asks for.
const QUALIFY_REMEDY: &str = "vk driver qualify --driver DIR --corpus DIR";

/// One learned driver, as `driver.learned` recorded it and `driver.qualified` completed it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LearnedRow {
    pub learned: DriverLearned,
    /// The manifest's name.
    pub name: String,
    /// The learn output directory (`--out`): `plan.json`, `invariants.json`, `driver/`.
    pub out: PathBuf,
    /// The plan's thresholds, copied at learn: what the gate holds a replay to.
    pub qualification: Qualification,
    /// The last `vk driver qualify`, once there has been one.
    #[serde(default)]
    pub qualified: Option<QualifiedRecord>,
}

/// What `driver.qualified` recorded of a replay: where its record is, the hash of that file's
/// bytes when the kernel read them, and the counts.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QualifiedRecord {
    pub record: PathBuf,
    pub qualification_sha256: String,
    pub passes: u32,
    pub failures: u32,
}

/// The refusal for a driver this node never learned: what `driver.qualify` says before anything
/// is spawned, and what [`RealKernel::driver_qualified`] says under the lock.
pub fn not_learned(driver_id: &str) -> KernelError {
    KernelError::Gate(format!(
        "driver {driver_id} was not learned on this node; remedy: vk driver learn"
    ))
}

/// The answer rule (spec §6, "never applied in part"): at each `{` of `answer`, in order, one
/// JSON value is parsed. An answer-shaped object — one with both an `invariant` and a `value` —
/// is judged whole: it chooses its value when it is exactly `{"invariant", "value"}` (no other
/// key), names this ambiguity's invariant and its value is a candidate's, and is otherwise passed
/// over whole, so a value inside an answer about another invariant, inside an answer outside the
/// candidates, or inside an answer carrying more than the rule asks, is never taken for this one.
/// Any other value chooses itself when it equals a candidate's value — compared as JSON: key
/// order is not meaning, but a superset of a candidate is not that candidate, nor is `11.0` the
/// candidate `11` — and otherwise the scan goes on at the next `{`, inside it included. Nothing
/// else ever comes back: an arch can only pick among what the miner listed, and a string
/// candidate only through the answer shape, never by being echoed.
pub fn answer_choice(answer: &str, ambiguity: &Value) -> Option<Value> {
    let invariant = ambiguity.get("invariant")?.as_str()?;
    let candidates: Vec<&Value> = ambiguity
        .get("candidates")?
        .as_array()?
        .iter()
        .filter_map(|c| c.get("value"))
        .collect();
    let is_candidate = |v: &Value| candidates.contains(&v);
    let mut at = 0;
    while let Some(offset) = answer[at..].find('{') {
        let start = at + offset;
        let mut values = serde_json::Deserializer::from_str(&answer[start..]).into_iter::<Value>();
        let Some(Ok(value)) = values.next() else {
            at = start + 1;
            continue;
        };
        let end = start + values.byte_offset();
        if let Some(shaped) = value
            .as_object()
            .filter(|o| o.contains_key("invariant") && o.contains_key("value"))
        {
            if shaped.len() == 2
                && shaped["invariant"] == invariant
                && is_candidate(&shaped["value"])
            {
                return Some(shaped["value"].clone());
            }
            at = end;
            continue;
        }
        if is_candidate(&value) {
            return Some(value);
        }
        at = start + 1;
    }
    None
}

impl RealKernel {
    /// A human act (I1 otherwise): `by` is set here from the principal, whatever the caller put;
    /// logs `driver.learned`, then writes the `learned` row keyed by `learned.driver_id`. A driver
    /// learned again starts unqualified: the replay on record was of another learning.
    pub fn driver_learned(
        &mut self,
        ctx: &Ctx,
        mut learned: DriverLearned,
        name: &str,
        out: &Path,
        qualification: Qualification,
    ) -> Result<(), KernelError> {
        learned.by = match &ctx.principal {
            Principal::Human { device_id } => device_id.clone(),
            Principal::Machine { .. } => {
                return Err(KernelError::I1(
                    "learning a driver writes code this node will run, which is a human act: \
                     run it from an enrolled device"
                        .into(),
                ))
            }
        };
        let row = LearnedRow {
            learned,
            name: name.into(),
            out: out.to_path_buf(),
            qualification,
            qualified: None,
        };
        // Ledger before the row, as everywhere else in this kernel.
        self.log("driver.learned", ctx.now_ms, &row.learned)?;
        self.store
            .db
            .put_json(LEARNED, &row.learned.driver_id, &row)
            .map_err(store_failed)
    }

    /// Kernel-written: refused for a `driver_id` with no `learned` row ("driver <id> was not
    /// learned on this node; remedy: vk driver learn"); logs `driver.qualified`, then records
    /// `record`'s path, `qualification_sha256`, passes and failures on the row. A replay under the
    /// thresholds is recorded all the same (spec §6): the gate is what refuses it.
    pub fn driver_qualified(
        &mut self,
        ctx: &Ctx,
        q: DriverQualified,
        record: &Path,
    ) -> Result<(), KernelError> {
        let mut row = self
            .learned(&q.driver_id)?
            .ok_or_else(|| not_learned(&q.driver_id))?;
        self.log("driver.qualified", ctx.now_ms, &q)?;
        row.qualified = Some(QualifiedRecord {
            record: record.to_path_buf(),
            qualification_sha256: q.qualification_sha256,
            passes: q.passes,
            failures: q.failures,
        });
        self.store
            .db
            .put_json(LEARNED, &q.driver_id, &row)
            .map_err(store_failed)
    }

    /// The `learned` row of `driver_id`, if this node learned it.
    pub fn learned(&self, driver_id: &str) -> Result<Option<LearnedRow>, KernelError> {
        self.store
            .db
            .get_json(LEARNED, driver_id)
            .map_err(store_failed)
    }

    /// The gate (ruling L-P4): `Ok` for every kind but `com`; for `com`, a `learned` row with a
    /// record whose file still hashes to `qualification_sha256` and whose passes meet the plan's
    /// thresholds (`passes ≥ min_passes`, `passes / (passes + failures) ≥ min_rate`), else
    /// [`KernelError::Gate`] naming the remedy. There is no override (ruling L-P16).
    pub fn check_qualified(&self, manifest: &DriverManifest) -> Result<(), KernelError> {
        if manifest.kind != DriverKind::Com {
            return Ok(());
        }
        let id = manifest.driver_id();
        let not_qualified = || {
            KernelError::Gate(format!(
                "driver {id} is not qualified; remedy: {QUALIFY_REMEDY}"
            ))
        };
        let Some(row) = self.learned(&id)? else {
            return Err(not_qualified());
        };
        let Some(q) = row.qualified else {
            return Err(not_qualified());
        };
        let changed = |why: String| {
            KernelError::Gate(format!(
                "driver {id}: qualification.json changed since vk driver qualify{why}; remedy: \
                 {QUALIFY_REMEDY}"
            ))
        };
        match std::fs::read(&q.record) {
            Ok(bytes) if hash_bytes(&bytes) == q.qualification_sha256 => {}
            Ok(_) => return Err(changed(String::new())),
            Err(e) => {
                return Err(changed(format!(
                    " ({} cannot be read: {})",
                    q.record.display(),
                    e.kind()
                )))
            }
        }
        let runs = u64::from(q.passes) + u64::from(q.failures);
        let Qualification {
            min_passes,
            min_rate,
        } = row.qualification;
        let rate_held = runs > 0 && q.passes as f64 / runs as f64 >= min_rate;
        if q.passes < min_passes || !rate_held {
            return Err(KernelError::Gate(format!(
                "driver {id} is not qualified: {} passes of {runs}, the plan asks {min_passes} at \
                 rate {min_rate}; remedy: {QUALIFY_REMEDY}",
                q.passes
            )));
        }
        Ok(())
    }

    /// The one model call of the pipeline (rulings L-P10, L-P20): a task over a fresh register
    /// whose goal is `serde_json::to_string(ambiguity)` and nothing else (label scope `business`,
    /// constraint [`ANSWER_RULE`], no evidence), with one `StepKind::Plan { arch_id }` step —
    /// `infer` with `Capability::Plan` — run at once; the answer is the register's last decision,
    /// `plan: ` removed, read by [`answer_choice`]. `None` is an answer outside the candidates: the
    /// ambiguity stays open and the planner purges it. An arch that cannot answer at all is the
    /// error `run_task_step` returns, its task failed on the record.
    pub fn learn_ambiguity(
        &mut self,
        ctx: &Ctx,
        arch_id: &str,
        ambiguity: &Value,
    ) -> Result<Option<Value>, KernelError> {
        let goal = serde_json::to_string(ambiguity)
            .map_err(|e| KernelError::Gate(format!("the ambiguity does not encode: {e}")))?;
        let label = Label {
            scope: Scope::Business,
            ..Label::bottom()
        };
        let task = self.create_task(
            ctx,
            &goal,
            ANSWER_KIND,
            label,
            &[ANSWER_RULE.to_string()],
            &[],
            vec![StepKind::Plan {
                arch_id: arch_id.into(),
            }],
        )?;
        self.run_task_step(ctx, &task.id)?;
        let reg = self.read_register(ctx, &task.register)?;
        Ok(reg
            .decisions
            .last()
            .and_then(|d| answer_choice(d.strip_prefix("plan: ").unwrap_or(d), ambiguity)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// The answer rule reads past text that is not JSON, and a `{` that does not open a value.
    #[test]
    fn the_answer_rule_skips_what_does_not_parse() {
        let amb = json!({"invariant": "body_font", "candidates": [
            {"value": {"name": "Garamond", "size": 11}, "support": 0.7}]});
        assert_eq!(
            answer_choice(
                "{ not json } then {\"invariant\": \"body_font\", \"value\": {\"name\": \"Garamond\", \"size\": 11}}",
                &amb
            ),
            Some(json!({"name": "Garamond", "size": 11}))
        );
        assert_eq!(answer_choice("{{{", &amb), None);
        // An answer about this invariant whose value is outside the candidates is judged whole:
        // a candidate nested inside it is not taken in part.
        assert_eq!(
            answer_choice(
                "{\"invariant\": \"body_font\", \"value\": {\"alt\": {\"name\": \"Garamond\", \"size\": 11}}}",
                &amb
            ),
            None
        );
        // An ambiguity without candidates chooses nothing.
        assert_eq!(answer_choice("{}", &json!({"invariant": "x"})), None);
    }

    /// Nothing near a candidate passes for it (review fix round 1): an answer carrying more than
    /// `{invariant, value}`, a value that is a superset of a candidate, and `11.0` for the
    /// candidate `11` are all refused, alone or inside the answer shape.
    #[test]
    fn the_answer_rule_refuses_supersets_and_near_equal_values() {
        let amb = json!({"invariant": "body_font", "candidates": [
            {"value": {"name": "Garamond", "size": 11}, "support": 0.7},
            {"value": {"name": "Calibri", "size": 12}, "support": 0.3}]});
        let refused = [
            // The answer shape with a key the rule does not ask for, judged whole: the
            // candidate inside it is not taken either.
            r#"{"invariant": "body_font", "value": {"name": "Garamond", "size": 11}, "extra": 1}"#,
            r#"{"invariant": "body_font", "value": {"name": "Garamond", "size": 11, "bold": true}}"#,
            r#"{"name": "Garamond", "size": 11, "bold": true}"#,
            r#"{"invariant": "body_font", "value": {"name": "Garamond", "size": 11.0}}"#,
            r#"{"name": "Garamond", "size": 11.0}"#,
        ];
        for answer in refused {
            assert_eq!(answer_choice(answer, &amb), None, "{answer}");
        }
        // The exact shape still chooses.
        assert_eq!(
            answer_choice(
                r#"{"value": {"size": 12, "name": "Calibri"}, "invariant": "body_font"}"#,
                &amb
            ),
            Some(json!({"name": "Calibri", "size": 12}))
        );
    }
}
