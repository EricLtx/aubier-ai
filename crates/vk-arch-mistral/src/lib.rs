//! Mistral's **native API** as an arch — `POST /v1/chat/completions` on
//! Mistral's **EU regional endpoint**, `https://api.eu.mistral.ai`.
//!
//! The arch a node mounts when the register should be answered by a European
//! provider in European data centres. The wire is the OpenAI-style one, so
//! the request, the answer and every refusal are
//! [`vk_arch_openai_compat::ChatArch`]'s, shared with the `openai-compat`
//! arch; what this crate adds is what a table can know about Mistral and an
//! arbitrary server cannot — the origin, the jurisdiction, the retention
//! window, the windows and the prices.
//!
//! ## What is honest about it
//!
//! It is not **governed**: the inference runs on Mistral's machines, so
//! `locality: Cloud`, `governed: false`, clearance stops at `Business` and
//! third-party data is refused.
//!
//! * **`jurisdiction: "EU"`.** Mistral offers three origins
//!   (docs.mistral.ai/inference/regional-inference, read 2026-10-16):
//!   `api.mistral.ai`, for which "Mistral does not commit to a specific
//!   inference location"; `api.us.mistral.ai`; and `api.eu.mistral.ai`,
//!   served from "multiple data centers in EU and EFTA countries". Only the
//!   last can carry an `EU` claim, so it is the fixed origin. The claim is a
//!   rounding: EFTA includes Switzerland, Norway, Iceland and Liechtenstein,
//!   which the manifest's `EU` does not name — the EEA plus Switzerland, all
//!   under an adequacy decision or the EEA itself. No country is named by
//!   Mistral.
//! * **`retention_days: Some(30)`.** Mistral's privacy policy (legal.mistral.ai
//!   /terms/privacy-policy, effective 2026-09-03): "we keep your Input and
//!   Output for the period necessary to generate the Output and then for
//!   thirty (30) rolling days to monitor abuse (unless zero data retention is
//!   activated)". Zero data retention exists on paid plans, on request and at
//!   Mistral's discretion; a node granted it is still told 30 here, because
//!   this crate cannot see the grant. Training on API data is a separate
//!   setting (Admin → Privacy → "Anonymous improvement data"), which no
//!   manifest field can express and which the account owner must turn off.
//! * **The price is the EU endpoint's**: the regional endpoints are "billed
//!   at 1.1× standard list pricing", so every figure in [`MODELS`] — the
//!   standard list — is multiplied by [`REGIONAL_UPCHARGE`] before it is
//!   claimed.
//!
//! ## The key
//!
//! Service `vk`, user `mistral`, put there by `vk secret set mistral`. The
//! same keyring, the same [`SecretString`] and the same scrub as the
//! Anthropic arch, shared rather than copied: never in the mount spec, never
//! in a manifest, never in a log line or an error. Sent as `Authorization:
//! Bearer`, which is what Mistral's OpenAPI document declares.
//!
//! ## I4′
//!
//! Mistral has no token-counting endpoint (none of the 226 paths in its
//! OpenAPI document counts), so the pre-check is the `bytes/3 + 64` estimate
//! against nine tenths of the model's documented window, and the post-check
//! is the answer's own `usage.prompt_tokens` against the whole window.
//! Mistral refuses rather than truncates: "the token count of your prompt
//! plus `max_tokens` cannot exceed the model's context length".

use anyhow::Result;
use std::time::Duration;
use vk_arch_openai_compat::{ChatArch, ChatSettings, Price, MAX_OUTPUT_TOKENS};
use vk_contracts::arch::{ArchIdentity, ArchManifest, Capability, Determinism, Locality};
use vk_contracts::labels::{Clearance, Scope};
use vk_kernel::arch::{AdapterError, ArchAdapter, Completion};

pub use vk_arch_openai_compat::{load_key, KeySource, SecretString};

/// Mistral's EU regional endpoint, and the only origin an ordinary node uses.
///
/// **Not a request field**, for the reason the Anthropic origin is not: a
/// client that could name the origin could name a collector, and the daemon
/// would put its own key on the wire to it. Daemon configuration
/// (`vkd --mistral-base-url`, for tests), bounded by
/// [`vk_arch_openai_compat::check_base_url`] there and again here.
pub const DEFAULT_BASE_URL: &str = "https://api.eu.mistral.ai";

/// The model a `mistral` arch runs on unless told otherwise: Mistral Large 3,
/// a pinned id. An alias (`mistral-large-latest`) is accepted but not listed —
/// "using an alias may expose you to silent updates in model behavior and
/// pricing" (Mistral's model lifecycle page), and the model id is the arch
/// identity.
pub const DEFAULT_MODEL: &str = "mistral-large-2512";

/// The keyring user this arch's key lives under: `vk`/`mistral`.
pub const KEYRING_USER: &str = "mistral";

