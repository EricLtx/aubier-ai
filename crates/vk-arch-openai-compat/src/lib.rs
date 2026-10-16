//! Any **OpenAI-compatible** chat-completions server as an arch — Mammouth,
//! llama-server, vLLM, anything that answers `POST /v1/chat/completions` —
//! and the base the other API arches of this family are built on.
//!
//! This is the compatibility arch: the one a node mounts when the provider
//! it wants has no adapter of its own but speaks the API everybody copied.
//! What it cannot do is know anything about that provider, so everything an
//! adapter with a table would read off it is **the mount's to state**:
//!
//! * the **origin** is daemon configuration — `vkd --openai-compat-base-url`,
//!   one per daemon, never a request field — bounded by the rule every API
//!   arch is held to ([`check_base_url`]): `https://`, or plaintext to a
//!   loopback *address* and nowhere else;
//! * the **model id** and the **context window** (`vk mount openai-compat
//!   --model … --ctx …`), because no table here could be authoritative for a
//!   server it has never seen;
//! * the **jurisdiction** and the **retention window**, because those are
//!   claims about a third party and a manifest exists to carry them where I2
//!   can read them. There is no default for either: an arch that would not
//!   say where its far end is, or how long it keeps what it is sent, is
//!   refused at mount ([`check_jurisdiction`]) rather than mounted with a
//!   blank where the claim should be.
//!
//! ## What is honest about it
//!
//! It is never **governed**: this kernel did not launch the server and does
//! not contain it, even when it runs on this machine. `locality: Cloud`,
//! except for a server on a loopback **address**, which is `Local` — the
//! socket goes nowhere, whatever the server then does. Clearance stops at
//! `Business` and third-party data is refused, on loopback as well: the arch
//! cannot see behind the socket, and a loopback relay that forwards to a
//! cloud looks exactly like a local llama-server from here. No price is
//! claimed (`cost_per_1k_tokens_eur: None`, printed `?`), because this node
//! has no price list for a server it cannot name.
//!
//! The jurisdiction, the retention and the context window the operator states
//! are **in the arch identity** (alongside the origin and the model), so a
//! corrected claim is a different arch rather than a quiet rewrite of an old
//! one.
//!
//! ## The key
//!
//! Service `vk`, user `openai-compat`, put there by `vk secret set
//! openai-compat` — the same keyring, the same [`SecretString`] and the same
//! scrub as the Anthropic arch, shared rather than copied. **Optional on
//! loopback only**: a llama-server on `127.0.0.1` is commonly run without one,
//! so a missing entry there means no `Authorization` header; anywhere else a
//! missing key refuses the mount and names the verb that fixes it.
//!
//! ## I4′
//!
//! There is no count endpoint on this wire, so the pre-check is the
//! `bytes/3 + 64` estimate against nine tenths of the stated window, and the
//! post-check is the answer's `usage.prompt_tokens` against the whole of it
//! ([`chat`]). Both are only as good as `--ctx`: llama-server and vLLM refuse
//! an oversize prompt rather than truncate it, but a server that truncates
//! silently (Ollama's own `/v1` does) at a window smaller than the one stated
//! would report a `prompt_tokens` the post-check cannot tell from an honest
//! one. State the window the server was started with.
pub mod chat;
pub mod http;

use anyhow::{anyhow, bail, ensure, Context, Result};
use std::time::Duration;
use vk_contracts::arch::{ArchIdentity, ArchManifest, Capability, Determinism, Locality};
use vk_contracts::labels::{Clearance, Scope};
use vk_kernel::arch::{AdapterError, ArchAdapter, Completion};

pub use chat::{ChatArch, ChatSettings, Price, CHAT_PATH};
pub use http::{classify_status, HttpFailure, MAX_OUTPUT_TOKENS};
/// The key plumbing and the origin rule, shared with the Anthropic arch.
pub use vk_arch_anthropic::{
    check_base_url, load_key, KeySource, SecretString, EUR_PER_USD, KEYRING_SERVICE,
};

/// The keyring user this arch's key lives under: `vk`/`openai-compat`.
pub const KEYRING_USER: &str = "openai-compat";

/// The largest window a mount may state. Not a model's — the operator states
/// the model's — but a bound on the claim: the stated window is in the arch
/// identity, so an absurd one is at least its own arch, but a
/// four-billion-token window would still leave I4′ bounding nothing at all.
/// Ten million is past every published window.
pub const MAX_STATED_CONTEXT: u32 = 10_000_000;

/// How long one call may take unless the mount says otherwise. Generous: a
/// local server on a CPU, or a reasoning model, can take minutes. A backstop
/// against a call that has stopped answering, not a budget.
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(600);

// --------------------------------------------------------------- the config

