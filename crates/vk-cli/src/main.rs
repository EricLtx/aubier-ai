//! `vk`: the VerticalAI command-line client — the shell of this OS.
//!
//! Every verb is one syscall over the local endpoint, printed as a table or,
//! with `--json`, as the daemon's own answer — plus, on the three verbs that
//! resolve something for the reader (`task show`'s release paths, `approve`'s
//! subject, `boot`'s pid), the field the client worked out, under its own
//! name. The three human verbs (`stop`,
//! `resume`, `approve`) are the exception: a connection is a machine
//! principal, so those carry a presence proof signed by this node's device
//! key over a nonce the daemon just issued (spec §3.6, invariant I1). In SP1a
//! the interactive machine *is* the enrolled device; SP1b replaces the key
//! file with a passkey.
mod capital;
mod install;
mod man;
mod render;
mod until;

use anyhow::{anyhow, Context, Result};
use base64::Engine as _;
use clap::{Parser, Subcommand};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use vk_contracts::principal::{Approval, ApprovalKind, Challenge, HumanKey, Principal};
use vk_contracts::recipe::RecipeStep;
use vk_ipc::client::Client;
use vk_ipc::transport::{default_endpoint, Endpoint};
use vk_ipc::PresenceProof;
use vk_kernel::presence::{KeySource, NodeDevice};
use zeroize::Zeroizing;

/// `vkd --web-port`'s default, handed on by `vk boot`. A literal rather than
/// `vk_web::DEFAULT_PORT` so this shell does not link the verifier.
const DEFAULT_WEB_PORT: u16 = 7734;
/// How often `vk approve --passkey` asks whether the approval has landed.
const APPROVAL_POLL: Duration = Duration::from_millis(500);
/// A `--all` run drives one step per call; this bounds it so a task that never
/// settles is reported rather than looped on forever.
const MAX_STEPS: usize = 256;
/// How long `vk boot` waits for the daemon it started to answer.
const BOOT_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Parser)]
#[command(name = "vk", about = "VerticalAI kernel shell", version)]
struct Cli {
    /// Machine-readable output: the syscall's answer, unrendered.
    #[arg(long, global = true)]
    json: bool,
    /// Daemon endpoint (default: $VK_ENDPOINT, else this user's own).
    #[arg(long, global = true)]
    endpoint: Option<String>,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum LivenessCmd {
    /// Renew a business's liveness lease. Human: signed with this node's
    /// device key. The renewal is on the ledger as `liveness.renewed`.
    Renew {
        /// The business whose automatic runs the lease allows.
        #[arg(long)]
        business: String,
        /// Until when: a time from now (`+30d`, `+12h`, `+90m`) or an RFC 3339
        /// timestamp. At most thirty days ahead.
        #[arg(long, default_value = until::DEFAULT_UNTIL)]
        until: String,
    },
}

#[derive(Subcommand)]
enum Cmd {
    /// Start vkd over a state directory and wait until it answers.
    Boot(BootArgs),
    /// What this node is, and what its boot sequence found.
    Status,
    /// List a namespace path.
    Ls {
        #[arg(default_value = "/")]
        path: String,
    },
    /// Tasks and how far each has got.
    Ps,
    /// Per-arch calls and tokens, stopped scopes, liveness.
    Top {
        /// Also list every call behind those totals: which arch, which task's
        /// which step, what came back and what it cost.
        #[arg(long)]
        calls: bool,
    },
    /// Mount an arch, or a driver.
    Mount {
        #[command(subcommand)]
        what: MountCmd,
    },
    /// Unmount an arch by id.
    Umount { arch_id: String },
    /// One arch in full: its manifest, its identity tuple and its clearance.
    Arch {
        #[command(subcommand)]
        what: ArchCmd,
    },
    /// The drivers this node has mounted: the browsers a harness step may act
    /// through (`vk mount driver` mounts one).
    Driver {
        #[command(subcommand)]
        what: DriverCmd,
    },
    /// Capital packages: the publisher key that signs this account's driver
    /// packages (`vk driver pack` makes one).
    Capital {
        #[command(subcommand)]
        what: capital::CapitalCmd,
    },
    /// The artefacts a task attached: read one before you sign it.
    Artefact {
        #[command(subcommand)]
        what: ArtefactCmd,
    },
    /// Submit, step and inspect tasks.
    Task {
        #[command(subcommand)]
        what: TaskCmd,
    },
    /// Run a confined agent harness (Claude Code) over a task.
    Harness {
        #[command(subcommand)]
        what: HarnessCmd,
    },
    /// STOP a scope. Human: signed with this node's device key.
    Stop {
        #[arg(default_value = "node")]
        scope: String,
    },
    /// Lift a STOP by its id. Human.
    Resume { stop_id: String },
    /// The liveness lease: how long automation may run unattended.
    Liveness {
        #[command(subcommand)]
        what: LivenessCmd,
    },
    /// Approve a task's latest artefact. Human: signed with this node's
    /// device key, or — with --passkey — with a passkey in the browser.
    Approve {
        task_id: String,
        /// Approve with an enrolled passkey (Windows Hello, a phone): prints
        /// the approval page's link, then waits for the approval to land.
        #[arg(long)]
        passkey: bool,
        /// Open the link in the default browser as well as printing it.
        #[arg(long, requires = "passkey")]
        open: bool,
        /// How long to wait for the passkey approval, in seconds.
        #[arg(long, default_value_t = 300, requires = "passkey")]
        timeout: u64,
    },
    /// Secrets this node's daemon needs, in the OS keyring. Needs no daemon.
    Secret {
        #[command(subcommand)]
        what: SecretCmd,
    },
    /// Passkeys: the human's own device, enrolled through the browser.
    Passkey {
        #[command(subcommand)]
        what: PasskeyCmd,
    },
    /// The ledger tail.
    Dmesg {
        #[arg(short = 'n', long = "lines", default_value_t = 20)]
        n: u64,
    },
    /// The ledger itself.
    Ledger {
        #[command(subcommand)]
        what: LedgerCmd,
    },
    /// Verify the whole store: the chain, the recorded head, every blob
    /// against its own address, every wrapped key, and the mounts.
    ///
    /// `vk ledger verify` answers one question — does the chain recompute.
    /// This answers whether the state this node says it has is the state it
    /// has. Exits non-zero if any tier fails, so a script can gate on it.
    ///
    /// A human act, like `stop` and `approve`: it reads every blob on the
    /// node, so it is signed with this node's device key.
    Fsck {
        /// Re-record the ledger head from the record as it is on disk.
        ///
        /// **The recovery path after a legitimate restore**, and nothing
        /// else. A restored backup is a shorter record than the one this
        /// store last wrote, which is the same shape as a tail somebody cut
        /// — the node cannot tell them apart and refuses to serve on either.
        /// This is a human saying which it was, and it goes on the record:
        /// the next `boot` event's report names both heads.
        ///
        /// Needs `--force` and the word `rebase` typed at the prompt.
        #[arg(long, requires = "force")]
        rebase_head: bool,
        /// Required beside `--rebase-head`. On its own it does nothing.
        #[arg(long)]
        force: bool,
        /// Take the typed confirmation as given, for scripts. The ceremony
        /// is still a presence proof by this node's device key.
        #[arg(long, requires = "rebase_head")]
        yes: bool,
    },
    /// The contracts this kernel speaks: no name lists them, a name renders
    /// one. Needs no daemon — the schemas are in this binary.
    Man { name: Option<String> },
}

/// Where the daemon's state and keys come from. The same flags `vkd` takes,
/// with the same meaning, because `vk boot` only hands them on.
#[derive(clap::Args)]
struct BootArgs {
    /// State directory to serve (default: this user's local app data).
    #[arg(long)]
    state_dir: Option<PathBuf>,
    #[arg(long, default_value = "node-1")]
    node_id: String,
    /// Master key file, for tests and CI; without it the OS keyring holds it.
    #[arg(long)]
    master_key_file: Option<PathBuf>,
    /// Node device key file (default: $VK_NODE_KEY_FILE, else the keyring).
    #[arg(long)]
    node_key_file: Option<PathBuf>,
    /// Run the daemon in this terminal instead of detaching it.
    #[arg(long)]
    foreground: bool,
    /// Start the daemon even though the ledger chain does not verify.
    #[arg(long)]
    force: bool,
    /// Loopback port for the passkey pages (`0`: one the OS picks).
    #[arg(long, default_value_t = DEFAULT_WEB_PORT)]
    web_port: u16,
    /// The Claude Code executable the daemon's `claude-code` arches run,
    /// passed on as `vkd --claude-bin` and checked by the daemon when it
    /// starts (default: `claude` on the daemon's PATH).
    #[arg(long)]
    claude_bin: Option<PathBuf>,
}

#[derive(Subcommand)]
enum SecretCmd {
    /// Put a secret in this account's keyring, under service `vk`.
    ///
    /// `vk secret set anthropic` is the one an API arch needs — and
    /// `mistral`, `gemini` and `openai-compat` for theirs: the daemon reads
    /// it at `vk mount <kind>` and again whenever it re-creates that arch at
    /// boot. The value is prompted for without echo and is never printed,
    /// never logged and never written to a file — it does not go into the
    /// mount spec either, which is stored in the clear and refuses anything
    /// credential-shaped.
    ///
    /// The keyring is **this account's**. A daemon running under the Windows
    /// service account (`vkd --as-service`) reads that account's keyring, not
    /// this one, and is given its key with `vkd --anthropic-key-file` (and
    /// `--mistral-key-file`, `--gemini-key-file`, `--openai-compat-key-file`)
    /// or by running this verb as that account.
    Set {
        /// What it is called: `anthropic`, `mistral`, `gemini` or
        /// `openai-compat` — the API arch that reads it.
        name: String,
        /// Read the value as one line on stdin instead of prompting, for
        /// scripts. Still never echoed and never printed back.
        #[arg(long)]
        stdin: bool,
        /// The keyring service. `vk` for everything this kernel reads; the
        /// flag exists so a test can use a throwaway name.
        #[arg(long, default_value = "vk")]
        service: String,
    },
}

#[derive(Subcommand)]
enum PasskeyCmd {
    /// Print the enrolment page's link (and open it with --open): the
    /// browser asks Windows Hello, or a phone, to make the passkey.
    Enroll {
        /// Open the link in the default browser as well as printing it.
        #[arg(long)]
        open: bool,
    },
    /// The passkeys enrolled on this node.
    Ls,
}

#[derive(Subcommand)]
enum MountCmd {
    /// A deterministic mock arch (SP1b mounts real engines).
    Mock {
        name: String,
        /// Context ceiling in tokens.
        #[arg(long, default_value_t = 4096)]
        ctx: u32,
    },
    /// Claude, through the installed Claude Code and this machine's own
    /// subscription: two arches, one to draft with and one to judge with.
    ///
    /// A cloud arch, so its clearance stops at Business and it refuses
    /// third-party data; see `contracts/tcb.md`. Customer nodes use API arches
    /// instead — a subscription is a person's, not a product's.
    ///
    /// The `claude` it runs is the daemon's, not this verb's: `claude` on the
    /// daemon's PATH, or the executable the daemon was started with
    /// (`vkd --claude-bin <path>`, `vk boot --claude-bin <path>`), checked
    /// when the daemon starts.
    ClaudeCode {
        #[arg(long, default_value = "claude-sonnet-5")]
        draft_model: String,
        #[arg(long, default_value = "claude-opus-5")]
        judge_model: String,
        /// How long one call may take, in seconds.
        #[arg(long, default_value_t = 180)]
        timeout: u32,
    },
    /// Claude through the **Anthropic API**, on a key in this account's
    /// keyring (`vk secret set anthropic`). The arch a customer node mounts.
    ///
    /// A cloud arch in the United States: `jurisdiction: US`, 30-day
    /// retention, clearance capped at Business with third-party data refused,
    /// and metered — unlike `vk mount claude-code`, every call is billed, so
    /// the manifest carries a per-token price and `vk top` shows what the
    /// calls cost. For a register that may not leave the Union, mount
    /// `bedrock` instead.
    Anthropic {
        /// The model, as the API names it (`claude-opus-5`,
        /// `claude-sonnet-5`, `claude-haiku-4-5`).
        #[arg(long, default_value = "claude-opus-5")]
        model: String,
        /// Context ceiling in tokens. `0` is the model's documented window,
        /// which is what nearly every mount wants.
        #[arg(long, default_value_t = 0)]
        ctx: u32,
        /// The longest answer one call may produce. Capped at 4096.
        #[arg(long, default_value_t = 4096)]
        max_tokens: u32,
        /// How long one call may take, in seconds.
        #[arg(long, default_value_t = 600)]
        timeout: u32,
    },
    /// Claude **hosted in the EU**, through Amazon Bedrock's `Converse` API
    /// in Frankfurt or Ireland, on this machine's AWS credentials.
    ///
    /// The EU jurisdiction: `jurisdiction: EU` and no retention window,
    /// because AWS keeps neither the inputs nor the outputs. Needs a build
    /// with the `bedrock` cargo feature; a daemon without it refuses the
    /// mount and says so.
    Bedrock {
        /// Which European region. Only these two: the manifest's
        /// `jurisdiction: EU` is the region, so a mount elsewhere is refused
        /// rather than mislabelled.
        #[arg(long, default_value = "eu-central-1",
              value_parser = ["eu-central-1", "eu-west-1"])]
        region: String,
        /// A model name (`claude-opus-5`), which is resolved to the region's
        /// cross-region inference profile, or a full Bedrock model id or
        /// profile ARN, which is used exactly as given.
        #[arg(long, default_value = "claude-opus-5")]
        model: String,
        /// Context ceiling in tokens. `0` is the model's documented window.
        #[arg(long, default_value_t = 0)]
        ctx: u32,
        /// The longest answer one call may produce. Capped at 4096.
        #[arg(long, default_value_t = 4096)]
        max_tokens: u32,
    },
    /// Mistral's **native API on its EU endpoint** (`api.eu.mistral.ai`), on a
    /// key in this account's keyring (`vk secret set mistral`).
    ///
    /// `jurisdiction: EU` — Mistral's EU and EFTA data centres — 30-day
    /// retention for abuse monitoring, clearance capped at Business with
    /// third-party data refused, metered at the EU endpoint's price (the
    /// standard list plus 10%).
    Mistral {
        /// A pinned model id (`mistral-large-2512`, `mistral-medium-3-5`,
        /// `mistral-small-2603`). An alias mounts, unpriced.
        #[arg(long, default_value = "mistral-large-2512")]
        model: String,
        /// Context ceiling in tokens. `0` is the model's documented window.
        #[arg(long, default_value_t = 0)]
        ctx: u32,
        /// The longest answer one call may produce. Capped at 4096.
        #[arg(long, default_value_t = 4096)]
        max_tokens: u32,
        /// How long one call may take, in seconds.
        #[arg(long, default_value_t = 600)]
        timeout: u32,
    },
    /// Google's **native Gemini API**, on a key in this account's keyring
    /// (`vk secret set gemini`) — a billing-enabled one for anything a
    /// customer uses.
    ///
    /// `jurisdiction: US`, 55-day retention for abuse monitoring (the paid
    /// tier's), clearance capped at Business with third-party data refused,
    /// metered. The answer bound counts the model's thinking too.
    Gemini {
        /// The model id (`gemini-3.8-flash`, `gemini-4-argon`,
        /// `gemini-3.5-flash-lite`).
        #[arg(long, default_value = "gemini-3.8-flash")]
        model: String,
        /// Context ceiling in tokens. `0` is the model's documented window.
        #[arg(long, default_value_t = 0)]
        ctx: u32,
        /// The longest answer one call may produce, thinking included.
        /// Capped at 4096.
        #[arg(long, default_value_t = 4096)]
        max_tokens: u32,
        /// How long one call may take, in seconds.
        #[arg(long, default_value_t = 600)]
        timeout: u32,
    },
    /// Any **OpenAI-compatible** chat-completions server — Mammouth,
    /// llama-server, vLLM — at the one origin the daemon was started with
    /// (`vkd --openai-compat-base-url`), on a key in this account's keyring
    /// (`vk secret set openai-compat`; a server on a loopback address may do
    /// without one).
    ///
    /// No table can know a server it has never seen, so this mount **states
    /// the claims** a manifest carries: the model, its window, where the
    /// server is and how long it keeps what it is sent. All four are
    /// required, and part of the arch id. Never governed; `locality: cloud`
    /// unless the server is on a loopback address; clearance capped at
    /// Business with third-party data refused; no price claimed.
    OpenaiCompat {
        /// The model id, exactly as the server names it.
        #[arg(long)]
        model: String,
        /// The context window the server was started with, in tokens.
        #[arg(long)]
        ctx: u32,
        /// Where the server is: an ISO 3166-1 alpha-2 code (FR, DE, US), EU,
        /// or local for a server on this machine's loopback.
        #[arg(long)]
        jurisdiction: String,
        /// How many days the far end keeps what it is sent, as its operator
        /// publishes it; 0 if it keeps nothing.
        #[arg(long)]
        retention_days: u32,
        /// The longest answer one call may produce. Capped at 4096.
        #[arg(long, default_value_t = 4096)]
        max_tokens: u32,
        /// How long one call may take, in seconds.
        #[arg(long, default_value_t = 600)]
        timeout: u32,
    },
    /// A Gemma-class model on this machine, served by Ollama in a container
    /// this kernel starts, caps and can stop.
    ///
    /// The governed arch: the inference is a process the kernel contains, on
    /// loopback, under a memory and CPU cap, so nothing about the call leaves
    /// this node. Its context ceiling is half the window asked for — Ollama
    /// silently truncates past that (spike 1a) and this node refuses instead.
    Ollama {
        /// The model tag, as Ollama names it (`gemma4:e4b`, `gemma3:1b`).
        #[arg(long)]
        model: String,
        /// Run it in a container this kernel starts and caps. The default.
        #[arg(long, conflicts_with = "external")]
        container: bool,
        /// Use an Ollama already serving at this URL instead. Not governed:
        /// this node did not start that process and cannot cap it, so this
        /// takes a presence proof. Loopback only (127.0.0.1, ::1, localhost).
        #[arg(long, value_name = "URL")]
        external: Option<String>,
        /// Context window asked of the server. Half of it is usable.
        #[arg(long, default_value_t = 8192)]
        ctx: u32,
        /// The longest answer one call may produce. Always sent: a local model
        /// with no bound runs until it decides to stop.
        #[arg(long, default_value_t = 2048)]
        max_tokens: u32,
        /// Replace an existing container that is not the one asked for. Stops
        /// it, removes it and makes it again under these caps; the volume, and
        /// the models in it, are kept.
        #[arg(long, conflicts_with = "external")]
        recreate: bool,
        // The three container flags are refused beside `--external`, rather
        // than ignored: a cap named for a server this node does not start is
        // a cap that would never be applied, and silently dropping it is how
        // somebody ends up believing an ungoverned arch is capped.
        /// Memory cap for the container: digits, then optionally k, m or g.
        #[arg(long, default_value = "12g", conflicts_with = "external")]
        memory: String,
        /// CPU cap for the container: a whole or decimal number.
        #[arg(long, default_value = "6", conflicts_with = "external")]
        cpus: String,
        /// The Ollama image. Pinned: the version is in the arch identity, and
        /// the daemon refuses any other image (it may only be repeated).
        #[arg(
            long,
            default_value = "ollama/ollama:0.33.3",
            conflicts_with = "external"
        )]
        image: String,
        /// Sampling seed. Part of the arch identity.
        #[arg(long, default_value_t = 7)]
        seed: u64,
    },
    /// A driver: a browser behind a process this kernel starts from a
    /// manifest, caps, and lets a harness step act through.
    ///
    /// A human act: signed with this node's device key, because it puts a
    /// browser under this node's control. Either an installed driver by
    /// NAME@VERSION — the tree `vk driver install` laid out, checked against
    /// its package's signature and fingerprint — or a development one by
    /// `--manifest`, whose command is resolved against the manifest file's
    /// own directory and whose files must hash to its `files_sha256`. The
    /// process is started now; `vk driver ls` says whether the kernel
    /// governs it.
    Driver {
        /// An installed driver, as `vk driver ls` lists it: `maskai-1@1.0.0`.
        #[arg(
            value_name = "NAME@VERSION",
            conflicts_with = "manifest",
            required_unless_present = "manifest"
        )]
        installed: Option<String>,
        /// A development driver's manifest (JSON), next to its files.
        #[arg(long, value_name = "FILE")]
        manifest: Option<PathBuf>,
    },
}

