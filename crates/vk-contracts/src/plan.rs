//! The driver plan: the artefact a human reads and approves before a driver is
//! generated from it (driver-learning spec §4.5). It names the actions the
//! driver will offer, each as an ordered list of ontology verbs, and what a
//! qualification replay must reach. Like a register map in a datasheet, it is
//! the typed contract between what was learned and what gets built; the kernel
//! hashes its bytes and records the hash, never a re-serialisation.
use crate::driver::{is_driver_name, is_sha256};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The plan a human approves (spec §4.5).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DriverPlan {
    /// The family of applications the driver belongs to: `office-document`.
    pub family: String,
    /// The application: `word` or `powerpoint`.
    pub app: String,
    pub ontology_sha256: String,
    pub catalogue_sha256: String,
    pub invariants_sha256: String,
    pub actions: Vec<PlanAction>,
    #[serde(default)]
    pub ambiguities: Vec<Ambiguity>,
    pub qualification: Qualification,
}

/// One action the generated driver offers: ordered steps, its pre-conditions
/// and the invariants that must hold after it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PlanAction {
    pub name: String,
    /// True only when every step's ontology verb is reversible (L-P13).
    pub reversible: bool,
    pub steps: Vec<PlanStep>,
    pub pre: Vec<String>,
    /// Invariant ids, and `pdf_exists`: one per `verify_*` step, in their order.
    pub post: Vec<String>,
    /// The `partial` bindings the steps use, for the human reading the plan.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

/// One step: an ontology verb and its arguments. An argument that is a string
/// starting with `$` is a reference: `$arg.<key>` or `$secret.<id>`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PlanStep {
    pub verb: String,
    #[serde(default)]
    pub args: serde_json::Map<String, Value>,
}

/// An invariant the corpus left open, and how it was settled.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Ambiguity {
    pub invariant: String,
    /// `arch:<arch_id>` | `human` | `purged`.
    pub resolved_by: String,
    /// The chosen candidate: present exactly when `resolved_by` is not `purged`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value: Option<Value>,
}

/// What the qualification replay must reach before the driver is trusted.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Qualification {
    pub min_passes: u32,
    pub min_rate: f64,
}

/// `a-z` and `_`, 1 to `max` characters.
fn is_snake(s: &str, max: usize) -> bool {
    !s.is_empty() && s.len() <= max && s.bytes().all(|b| b.is_ascii_lowercase() || b == b'_')
}

/// `a-z`, `0-9` and `_`, 1 to `max` characters.
fn is_snake_digits(s: &str, max: usize) -> bool {
    !s.is_empty()
        && s.len() <= max
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}

/// An argument key: `a-z`, `0-9` and `_`, 1 to 32 characters, starting with a letter.
fn is_arg_key(s: &str) -> bool {
    is_snake_digits(s, 32) && s.as_bytes()[0].is_ascii_lowercase()
}

/// Every string at any depth of `v` that starts with `$` must be `$arg.<key>`
/// or `$secret.<id>`; the first one that is not is returned.
fn bad_reference(v: &Value) -> Option<&str> {
    match v {
        Value::String(s) if s.starts_with('$') => {
            let ok = s
                .strip_prefix("$arg.")
                .or_else(|| s.strip_prefix("$secret."))
                .is_some_and(is_arg_key);
            (!ok).then_some(s.as_str())
        }
        Value::Array(items) => items.iter().find_map(bad_reference),
        Value::Object(map) => map.values().find_map(bad_reference),
        _ => None,
    }
}