/// The ceiling for a model with no row in [`MODELS`]: the smallest window a
/// listed model has. Guessing low costs only a prompt projected further than
/// it had to be.
pub const FALLBACK_CONTEXT: u32 = 128_000;

/// What the EU regional endpoint costs over the standard list: "billed at
/// 1.1× standard list pricing (a 10% upcharge)".
pub const REGIONAL_UPCHARGE: f64 = 1.1;

/// One model, as Mistral documents it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Model {
    /// The API model id, exactly as it is sent — a pinned one, not an alias.
    pub id: &'static str,
    /// The documented context window, in tokens — the I4′ ceiling.
    pub context_window: u32,
    /// Standard list price for input tokens, USD per million (before the
    /// regional upcharge).
    pub input_usd_per_mtok: f64,
    /// Standard list price for output tokens, USD per million.
    pub output_usd_per_mtok: f64,
    /// Order-of-magnitude p50 latency for a ~500-token answer, in ms. Not
    /// measured — there is no key on this machine — and outside the identity.
    pub latency_ms_p50: u32,
}

/// Every model this node will price.
///
/// **Source:** docs.mistral.ai/models (and each model's own page) and
/// docs.mistral.ai/inference/pricing, **read 2026-10-16**. Prices are the
/// standard list in USD per million tokens; the pricing page's EUR figures
/// render client-side and were not read. Windows are the documented "256k" and
/// "128k" read as 256 000 and 128 000 — the lower reading, so a prompt is
/// refused here before Mistral would refuse it.
///
/// Pinned ids only (see [`DEFAULT_MODEL`]). Left out: Magistral and Devstral,
/// deprecated with retirement dates already past (their reasoning moved to
/// `reasoning_effort` on the general models), and the older ids Mistral is
/// retiring. **Mistral Large 4** (`mistral-large-4-0`, public preview since
/// 2026-10-06) is listed at its **regular** price, $1.36 / $4.18 — a sale
/// price of $0.68 / $2.09 was on offer on the day the table was read, and a
/// table that recorded the sale would understate every call once it ends.
pub const MODELS: &[Model] = &[
    Model {
        id: "mistral-large-2512",
        context_window: 256_000,
        input_usd_per_mtok: 0.50,
        output_usd_per_mtok: 1.50,
        latency_ms_p50: 8_000,
    },
    Model {
        id: "mistral-large-4-0",
        context_window: 1_000_000,
        input_usd_per_mtok: 1.36,
        output_usd_per_mtok: 4.18,
        latency_ms_p50: 10_000,
    },
    Model {
        id: "mistral-medium-3-5",
        context_window: 256_000,
        input_usd_per_mtok: 1.50,
        output_usd_per_mtok: 7.50,
        latency_ms_p50: 8_000,
    },
    Model {
        id: "mistral-small-2603",
        context_window: 256_000,
        input_usd_per_mtok: 0.15,
        output_usd_per_mtok: 0.60,
        latency_ms_p50: 4_000,
    },
    Model {
        id: "ministral-14b-2512",
        context_window: 256_000,
        input_usd_per_mtok: 0.20,
        output_usd_per_mtok: 0.20,
        latency_ms_p50: 3_000,
    },
    Model {
        id: "ministral-8b-2512",
        context_window: 256_000,
        input_usd_per_mtok: 0.15,
        output_usd_per_mtok: 0.15,
        latency_ms_p50: 2_500,
    },
    Model {
        id: "ministral-3b-2512",
        context_window: 256_000,
        input_usd_per_mtok: 0.10,
        output_usd_per_mtok: 0.10,
        latency_ms_p50: 2_000,
    },
    Model {
        id: "codestral-2508",
        context_window: 128_000,
        input_usd_per_mtok: 0.30,
        output_usd_per_mtok: 0.90,
        latency_ms_p50: 4_000,
    },
];

/// The table's row for `id`, or `None` for a model this node has no numbers
/// for — which mounts, with no price claimed and the fallback ceiling.
pub fn model(id: &str) -> Option<&'static Model> {
    MODELS.iter().find(|m| m.id == id)
}

/// The documented window for `id`, or [`FALLBACK_CONTEXT`].
pub fn context_window(id: &str) -> u32 {
    model(id).map_or(FALLBACK_CONTEXT, |m| m.context_window)
}

/// What one call costs on the EU endpoint: the standard list price times the
/// regional upcharge, or `None` for an unlisted model.
pub fn price(id: &str) -> Option<Price> {
    model(id).map(|m| Price {
        input_usd_per_mtok: m.input_usd_per_mtok * REGIONAL_UPCHARGE,
        output_usd_per_mtok: m.output_usd_per_mtok * REGIONAL_UPCHARGE,
    })
}