#[derive(Subcommand)]
enum DriverCmd {
    /// The mounted drivers and the installed ones: state, governance,
    /// source, version and actions.
    Ls,
    /// One driver in full: its identity, state, hosts and actions.
    Show { name: String },
    /// Unmount a driver by name and stop its process, even mid-action.
    ///
    /// A human act, as the mount is: signed with this node's device key,
    /// because it ends the browser session the driver holds.
    Umount { name: String },
    /// Pack a development driver into a signed package (an OCI image
    /// layout) that `vk driver install` installs on any node: its files,
    /// the pinned CPython, the wheels of its `requirements.txt`, locked.
    ///
    /// A human act: signed with this node's device key, and the package with
    /// this account's publisher key (`vk capital key init`). The daemon
    /// does the work — a download on first use, pip, a whole tree hashed —
    /// and records `driver.packed`.
    Pack(capital::PackArgs),
    /// Install a package `vk driver pack` wrote, under the install root
    /// (`%ProgramFiles%\Aubier\drivers\<name>\<version>`): from an elevated
    /// shell, offline, off the ledger, no daemon needed.
    ///
    /// Every digest of the package and its publisher's signature are
    /// checked; a publisher this node has not pinned is shown — key id and
    /// public key — and pinned only if you confirm it (`--yes` confirms,
    /// for CI). The tree is built, held to the fingerprint the publisher
    /// packed, made read-only to everyone but the administrators, and only
    /// then put in place. `vk mount driver NAME@VERSION` mounts it.
    Install(install::InstallArgs),
    /// Remove an installed version, from an elevated shell. Refused while
    /// the daemon reports it mounted: `vk driver umount NAME` first.
    Uninstall(install::UninstallArgs),
    /// Learn a driver from a corpus of the firm's documents (spec §4.10): the
    /// type library's catalogue, the binding, the mined invariants, a plan, a
    /// generated driver under `--out/driver`. A human act: signed with this
    /// node's device key; `driver.learned` goes on the ledger.
    Learn {
        #[arg(long)]
        family: String,
        #[arg(long)]
        app: String,
        #[arg(long, value_name = "DIR", required_unless_present = "deliberate")]
        corpus: Option<PathBuf>,
        /// A new directory; its name is the driver's (word-maison). With
        /// `--deliberate`, an earlier learning's output instead.
        #[arg(long, value_name = "DIR")]
        out: PathBuf,
        #[arg(long, default_value_t = 0.8)]
        threshold: f64,
        /// The arch that may choose among an ambiguous invariant's candidates, by
        /// name or id; without it every ambiguity is purged, and no model is called.
        #[arg(long, value_name = "ARCH")]
        arch: Option<String>,
        /// The fallback for a verb the application could not bind: the arch drafts a
        /// projection rule for VERB into `--out/proposals/VERB.json`. It proposes and
        /// applies nothing; a human versions a rule into the ontology. Needs `--arch`.
        #[arg(long, value_name = "VERB", requires = "arch")]
        deliberate: Option<String>,
    },
    /// Replay a learned driver on copies of its corpus; `driver.qualified`
    /// records the result, and a `com` driver mounts only when it meets its
    /// plan's thresholds.
    Qualify {
        #[arg(long, value_name = "DIR")]
        driver: PathBuf,
        #[arg(long, value_name = "DIR")]
        corpus: PathBuf,
    },
    /// A learned driver's plan, for review before `vk driver qualify`.
    Plan {
        #[command(subcommand)]
        what: PlanCmd,
    },
}