impl DriverPlan {
    /// Refusals, in this order, each naming its field and its remedy: family and app plain tokens;
    /// the three hashes `sha256:` + 64 lower-case hex; `actions is empty`; per action: name 1..32 of
    /// a-z and `_`, unique; steps non-empty and the first step's verb `open`; every verb 1..40 of a-z
    /// and `_`; every args key 1..32 of a-z, 0-9 and `_`, starting with a letter; every string arg
    /// starting with `$` is `$arg.<key>` or `$secret.<id>` (key/id as an args key); every `post` entry
    /// 1..40 of a-z, 0-9 and `_`, and as many entries as `verify_*` steps; every ambiguity's
    /// `resolved_by` is `purged`, `human` or `arch:<non-empty>`, with `value` present exactly when it
    /// is not `purged`; `min_passes ≥ 1`; `0 ≤ min_rate ≤ 1`.
    pub fn validate(&self) -> Result<(), String> {
        for (field, value) in [("family", &self.family), ("app", &self.app)] {
            if !is_driver_name(value) {
                return Err(format!(
                    "{field} `{value}` must be 1 to 32 characters of a-z, 0-9 and `-`; \
                     remedy: use the plain token the ontology names"
                ));
            }
        }
        for (field, value) in [
            ("ontology_sha256", &self.ontology_sha256),
            ("catalogue_sha256", &self.catalogue_sha256),
            ("invariants_sha256", &self.invariants_sha256),
        ] {
            if !is_sha256(value) {
                return Err(format!(
                    "{field} `{value}` must be `sha256:` and 64 lower-case hex digits; \
                     remedy: copy the hash of the file the plan was built from"
                ));
            }
        }
        if self.actions.is_empty() {
            return Err("actions is empty: a plan holds at least one action; \
                 remedy: learn again from a corpus that yields an invariant"
                .into());
        }
        let mut seen: Vec<&str> = Vec::new();
        for action in &self.actions {
            let name = action.name.as_str();
            if !is_snake(name, 32) {
                return Err(format!(
                    "action name `{name}` must be 1 to 32 characters of a-z and `_`; \
                     remedy: rename the action with lower-case words joined by `_`"
                ));
            }
            if seen.contains(&name) {
                return Err(format!(
                    "duplicate action name `{name}`; remedy: give each action its own name"
                ));
            }
            seen.push(name);
            if action.steps.first().map(|s| s.verb.as_str()) != Some("open") {
                return Err(format!(
                    "action {name}: the first step is open, the step that names the document; \
                     remedy: start the action with an open step"
                ));
            }
            for step in &action.steps {
                if !is_snake(&step.verb, 40) {
                    return Err(format!(
                        "action {name}: verb `{}` must be 1 to 40 characters of a-z and `_`; \
                         remedy: use a verb of the ontology",
                        step.verb
                    ));
                }
                for key in step.args.keys() {
                    if !is_arg_key(key) {
                        return Err(format!(
                            "action {name}: step {}: args key `{key}` must be 1 to 32 characters \
                             of a-z, 0-9 and `_`, starting with a letter; \
                             remedy: rename the argument",
                            step.verb
                        ));
                    }
                }
                if let Some(bad) = step.args.values().find_map(bad_reference) {
                    return Err(format!(
                        "action {name}: step {}: reference `{bad}` is neither `$arg.<key>` nor \
                         `$secret.<id>`; remedy: name an argument or a secret, or write the \
                         value without a leading `$`",
                        step.verb
                    ));
                }
            }
            if let Some(bad) = action.post.iter().find(|p| !is_snake_digits(p, 40)) {
                return Err(format!(
                    "action {name}: post entry `{bad}` must be 1 to 40 characters of a-z, 0-9 \
                     and `_`; remedy: name an invariant id or `pdf_exists`"
                ));
            }
            let verifies = action
                .steps
                .iter()
                .filter(|s| s.verb.starts_with("verify_"))
                .count();
            if action.post.len() != verifies {
                return Err(format!(
                    "action {name}: post has {} entries for {verifies} verify_* steps; \
                     remedy: one post entry per verify_* step, in their order",
                    action.post.len()
                ));
            }
        }
        for a in &self.ambiguities {
            let resolved = match a.resolved_by.as_str() {
                "purged" => false,
                "human" => true,
                s if s.strip_prefix("arch:").is_some_and(|id| !id.is_empty()) => true,
                other => {
                    return Err(format!(
                        "ambiguity {}: resolved_by `{other}` must be `purged`, `human` or \
                         `arch:<id>`; remedy: record who settled it",
                        a.invariant
                    ));
                }
            };
            if resolved && a.value.is_none() {
                return Err(format!(
                    "ambiguity {}: value is missing for a resolved ambiguity; \
                     remedy: record the chosen candidate",
                    a.invariant
                ));
            }
            if !resolved && a.value.is_some() {
                return Err(format!(
                    "ambiguity {}: value must be absent for a purged ambiguity; \
                     remedy: drop the value, or record who resolved it",
                    a.invariant
                ));
            }
        }
        if self.qualification.min_passes < 1 {
            return Err("qualification.min_passes must be at least 1; \
                 remedy: a driver is qualified by at least one replay"
                .into());
        }
        if !(0.0..=1.0).contains(&self.qualification.min_rate) {
            return Err(format!(
                "qualification.min_rate {} must be between 0 and 1; \
                 remedy: write the rate as a fraction",
                self.qualification.min_rate
            ));
        }
        Ok(())
    }
}

