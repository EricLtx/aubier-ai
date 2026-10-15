//! Arch manifests (spec §3.7). An arch is any AI system behind an adapter.
use crate::hash_canonical;
use crate::labels::Clearance;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum Capability {
    Generate,
    Embed,
    Predict,
    Perceive,
    Plan,
    Judge,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Locality {
    Local,
    OnPrem,
    Peer,
    Cloud,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Determinism {
    SeededDeterministic,
    NonDeterministic,
}

/// Everything that changes model behaviour. Any change = a new arch id.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ArchIdentity {
    pub weights_sha256: String,
    pub engine: String,
    pub engine_version: String,
    pub backend: String,
    pub quant: String,
    pub kv_cache: String,
    pub threads: u32,
    pub batch: u32,
    pub sampling: BTreeMap<String, String>,
    pub seed: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ArchManifest {
    pub name: String,
    pub capabilities: BTreeSet<Capability>,
    pub locality: Locality,
    /// ISO 3166 country code, or "EU".
    pub jurisdiction: String,
    pub retention_days: Option<u32>,
    /// What a thousand prompt tokens cost on this arch, in euros — or `None`
    /// when this node has no price list for it.
    ///
    /// `None` is not zero and must never be rendered as one (SP1b Task 2b fix
    /// round 1, Ruling 30). `Some(0.0)` is a claim: nothing is billed, which
    /// is true of a local model and of a subscription. `None` is the absence
    /// of a claim — the Bedrock arch, whose prices are AWS's and which this
    /// node has not read — and an operator shown `0` there would believe the
    /// calls were free. `vk top` prints `?`.
    pub cost_per_1k_tokens_eur: Option<f64>,
    pub latency_ms_p50: u32,
    pub context_ceiling: u32,
    pub determinism: Determinism,
    pub identity: ArchIdentity,
    pub clearance: Clearance,
    /// True only if the kernel launched and contains the inference process (spec §3.3).
    pub governed: bool,
    /// What the provider's plan lets this arch be asked for, when the
    /// manifest makes a claim about it (founder decision 2026-10-15).
    ///
    /// A **claim**, like `retention_days` and `cost_per_1k_tokens_eur`: what
    /// the plan says, not what this node has measured and not something the
    /// kernel enforces. What the kernel *observes* — a 429, a spent balance,
    /// a model the provider has withdrawn — is the arch's health, which the
    /// kernel holds beside the manifest and never writes into it.
    ///
    /// Part of the arch id when present (see [`ArchManifest::arch_id`]): a
    /// plan change is a new arch. Absent, it is not serialised at all, so a
    /// manifest written before this field existed keeps both its arch id and
    /// its canonical hash — which is what the `arches` table holds and what
    /// the boot path compares against.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quota: Option<QuotaProfile>,
}

/// The provider plan an arch is used under, and the limits that plan states.
///
/// Every limit is optional, because a plan does not always publish one — a
/// subscription states a usage window rather than a token rate — and a limit
/// this node does not know is left out rather than guessed. A limit of zero
/// is refused (schema and [`ArchManifest::validate`] alike): absent already
/// says "no figure", and a plan that allows nothing is not a plan the arch is
/// used under but a spent one, which is health the kernel observes
/// (`exhausted`), not a claim a manifest makes.
///
/// Unknown fields are refused: `tokens_per_min` mistyped for
/// `tokens_per_minute` would otherwise read back as "no limit stated", the
/// one silent misreading a quota can have. Absent limits are not serialised,
/// so the arch id of a quota stays the same if a later limit is added to this
/// type and left unset.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct QuotaProfile {
    /// A free label for the plan: `subscription`, `pay-as-you-go`,
    /// `free-tier`, a provider's tier name. Printed by `vk arch show`, so at
    /// most 64 characters and never blank.
    #[schemars(length(min = 1, max = 64))]
    pub plan: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[schemars(range(min = 1))]
    pub requests_per_minute: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[schemars(range(min = 1))]
    pub tokens_per_minute: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[schemars(range(min = 1))]
    pub tokens_per_day: Option<u64>,
}

/// The longest `QuotaProfile::plan` may be, in characters.
pub const MAX_PLAN_LABEL: usize = 64;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ArchError {
    #[error("manifest declares no capabilities")]
    NoCapabilities,
    #[error("context_ceiling must be > 0")]
    ZeroContext,
    /// The quota claim cannot be read as one; the text says which part.
    #[error("quota: {0}")]
    Quota(String),
}

impl ArchManifest {
    /// The arch id: `hash_canonical` of the identity tuple — and of the
    /// quota, when the manifest declares one.
    ///
    /// Not of the whole manifest. `clearance`, `retention_days`, the price and
    /// the rest can differ under one id; the kernel refuses to let a second
    /// manifest with the same id replace the first ("already mounted with a
    /// different manifest"), which is what keeps them pinned. The quota is the
    /// one manifest field outside `ArchIdentity` that is hashed, by founder
    /// decision (2026-10-15): two plans are two arches, so the counters, the
    /// usage rows and the health of a subscription and of a metered key on
    /// the same model never mix — **a plan change is a new arch id**.
    ///
    /// The quota is laid beside the identity's own fields, and skipped when
    /// absent, so the bytes hashed for a manifest with no quota are exactly
    /// the identity's canonical JSON: every arch mounted before this field
    /// existed keeps its id (pinned by a test).
    pub fn arch_id(&self) -> String {
        #[derive(Serialize)]
        struct Hashed<'a> {
            #[serde(flatten)]
            identity: &'a ArchIdentity,
            #[serde(skip_serializing_if = "Option::is_none")]
            quota: Option<&'a QuotaProfile>,
        }
        hash_canonical(&Hashed {
            identity: &self.identity,
            quota: self.quota.as_ref(),
        })
    }

    pub fn validate(&self) -> Result<(), ArchError> {
        if self.capabilities.is_empty() {
            return Err(ArchError::NoCapabilities);
        }
        if self.context_ceiling == 0 {
            return Err(ArchError::ZeroContext);
        }
        if let Some(q) = &self.quota {
            q.validate()?;
        }
        Ok(())
    }
}