/// The manifest's price tag — input tokens, per thousand, in euros, at the
/// node's one pinned rate ([`vk_arch_openai_compat::EUR_PER_USD`]) — or
/// `None`, never `0.0`, for an unlisted model (Ruling 30).
pub fn cost_per_1k_tokens_eur(id: &str) -> Option<f64> {
    price(id).map(|p| p.input_usd_per_mtok / 1_000.0 * vk_arch_openai_compat::EUR_PER_USD)
}

/// Everything `arch.mount { kind: "mistral" }` may say. Not the key.
#[derive(Debug, Clone, PartialEq)]
pub struct MistralConfig {
    pub model: String,
    /// The daemon's origin: [`DEFAULT_BASE_URL`] on every real node. In the
    /// identity, so an arch answered by a stand-in is visibly not this one.
    pub base_url: String,
    /// The mount's own bound on one answer, capped at [`MAX_OUTPUT_TOKENS`].
    pub max_tokens: u32,
    /// The I4′ ceiling; `0` is the model's documented window.
    pub context_ceiling: u32,
    /// How long one call may take.
    pub timeout: Duration,
}

impl Default for MistralConfig {
    fn default() -> MistralConfig {
        MistralConfig {
            model: DEFAULT_MODEL.into(),
            base_url: DEFAULT_BASE_URL.into(),
            max_tokens: MAX_OUTPUT_TOKENS,
            context_ceiling: 0,
            timeout: Duration::from_secs(600),
        }
    }
}

impl MistralConfig {
    fn ceiling(&self) -> u32 {
        if self.context_ceiling > 0 {
            self.context_ceiling
        } else {
            context_window(&self.model)
        }
    }
}

/// The `mistral` arch: the chat-completions wire under Mistral's manifest.
pub struct MistralAdapter(ChatArch);

impl MistralAdapter {
    /// The arch, or the reason there is none: an origin this node will not
    /// send a key to, or a TLS stack that will not start.
    pub fn with_config(key: SecretString, cfg: MistralConfig) -> Result<MistralAdapter> {
        let manifest = Self::manifest_for(&cfg);
        let settings = ChatSettings {
            provider: "the Mistral API",
            price: price(&cfg.model),
            base_url: cfg.base_url,
            model: cfg.model,
            max_tokens: cfg.max_tokens,
            timeout: cfg.timeout,
            user_agent: "verticalai-vk-arch-mistral",
        };
        Ok(MistralAdapter(ChatArch::new(
            manifest,
            settings,
            Some(key),
        )?))
    }

    /// The manifest for this configuration. Only `ArchIdentity` is hashed
    /// into the arch id: the model, the wire, the origin. Price and latency
    /// sit outside it.
    pub fn manifest_for(cfg: &MistralConfig) -> ArchManifest {
        ArchManifest {
            name: format!("mistral/{}", cfg.model),
            capabilities: [Capability::Generate, Capability::Plan, Capability::Judge].into(),
            locality: Locality::Cloud,
            // The EU regional endpoint: EU and EFTA data centres (see the
            // crate documentation for what the rounding leaves out).
            jurisdiction: "EU".into(),
            // Thirty rolling days for abuse monitoring, per the privacy policy.
            retention_days: Some(30),
            cost_per_1k_tokens_eur: cost_per_1k_tokens_eur(&cfg.model),
            latency_ms_p50: model(&cfg.model).map_or(10_000, |m| m.latency_ms_p50),
            context_ceiling: cfg.ceiling(),
            determinism: Determinism::NonDeterministic,
            identity: ArchIdentity {
                weights_sha256: format!("model:{}", cfg.model),
                engine: "mistral-chat-completions".into(),
                engine_version: "v1".into(),
                backend: "mistral-eu".into(),
                quant: "-".into(),
                kv_cache: "-".into(),
                threads: 1,
                batch: 1,
                sampling: [
                    // No `reasoning_effort` is sent: a model that takes none
                    // (Ministral, Codestral) is not something to guess about,
                    // so every model answers at its own default — and the
                    // identity says so rather than leaving it implicit.
                    (
                        "reasoning_effort".to_string(),
                        "provider-default".to_string(),
                    ),
                    ("base_url".to_string(), cfg.base_url.clone()),
                ]
                .into_iter()
                .collect(),
                seed: None,
            },
            clearance: Clearance {
                max_scope: Scope::Business,
                third_party_allowed: false,
            },
            governed: false,
        }
    }
}

impl ArchAdapter for MistralAdapter {
    fn manifest(&self) -> &ArchManifest {
        self.0.manifest()
    }
    fn context_budget(&self) -> u32 {
        self.0.context_budget()
    }
    fn count_tokens(&self, text: &str) -> u32 {
        self.0.count_tokens(text)
    }
    fn complete(&self, prompt: &str, max_tokens: u32) -> Result<Completion, AdapterError> {
        self.0.complete(prompt, max_tokens)
    }
}
