//! Task recipes (pilot spec §5.1): a task described as an ordered list of steps,
//! so a process longer than one produce-and-release — several harness runs,
//! several human approvals — is a contract the kernel can run and a human can read.
//!
//! `RecipeStep` is serde-identical to the kernel's `StepKind`, pinned by a test
//! in `vk-kernel`; this crate does not depend on the kernel.
use crate::driver::plain_token;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case", tag = "kind", deny_unknown_fields)]
pub enum RecipeStep {
    Plan {
        arch_id: String,
    },
    Draft {
        arch_id: String,
    },
    Judge {
        arch_id: String,
    },
    Harness {
        name: String,
        #[serde(default)]
        drivers: Vec<String>,
        #[serde(default)]
        instruction: Option<String>,
        #[serde(default)]
        max_actions: Option<u32>,
        #[serde(default)]
        timeout_s: Option<u64>,
    },
    Approve,
    Release {
        to_dir: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TaskRecipe {
    pub goal: String,
    pub artefact_kind: String,
    #[serde(default)]
    pub constraints: Vec<String>,
    #[serde(default)]
    pub evidence: Vec<String>,
    pub steps: Vec<RecipeStep>,
}

impl TaskRecipe {
    /// Refusals, in this order: empty goal; an `artefact_kind` that is not a
    /// short plain token; no steps; more than one Release; a Release that is not
    /// last; an Approve as the first step (nothing to sign yet); a Harness
    /// naming a driver twice.
    pub fn validate(&self) -> Result<(), String> {
        if self.goal.trim().is_empty() {
            return Err("recipe has no goal: say what the task is for".into());
        }
        if !plain_token(&self.artefact_kind) {
            return Err(format!(
                "artefact_kind `{}` must be a short plain token \
                 (at most 32 of A-Z a-z 0-9 . _ -, not starting with a dot)",
                self.artefact_kind
            ));
        }
        if self.steps.is_empty() {
            return Err("recipe has no steps: add at least one".into());
        }
        let releases = self
            .steps
            .iter()
            .filter(|s| matches!(s, RecipeStep::Release { .. }))
            .count();
        if releases > 1 {
            return Err(format!(
                "recipe has {releases} release steps: keep only the last one"
            ));
        }
        if releases == 1 && !matches!(self.steps.last(), Some(RecipeStep::Release { .. })) {
            return Err("a release step must be the last step of the recipe".into());
        }
        if matches!(self.steps.first(), Some(RecipeStep::Approve)) {
            return Err(
                "an approve step cannot come first: put the step that produces what is signed before it"
                    .into(),
            );
        }
        for (i, s) in self.steps.iter().enumerate() {
            if let RecipeStep::Harness { drivers, .. } = s {
                let mut seen = std::collections::BTreeSet::new();
                if let Some(d) = drivers.iter().find(|d| !seen.insert(d.as_str())) {
                    return Err(format!(
                        "step {i}: harness names driver `{d}` twice: list each driver once"
                    ));
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn recipe() -> TaskRecipe {
        serde_json::from_str(include_str!(
            "../../../contracts/examples/task_recipe/valid/recette.json"
        ))
        .unwrap()
    }
    fn refuses(edit: impl FnOnce(&mut TaskRecipe), needle: &str) {
        let mut r = recipe();
        edit(&mut r);
        let e = r.validate().unwrap_err();
        assert!(e.contains(needle), "{needle}: {e}");
    }

    #[test]
    fn valid_example_validates_and_round_trips() {
        let r = recipe();
        r.validate().unwrap();
        let again: TaskRecipe = serde_json::from_value(serde_json::to_value(&r).unwrap()).unwrap();
        assert_eq!(r, again);
    }
    #[test]
    fn every_refusal_names_its_remedy() {
        refuses(|r| r.goal = "  ".into(), "no goal");
        refuses(|r| r.artefact_kind = ".hidden".into(), "artefact_kind");
        refuses(|r| r.artefact_kind = "a/b".into(), "artefact_kind");
        refuses(|r| r.steps.clear(), "no steps");
        refuses(
            |r| r.steps.push(RecipeStep::Release { to_dir: "x".into() }),
            "release",
        );
        refuses(
            |r| r.steps.push(RecipeStep::Approve),
            "release step must be the last",
        );
        refuses(
            |r| r.steps.insert(0, RecipeStep::Approve),
            "cannot come first",
        );
        refuses(
            |r| {
                r.steps.insert(
                    0,
                    RecipeStep::Harness {
                        name: "claude-code".into(),
                        drivers: vec!["a".into(), "a".into()],
                        instruction: None,
                        max_actions: None,
                        timeout_s: None,
                    },
                )
            },
            "twice",
        );
    }
    #[test]
    fn a_typo_in_a_recipe_or_a_step_is_refused() {
        assert!(serde_json::from_str::<TaskRecipe>(
            r#"{"goal":"g","artefact_kind":"r.md","steps":[],"contraints":[]}"#
        )
        .is_err());
        assert!(serde_json::from_str::<RecipeStep>(
            r#"{"kind":"harness","name":"h","driver":["x"]}"#
        )
        .is_err());
    }
    #[test]
    fn harness_options_default_when_absent() {
        let s: RecipeStep = serde_json::from_str(r#"{"kind":"harness","name":"h"}"#).unwrap();
        assert_eq!(
            s,
            RecipeStep::Harness {
                name: "h".into(),
                drivers: vec![],
                instruction: None,
                max_actions: None,
                timeout_s: None
            }
        );
    }
}