#[derive(Subcommand)]
enum PlanCmd {
    /// The actions of the plan `vk driver learn` wrote into DIR (its
    /// `--out`), step by step, its ambiguities and how each was settled, and
    /// the invariants it left out.
    Show {
        /// The directory `vk driver learn --out` wrote.
        dir: PathBuf,
    },
}

#[derive(Subcommand)]
enum ArtefactCmd {
    /// Print an artefact's bytes: to stdout when they are UTF-8 text, to
    /// `--out FILE` otherwise. Human: signed with this node's device key,
    /// because it takes plaintext out of the encrypted store (Ruling R22).
    /// `vk task show TASK --json` lists a task's artefacts, hash and kind;
    /// the last one is what its approve step signs.
    Cat {
        /// The artefact's address: `sha256:` and 64 hex digits (the prefix
        /// may be left out).
        hash: String,
        /// Write the bytes to this file instead of printing them.
        #[arg(long, value_name = "FILE")]
        out: Option<PathBuf>,
    },
}

#[derive(Subcommand)]
enum TaskCmd {
    /// Submit a task: plan, then draft (or a harness), then optionally judge,
    /// wait for a human and release — or, with `--steps`, the task a recipe
    /// file describes.
    Submit {
        #[arg(long, required_unless_present = "steps")]
        goal: Option<String>,
        /// The kind of artefact the draft produces (its file extension on release).
        #[arg(long, default_value = "note")]
        artefact: String,
        #[arg(long, value_name = "ARCH", required_unless_present = "steps")]
        plan: Option<String>,
        /// The arch that drafts the artefact. Either this or `--harness`: a
        /// task needs exactly one step that produces what is approved.
        #[arg(
            long,
            value_name = "ARCH",
            required_unless_present_any = ["harness", "steps"]
        )]
        draft: Option<String>,
        /// Draft with a confined agent harness instead of an arch, by name
        /// (`claude-code`). The step is run by `vk harness run <TASK>`, not by
        /// `vk task step`, which says so when it reaches one.
        #[arg(long, value_name = "NAME", conflicts_with = "draft")]
        harness: Option<String>,
        /// Have this arch judge the draft. Its verdict is raised into the
        /// register's open questions, where a later step — or a reader — sees
        /// it beside the artefact it is about.
        #[arg(long, value_name = "ARCH")]
        judge: Option<String>,
        /// Wait for a human approval of the drafted artefact.
        #[arg(long)]
        approve: bool,
        /// Release the artefacts into this subpath of the kernel's export root.
        #[arg(long, value_name = "DIR")]
        release: Option<String>,
        /// A task recipe (JSON: goal, artefact_kind, constraints, evidence,
        /// steps), checked here and posted whole. The recipe is the whole
        /// task, so no other submit flag may be given beside it. A step's
        /// `arch_id` is an id (`sha256:…`) or the manifest name of a mounted
        /// arch (`vk ls /arches`), resolved here to the one arch carrying it.
        #[arg(
            long,
            value_name = "FILE",
            conflicts_with_all = [
                "goal", "artefact", "plan", "draft", "harness", "judge", "approve", "release"
            ]
        )]
        steps: Option<PathBuf>,
    },
    /// Run the next step, or with --all until the task waits or finishes.
    Step {
        task_id: String,
        #[arg(long)]
        all: bool,
    },
    /// One task in full, with where its release steps write.
    Show { task_id: String },
}

#[derive(Subcommand)]
enum HarnessCmd {
    /// Launch the confined harness over a task's harness step.
    ///
    /// Give an existing TASK whose next step is a harness step, or `--goal` to
    /// create a task (harness step, then a human approval) and run it.
    /// `--dry-run` prints the launch line, the `mcp.json` (token redacted) and
    /// the permission fence, and runs nothing. Which binary, model and timeout
    /// the harness runs with is the daemon's to set (`vkd --harness-bin`,
    /// `--harness-model`, `--harness-timeout-secs`), never this verb's.
    Run {
        /// The task to run. Omit it and pass `--goal` to create one.
        task: Option<String>,
        /// Create a fresh task with this goal — a harness step then an
        /// approval — and run its harness step.
        #[arg(long)]
        goal: Option<String>,
        /// Which harness. Only `claude-code` in SP1b.
        #[arg(long, default_value = "claude-code")]
        name: String,
        /// Print the launch line, `mcp.json` and `settings.json` and exit,
        /// without running.
        #[arg(long)]
        dry_run: bool,
        /// Keep the workspace after the run instead of removing it (it is
        /// swept a day later by the daemon's retention sweep, and at the
        /// daemon's next boot, whichever comes first).
        #[arg(long)]
        keep: bool,
    },
}

#[derive(Subcommand)]
enum ArchCmd {
    /// One arch in full, by id: what it is, whether it would answer a prompt,
    /// the identity tuple the arch id hashes, the clearance it may be handed,
    /// and what the next boot would make it from.
    Show { arch_id: String },
}

#[derive(Subcommand)]
enum LedgerCmd {
    /// Verify the hash chain.
    Verify,
}

fn main() {
    let cli = Cli::parse();
    if let Err(e) = run(&cli) {
        // One line, no backtrace and no panic: a refused syscall is an answer,
        // not a crash. Usage errors exit 2, and clap has already printed them.
        eprintln!("vk: {e:#}");
        std::process::exit(1);
    }
}

fn run(cli: &Cli) -> Result<()> {
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    match &cli.cmd {
        Cmd::Boot(args) => boot(cli, &rt, args),
        Cmd::Man { name } => man(cli, name.as_deref()),
        // The keyring is this account's, not the daemon's to reach into: the
        // shell writes it directly and no syscall is involved.
        Cmd::Secret { what } => secret(cli, what),
        // An OS act by an administrator, off the ledger: the shell does it,
        // and asks the daemon at most what is mounted.
        Cmd::Driver {
            what: DriverCmd::Install(args),
        } => install::install(cli, args),
        Cmd::Driver {
            what: DriverCmd::Uninstall(args),
        } => rt.block_on(install::uninstall(cli, args)),
        // The pinned release, fetched with the pack's own code for CI: no
        // daemon, nothing on the ledger.
        Cmd::Capital {
            what:
                capital::CapitalCmd::Python {
                    what: capital::PythonCmd::Fetch { cache, file },
                },
        } => {
            let got = capital::python_fetch(cache, file)?;
            println!("{}", plain_path(&got));
            Ok(())
        }
        _ => rt.block_on(call(cli)),
    }
}

/// `vk man`, which answers out of the binary: a contract is readable on a
/// machine whose daemon will not start, which is exactly when somebody needs
/// to read one.
fn man(cli: &Cli, name: Option<&str>) -> Result<()> {
    match name {
        None if cli.json => println!("{}", serde_json::to_string_pretty(&man::names())?),
        None => println!("{}", man::list()),
        // `--json` is the schema itself, byte for byte: what a generator or a
        // validator wants, where the rendered page is what a person wants.
        Some(n) if cli.json => print!("{}", man::schema(n).ok_or_else(|| man::unknown(n))?),
        Some(n) => println!("{}", man::render(n)?),
    }
    Ok(())
}

/// `$VK_ENDPOINT` or `--endpoint`, when either says where the daemon is.
fn endpoint_override(cli: &Cli) -> Option<String> {
    cli.endpoint
        .clone()
        .or_else(|| std::env::var("VK_ENDPOINT").ok())
}

fn endpoint(cli: &Cli) -> Endpoint {
    endpoint_override(cli)
        .map(Endpoint)
        .unwrap_or_else(default_endpoint)
}

/// `vk secret set NAME` — the one verb that writes a credential, and the only
/// place in this shell that handles one.
///
/// Three rules, all of them testable by reading this function: the value is
/// never echoed (the terminal's echo is off while it is typed), never printed
/// (what is reported back is the name, never the value) and never written
/// anywhere but the keyring. An empty value is refused rather than stored,
/// because an empty entry is worse than a missing one: `vk mount anthropic`
/// would find something and fail at the far end instead of here.
fn secret(cli: &Cli, what: &SecretCmd) -> Result<()> {
    let SecretCmd::Set {
        name,
        stdin,
        service,
    } = what;
    let name = name.trim();
    if name.is_empty() || name.split_whitespace().count() != 1 {
        return Err(anyhow!(
            "a secret's name is one word with no spaces in it; `anthropic`, `mistral`, \
             `gemini` and `openai-compat` are the ones the API arches read"
        ));
    }
    // Wrapped the moment it exists and never copied out of the wrapper: this
    // is the one process on this machine that sees the key as a human typed
    // it, and a plain `String` would leave it in freed heap (fix round 1,
    // Minor 5).
    let value: Zeroizing<String> = Zeroizing::new(if *stdin {
        let mut line = Zeroizing::new(String::new());
        std::io::stdin()
            .read_line(&mut line)
            .context("read the secret from stdin")?;
        line.to_string()
    } else {
        rpassword::prompt_password(format!("{service}/{name}: "))
            .context("read the secret from the terminal")?
    });
    let value = value.trim();
    if value.is_empty() {
        return Err(anyhow!(
            "nothing was entered; the keyring was not touched. An empty entry is worse \
             than a missing one: the mount would find it and fail at the far end"
        ));
    }
    keyring::Entry::new(service, name)
        .and_then(|e| e.set_password(value))
        .with_context(|| format!("cannot write {service}/{name} to this account's keyring"))?;
    // The name, never the value.
    show(
        cli,
        json!({ "service": service, "name": name }),
        render::secret_set,
    )
}