impl QuotaProfile {
    /// The same rules the schema states, for a quota that was built rather
    /// than parsed against it.
    pub fn validate(&self) -> Result<(), ArchError> {
        let quota = |why: &str| Err(ArchError::Quota(why.into()));
        if self.plan.trim().is_empty() {
            return quota("name the plan (`subscription`, `pay-as-you-go`, `free-tier`…)");
        }
        if self.plan.chars().count() > MAX_PLAN_LABEL {
            return quota("the plan label is longer than 64 characters");
        }
        if self.requests_per_minute == Some(0)
            || self.tokens_per_minute == Some(0)
            || self.tokens_per_day == Some(0)
        {
            return quota("a limit of 0 is not a limit; leave it out when the plan states none");
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::labels::*;

    fn gemma() -> ArchManifest {
        ArchManifest {
            name: "gemma-4-9b".into(),
            capabilities: [Capability::Generate, Capability::Plan].into(),
            locality: Locality::Local,
            jurisdiction: "FR".into(),
            retention_days: None,
            cost_per_1k_tokens_eur: Some(0.0),
            latency_ms_p50: 900,
            context_ceiling: 8192,
            determinism: Determinism::SeededDeterministic,
            identity: ArchIdentity {
                weights_sha256: "sha256:abc".into(),
                engine: "llama-server".into(),
                engine_version: "b5000".into(),
                backend: "cuda".into(),
                quant: "Q4_K_M".into(),
                kv_cache: "f16".into(),
                threads: 8,
                batch: 512,
                sampling: Default::default(),
                seed: Some(7),
            },
            clearance: Clearance {
                max_scope: Scope::Personal,
                third_party_allowed: true,
            },
            governed: true,
            quota: None,
        }
    }

    #[test]
    fn arch_id_changes_when_any_identity_field_changes() {
        let a = gemma();
        let mut b = gemma();
        b.identity.quant = "Q8_0".into();
        let mut c = gemma();
        c.identity.seed = Some(8);
        assert_ne!(a.arch_id(), b.arch_id());
        assert_ne!(a.arch_id(), c.arch_id());
        assert_eq!(a.arch_id(), gemma().arch_id());
    }

    #[test]
    fn arch_id_ignores_non_identity_fields() {
        let a = gemma();
        let mut b = gemma();
        b.latency_ms_p50 = 5;
        assert_eq!(a.arch_id(), b.arch_id());
    }

    /// The example every reader of `contracts/` starts from, as committed.
    fn committed_example() -> ArchManifest {
        serde_json::from_str(include_str!(
            "../../../contracts/examples/arch_manifest/valid/gemma_local.json"
        ))
        .expect("the committed example parses")
    }

    /// An arch mounted before `quota` existed keeps its id, and its stored
    /// manifest keeps its hash — pinned to the values computed on the code
    /// as it was before the field was added (commit 151c55f). The second one
    /// is what `manifest_difference` compares at boot and what the `arches`
    /// table holds, so a manifest that grew a `"quota": null` on its way
    /// through serde would mark every arch on every node `unavailable` after
    /// the upgrade, with "manifest changed" as the reason.
    #[test]
    fn a_manifest_without_a_quota_keeps_its_arch_id_and_its_hash() {
        let m = committed_example();
        assert_eq!(m.quota, None, "the example declares no quota");
        assert_eq!(
            m.arch_id(),
            "sha256:a64593559b8b1226e175e97a388f5053c523236cb88d8b26111d036b5852a71d"
        );
        assert_eq!(
            hash_canonical(&m),
            "sha256:79100d6c1fdbe41742a795828b80949b6b630ba3ace1bb7144c7574238cac9d8"
        );
        let text = serde_json::to_string(&m).expect("serialises");
        assert!(!text.contains("quota"), "no quota key is written: {text}");
    }

    fn quota(plan: &str) -> QuotaProfile {
        QuotaProfile {
            plan: plan.into(),
            requests_per_minute: Some(50),
            tokens_per_minute: Some(30_000),
            tokens_per_day: None,
        }
    }

    /// Founder decision 2026-10-15: a plan is part of what an arch is. Two
    /// plans on the same model are two arches, and the same plan stated twice
    /// is one.
    #[test]
    fn a_plan_change_is_a_new_arch_id() {
        let bare = gemma();
        let mut metered = gemma();
        metered.quota = Some(quota("pay-as-you-go"));
        let mut subscribed = gemma();
        subscribed.quota = Some(quota("subscription"));
        let mut faster = metered.clone();
        faster.quota.as_mut().unwrap().tokens_per_minute = Some(80_000);

        assert_ne!(bare.arch_id(), metered.arch_id(), "declaring a plan");
        assert_ne!(metered.arch_id(), subscribed.arch_id(), "changing it");
        assert_ne!(metered.arch_id(), faster.arch_id(), "changing a limit");
        let mut again = gemma();
        again.quota = Some(quota("pay-as-you-go"));
        assert_eq!(metered.arch_id(), again.arch_id(), "the same claim twice");
    }

    #[test]
    fn a_quota_round_trips_and_leaves_absent_limits_out() {
        let mut m = gemma();
        m.quota = Some(quota("free-tier"));
        let v = serde_json::to_value(&m).unwrap();
        assert_eq!(
            v["quota"],
            serde_json::json!({"plan": "free-tier", "requests_per_minute": 50, "tokens_per_minute": 30000})
        );
        let back: ArchManifest = serde_json::from_value(v).unwrap();
        assert_eq!(back, m);
        assert!(m.validate().is_ok());
    }

    #[test]
    fn a_zero_limit_a_blank_plan_and_an_unknown_field_are_refused() {
        let refused = |q: QuotaProfile| {
            let mut m = gemma();
            m.quota = Some(q);
            m.validate()
        };
        let mut zero = quota("pay-as-you-go");
        zero.tokens_per_minute = Some(0);
        assert!(matches!(refused(zero), Err(ArchError::Quota(_))));
        let mut zero_day = quota("pay-as-you-go");
        zero_day.tokens_per_day = Some(0);
        assert!(matches!(refused(zero_day), Err(ArchError::Quota(_))));
        assert!(matches!(refused(quota("  ")), Err(ArchError::Quota(_))));
        assert!(matches!(
            refused(quota(&"x".repeat(MAX_PLAN_LABEL + 1))),
            Err(ArchError::Quota(_))
        ));
        assert!(refused(quota(&"x".repeat(MAX_PLAN_LABEL))).is_ok());
        let typo = serde_json::json!({"plan": "free-tier", "tokens_per_min": 10});
        assert!(serde_json::from_value::<QuotaProfile>(typo).is_err());
    }

    #[test]
    fn manifest_must_declare_at_least_one_capability() {
        let mut a = gemma();
        a.capabilities.clear();
        assert!(a.validate().is_err());
    }
}