/// Everything `arch.mount { kind: "openai-compat" }` makes an arch out of.
/// Note what is not here: the key.
#[derive(Debug, Clone, PartialEq)]
pub struct OpenAiCompatConfig {
    /// The daemon's `--openai-compat-base-url`: the origin, and any path
    /// prefix in front of `/v1/chat/completions` — `https://api.mammouth.ai`,
    /// `http://127.0.0.1:8080`, never with `/v1` on the end.
    pub base_url: String,
    /// The model id, exactly as the server names it.
    pub model: String,
    /// The window the server really has, as the operator states it.
    pub context_ceiling: u32,
    /// Where the server is: an ISO 3166-1 alpha-2 code, `EU`, or — for a
    /// server on this machine's loopback — `local`.
    pub jurisdiction: String,
    /// How many days the far end keeps what it is sent, as its operator
    /// publishes it. `0` is a claim too: it keeps nothing.
    pub retention_days: u32,
    /// The mount's own bound on one answer, capped at [`MAX_OUTPUT_TOKENS`].
    pub max_tokens: u32,
    /// How long one call may take.
    pub timeout: Duration,
}

impl OpenAiCompatConfig {
    /// The four claims and the origin, with the ordinary bounds.
    pub fn new(
        base_url: &str,
        model: &str,
        context_ceiling: u32,
        jurisdiction: &str,
        retention_days: u32,
    ) -> OpenAiCompatConfig {
        OpenAiCompatConfig {
            base_url: base_url.to_string(),
            model: model.to_string(),
            context_ceiling,
            jurisdiction: jurisdiction.to_string(),
            retention_days,
            max_tokens: MAX_OUTPUT_TOKENS,
            timeout: DEFAULT_TIMEOUT,
        }
    }

    /// Every claim this config makes, checked: the origin, the model, the
    /// window and the jurisdiction.
    pub fn check(&self) -> Result<()> {
        check_compat_base_url(&self.base_url)?;
        ensure!(
            !self.model.trim().is_empty(),
            "openai-compat: name the model the server serves (--model)"
        );
        ensure!(
            self.context_ceiling > 0,
            "openai-compat: state the context window the server was started with (--ctx); \
             no table here can know it"
        );
        ensure!(
            self.context_ceiling <= MAX_STATED_CONTEXT,
            "openai-compat: a context window of {} tokens is past every published one \
             ({MAX_STATED_CONTEXT} is the most this node accepts)",
            self.context_ceiling
        );
        check_max_tokens(self.max_tokens)?;
        check_jurisdiction(&self.jurisdiction, on_loopback(&self.base_url))
    }
}

/// Is this origin a loopback **address** — `127.0.0.0/8` or `::1`? Not the
/// name `localhost`, which is whatever `hosts` says today.
pub fn on_loopback(base_url: &str) -> bool {
    matches!(
        vk_kernel::arch::origin_loopback(base_url),
        Ok((_, Some(vk_kernel::arch::Loopback::Address)))
    )
}

/// `vkd --openai-compat-base-url`'s rule: the one every API arch is held to
/// ([`check_base_url`]), and no `/v1` on the end — this adapter appends
/// `/v1/chat/completions` itself, so a base URL copied from an OpenAI SDK
/// example (`…/v1`) would post to `…/v1/v1/chat/completions` and 404 on every
/// call. Refused where it is configured, with the fix in the message.
pub fn check_compat_base_url(raw: &str) -> Result<()> {
    check_base_url(raw)?;
    let trimmed = raw.trim_end_matches('/');
    ensure!(
        !trimmed.ends_with("/v1"),
        "{raw} ends in /v1: this adapter appends /v1/chat/completions itself, so give the \
         base without it ({})",
        trimmed.trim_end_matches("/v1")
    );
    let (url, _) = vk_kernel::arch::origin_loopback(raw)?;
    ensure!(
        url.query().is_none() && url.fragment().is_none(),
        "{raw} carries a query or a fragment, which a path is appended after"
    );
    Ok(())
}

/// Is `j` a claim a manifest can carry?
///
/// An ISO 3166-1 alpha-2 code in capitals (`FR`, `US`), `EU`, or `local` —
/// and `local` only for a server on this machine's loopback, since a server
/// anywhere else is in some country and the claim is to name it. Refused:
/// anything else, including `unknown`, an empty string, and ISO 3166's
/// **user-assigned** codes (`AA`, `QM`–`QZ`, `XA`–`XZ`, `ZZ`), which name no
/// country and are how "unknown" is usually spelt as a code.
pub fn check_jurisdiction(j: &str, loopback: bool) -> Result<()> {
    if j == "local" {
        ensure!(
            loopback,
            "jurisdiction local is for a server on this machine's loopback; this one is \
             not, so name the country it is in (FR, US, …) or EU"
        );
        return Ok(());
    }
    let b = j.as_bytes();
    if b.len() != 2 || !b.iter().all(u8::is_ascii_uppercase) {
        bail!(
            "jurisdiction {j:?} is not a claim: give an ISO 3166-1 alpha-2 country code in \
             capitals (FR, DE, US), EU, or local for a server on this machine's loopback"
        );
    }
    let user_assigned = j == "AA" || j == "ZZ" || (b[0] == b'Q' && b[1] >= b'M') || b[0] == b'X';
    ensure!(
        !user_assigned,
        "jurisdiction {j} is one of ISO 3166's user-assigned codes, which name no country \
         and usually stand for \"unknown\"; name the country the server is in"
    );
    Ok(())
}