fn show(cli: &Cli, v: Value, render: fn(&Value) -> String) -> Result<()> {
    if cli.json {
        println!("{}", serde_json::to_string_pretty(&v)?);
    } else {
        println!("{}", render(&v));
    }
    Ok(())
}

fn field(v: &Value, name: &str) -> Result<String> {
    v[name]
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| anyhow!("no {name} in the daemon's answer: {v}"))
}

async fn call(cli: &Cli) -> Result<()> {
    let ep = endpoint(cli);
    let c = Client::connect(&ep)
        .await
        .with_context(|| format!("no daemon on {} (start one with `vk boot`)", ep.0))?;
    match &cli.cmd {
        // Dispatched before the client connects: one starts a daemon rather
        // than calling one, the other needs none at all.
        Cmd::Boot(_)
        | Cmd::Man { .. }
        | Cmd::Secret { .. }
        | Cmd::Driver {
            what: DriverCmd::Install(_) | DriverCmd::Uninstall(_),
        }
        | Cmd::Capital {
            what: capital::CapitalCmd::Python { .. },
        } => unreachable!("not a syscall"),
        Cmd::Status => show(
            cli,
            c.call("boot.info", json!({}), None).await?,
            render::status,
        ),
        Cmd::Ls { path } => show(
            cli,
            c.call("ns.ls", json!({ "path": path }), None).await?,
            render::ls,
        ),
        Cmd::Ps => show(cli, c.call("task.ls", json!({}), None).await?, render::ps),
        Cmd::Top { calls } => show(
            cli,
            c.call("top", json!({ "calls": calls }), None).await?,
            render::top,
        ),
        Cmd::Mount {
            what: MountCmd::Mock { name, ctx },
        } => show(
            cli,
            c.call(
                "arch.mount_mock",
                json!({ "name": name, "context_ceiling": ctx }),
                None,
            )
            .await?,
            render::mounted,
        ),
        // Two mounts, one verb: a role needs a model behind it, and one call
        // that leaves the node with a draft arch and no judge arch would be a
        // node whose tasks stall at their second step.
        Cmd::Mount {
            what:
                MountCmd::ClaudeCode {
                    draft_model,
                    judge_model,
                    timeout,
                },
        } => {
            // A proof per call: a nonce is spent by the request that shows
            // it, so the second mount asks for one of its own (fix round 1,
            // Critical 1 — a cloud arch is a human act).
            // `&Client` rather than the client, so the closure stays `Copy`
            // and can be called for each of the two roles.
            let client = &c;
            let mount = move |model: String| async move {
                let proof = presence(client).await?;
                client
                    .call(
                        "arch.mount",
                        json!({
                            "kind": "claude-code",
                            "config": {
                                "model": model,
                                "timeout_secs": timeout,
                            },
                        }),
                        Some(proof),
                    )
                    .await
            };
            let draft = mount(draft_model.clone()).await?;
            let judge = match mount(judge_model.clone()).await {
                Ok(v) => v,
                // All or nothing. A verb whose whole reason to exist is that a
                // node needs both arches must not leave one behind when the
                // second refuses — the unmount is best effort, and its own
                // failure is reported beside the one that caused it.
                Err(why) => {
                    let id = draft["arch_id"].as_str().unwrap_or_default();
                    let rolled_back = c
                        .call("arch.unmount", json!({ "arch_id": id }), None)
                        .await
                        .is_ok();
                    return Err(if rolled_back {
                        why.context(format!(
                            "mounting {judge_model} failed; unmounted {id} again"
                        ))
                    } else {
                        why.context(format!(
                            "mounting {judge_model} failed, and {id} could not be unmounted; \
                             remove it with: vk umount {id}"
                        ))
                    });
                }
            };
            show(
                cli,
                json!({ "draft": draft, "judge": judge }),
                render::mounted_roles,
            )
        }
        // One mount, one arch, and no key on the wire: the daemon reads it
        // out of its own keyring, so what travels is the model and the
        // numbers (Task 2b). It does travel with a **presence proof**, like
        // `stop` and `approve` do: mounting a cloud arch authorises this
        // node's registers to leave the machine for a third party, and it
        // persists across every restart, so the daemon refuses it from a bare
        // machine principal (fix round 1, Critical 1).
        Cmd::Mount {
            what:
                MountCmd::Anthropic {
                    model,
                    ctx,
                    max_tokens,
                    timeout,
                },
        } => {
            let proof = presence(&c).await?;
            show(
                cli,
                c.call(
                    "arch.mount",
                    json!({
                        "kind": "anthropic",
                        "config": {
                            "model": model,
                            "context_ceiling": ctx,
                            "max_tokens": max_tokens,
                            "timeout_secs": timeout,
                        },
                    }),
                    Some(proof),
                )
                .await?,
                render::mounted_arch,
            )
        }
        Cmd::Mount {
            what:
                MountCmd::Bedrock {
                    region,
                    model,
                    ctx,
                    max_tokens,
                },
        } => {
            let proof = presence(&c).await?;
            show(
                cli,
                c.call(
                    "arch.mount",
                    json!({
                        "kind": "bedrock",
                        "config": {
                            "region": region,
                            "model": model,
                            "context_ceiling": ctx,
                            "max_tokens": max_tokens,
                        },
                    }),
                    Some(proof),
                )
                .await?,
                render::mounted_arch,
            )
        }
        // The two newer API arches with a model table: the model and the
        // numbers travel, the key and the origin stay the daemon's, and the
        // mount is a human act like `vk mount anthropic`.
        Cmd::Mount {
            what:
                what @ (MountCmd::Mistral {
                    model,
                    ctx,
                    max_tokens,
                    timeout,
                }
                | MountCmd::Gemini {
                    model,
                    ctx,
                    max_tokens,
                    timeout,
                }),
        } => {
            let kind = if matches!(what, MountCmd::Mistral { .. }) {
                "mistral"
            } else {
                "gemini"
            };
            let proof = presence(&c).await?;
            show(
                cli,
                c.call(
                    "arch.mount",
                    json!({
                        "kind": kind,
                        "config": {
                            "model": model,
                            "context_ceiling": ctx,
                            "max_tokens": max_tokens,
                            "timeout_secs": timeout,
                        },
                    }),
                    Some(proof),
                )
                .await?,
                render::mounted_arch,
            )
        }
        // The claims travel, the key and the origin do not: both are the
        // daemon's. A human act like every other API mount.
        Cmd::Mount {
            what:
                MountCmd::OpenaiCompat {
                    model,
                    ctx,
                    jurisdiction,
                    retention_days,
                    max_tokens,
                    timeout,
                },
        } => {
            let proof = presence(&c).await?;
            show(
                cli,
                c.call(
                    "arch.mount",
                    json!({
                        "kind": "openai-compat",
                        "config": {
                            "model": model,
                            "context_ceiling": ctx,
                            "jurisdiction": jurisdiction,
                            "retention_days": retention_days,
                            "max_tokens": max_tokens,
                            "timeout_secs": timeout,
                        },
                    }),
                    Some(proof),
                )
                .await?,
                render::mounted_arch,
            )
        }
        // One mount, one arch: a local model is one engine, and which role it
        // is given is the task's to say.
        Cmd::Mount {
            what:
                MountCmd::Ollama {
                    model,
                    container: _,
                    external,
                    ctx,
                    max_tokens,
                    recreate,
                    memory,
                    cpus,
                    image,
                    seed,
                },
        } => {
            // `--container` is the default, so it needs no branch of its own;
            // naming a server is what switches the mode, and the daemon reads
            // exactly that: a `base_url` means external, its absence means the
            // container this node governs.
            let mut config = json!({
                "model": model,
                "num_ctx": ctx,
                "max_tokens": max_tokens,
                "seed": seed,
            });
            // An engine this node did not start is one it cannot govern, so
            // naming it is a human act and carries a presence proof (Ruling
            // 42); the governed container takes none.
            let proof = match external {
                Some(_) => Some(presence(&c).await?),
                None => None,
            };
            match external {
                Some(url) => config["base_url"] = json!(url),
                None => {
                    config["image"] = json!(image);
                    config["memory"] = json!(memory);
                    config["cpus"] = json!(cpus);
                    config["recreate"] = json!(recreate);
                }
            }
            show(
                cli,
                c.call(
                    "arch.mount",
                    json!({ "kind": "ollama", "config": config }),
                    proof,
                )
                .await?,
                render::mounted_arch,
            )
        }
        Cmd::Umount { arch_id } => show(
            cli,
            c.call("arch.unmount", json!({ "arch_id": arch_id }), None)
                .await?,
            render::unmounted,
        ),
        Cmd::Arch {
            what: ArchCmd::Show { arch_id },
        } => show(
            cli,
            c.call("arch.show", json!({ "arch_id": arch_id }), None)
                .await?,
            render::arch_show,
        ),
        // A browser under this node's control is a human act (I1): the
        // manifest is read first, so a file that is not there spends no
        // nonce, then the request carries a presence proof.
        Cmd::Mount {
            what:
                MountCmd::Driver {
                    installed,
                    manifest,
                },
        } => {
            let params = match (installed, manifest) {
                (Some(installed), _) => json!({ "installed": installed }),
                (None, Some(manifest)) => {
                    let (manifest, root) = driver_manifest(manifest)?;
                    json!({ "manifest": manifest, "root": root })
                }
                (None, None) => unreachable!("clap requires one of the two"),
            };
            let proof = presence(&c).await?;
            show(
                cli,
                c.call("driver.mount", params, Some(proof)).await?,
                render::mounted_driver,
            )
        }
        Cmd::Artefact {
            what: ArtefactCmd::Cat { hash, out },
        } => artefact_cat(cli, &c, hash, out.as_deref()).await,
        Cmd::Driver {
            what: DriverCmd::Ls,
        } => show(
            cli,
            c.call("driver.ls", json!({}), None).await?,
            render::drivers,
        ),
        Cmd::Driver {
            what: DriverCmd::Show { name },
        } => show(
            cli,
            c.call("driver.show", json!({ "name": name }), None).await?,
            render::driver,
        ),
        // Ending the browser session a driver holds is a human act too
        // (Ruling R25), signed with this node's device key like the mount.
        Cmd::Driver {
            what: DriverCmd::Pack(args),
        } => capital::pack(cli, &c, args).await,
        Cmd::Capital { what } => capital::capital(cli, &c, what).await,
        Cmd::Driver {
            what: DriverCmd::Umount { name },
        } => {
            let proof = presence(&c).await?;
            show(
                cli,
                c.call("driver.unmount", json!({ "name": name }), Some(proof))
                    .await?,
                render::unmounted_driver,
            )
        }
        // Learning a driver writes code this node will run: a human act,
        // signed with this node's device key. The paths are made absolute
        // here, where they mean what the person typed — the daemon's working
        // directory is its own — and an arch named is resolved to its id.
        Cmd::Driver {
            what:
                DriverCmd::Learn {
                    family,
                    app,
                    corpus,
                    out,
                    threshold,
                    arch,
                    deliberate,
                },
        } => {
            let arch = match arch {
                Some(arch) => Some(
                    resolve_arch(arch, &c.call("arch.ls", json!({}), None).await?)
                        .map_err(|e| anyhow!("--arch {arch}: {e}"))?,
                ),
                None => None,
            };
            let mut params = json!({
                "family": family,
                "app": app,
                "out": plain_path(&absolute(out)?),
                "threshold": threshold,
            });
            if let Some(corpus) = corpus {
                params["corpus"] = json!(plain_path(&absolute(corpus)?));
            }
            if let Some(arch) = arch {
                params["arch"] = json!(arch);
            }
            let proof = presence(&c).await?;
            if let Some(verb) = deliberate {
                params["deliberate"] = json!(verb);
                return show(
                    cli,
                    c.call("driver.learn", params, Some(proof)).await?,
                    render::proposed,
                );
            }
            show(
                cli,
                c.call("driver.learn", params, Some(proof)).await?,
                render::learned,
            )
        }
        Cmd::Driver {
            what: DriverCmd::Qualify { driver, corpus },
        } => show(
            cli,
            c.call(
                "driver.qualify",
                json!({
                    "driver": plain_path(&absolute(driver)?),
                    "corpus": plain_path(&absolute(corpus)?),
                }),
                None,
            )
            .await?,
            render::qualified,
        ),
        Cmd::Driver {
            what: DriverCmd::Plan {
                what: PlanCmd::Show { dir },
            },
        } => show(
            cli,
            c.call(
                "driver.plan_show",
                json!({ "dir": plain_path(&absolute(dir)?) }),
                None,
            )
            .await?,
            render::plan_shown,
        ),
        Cmd::Task { what } => task(cli, &c, what).await,
        Cmd::Harness { what } => harness(cli, &c, what).await,
        Cmd::Stop { scope } => {
            let proof = presence(&c).await?;
            show(
                cli,
                c.call("stop", json!({ "scope": scope }), Some(proof))
                    .await?,
                render::stopped,
            )
        }
        Cmd::Liveness {
            what: LivenessCmd::Renew { business, until },
        } => {
            // The lease is a human's vouching, bounded in time (I4): signed
            // with this node's device key like `stop` and `approve`, which
            // refuses with the remedy when this account has no enrolled key.
            let ttl_ms = until::ttl_ms(until, now_ms())?;
            let proof = presence(&c).await?;
            show(
                cli,
                c.call(
                    "liveness.renew",
                    json!({ "business": business, "ttl_ms": ttl_ms }),
                    Some(proof),
                )
                .await?,
                render::liveness_renewed,
            )
        }
        Cmd::Resume { stop_id } => {
            let proof = presence(&c).await?;
            show(
                cli,
                c.call("resume", json!({ "stop_id": stop_id }), Some(proof))
                    .await?,
                render::ok,
            )
        }
        Cmd::Approve {
            task_id,
            passkey: false,
            ..
        } => approve(cli, &c, task_id).await,
        Cmd::Approve {
            task_id,
            passkey: true,
            open,
            timeout,
        } => approve_with_passkey(cli, &c, task_id, *open, Duration::from_secs(*timeout)).await,
        Cmd::Passkey { what } => passkey(cli, &c, what).await,
        Cmd::Dmesg { n } => show(
            cli,
            c.call("ledger.tail", json!({ "n": n }), None).await?,
            render::dmesg,
        ),
        Cmd::Ledger {
            what: LedgerCmd::Verify,
        } => show(
            cli,
            c.call("ledger.verify", json!({}), None).await?,
            render::verified,
        ),
        Cmd::Fsck {
            rebase_head,
            force: _,
            yes,
        } => fsck(cli, &c, *rebase_head, *yes).await,
    }
}