/// `plan_sha256` (ruling L-P13): the SHA-256 of plan.json's bytes as they are on disk — no
/// re-serialisation, the same rule as vkforge's `plan.plan_sha256(path)`.
pub fn plan_sha256(bytes: &[u8]) -> String {
    crate::hash_bytes(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const EXAMPLE: &[u8] =
        include_bytes!("../../../contracts/examples/driver_plan/valid/word_maison.json");

    fn example() -> DriverPlan {
        serde_json::from_slice(EXAMPLE).unwrap()
    }

    #[test]
    fn the_example_plan_validates_and_its_hash_is_the_hash_of_its_bytes() {
        let p = example();
        p.validate().unwrap();
        assert_eq!(plan_sha256(EXAMPLE), crate::hash_bytes(EXAMPLE));
        assert!(plan_sha256(EXAMPLE).starts_with("sha256:"));
        // The bytes, never a re-serialisation (L-P13): the same plan written otherwise is another hash.
        let rewritten = serde_json::to_vec(&p).unwrap();
        assert_eq!(serde_json::from_slice::<DriverPlan>(&rewritten).unwrap(), p);
        assert_ne!(plan_sha256(&rewritten), plan_sha256(EXAMPLE));
    }

    #[test]
    fn a_plan_without_actions_or_with_a_malformed_action_is_refused() {
        let refuse = |edit: &dyn Fn(&mut DriverPlan), needle: &str| {
            let mut p = example();
            edit(&mut p);
            let e = p.validate().unwrap_err();
            assert!(e.contains(needle), "{needle}: {e}");
        };
        refuse(&|p| p.actions.clear(), "actions is empty");
        refuse(
            &|p| p.actions[0].steps[1].verb = "Apply-Style".into(),
            "verb",
        );
        refuse(
            &|p| {
                p.actions[0].steps.remove(0);
            },
            "first step",
        );
        refuse(
            &|p| {
                p.actions[0].post.pop();
            },
            "verify_",
        );
        refuse(&|p| p.actions[0].name = "Appliquer".into(), "action name");
        refuse(
            &|p| {
                let a = p.actions[0].clone();
                p.actions.push(a);
            },
            "duplicate",
        );
        refuse(
            &|p| {
                p.actions[0].steps[1].args.insert("from-x".into(), json!(1));
            },
            "from-x",
        );
        refuse(
            &|p| p.ontology_sha256 = "sha256:abc".into(),
            "ontology_sha256",
        );
        refuse(&|p| p.qualification.min_rate = 1.5, "min_rate");
        refuse(&|p| p.qualification.min_passes = 0, "min_passes");
    }

    #[test]
    fn a_reference_names_an_argument_or_a_secret_and_nothing_else() {
        let set = |v: Value| {
            let mut p = example();
            p.actions[0].steps[3].args.insert("text".into(), v);
            p.validate()
        };
        assert!(set(json!("$secret.footer_text")).is_ok());
        assert!(set(json!("$secret.")).unwrap_err().contains("$secret."));
        assert!(set(json!("$env.HOME")).unwrap_err().contains("$env.HOME"));
        assert!(set(json!("$arg.Document"))
            .unwrap_err()
            .contains("$arg.Document"));
    }

    #[test]
    fn an_ambiguity_carries_a_value_exactly_when_it_was_resolved() {
        let with = |resolved_by: &str, value: Option<Value>| {
            let mut p = example();
            p.ambiguities = vec![Ambiguity {
                invariant: "body_font".into(),
                resolved_by: resolved_by.into(),
                value,
            }];
            p.validate()
        };
        let garamond = json!({"name": "Garamond", "size": 11});
        assert!(with("purged", None).is_ok());
        assert!(with("arch:sha256:abc", Some(garamond.clone())).is_ok());
        assert!(with("purged", Some(garamond.clone()))
            .unwrap_err()
            .contains("value"));
        assert!(with("arch:sha256:abc", None).unwrap_err().contains("value"));
        assert!(with("arch:", Some(garamond))
            .unwrap_err()
            .contains("resolved_by"));
    }

    #[test]
    fn unknown_fields_are_refused_at_parse() {
        let mut v: Value = serde_json::from_slice(EXAMPLE).unwrap();
        v["actions"][0]["steps"][0]["target"] = json!("x");
        assert!(serde_json::from_value::<DriverPlan>(v).is_err());
        let mut v: Value = serde_json::from_slice(EXAMPLE).unwrap();
        v["excluded"] = json!([]);
        assert!(
            serde_json::from_value::<DriverPlan>(v).is_err(),
            "excluded lives in plan-excluded.json (L-P13)"
        );
    }
}