/// A mount's answer bound, refused at `0` rather than clamped to 1 behind
/// the operator's back (review M-7): a bound of nothing is a mistake to name,
/// not a value to repair. Above [`MAX_OUTPUT_TOKENS`] it is capped, as on
/// every API arch, and the flag's help says so.
pub fn check_max_tokens(max_tokens: u32) -> Result<()> {
    ensure!(
        max_tokens > 0,
        "max_tokens 0 would bound every answer to nothing: give at least 1 (at most \
         {MAX_OUTPUT_TOKENS} is used)"
    );
    Ok(())
}

/// This arch's key, if it has one.
///
/// Off loopback the key is required and every failure names `vk secret set
/// openai-compat`. On a loopback address the keyring is read **once**, and
/// only **no entry** means no key and no `Authorization` header (review
/// M-3); any other answer — a locked keyring, a node with none, an empty
/// entry — is refused with the remedy, rather than turned into a keyless
/// call nobody chose. A file the operator named
/// (`vkd --openai-compat-key-file`) must be readable, since naming one says a
/// key is wanted; a node with no keyring that serves a keyless loopback
/// server names a file instead.
pub fn load_key_for(source: &KeySource, base_url: &str) -> Result<Option<SecretString>> {
    if !on_loopback(base_url) {
        return load_key(source, KEYRING_USER).map(Some);
    }
    match source {
        KeySource::File(_) => load_key(source, KEYRING_USER).map(Some),
        KeySource::Keyring => loopback_key(
            keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER).and_then(|e| e.get_password()),
        ),
    }
}

/// What the keyring's one answer means for a server on loopback.
fn loopback_key(answer: keyring::Result<String>) -> Result<Option<SecretString>> {
    let remedy = format!("set it with: vk secret set {KEYRING_USER}");
    // Wrapped the moment it exists: the copy `SecretString` makes wipes
    // itself, and this one is wiped when it goes.
    match answer.map(zeroize::Zeroizing::new) {
        Err(keyring::Error::NoEntry) => Ok(None),
        Ok(secret) if secret.trim().is_empty() => {
            bail!("the keyring entry {KEYRING_SERVICE}/{KEYRING_USER} is empty; {remedy}")
        }
        Ok(secret) => Ok(Some(SecretString::new(secret.trim()))),
        Err(e) => Err(anyhow!(e))
            .with_context(|| format!("cannot read {KEYRING_SERVICE}/{KEYRING_USER}; {remedy}")),
    }
}

// -------------------------------------------------------------- the adapter

/// The `openai-compat` arch: the chat-completions wire ([`ChatArch`]) under a
/// manifest made of the operator's claims.
pub struct OpenAiCompatAdapter(ChatArch);

impl OpenAiCompatAdapter {
    /// The arch, or the reason there is none: a claim this node will not
    /// carry, an origin it will not send a key to, or a key missing where one
    /// is needed.
    pub fn with_config(
        key: Option<SecretString>,
        cfg: OpenAiCompatConfig,
    ) -> Result<OpenAiCompatAdapter> {
        cfg.check()?;
        ensure!(
            key.is_some() || on_loopback(&cfg.base_url),
            "{} is not on this machine, and a server off it takes a key: set it with: vk \
             secret set {KEYRING_USER}",
            cfg.base_url
        );
        let manifest = Self::manifest_for(&cfg);
        let settings = ChatSettings {
            provider: "the OpenAI-compatible server",
            base_url: cfg.base_url,
            model: cfg.model,
            max_tokens: cfg.max_tokens,
            timeout: cfg.timeout,
            // No price list for a server this node cannot name: no price is
            // claimed, per call or in the manifest (Ruling 30).
            price: None,
            user_agent: "verticalai-vk-arch-openai-compat",
        };
        Ok(OpenAiCompatAdapter(ChatArch::new(manifest, settings, key)?))
    }