/// `vk fsck`, and the one write in it.
///
/// Three things happen here and nowhere else. The **typed confirmation**: a
/// rebase re-records where this node's record ends, and a flag is too easy to
/// pass by accident for that — so the word is typed, on this end, before a
/// syscall is made. The **presence proof**: reading every blob on the node,
/// and overriding its store's own refusal, are human acts (invariant I1), so
/// the call is signed with this node's device key like `stop` and `approve`.
/// And the **exit code**: the report is printed either way — a person running
/// `fsck` wants to see what failed — and then a store that does not verify
/// leaves by the error path, so `vk fsck && deploy` does what it looks like.
async fn fsck(cli: &Cli, c: &Client, rebase_head: bool, yes: bool) -> Result<()> {
    if rebase_head && !yes {
        confirm_rebase()?;
    }
    let proof = presence(c).await?;
    let answer = c
        .call(
            "store.fsck",
            json!({ "rebase_head": rebase_head }),
            Some(proof),
        )
        .await?;
    show(cli, answer.clone(), render::fsck)?;
    anyhow::ensure!(
        answer["ok"] == Value::Bool(true),
        "this store does not verify; the tiers above say which part of it"
    );
    Ok(())
}

/// The word, typed out. Refused on anything else, including an empty line and
/// a closed stdin — a confirmation nobody typed is not a confirmation.
fn confirm_rebase() -> Result<()> {
    // To stderr, so a `--json` run's stdout is still only the answer.
    eprint!(
        "This re-records where this node's record ends. Do it only if that record was \
         restored from a backup on purpose.\nType `rebase` to confirm: "
    );
    use std::io::Write as _;
    std::io::stderr().flush().ok();
    let mut line = String::new();
    std::io::stdin()
        .read_line(&mut line)
        .context("read the confirmation from stdin")?;
    anyhow::ensure!(
        line.trim() == "rebase",
        "not confirmed: the ledger head was not touched"
    );
    Ok(())
}

async fn task(cli: &Cli, c: &Client, what: &TaskCmd) -> Result<()> {
    match what {
        // The recipe is the whole task: what it says is what is posted, the
        // constraints and evidence the kernel writes into the register
        // included. Clap refuses every other submit flag beside it.
        TaskCmd::Submit {
            steps: Some(file), ..
        } => {
            let mut recipe = recipe(file)?;
            // Ruling R21: arches by name. Asked for only when a step needs
            // it, and resolved before anything is posted.
            if recipe.steps.iter().any(names_an_arch) {
                let arches = c.call("arch.ls", json!({}), None).await?;
                resolve_arch_names(&mut recipe.steps, &arches)
                    .map_err(|e| anyhow!("{}: {e}", file.display()))?;
            }
            let created = c
                .call(
                    "task.create",
                    json!({
                        "goal": recipe.goal,
                        "artefact_type": recipe.artefact_kind,
                        "constraints": recipe.constraints,
                        "evidence": recipe.evidence,
                        "steps": recipe.steps,
                    }),
                    None,
                )
                .await?;
            show(cli, created, render::task)
        }
        TaskCmd::Submit {
            goal,
            artefact,
            plan,
            draft,
            harness,
            judge,
            approve,
            release,
            steps: None,
        } => {
            // Clap requires both without `--steps`; said again here, so a
            // change to the flags cannot post a task with no goal.
            let goal = goal
                .as_deref()
                .ok_or_else(|| anyhow!("give --goal, or a recipe with --steps"))?;
            let plan = plan
                .as_deref()
                .ok_or_else(|| anyhow!("give --plan <ARCH>, or a recipe with --steps"))?;
            let mut steps = vec![json!({ "kind": "plan", "arch_id": plan })];
            // Exactly one of the two produces what is approved. Clap already
            // refuses both and refuses neither; this match is what turns the
            // one that was given into its step.
            match (draft, harness) {
                (Some(arch), _) => steps.push(json!({ "kind": "draft", "arch_id": arch })),
                (None, Some(name)) => steps.push(json!({ "kind": "harness", "name": name })),
                (None, None) => return Err(anyhow!("give --draft <ARCH> or --harness <NAME>")),
            }
            if let Some(arch) = judge {
                steps.push(json!({ "kind": "judge", "arch_id": arch }));
            }
            if *approve {
                steps.push(json!({ "kind": "approve" }));
            }
            // A release destination is a subpath of the kernel's export root,
            // never a path of the caller's choosing: `vk task show` prints
            // where it resolved to.
            if let Some(dir) = release {
                steps.push(json!({ "kind": "release", "to_dir": dir }));
            }
            let created = c
                .call(
                    "task.create",
                    json!({ "goal": goal, "artefact_type": artefact, "steps": steps }),
                    None,
                )
                .await?;
            show(cli, created, render::task)
        }
        TaskCmd::Step { task_id, all } => {
            let step = || c.call("task.step", json!({ "task_id": task_id }), None);
            let mut t = step().await?;
            if *all {
                let mut runs = 1;
                while !settled(&t) {
                    if runs >= MAX_STEPS {
                        return Err(anyhow!(
                            "task {task_id} had not settled after {MAX_STEPS} steps"
                        ));
                    }
                    t = step().await?;
                    runs += 1;
                }
            }
            show(cli, t, render::task)
        }
        TaskCmd::Show { task_id } => {
            let mut t = c
                .call("task.show", json!({ "task_id": task_id }), None)
                .await?;
            let root = field(&c.call("boot.info", json!({}), None).await?, "export_root")?;
            let paths = render::release_paths(&t, &root);
            if let Some(o) = t.as_object_mut() {
                o.insert("release_paths".into(), json!(paths));
            }
            show(cli, t, render::task)
        }
    }
}

/// `vk harness run`: the confined Claude Code harness over a task's harness step.
///
/// The launch, the containment, the workspace projection and the egress telemetry
/// all live in the daemon; this only names the task (or creates one from a goal),
/// hands over the options and prints what came back.
async fn harness(cli: &Cli, c: &Client, what: &HarnessCmd) -> Result<()> {
    let HarnessCmd::Run {
        task,
        goal,
        name,
        dry_run,
        keep,
    } = what;

    // An existing task, or a fresh one from a goal: a harness step, then the
    // approval the harness's last instruction (`vk_request_approval`) asks for
    // — so the request has a step to land on.
    let task_id = match (task, goal) {
        (Some(id), _) => id.clone(),
        (None, Some(g)) => {
            let created = c
                .call(
                    "task.create",
                    json!({
                        "goal": g,
                        "artefact_type": "proposal",
                        "steps": [ { "kind": "harness", "name": name }, { "kind": "approve" } ],
                    }),
                    None,
                )
                .await?;
            field(&created, "id")?
        }
        (None, None) => {
            return Err(anyhow!(
                "give a task id to run, or --goal to create one: vk harness run <TASK> | --goal \"...\""
            ))
        }
    };

    // Only what the request may carry (Ruling 20): the binary, the model and
    // the budget are the daemon's configuration.
    let params = json!({
        "task_id": task_id,
        "name": name,
        "dry_run": dry_run,
        "keep": keep,
    });
    let answer = c.call("harness.run", params, None).await?;
    if *dry_run {
        show(cli, answer, render::harness_dry_run)
    } else {
        show(cli, answer, render::harness_run)
    }
}

