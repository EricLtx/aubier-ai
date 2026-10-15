//! What the kernel has observed of each mounted arch's provider (founder
//! decision 2026-10-15).
//!
//! The manifest's `quota` is a **claim** — what the plan says. This is the
//! other half: what the provider actually answered the last time it was
//! called. The two are kept apart on purpose. A claim is part of what an arch
//! *is* (it is hashed into the arch id); an observation changes from one call
//! to the next and is part of nothing but this process's memory of the arch.
//!
//! Neither is enforcement. The kernel does not meter calls against the
//! quota, and health is not a budget: it is the last thing the far end said
//! about the plan, kept so that a task step is not sent to an arch that has
//! already said it will refuse it.
//!
//! In memory only, like the harness tokens and the in-flight driver acts: a
//! restart re-makes every adapter through its factory, which is a remount,
//! and a remount clears health. Nothing here is written to the store or the
//! ledger as a kind of its own — a call that changes an arch's health says so
//! in the `infer` event it already writes (`health_after`).
//!
//! The state machine, per arch:
//!
//! ```text
//!             successful call
//!   ┌──────────────────────────────────────────────┐
//!   ▼                                              │
//! healthy ──429──▶ rate_limited ── window passes ──▶ (next call is allowed and decides)
//!   │  ▲               │
//!   │  └── remount ────┤
//!   ├──402──▶ exhausted ── sticky: remount (or restart) clears it
//!   └──404──▶ retired   ── sticky: remount (or restart) clears it
//! ```
//!
//! A step is never sent to an `exhausted` or `retired` arch, so the only way
//! out of either is a remount — which is the remedy the refusal names.
use crate::arch::AdapterError;
use serde::{Deserialize, Serialize};

/// How long the kernel holds back an arch whose provider rate-limited it
/// without saying for how long. The figure the founder set (2026-10-15).
pub const DEFAULT_RATE_LIMIT_BACKOFF_MS: u64 = 60_000;

/// The longest a `last_error` is kept, in characters: it is printed on one
/// line of `vk arch show` and under the `vk top` table.
const MAX_LAST_ERROR: usize = 300;

/// Where an arch's provider stands, as this kernel last saw it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum HealthState {
    /// The last call came back, or none has been made since the mount.
    Healthy,
    /// The provider asked to be left alone. `until_ms` is the end of the wait
    /// the provider named; `None` when it named none, in which case the
    /// kernel waits [`DEFAULT_RATE_LIMIT_BACKOFF_MS`] from `since_ms`. Kept
    /// as `None` rather than filled in, so a reader can tell the provider's
    /// figure from the kernel's guess.
    RateLimited {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        until_ms: Option<u64>,
    },
    /// The plan has nothing left to spend. Sticky.
    Exhausted,
    /// The provider no longer serves the model. Sticky.
    Retired,
}

/// One arch's health: the state, since when, and what the provider said.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ArchHealth {
    #[serde(flatten)]
    pub state: HealthState,
    /// When the arch entered this state, in the caller's milliseconds.
    pub since_ms: u64,
    /// The adapter's account of the failure that put it here — scrubbed by
    /// the adapter, cut to one line's worth. `None` while healthy.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
}

impl ArchHealth {
    /// Healthy since `now_ms`: a fresh mount, or a call that came back.
    pub fn healthy(now_ms: u64) -> ArchHealth {
        ArchHealth {
            state: HealthState::Healthy,
            since_ms: now_ms,
            last_error: None,
        }
    }

    /// The word every surface prints: `healthy`, `rate_limited`, `exhausted`
    /// or `retired` — the same spelling the JSON carries.
    pub fn name(&self) -> &'static str {
        match self.state {
            HealthState::Healthy => "healthy",
            HealthState::RateLimited { .. } => "rate_limited",
            HealthState::Exhausted => "exhausted",
            HealthState::Retired => "retired",
        }
    }

    pub fn is_healthy(&self) -> bool {
        self.state == HealthState::Healthy
    }

    /// Until when a call must not be made, for a rate-limited arch; `None`
    /// for every other state. The provider's figure when it gave one, the
    /// kernel's default otherwise.
    pub fn held_until_ms(&self) -> Option<u64> {
        match self.state {
            HealthState::RateLimited { until_ms } => Some(
                until_ms
                    .unwrap_or_else(|| self.since_ms.saturating_add(DEFAULT_RATE_LIMIT_BACKOFF_MS)),
            ),
            _ => None,
        }
    }

    /// The health a failed call leaves its arch in, observed at `now_ms`, or
    /// `None` for a failure that says nothing about the provider's plan — an
    /// I4′ refusal is about the prompt, and a machinery failure is about this
    /// node. Those leave the health exactly as it was.
    pub fn after_error(e: &AdapterError, now_ms: u64) -> Option<ArchHealth> {
        let state = match e {
            AdapterError::RateLimited { retry_after } => HealthState::RateLimited {
                until_ms: retry_after.map(|d| {
                    let wait = u64::try_from(d.as_millis()).unwrap_or(u64::MAX);
                    now_ms.saturating_add(wait)
                }),
            },
            AdapterError::Exhausted { .. } => HealthState::Exhausted,
            AdapterError::Retired { .. } => HealthState::Retired,
            AdapterError::I4Prime { .. } | AdapterError::Other(_) => return None,
        };
        Some(ArchHealth {
            state,
            since_ms: now_ms,
            last_error: Some(one_line(&e.to_string())),
        })
    }
}