    /// The manifest for this configuration.
    pub fn manifest_for(cfg: &OpenAiCompatConfig) -> ArchManifest {
        let loopback = on_loopback(&cfg.base_url);
        ArchManifest {
            name: format!("openai-compat/{}", cfg.model),
            capabilities: [Capability::Generate, Capability::Plan, Capability::Judge].into(),
            // Where the socket goes, which is all this arch can see.
            locality: if loopback {
                Locality::Local
            } else {
                Locality::Cloud
            },
            jurisdiction: cfg.jurisdiction.clone(),
            retention_days: Some(cfg.retention_days),
            cost_per_1k_tokens_eur: None,
            // Nothing measured: an order of magnitude for a hosted model's
            // ~500-token answer. Outside the identity, so a measurement can
            // replace it without minting a new arch.
            latency_ms_p50: 10_000,
            context_ceiling: cfg.context_ceiling,
            determinism: Determinism::NonDeterministic,
            identity: ArchIdentity {
                weights_sha256: format!("model:{}", cfg.model),
                engine: "openai-chat-completions".into(),
                engine_version: "v1".into(),
                backend: "openai-compat".into(),
                quant: "-".into(),
                kv_cache: "-".into(),
                threads: 1,
                batch: 1,
                sampling: [
                    // Where the call goes: a mount pointed at another server
                    // is another arch.
                    ("base_url".to_string(), cfg.base_url.clone()),
                    // The operator's claims. A corrected claim is a new arch,
                    // not a rewrite of the old one's manifest under its id.
                    ("jurisdiction".to_string(), cfg.jurisdiction.clone()),
                    ("retention_days".to_string(), cfg.retention_days.to_string()),
                    ("context".to_string(), cfg.context_ceiling.to_string()),
                ]
                .into_iter()
                .collect(),
                seed: None,
            },
            clearance: Clearance {
                max_scope: Scope::Business,
                third_party_allowed: false,
            },
            // This kernel did not launch the server and does not contain it.
            governed: false,
        }
    }
}

impl ArchAdapter for OpenAiCompatAdapter {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_jurisdiction_is_a_country_the_union_or_this_machine() {
        for good in ["FR", "DE", "US", "EU", "CH"] {
            assert!(check_jurisdiction(good, false).is_ok(), "{good}");
        }
        assert!(check_jurisdiction("local", true).is_ok());
        for bad in [
            "", "unknown", "Unknown", "us", "FRA", "E U", "ZZ", "XX", "XK", "QM", "QZ", "AA",
        ] {
            assert!(check_jurisdiction(bad, false).is_err(), "{bad:?}");
        }
        // `local` is a claim only a loopback server can make.
        let err = check_jurisdiction("local", false).expect_err("off loopback");
        assert!(format!("{err:#}").contains("name the country"), "{err:#}");
    }

    #[test]
    fn a_base_url_is_held_to_the_api_rule_and_carries_no_v1() {
        for good in [
            "https://api.mammouth.ai",
            "https://openrouter.ai/api",
            "http://127.0.0.1:8080",
            "http://[::1]:8000/",
        ] {
            assert!(check_compat_base_url(good).is_ok(), "{good}");
        }
        for bad in [
            "http://192.168.1.10:8080",
            "http://localhost:8080",
            "https://api.mammouth.ai/v1",
            "http://127.0.0.1:8080/v1/",
            "https://api.example.com/?key=x",
            "ftp://api.example.com",
        ] {
            assert!(check_compat_base_url(bad).is_err(), "{bad}");
        }
        let err = check_compat_base_url("https://api.mammouth.ai/v1").expect_err("v1");
        assert!(
            format!("{err:#}").contains("(https://api.mammouth.ai)"),
            "say what to give instead: {err:#}"
        );
    }

    /// Review M-3: on loopback, only "no entry" means "no key". Every other
    /// answer from the keyring — a locked one, an empty entry — is the
    /// operator's to hear about, not a reason to call the server keyless.
    #[test]
    fn on_loopback_only_a_missing_entry_means_no_key() {
        assert!(loopback_key(Err(keyring::Error::NoEntry))
            .expect("no entry is no key")
            .is_none());
        let key = loopback_key(Ok(" sk-local 
"
        .to_string()))
        .expect("read")
        .expect("a key");
        assert_eq!(key.scrub("sk-local"), "[redacted]");
        for answer in [
            Err(keyring::Error::PlatformFailure(
                "the keyring is locked".into(),
            )),
            Err(keyring::Error::NoStorageAccess("no secret service".into())),
            Ok("   ".to_string()),
        ] {
            let err = loopback_key(answer).expect_err("not a missing entry");
            assert!(
                format!("{err:#}").contains("vk secret set openai-compat"),
                "{err:#}"
            );
        }
    }

    #[test]
    fn only_a_loopback_address_is_on_loopback() {
        assert!(on_loopback("http://127.0.0.1:8080"));
        assert!(on_loopback("http://[::1]:8080"));
        assert!(!on_loopback("http://localhost:8080"));
        assert!(!on_loopback("https://api.mammouth.ai"));
        assert!(!on_loopback("not a url"));
    }
}