/// `vk artefact cat` (Ruling R22): the bytes of one artefact, read with a
/// presence proof — the founder reads the verdict and the report before he
/// signs them. Text goes to stdout as it is; anything else is refused unless
/// `--out` names a file to hold it, because raw bytes on a terminal are
/// neither readable nor safe. `--json` prints the daemon's answer (base64).
async fn artefact_cat(cli: &Cli, c: &Client, hash: &str, out: Option<&Path>) -> Result<()> {
    let proof = presence(c).await?;
    let answer = c
        .call("artefact.read", json!({ "hash": hash }), Some(proof))
        .await?;
    if cli.json {
        println!("{}", serde_json::to_string_pretty(&answer)?);
        return Ok(());
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(field(&answer, "bytes_base64")?)
        .context("the daemon's bytes_base64 is not base64")?;
    match out {
        Some(file) => {
            std::fs::write(file, &bytes).with_context(|| format!("write {}", file.display()))?;
            println!("wrote {} bytes to {}", bytes.len(), file.display());
        }
        None => match std::str::from_utf8(&bytes) {
            Ok(text) => {
                use std::io::Write as _;
                let mut stdout = std::io::stdout().lock();
                // The bytes as they are, not a byte added: what is read
                // here is what was signed.
                stdout.write_all(text.as_bytes())?;
                stdout.flush()?;
            }
            Err(_) => return Err(anyhow!("{} bytes, not text; use --out FILE", bytes.len())),
        },
    }
    Ok(())
}

/// A task recipe file (pilot spec §5.1), read and checked on this end: a typo
/// in it, or a recipe the kernel could only half run, is refused before
/// anything is posted.
fn recipe(file: &Path) -> Result<vk_contracts::recipe::TaskRecipe> {
    let text = std::fs::read_to_string(file)
        .with_context(|| format!("read the recipe {}", file.display()))?;
    let recipe: vk_contracts::recipe::TaskRecipe = serde_json::from_str(&text)
        .with_context(|| format!("{} is not a task recipe", file.display()))?;
    recipe
        .validate()
        .map_err(|e| anyhow!("{}: {e}", file.display()))?;
    Ok(recipe)
}

/// The arch a plan, draft or judge step runs on, with its role; `None` for
/// the steps that run on no arch.
fn step_arch(step: &mut RecipeStep) -> Option<(&'static str, &mut String)> {
    match step {
        RecipeStep::Plan { arch_id } => Some(("plan", arch_id)),
        RecipeStep::Draft { arch_id } => Some(("draft", arch_id)),
        RecipeStep::Judge { arch_id } => Some(("judge", arch_id)),
        RecipeStep::Harness { .. } | RecipeStep::Approve | RecipeStep::Release { .. } => None,
    }
}

/// Does this step name its arch rather than give its id?
fn names_an_arch(step: &RecipeStep) -> bool {
    match step {
        RecipeStep::Plan { arch_id }
        | RecipeStep::Draft { arch_id }
        | RecipeStep::Judge { arch_id } => !arch_id.starts_with("sha256:"),
        RecipeStep::Harness { .. } | RecipeStep::Approve | RecipeStep::Release { .. } => false,
    }
}

/// Ruling R21: a recipe may name an arch by its manifest name, because an
/// arch id is a content hash that differs per machine and per Claude version,
/// so a committed recipe cannot carry one. Every `arch_id` that is not a
/// `sha256:` id is replaced by the id of the one arch `arches` (an `arch.ls`
/// answer) lists under that name. No arch, or several, is a refusal naming
/// the step, the name and — for several — every id that carries it, and
/// pointing at `vk ls /arches`, where the names and ids are. An id is left as
/// written: whether it is mounted is the kernel's to say when the step runs.
fn resolve_arch_names(steps: &mut [RecipeStep], arches: &Value) -> Result<()> {
    for (i, step) in steps.iter_mut().enumerate() {
        let Some((role, arch)) = step_arch(step) else {
            continue;
        };
        *arch = resolve_arch(arch, arches).map_err(|e| anyhow!("step {} ({role}): {e}", i + 1))?;
    }
    Ok(())
}

/// One arch named as a recipe step or `vk driver learn --arch` names it,
/// against `arch.ls`: an id (`sha256:…`) is kept as it is, a name becomes the
/// id of the one mounted arch carrying it, and none or several is refused —
/// several with each one's id and state, so the person can tell which to
/// unmount. The one place this matching is written.
fn resolve_arch(arch: &str, arches: &Value) -> Result<String> {
    if arch.starts_with("sha256:") {
        return Ok(arch.to_string());
    }
    let rows = arches
        .as_array()
        .ok_or_else(|| anyhow!("arch.ls answered something other than a list: {arches}"))?;
    let carrying: Vec<&Value> = rows
        .iter()
        .filter(|a| a["manifest"]["name"].as_str() == Some(arch))
        .collect();
    match carrying.as_slice() {
        [one] => one["arch_id"]
            .as_str()
            .map(str::to_owned)
            .ok_or_else(|| anyhow!("arch.ls listed `{arch}` without an id: {one}")),
        [] => Err(anyhow!(
            "no mounted arch is named `{arch}`; mount it, or write its id instead \
             (`vk ls /arches` lists the names and ids this node has)"
        )),
        many => {
            let ids = many
                .iter()
                .map(|a| {
                    format!(
                        "{} ({})",
                        a["arch_id"].as_str().unwrap_or("?"),
                        a["state"].as_str().unwrap_or("?")
                    )
                })
                .collect::<Vec<_>>()
                .join(", ");
            Err(anyhow!(
                "{} mounted arches are named `{arch}`: {ids}; unmount the one you do not mean \
                 (`vk umount <ID>`) or write its id instead (`vk ls /arches` lists them)",
                many.len()
            ))
        }
    }
}

/// A path as the person typed it, made absolute against this shell's working
/// directory: the daemon's is its own.
fn absolute(path: &Path) -> Result<PathBuf> {
    std::path::absolute(path).with_context(|| format!("make {} absolute", path.display()))
}

/// `vk mount driver --manifest FILE`'s request: the manifest as written, and
/// the root its relative command resolves against (Ruling R6) — the
/// canonical directory of the manifest file, so the daemon, whose working
/// directory is its own, is told where the driver's files are rather than
/// where this shell stood. Spelled without Windows' verbatim `\\?\` prefix,
/// so it reads back in `vk driver show` as a person would type it.
fn driver_manifest(file: &Path) -> Result<(Value, String)> {
    let text = std::fs::read_to_string(file)
        .with_context(|| format!("read the driver manifest {}", file.display()))?;
    let manifest: Value =
        serde_json::from_str(&text).with_context(|| format!("{} is not JSON", file.display()))?;
    let canonical =
        std::fs::canonicalize(file).with_context(|| format!("resolve {}", file.display()))?;
    let dir = canonical
        .parent()
        .ok_or_else(|| anyhow!("{} has no directory", canonical.display()))?;
    Ok((manifest, plain_path(dir)))
}

/// A path as a person would type it: Windows' verbatim `\\?\` prefix, which
/// `canonicalize` puts there, taken off.
fn plain_path(path: &Path) -> String {
    let path = path.display().to_string();
    #[cfg(windows)]
    let path = match (path.strip_prefix(r"\\?\UNC\"), path.strip_prefix(r"\\?\")) {
        (Some(share), _) => format!(r"\\{share}"),
        (None, Some(local)) => local.to_string(),
        (None, None) => path,
    };
    path
}

/// A task that will not move again without something else happening: a human,
/// a fix, or a resume.
fn settled(t: &Value) -> bool {
    matches!(
        t["status"].as_str(),
        Some("waiting_human") | Some("done") | Some("failed") | Some("stopped")
    )
}

/// This node's own device key, which in SP1a is the human's key: from
/// `$VK_NODE_KEY_FILE` if it names one, else the OS keyring. The daemon says
/// which node it is, so the key is the one enrolled as `node:<node_id>`.
///
/// **Loaded, never minted** (final review I-2). The key is created and
/// enrolled by the daemon that serves the node — `vk boot`, which starts `vkd
/// --auto-enroll-node` — and a key this shell made up would be one no node
/// enrolled: every ceremony signed with it would be refused with nothing
/// saying why. So an account with no key, or with one the node has not
/// enrolled, is refused here, before anything is signed, with the remedy.
async fn node_device(c: &Client) -> Result<NodeDevice> {
    let node_id = field(&c.call("boot.info", json!({}), None).await?, "node_id")?;
    let (source, place) = match std::env::var_os("VK_NODE_KEY_FILE") {
        Some(path) => {
            let place = format!("the file {}", PathBuf::from(&path).display());
            (KeySource::File(PathBuf::from(path)), place)
        }
        None => (
            KeySource::Keyring,
            "this account's keyring (no VK_NODE_KEY_FILE)".to_string(),
        ),
    };
    let device_id = format!("node:{node_id}");
    let Some(device) = NodeDevice::load(source, &node_id).context("this node's device key")? else {
        anyhow::bail!(
            "this account has no device key for node {node_id} in {place}, and vk does not \
             create one: a key this shell made would be one the node never enrolled. The \
             daemon that serves the node creates and enrols it — `vk boot` does, for a daemon \
             you start. Under the Windows service the node's key is the service account's, not \
             yours: see README, \"Human ceremonies under the service\""
        );
    };
    let enrolled = c.call("ns.ls", json!({ "path": "/devices" }), None).await?;
    let listed = enrolled["entries"]
        .as_array()
        .is_some_and(|ids| ids.iter().any(|id| id.as_str() == Some(device_id.as_str())));
    anyhow::ensure!(
        listed,
        "{device_id} is not enrolled on this node, so nothing signed with the key in {place} \
         would be accepted. The daemon enrols the node's key when it starts with \
         --auto-enroll-node (as `vk boot` starts it); under the Windows service, see README, \
         \"Human ceremonies under the service\""
    );
    Ok(device)
}

/// A nonce this daemon issued: single use, and spent by the request that
/// carries it.
async fn challenge(c: &Client) -> Result<String> {
    field(
        &c.call("presence.challenge", json!({}), None).await?,
        "nonce",
    )
}

/// Wall-clock milliseconds since the Unix epoch.
fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| u64::try_from(d.as_millis()).unwrap_or(u64::MAX))
}

/// Proof that the device key was present for the next request.
async fn presence(c: &Client) -> Result<PresenceProof> {
    let device = node_device(c).await?;
    Ok(PresenceProof::sign(&device, &challenge(c).await?))
}