/// `text` on one line and at most [`MAX_LAST_ERROR`] characters, marked when
/// cut: a provider that answers with a page of HTML must not put it on the
/// operator's screen.
fn one_line(text: &str) -> String {
    let flat: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if flat.chars().count() <= MAX_LAST_ERROR {
        return flat;
    }
    format!("{}…", flat.chars().take(MAX_LAST_ERROR).collect::<String>())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn a_rate_limit_holds_the_arch_for_the_provider_s_figure_or_sixty_seconds() {
        let said = ArchHealth::after_error(
            &AdapterError::RateLimited {
                retry_after: Some(Duration::from_secs(30)),
            },
            1_000,
        )
        .expect("a rate limit is health");
        assert_eq!(said.name(), "rate_limited");
        assert_eq!(said.since_ms, 1_000);
        assert_eq!(said.held_until_ms(), Some(31_000));

        let silent =
            ArchHealth::after_error(&AdapterError::RateLimited { retry_after: None }, 1_000)
                .expect("a rate limit is health");
        assert_eq!(
            silent.state,
            HealthState::RateLimited { until_ms: None },
            "the provider's silence is kept, not filled in"
        );
        assert_eq!(silent.held_until_ms(), Some(61_000));
    }

    #[test]
    fn exhausted_and_retired_are_states_and_the_other_failures_are_not() {
        let spent = ArchHealth::after_error(
            &AdapterError::Exhausted {
                detail: "credit balance is too low".into(),
            },
            5,
        )
        .expect("health");
        assert_eq!(spent.name(), "exhausted");
        assert!(spent
            .last_error
            .as_deref()
            .unwrap()
            .contains("credit balance"));
        assert_eq!(spent.held_until_ms(), None, "no window: it is sticky");

        let gone = ArchHealth::after_error(
            &AdapterError::Retired {
                detail: "model: claude-2.1".into(),
            },
            5,
        )
        .expect("health");
        assert_eq!(gone.name(), "retired");

        let prompt = AdapterError::I4Prime {
            needed: 9,
            ceiling: 8,
        };
        assert_eq!(ArchHealth::after_error(&prompt, 5), None);
        let machinery = AdapterError::Other(anyhow::anyhow!("claude exited 1"));
        assert_eq!(ArchHealth::after_error(&machinery, 5), None);
    }

    #[test]
    fn the_wire_shape_is_flat_and_names_the_state() {
        let h = ArchHealth::after_error(
            &AdapterError::RateLimited {
                retry_after: Some(Duration::from_secs(2)),
            },
            10,
        )
        .unwrap();
        let v = serde_json::to_value(&h).unwrap();
        assert_eq!(v["state"], "rate_limited");
        assert_eq!(v["until_ms"], 2_010);
        assert_eq!(v["since_ms"], 10);
        let back: ArchHealth = serde_json::from_value(v).unwrap();
        assert_eq!(back, h);
        let fine = serde_json::to_value(ArchHealth::healthy(3)).unwrap();
        assert_eq!(fine, serde_json::json!({"state": "healthy", "since_ms": 3}));
    }

    #[test]
    fn a_long_error_is_cut_to_one_line() {
        let page = format!("<html>\n{}\n</html>", "x".repeat(1_000));
        let h = ArchHealth::after_error(&AdapterError::Retired { detail: page }, 0).unwrap();
        let kept = h.last_error.unwrap();
        assert!(!kept.contains('\n'));
        assert_eq!(kept.chars().count(), MAX_LAST_ERROR + 1, "{kept}");
    }
}