/// The approval ceremony with this node's device key: ask the kernel for the
/// challenge it minted for the task — the subject, the resource, the nonce
/// and the expiry are all the kernel's, never this shell's (SP1b Task 5,
/// invariant I1) — sign its digest, and present it with a presence proof.
///
/// Two signatures, two domains: the presence proof signs
/// `sha256("presence|<nonce>")` over a nonce of its own, the approval signs
/// `H(resource|action|nonce|expiry)` over the minted challenge, so neither
/// can be replayed as the other, and `approve` accepts the approval only
/// while the minted challenge is unspent and unexpired.
async fn approve(cli: &Cli, c: &Client, task_id: &str) -> Result<()> {
    let device = node_device(c).await?;
    let challenge: Challenge = serde_json::from_value(
        c.call("approval.challenge", json!({ "task_id": task_id }), None)
            .await?,
    )
    .context("the daemon's approval challenge")?;
    let subject = challenge.action_digest.clone();
    let approval = Approval {
        subject_hash: subject.clone(),
        kind: ApprovalKind::Human,
        approver: Principal::Human {
            device_id: device.device_id(),
        },
        signature_hex: Some(hex::encode(device.sign(&challenge.digest()))),
        challenge: Some(challenge),
    };
    let proof = presence(c).await?;
    let mut answer = c
        .call("approve", json!({ "approval": approval }), Some(proof))
        .await?;
    // What was approved, named back to the person who just approved it — and
    // what has to happen next, because recording an approval does not run the
    // step that was waiting for it.
    if let Some(o) = answer.as_object_mut() {
        o.insert("task_id".into(), json!(task_id));
        o.insert("subject_hash".into(), json!(subject));
    }
    show(cli, answer, render::approved)
}

/// The approval ceremony with a passkey: the daemon mints a link to the
/// task's approval page, this shell prints it (and opens it with `--open`),
/// and waits — polling `task.show` — for the task to leave `waiting_human`,
/// which is what the page's `finish` does once the passkey has signed and
/// the kernel has recorded. Nothing is signed here: the browser, the
/// authenticator and the daemon do the whole ceremony between them.
async fn approve_with_passkey(
    cli: &Cli,
    c: &Client,
    task_id: &str,
    open: bool,
    timeout: Duration,
) -> Result<()> {
    let linked = c
        .call(
            "web.link",
            json!({ "page": "approve", "task_id": task_id }),
            None,
        )
        .await?;
    let url = field(&linked, "url")?;
    let subject = field(&linked, "subject_hash")?;
    // The link goes to the person at once, before the wait: to stdout as the
    // answer of the moment, or — under `--json`, where stdout is the final
    // answer — to stderr, so a script capturing the answer still shows it.
    if cli.json {
        eprintln!("{url}");
    } else {
        println!("{}", render::link(&json!({ "url": url, "waiting": true })));
    }
    if open {
        open_browser(&url)?;
    }
    let start = Instant::now();
    let status = loop {
        let t = c
            .call("task.show", json!({ "task_id": task_id }), None)
            .await?;
        match t["status"].as_str() {
            Some("waiting_human") | None => {}
            // The approve step ran on the approval: the task finished, or
            // went on to its next step.
            Some(status @ ("done" | "running")) => break status.to_string(),
            // It left `waiting_human` some other way — a STOP, a failure —
            // and nothing says a passkey approved anything.
            Some(other) => {
                return Err(anyhow!(
                    "task {task_id} is {other}; no passkey approval landed on it"
                ))
            }
        }
        if start.elapsed() > timeout {
            return Err(anyhow!(
                "no passkey approval of {task_id} arrived within {}s; the link stays open for \
                 a few minutes: {url}",
                timeout.as_secs()
            ));
        }
        tokio::time::sleep(APPROVAL_POLL).await;
    };
    show(
        cli,
        json!({
            "ok": true,
            "task_id": task_id,
            "subject_hash": subject,
            "status": status,
            "url": url,
        }),
        render::approved,
    )
}

/// `vk passkey enroll | ls`.
async fn passkey(cli: &Cli, c: &Client, what: &PasskeyCmd) -> Result<()> {
    match what {
        PasskeyCmd::Enroll { open } => {
            // Enrolling a passkey is a human act: the link is minted only
            // under a presence proof by this node's device key (review
            // Important 2), so a process with the pipe and no key gets none.
            let proof = presence(c).await?;
            let linked = c
                .call("web.link", json!({ "page": "enroll" }), Some(proof))
                .await?;
            let url = field(&linked, "url")?;
            if *open {
                open_browser(&url)?;
            }
            show(cli, json!({ "url": url, "opened": open }), render::link)
        }
        PasskeyCmd::Ls => {
            let listed = c.call("passkey.ls", json!({}), None).await?;
            // An enrolled passkey the daemon's verifier cannot read is an
            // error (final review M-11): shown in the list, and the verb
            // fails, because that passkey cannot approve anything.
            let unreadable: Vec<String> = listed
                .as_array()
                .into_iter()
                .flatten()
                .filter(|p| p["unreadable"].is_string())
                .map(|p| p["device_id"].as_str().unwrap_or("?").to_string())
                .collect();
            show(cli, listed, render::passkeys)?;
            anyhow::ensure!(
                unreadable.is_empty(),
                "UNREADABLE passkey(s) {}: enrolled, but this daemon's verifier cannot read \
                 them, so they cannot approve; enrol the authenticator again",
                unreadable.join(", ")
            );
            Ok(())
        }
    }
}

/// Hand `url` to the default browser: `start` on Windows, `open` on macOS,
/// `xdg-open` elsewhere. Detached, and only its launch is checked — what
/// the browser then does with the page is the person's, not this shell's.
fn open_browser(url: &str) -> Result<()> {
    let mut cmd = if cfg!(windows) {
        let mut c = Command::new("cmd");
        // `start` treats its first quoted argument as a window title.
        c.args(["/C", "start", "", url]);
        c
    } else if cfg!(target_os = "macos") {
        let mut c = Command::new("open");
        c.arg(url);
        c
    } else {
        let mut c = Command::new("xdg-open");
        c.arg(url);
        c
    };
    cmd.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .with_context(|| format!("open {url} in a browser"))?;
    Ok(())
}

/// The `boot.info` of the daemon serving this endpoint, if one is. It carries
/// the state directory, which is what tells a second `vk boot` whether the
/// daemon already there is the one the caller asked for.
async fn serving(ep: &Endpoint) -> Option<Value> {
    let c = Client::connect(ep).await.ok()?;
    c.call("boot.info", json!({}), None).await.ok()
}

/// Two paths naming the same directory, canonicalised where the filesystem can
/// say so (`C:\X` against `c:\x`, a substituted drive, a symlinked `/tmp`).
fn same_dir(a: &Path, b: &Path) -> bool {
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(x), Ok(y)) => x == y,
        _ => a == b,
    }
}

/// The node key file `vk boot` hands to the daemon: the flag, else the same
/// environment variable this shell signs with, so both ends use one key.
fn node_key_file(a: &BootArgs) -> Option<PathBuf> {
    a.node_key_file
        .clone()
        .or_else(|| std::env::var_os("VK_NODE_KEY_FILE").map(PathBuf::from))
}

/// Windows: take the inherit flag off *our own* standard handles before
/// starting the daemon.
///
/// `CreateProcess` hands a child every inheritable handle in the process, not
/// only the three it is given — and `vk`'s own stdout is an inheritable pipe
/// whenever something captured it, which is exactly what `out=$(vk boot
/// --json)` does. The daemon would hold that pipe for its whole life and the
/// capture would never see EOF, however carefully the child's own stdio was
/// redirected. `DETACHED_PROCESS` does not cover this; nothing but clearing
/// the flag does. (On Unix the redirect is enough: the child's `dup2` replaces
/// the inherited descriptor, and everything else is close-on-exec.)
#[cfg(windows)]
fn stop_inheriting_our_stdio() {
    use std::os::windows::io::AsRawHandle;
    const HANDLE_FLAG_INHERIT: u32 = 0x0000_0001;
    extern "system" {
        fn SetHandleInformation(handle: *mut std::ffi::c_void, mask: u32, flags: u32) -> i32;
    }
    for handle in [
        std::io::stdin().as_raw_handle(),
        std::io::stdout().as_raw_handle(),
        std::io::stderr().as_raw_handle(),
    ] {
        // A handle that cannot be told this (a console, a closed stream) was
        // not one a child could hold a pipe open with either.
        unsafe { SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0) };
    }
}

/// Cut the daemon loose from this terminal.
///
/// Inheriting the console would undo the whole point of starting it in the
/// background: on Windows a Ctrl+C or a closed window sends `CTRL_C_EVENT` /
/// `CTRL_CLOSE_EVENT` to everything attached to that console, and on Unix the
/// terminal's foreground group gets `SIGINT` and its session `SIGHUP` — so the
/// daemon, and every task in flight, would die with the shell that started it.
/// Inheriting stdout has a second cost: `out=$(vk boot --json)` would block for
/// the daemon's whole life waiting for a pipe the daemon still holds.
///
/// Its output is not lost, it is appended to `<state_dir>/vkd.log`.
fn detach(cmd: &mut Command, state_dir: &Path) -> Result<()> {
    let path = state_dir.join("vkd.log");
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .with_context(|| format!("open {}", path.display()))?;
    cmd.stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console to inherit,
        // and no console signals from this one.
        cmd.creation_flags(0x0000_0008 | 0x0000_0200);
        stop_inheriting_our_stdio();
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // Its own *session*, not only its own process group (SP1a review
        // M7): `setsid` in the child leaves the terminal's session, so the
        // `SIGHUP` the kernel sends to that session when the shell that ran
        // `vk boot` closes never reaches the daemon. A new session is a new
        // process group too.
        // SAFETY: `setsid` is async-signal-safe and touches nothing of the
        // parent's; it is the only call made between fork and exec.
        unsafe {
            cmd.pre_exec(|| {
                if libc::setsid() == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }
    Ok(())
}

/// How a daemon this shell started stopped being a daemon it is waiting for.
enum Started {
    /// It answered on the endpoint, with the `boot.info` it answered with —
    /// which says how many arches are still coming up behind it, so `vk boot`
    /// can tell the person who ran it (Task 1b review, Important 1).
    Serving(Box<Value>),
    /// It gave up before answering — a refused ledger, a key it could not
    /// read, an endpoint already taken.
    Exited(std::process::ExitStatus),
    /// The OS will not say whether it is still running.
    Lost(std::io::Error),
    /// Still running, still not answering, out of patience.
    Silent,
}

/// Start `vkd`, which lives next to this binary, and hand it the state
/// directory and keys this call named.
///
/// `--auto-enroll-node` goes with it: in SP1a the machine a person is sitting
/// at is the device that speaks for them, and without it this shell could
/// never stop, resume or approve anything.
fn boot(cli: &Cli, rt: &tokio::runtime::Runtime, a: &BootArgs) -> Result<()> {
    let exe =
        std::env::current_exe()?.with_file_name(format!("vkd{}", std::env::consts::EXE_SUFFIX));
    anyhow::ensure!(exe.exists(), "no vkd next to vk at {}", exe.display());
    // Resolved here, the way `vkd` itself would resolve it (and refusing a
    // synced folder here rather than in a daemon that is already detached), so
    // that it can be compared with what a daemon already on this endpoint
    // serves. Resolved only: this call may still refuse, and a refusal that
    // left a state directory behind would be a node the user never asked for.
    let state_dir = vk_store::paths::resolve_state_dir(a.state_dir.clone())?;
    let ep = endpoint(cli);
    if let Some(info) = rt.block_on(serving(&ep)) {
        let served = field(&info, "state_dir")?;
        // One endpoint, one store. Reporting "already serving" for a daemon on
        // a *different* state directory would send every later `vk task
        // submit`, `vk approve` and `vk ledger verify` to a store the caller
        // did not ask for, with `vk status` the only clue.
        anyhow::ensure!(
            same_dir(Path::new(&served), &state_dir),
            "a daemon on {} is serving {served}, not {}; stop it, or pick another endpoint \
             with --endpoint",
            ep.0,
            state_dir.display()
        );
        return show(
            cli,
            json!({
                "endpoint": ep.0,
                "state_dir": state_dir.display().to_string(),
                "already_running": true,
                "arches_starting": info["arches_starting"].as_u64().unwrap_or(0),
            }),
            render::booted,
        );
    }
    // Committed now: the daemon's log lands inside it before the daemon is
    // even started.
    let state_dir = vk_store::paths::state_dir(Some(state_dir))?;
    let mut cmd = Command::new(&exe);
    cmd.arg("--auto-enroll-node")
        .arg("--state-dir")
        .arg(&state_dir)
        .args(["--node-id", &a.node_id])
        .args(["--endpoint", &ep.0]);
    if let Some(key) = &a.master_key_file {
        cmd.arg("--master-key-file").arg(key);
    }
    if let Some(key) = node_key_file(a) {
        cmd.arg("--node-key-file").arg(key);
    }
    if a.force {
        cmd.arg("--force");
    }
    cmd.args(["--web-port", &a.web_port.to_string()]);
    if let Some(bin) = &a.claude_bin {
        cmd.arg("--claude-bin").arg(bin);
    }
    if a.foreground {
        let status = cmd
            .status()
            .with_context(|| format!("run {}", exe.display()))?;
        anyhow::ensure!(status.success(), "vkd exited with {status}");
        return Ok(());
    }
    detach(&mut cmd, &state_dir)?;
    let mut child = cmd
        .spawn()
        .with_context(|| format!("start {}", exe.display()))?;
    let pid = child.id();
    // Started is not serving: the daemon is up when it answers. It is also not
    // *coming* up if it has already gone — a `vkd` that refuses its own ledger
    // exits in milliseconds, and waiting out the timeout would tell the one
    // person who can fix it that their endpoint is slow.
    let start = Instant::now();
    let outcome = rt.block_on(async {
        loop {
            if let Some(info) = serving(&ep).await {
                return Started::Serving(Box::new(info));
            }
            match child.try_wait() {
                Ok(Some(status)) => return Started::Exited(status),
                Err(e) => return Started::Lost(e),
                Ok(None) => {}
            }
            if start.elapsed() > BOOT_TIMEOUT {
                return Started::Silent;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    });
    let log = state_dir.join("vkd.log");
    let info = match outcome {
        Started::Serving(info) => *info,
        // It said why on its way out. The log is where a detached daemon
        // speaks, so its last lines are the answer — naming a file to go and
        // read is not.
        Started::Exited(status) => anyhow::bail!(
            "vkd (pid {pid}) exited with {status} before it answered on {}{}",
            ep.0,
            match std::fs::read_to_string(&log) {
                Ok(c) if !c.trim().is_empty() => format!(", saying:\n{}", render::log_tail(&c, 4)),
                _ => format!("; see {}", log.display()),
            }
        ),
        // The OS will not say whether it is still running, so assume it is:
        // a daemon nobody knows the pid of would sit on the endpoint and make
        // every later `vk boot` refuse, blaming a daemon the user never sees
        // — the same reason `Silent` kills (SP1b Task 8, deferred minor).
        Started::Lost(e) => {
            let _ = child.kill();
            let _ = child.wait();
            anyhow::bail!("lost track of vkd (pid {pid}), and killed it: {e}")
        }
        Started::Silent => {
            // It is ours and nobody else knows its pid, so it does not outlive
            // the call that started it: otherwise it would sit on the endpoint
            // and every later `vk boot` would refuse, blaming a daemon the
            // user never sees.
            let _ = child.kill();
            let _ = child.wait();
            anyhow::bail!(
                "vkd (pid {pid}) did not answer on {} within {BOOT_TIMEOUT:?}; see {}",
                ep.0,
                log.display()
            );
        }
    };
    show(
        cli,
        json!({
            "pid": pid,
            "endpoint": ep.0,
            "state_dir": state_dir.display().to_string(),
            // The node is serving; some of its arches may still be coming up.
            // Said here rather than left to be discovered by a task step that
            // asks to be retried (Task 1b review, Important 1).
            "arches_starting": info["arches_starting"].as_u64().unwrap_or(0),
        }),
        render::booted,
    )
}

#[cfg(all(test, unix))]
mod detach_tests {
    use super::detach;
    use std::process::Command;

    /// A detached daemon leads its own **session**, not only its own process
    /// group (SP1a review M7): `setsid` in the child, so the terminal's
    /// `SIGHUP` — sent to the session when the shell that ran `vk boot`
    /// closes — never reaches it.
    ///
    /// On Linux the child reports its session id (`ps -o sess=` is numeric
    /// there) and it must **differ** from this process's — exactly what tells
    /// `setsid` (a new session) from a mere `process_group(0)` (a new group in
    /// the same session). macOS's `ps` reports no session id at all (`sess`
    /// prints `0` for every process), so there the test checks the portable,
    /// necessary consequence of `setsid`: the child leads a process group of
    /// its own (`pgid == pid`) that is not this process's.
    #[test]
    fn a_detached_daemon_leads_its_own_session() {
        let d = tempfile::tempdir().unwrap();
        let mut cmd = Command::new("sh");
        #[cfg(target_os = "linux")]
        cmd.args(["-c", "ps -o sess= -p $$"]);
        #[cfg(not(target_os = "linux"))]
        cmd.args(["-c", "ps -o pgid= -p $$"]);
        detach(&mut cmd, d.path()).unwrap();
        let mut child = cmd.spawn().unwrap();
        let pid = child.id();
        let status = child.wait().unwrap();
        assert!(status.success(), "{status}");
        let reported = std::fs::read_to_string(d.path().join("vkd.log")).unwrap();
        let reported = reported.trim().to_string();
        assert!(!reported.is_empty(), "the child printed nothing");

        #[cfg(target_os = "linux")]
        {
            // This process's own session, read the same way: the two differ.
            let ours = Command::new("ps")
                .args(["-o", "sess=", "-p", &std::process::id().to_string()])
                .output()
                .unwrap();
            let ours = String::from_utf8_lossy(&ours.stdout);
            assert_ne!(
                reported,
                ours.trim(),
                "the child must be in a different session (setsid), not this one"
            );
            let _ = pid;
        }
        #[cfg(not(target_os = "linux"))]
        {
            let pgid: i32 = reported
                .parse()
                .unwrap_or_else(|e| panic!("{e}: {reported:?}"));
            assert_eq!(pgid, pid as i32, "the child leads its own process group");
            // SAFETY: `getpgrp` reads this process's own process group id.
            let ours = unsafe { libc::getpgrp() };
            assert_ne!(pgid, ours, "and it is not this process's group");
        }
    }
}

#[cfg(test)]
mod recipe_tests {
    use super::{names_an_arch, resolve_arch, resolve_arch_names, RecipeStep};
    use serde_json::json;

    /// `vk driver learn --arch` reads a name as a recipe's arch is read.
    #[test]
    fn learn_arch_resolves_a_name_and_keeps_an_id() {
        assert_eq!(
            resolve_arch("claude-code/claude-opus-5", &listing()).unwrap(),
            "sha256:bbb"
        );
        assert_eq!(
            resolve_arch("sha256:not-mounted", &listing()).unwrap(),
            "sha256:not-mounted"
        );
        let e = resolve_arch("nobody", &listing()).unwrap_err().to_string();
        assert!(e.contains("no mounted arch is named `nobody`"), "{e}");
        let e = resolve_arch("twice", &listing()).unwrap_err().to_string();
        assert!(e.contains("2 mounted arches are named `twice`"), "{e}");
        assert!(
            e.contains("sha256:c01 (ready)") && e.contains("sha256:c02 (unavailable)"),
            "the one function keeps each arch's state: {e}"
        );
    }

    fn listing() -> serde_json::Value {
        json!([
            {"arch_id": "sha256:aaa", "manifest": {"name": "claude-code/claude-sonnet-5"}, "state": "ready", "reason": null},
            {"arch_id": "sha256:bbb", "manifest": {"name": "claude-code/claude-opus-5"}, "state": "ready", "reason": null},
            {"arch_id": "sha256:c01", "manifest": {"name": "twice"}, "state": "ready", "reason": null},
            {"arch_id": "sha256:c02", "manifest": {"name": "twice"}, "state": "unavailable", "reason": "gone"}
        ])
    }

    fn plan(arch: &str) -> RecipeStep {
        RecipeStep::Plan {
            arch_id: arch.into(),
        }
    }

    /// Ruling R21: names become the ids `arch.ls` lists under them, ids and
    /// the steps that run on no arch are left exactly as written.
    #[test]
    fn a_name_resolves_to_the_one_arch_carrying_it_and_an_id_is_left_alone() {
        let mut steps = vec![
            plan("claude-code/claude-sonnet-5"),
            RecipeStep::Harness {
                name: "claude-code".into(),
                drivers: vec!["maskai-1".into()],
                instruction: None,
                max_actions: None,
                timeout_s: None,
            },
            RecipeStep::Judge {
                arch_id: "claude-code/claude-opus-5".into(),
            },
            RecipeStep::Approve,
            RecipeStep::Draft {
                arch_id: "sha256:not-mounted".into(),
            },
            RecipeStep::Release {
                to_dir: "runs".into(),
            },
        ];
        assert!(steps.iter().any(names_an_arch));
        let before = steps.clone();
        resolve_arch_names(&mut steps, &listing()).unwrap();
        assert_eq!(steps[0], plan("sha256:aaa"));
        assert_eq!(
            steps[2],
            RecipeStep::Judge {
                arch_id: "sha256:bbb".into()
            }
        );
        for i in [1, 3, 4, 5] {
            assert_eq!(steps[i], before[i], "step {i} untouched");
        }
        assert!(!steps.iter().any(names_an_arch));
    }

    #[test]
    fn no_arch_or_several_under_a_name_is_refused_with_the_listing_named() {
        let e = resolve_arch_names(&mut [RecipeStep::Approve, plan("nobody")], &listing())
            .unwrap_err()
            .to_string();
        assert!(e.contains("step 2 (plan)"), "{e}");
        assert!(e.contains("`nobody`"), "{e}");
        assert!(e.contains("vk ls /arches"), "{e}");

        let e = resolve_arch_names(&mut [plan("twice")], &listing())
            .unwrap_err()
            .to_string();
        assert!(e.contains("2 mounted arches are named `twice`"), "{e}");
        assert!(e.contains("sha256:c01 (ready)"), "{e}");
        assert!(e.contains("sha256:c02 (unavailable)"), "{e}");
        assert!(e.contains("vk umount"), "{e}");
        assert!(e.contains("vk ls /arches"), "{e}");
    }
}
