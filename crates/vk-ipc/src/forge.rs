//! vkforge under the daemon (driver-learning spec §4.10, plan Tasks 9 and 11): `driver.learn`,
//! `driver.qualify`, `driver.plan_show` and `driver.debrief`.
//!
//! Learning a driver is six stages of `drivers/vkforge` — inventory, bind, mine, plan, generate,
//! qualify — each a **governed one-shot process** (ruling L-P2): `<python> -I
//! <root>/vkforge/__main__.py <stage> …`, started in the vkforge root, put under the platform's
//! governor the moment it exists, killed with its whole tree at its deadline, its stdout and
//! stderr kept under the state directory. Never protocol 1: a pipeline is not request/response.
//! A stage that refuses speaks the kernel's voice — one line, `vkforge <stage>: …; remedy: …`
//! (ruling L-P15) — and that line is the refusal, verbatim.
//!
//! The seventh stage, `debrief`, reads a governed run's evidence after the fact and proposes
//! what to change; it is a machine act that applies nothing and writes no ledger row (L-P5).
//!
//! None of it runs under the kernel lock (ruling L-P17): the arms are dispatched before the
//! general lock, as `driver.pack` is, and a stage runs for seconds to minutes on the connection's
//! blocking thread. The kernel is asked, in short phases, for what only it does: the one model
//! call per ambiguity, `driver.learned`, the `learned` row, `driver.qualified`.
//!
//! OS analogy: the factory line that turns a reference board's datasheet and a stack of sample
//! boards into a driver — each station a separate jig on a timer, the line's supervisor
//! recording the build and the bench test, and the boot ROM refusing an image without its test
//! report.
use crate::server::{bad, internal, kerr, lock, Shared};
use crate::{RpcError, E_STORE};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fs::File;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Mutex, PoisonError};
use std::time::{Duration, Instant};
use vk_contracts::arch::Capability;
use vk_contracts::driver::{is_driver_name, DriverLearned, DriverManifest, DriverQualified};
use vk_contracts::hash_bytes;
use vk_contracts::plan::{plan_sha256, DriverPlan};
use vk_contracts::syscalls::Ctx;

/// What the daemon runs vkforge with (L-P2): `vkd --vkforge-root`, the interpreter and the test
/// seam, read once at start.
#[derive(Debug, Clone)]
pub struct ForgeConfig {
    /// The `drivers/` directory: vkforge is `<root>/vkforge/__main__.py`.
    pub root: PathBuf,
    /// The interpreter (L-P12): `VK_FORGE_PYTHON`, else `%LOCALAPPDATA%\vk\venvs\vkforge\Scripts\python.exe`.
    pub python: PathBuf,
    /// `VK_FORGE_FIXTURES=1`, a test seam like `VK_TEST_PYTHON_RELEASE`: the recorded catalogues
    /// (`inventory --fixture`) and the fake application (`qualify --fake`). `vkd` warns when it is set.
    pub fixtures: bool,
    /// The deadline of each `learn` stage: `STAGE_DEADLINE` from `at`; a test sets it shorter
    /// (L-P30). `qualify` and `debrief` keep their own.
    pub stage_deadline: Duration,
}

impl ForgeConfig {
    /// `root` as `vkd --vkforge-root` gives it, with the interpreter and the seam read from the
    /// environment and `STAGE_DEADLINE`. Infallible (L-P26): a root without
    /// `vkforge/__main__.py` is refused by `run_stage`, at the first spawn.
    ///
    /// The root is made absolute here, against the daemon's working directory as it is at start
    /// (review fix round 1): a stage runs with `current_dir = root`, so a relative script path or
    /// `--fixture` path would otherwise be read a second time against the root itself
    /// (`drivers\drivers\vkforge\__main__.py`), after the check had passed against the daemon's.
    pub fn at(root: PathBuf) -> ForgeConfig {
        let root = std::path::absolute(&root).unwrap_or(root);
        let python = std::env::var_os("VK_FORGE_PYTHON")
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap_or_default())
                    .join("vk")
                    .join("venvs")
                    .join("vkforge")
                    .join("Scripts")
                    .join("python.exe")
            });
        ForgeConfig {
            root,
            python,
            fixtures: std::env::var("VK_FORGE_FIXTURES").is_ok_and(|v| v == "1"),
            stage_deadline: STAGE_DEADLINE,
        }
    }
}

/// One stage that ran to a zero exit.
#[derive(Debug, Clone)]
pub struct StageRun {
    pub stage: &'static str,
    pub args: Vec<String>,
    pub exit: i32,
    pub duration_ms: u64,
    /// The end of what the stage printed: its one summary line.
    pub stdout_tail: String,
}

/// The deadline of each stage of `driver.learn`.
pub const STAGE_DEADLINE: Duration = Duration::from_secs(600);

/// The memory cap of a stage's whole tree: a corpus parsed with lxml, or a driver replayed on
/// the fake application, stays far below it.
const STAGE_MEMORY: u64 = 1 << 30;

/// `driver.qualify`'s budget (ruling L-P53): a minute, and 130 s per document — above the stage's
/// own 90 s per pass plus 10 s for the driver's hello, so the stage's own timeout always speaks
/// first.
const QUALIFY_BASE: Duration = Duration::from_secs(60);
const QUALIFY_PER_DOCUMENT: Duration = Duration::from_secs(130);

/// How much of a stage's stdout a [`StageRun`] keeps.
const TAIL_BYTES: usize = 2000;

/// Refuses, before spawning, a root without `vkforge/__main__.py`: `bad("vkforge root not found;
/// remedy: start vkd with --vkforge-root <drivers/>")` (L-P26). Then spawns
/// `<python> -I <root>/vkforge/__main__.py <stage> <args>` with `current_dir = root` — the
/// script path, never `-m` — under `vk_harness::confine::for_this_platform(1 << 30, None)`
/// (`Governor::contain` the moment the child exists, failing closed on Windows as the harness does),
/// stdout to `<work>/forge-<stage>.out`, stderr to `<work>/forge-<stage>.err`, killed at `deadline`
/// (`internal("vkforge <stage> ran past its deadline of <n> s; see <log>")`). A non-zero exit is
/// `bad(<the last non-empty line of the .err file>)`, verbatim: the stage speaks the kernel's voice
/// (L-P15); an exit with no line is `internal("vkforge <stage> exited <code>; see <log>")`.
pub fn run_stage(
    cfg: &ForgeConfig,
    stage: &'static str,
    args: &[String],
    work: &Path,
    deadline: Duration,
) -> Result<StageRun, RpcError> {
    let script = cfg.root.join("vkforge").join("__main__.py");
    if !script.is_file() {
        return Err(bad(
            "vkforge root not found; remedy: start vkd with --vkforge-root <drivers/>",
        ));
    }
    vk_store::paths::private_dir(work)
        .map_err(|e| io_failed("create the vkforge log directory", &e))?;
    let out_log = work.join(format!("forge-{stage}.out"));
    let err_log = work.join(format!("forge-{stage}.err"));
    let stdout =
        File::create(&out_log).map_err(|e| io_failed("create the stage's stdout log", &e))?;
    let stderr =
        File::create(&err_log).map_err(|e| io_failed("create the stage's stderr log", &e))?;
    // Made before the child, so a governor the OS refuses is no spawn.
    let governor = vk_harness::confine::for_this_platform(STAGE_MEMORY, None).map_err(|e| {
        internal(&format!(
            "vkforge {stage}: no governor for the stage: {e:#}"
        ))
    })?;
    let mut cmd = Command::new(&cfg.python);
    cmd.arg("-I")
        .arg(&script)
        .arg(stage)
        .args(args)
        .current_dir(&cfg.root)
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    #[cfg(unix)]
    {
        // The stage leads its own process group, so `kill_tree` signals all of it.
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    let started = Instant::now();
    let mut child = cmd.spawn().map_err(|e| {
        internal(&format!(
            "vkforge {stage} could not start {} ({e}); remedy: drivers\\vkforge\\setup.ps1 makes \
             the virtualenv, or set VK_FORGE_PYTHON",
            cfg.python.display()
        ))
    })?;
    // Immediately, before the stage can do much: contained, or — on Windows, where a governor
    // exists and refused — killed before it ran (the harness's Ruling 21.8).
    if let Err(e) = governor.contain(&child) {
        if cfg!(windows) {
            vk_harness::launch::kill_tree(&mut child);
            let _ = child.wait();
            return Err(internal(&format!(
                "vkforge {stage} could not be put under the governor, so it was killed before it \
                 ran: {e:#}"
            )));
        }
        tracing::warn!(stage, error = %format!("{e:#}"), "vkforge stage runs ungoverned");
    }
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if started.elapsed() < deadline => {
                std::thread::sleep(Duration::from_millis(50))
            }
            Ok(None) => {
                vk_harness::launch::kill_tree(&mut child);
                let _ = child.wait();
                // The Job Object closes with the governor: whatever the stage started goes too.
                drop(governor);
                return Err(internal(&format!(
                    "vkforge {stage} ran past its deadline of {} s; see {}",
                    deadline.as_secs(),
                    err_log.display()
                )));
            }
            Err(e) => {
                vk_harness::launch::kill_tree(&mut child);
                let _ = child.wait();
                drop(governor);
                return Err(internal(&format!(
                    "vkforge {stage}: its exit could not be read ({e}); see {}",
                    err_log.display()
                )));
            }
        }
    };
    // Whatever the stage left running ends with its Job.
    drop(governor);
    let duration_ms = u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX);
    let exit = status.code().unwrap_or(-1);
    if !status.success() {
        let said = std::fs::read(&err_log).unwrap_or_default();
        let said = String::from_utf8_lossy(&said);
        return Err(match said.lines().map(str::trim).rfind(|l| !l.is_empty()) {
            Some(line) => bad(line),
            None => internal(&format!(
                "vkforge {stage} exited {exit}; see {}",
                err_log.display()
            )),
        });
    }
    let printed = std::fs::read(&out_log).unwrap_or_default();
    let printed = String::from_utf8_lossy(&printed);
    let printed = printed.trim_end();
    let mut cut = printed.len().saturating_sub(TAIL_BYTES);
    while !printed.is_char_boundary(cut) {
        cut += 1;
    }
    let run = StageRun {
        stage,
        args: args.to_vec(),
        exit,
        duration_ms,
        stdout_tail: printed[cut..].to_string(),
    };
    tracing::info!(stage, duration_ms, tail = %run.stdout_tail, "vkforge stage done");
    Ok(run)
}

/// What `driver.learn` takes, and nothing else.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct LearnParams {
    family: String,
    app: String,
    /// Required, but by `learn` and not by serde: the deliberate fallback names no corpus.
    #[serde(default)]
    corpus: Option<PathBuf>,
    out: PathBuf,
    #[serde(default)]
    threshold: Option<f64>,
    /// An arch id: the CLI resolves a name, as recipes do.
    #[serde(default)]
    arch: Option<String>,
    /// A verb of the ontology, by presence: the fallback (ruling L-P3) in place of learning.
    #[serde(default)]
    deliberate: Option<String>,
}

/// The one family vkforge v1 knows, and its applications.
const FAMILY: &str = "office-document";
const APPS: [&str; 2] = ["word", "powerpoint"];
const DEFAULT_THRESHOLD: f64 = 0.8;

/// `OUT/.vault.json`, the clear text the miner set aside for the generator: deleted on every
/// way out of `learn` once `mine` may have written it — a refusal, an error, a panic — and,
/// strictly, after `generate`.
struct Vault(PathBuf);

impl Vault {
    fn remove(&self) -> std::io::Result<()> {
        match std::fs::remove_file(&self.0) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            other => other,
        }
    }
}

impl Drop for Vault {
    fn drop(&mut self) {
        if let Err(e) = self.remove() {
            tracing::error!(vault = %self.0.display(), error = %e, "the vault could not be deleted");
        }
    }
}

/// `driver.learn {family, app, corpus, out, threshold?, arch?}` — or, with `deliberate`, `{family,
/// app, out, arch, deliberate}` (see [`deliberate`]) — for a principal `dispatch` has
/// already proved human (I1). Both take the `Ctx` their pre-lock arm built as the `driver.pack`
/// arm does (L-P25): `learn`'s is human (`driver_learned`).
///
/// Refused before any stage is spawned: an unknown family or app, a relative or missing
/// corpus, a relative `out`, an `out` that holds anything or lies inside the corpus, an `out`
/// whose last component is not a driver name (it becomes the driver's), a threshold outside
/// `(0, 1]`, an arch this node has not mounted or that cannot plan, a name another `learn` or
/// `qualify` is working on (L-P55). Then the stages, each through [`run_stage`] with
/// `cfg.stage_deadline`, their logs under `<state_dir>/forge/<name>/`; the one model call per
/// ambiguity when `arch` is given; `driver.learned` and the `learned` row last.
pub fn learn(
    kernel: &Shared,
    cfg: &ForgeConfig,
    state_dir: &Path,
    ctx: &Ctx,
    p: Value,
) -> Result<Value, RpcError> {
    let LearnParams {
        family,
        app,
        corpus,
        out,
        threshold,
        arch,
        deliberate: verb,
    } = serde_json::from_value(p).map_err(|e| {
        bad(&format!(
            "driver.learn takes family, app, corpus, out, threshold, arch and deliberate, nothing \
             else ({e}); remedy: vk driver learn --family office-document --app word --corpus DIR \
             --out DIR"
        ))
    })?;
    if family != FAMILY {
        return Err(bad(&format!(
            "family {family} is not known; remedy: --family {FAMILY}"
        )));
    }
    if !APPS.contains(&app.as_str()) {
        return Err(bad(&format!(
            "app {app} is not known; remedy: --app word or --app powerpoint"
        )));
    }
    if let Some(verb) = verb {
        if corpus.is_some() || threshold.is_some() {
            return Err(bad(
                "--deliberate takes no corpus or threshold; remedy: drop them",
            ));
        }
        return deliberate(kernel, cfg, state_dir, ctx, &app, &out, arch, &verb);
    }
    let corpus = corpus.ok_or_else(|| {
        bad(
            "driver.learn needs a corpus; remedy: --corpus names a folder of the firm's documents \
             of one kind",
        )
    })?;
    for (flag, path) in [("corpus", &corpus), ("out", &out)] {
        if !path.is_absolute() {
            return Err(bad(&format!(
                "{flag} {} is not absolute; remedy: name it by its absolute path",
                path.display()
            )));
        }
    }
    if !corpus.is_dir() {
        return Err(bad(&format!(
            "corpus {} is not a directory; remedy: --corpus names a folder of the firm's documents \
             of one kind",
            corpus.display()
        )));
    }
    if !empty_or_absent(&out) {
        return Err(bad(&format!(
            "out {} is not empty; remedy: a new directory",
            out.display()
        )));
    }
    if inside_corpus(&out, &corpus) {
        return Err(bad(&format!(
            "out {} is inside the corpus; remedy: a new directory outside it",
            out.display()
        )));
    }
    let name = out
        .file_name()
        .and_then(|n| n.to_str())
        .filter(|n| is_driver_name(n))
        .ok_or_else(|| {
            bad(&format!(
                "out {}: its last component becomes the driver's name, and it is not a driver \
                 name (1 to 32 characters of a-z, 0-9 and -); remedy: name the directory like \
                 word-maison",
                out.display()
            ))
        })?
        .to_string();
    let threshold = threshold.unwrap_or(DEFAULT_THRESHOLD);
    if !(threshold > 0.0 && threshold <= 1.0) {
        return Err(bad(&format!(
            "threshold {threshold} is not in (0, 1]; remedy: --threshold 0.8"
        )));
    }
    if let Some(arch) = &arch {
        // The one model call is a Plan step (L-P10): an arch that cannot plan is refused here,
        // not after inventory, bind and mine have run.
        let plans = lock(kernel)?
            .arches()
            .into_iter()
            .find(|(id, _)| id == arch)
            .map(|(_, m)| m.capabilities.contains(&Capability::Plan));
        match plans {
            None => {
                return Err(bad(&format!(
                    "arch {arch} is not mounted on this node; remedy: vk ls /arches lists the \
                     arches this node has, by name and id"
                )))
            }
            Some(false) => {
                return Err(bad(&format!(
                    "arch {arch} cannot plan; remedy: an arch with the Plan capability"
                )))
            }
            Some(true) => {}
        }
    }

    // One request per driver name at a time (L-P55), held to the end of this function.
    let _claim = Claim::take(state_dir, &name, "learn")?;
    let logs = state_dir.join("forge").join(&name);
    if logs.exists() {
        // The daemon's own scratch: the logs of an earlier learning under this name.
        std::fs::remove_dir_all(&logs)
            .map_err(|e| io_failed("clear the earlier vkforge logs", &e))?;
    }
    let deadline = cfg.stage_deadline;
    let stage =
        |stage: &'static str, args: Vec<String>| run_stage(cfg, stage, &args, &logs, deadline);
    let binding = out.join(format!("binding-{app}.json"));
    let invariants = out.join("invariants.json");
    let plan_file = out.join("plan.json");

    // 1. The type library's catalogue (or the recorded one, under the test seam).
    let mut args = vec!["--app".into(), app.clone(), "--out".into(), arg(&out)];
    if cfg.fixtures {
        let fixture = cfg
            .root
            .join("vkforge")
            .join("tests")
            .join("fixtures")
            .join(format!("catalogue-{app}.json"));
        args.extend(["--fixture".into(), arg(&fixture)]);
    }
    stage("inventory", args)?;
    // 2. The binding of the ontology's verbs to that catalogue.
    stage(
        "bind",
        vec![
            "--catalogue".into(),
            arg(&out.join("catalogue.json")),
            "--out".into(),
            arg(&out),
        ],
    )?;
    // 3. The invariants of the corpus. From here the vault may exist, and goes on every way out.
    let vault = Vault(out.join(".vault.json"));
    stage(
        "mine",
        vec![
            "--corpus".into(),
            arg(&corpus),
            "--out".into(),
            arg(&out),
            "--threshold".into(),
            threshold.to_string(),
        ],
    )?;
    // 4. With an arch, one model call per ambiguity; then the plan.
    let mut args = vec![
        "--invariants".into(),
        arg(&invariants),
        "--binding".into(),
        arg(&binding),
        "--out".into(),
        arg(&out),
    ];
    if let Some(arch) = &arch {
        let mined = read_json(&invariants, "mine", "invariants.json")?;
        let mut resolver = BTreeMap::new();
        for ambiguity in mined["ambiguities"]
            .as_array()
            .map_or(&[][..], |a| a.as_slice())
        {
            let Some(invariant) = ambiguity["invariant"].as_str() else {
                return Err(internal(
                    "vkforge mine wrote an ambiguity without an invariant",
                ));
            };
            // One short lock phase each; the act is stamped when it happens.
            let now = Ctx {
                now_ms: vk_kernel::now_ms(),
                ..ctx.clone()
            };
            let chosen = lock(kernel)?
                .learn_ambiguity(&now, arch, ambiguity)
                .map_err(kerr)?;
            if let Some(value) = chosen {
                resolver.insert(
                    invariant.to_string(),
                    json!({"value": value, "resolved_by": format!("arch:{arch}")}),
                );
            }
        }
        let file = out.join("resolver.json");
        let mut bytes = serde_json::to_vec_pretty(&resolver)
            .map_err(|e| internal(&format!("resolver.json does not encode: {e}")))?;
        bytes.push(b'\n');
        std::fs::write(&file, bytes).map_err(|e| io_failed("write resolver.json", &e))?;
        args.extend(["--resolver".into(), arg(&file)]);
    }
    stage("plan", args)?;
    // 5. The driver, generated from the plan, its secrets resolved from the vault.
    stage(
        "generate",
        vec![
            "--plan".into(),
            arg(&plan_file),
            "--binding".into(),
            arg(&binding),
            "--invariants".into(),
            arg(&invariants),
            "--vault".into(),
            arg(&vault.0),
            "--out".into(),
            arg(&out.join("driver")),
            "--name".into(),
            name.clone(),
        ],
    )?;
    // 6. The vault goes, strictly; then what was learned is read back and recorded.
    vault.remove().map_err(|e| {
        internal(&format!(
            "the vault {} could not be deleted ({e}); remedy: delete it by hand, it holds clear \
             text from the corpus",
            vault.0.display()
        ))
    })?;
    let plan_bytes = read_output(&plan_file, "plan", "plan.json")?;
    let plan: DriverPlan = serde_json::from_slice(&plan_bytes).map_err(|e| {
        internal(&format!(
            "vkforge plan wrote a plan.json that does not parse: {e}"
        ))
    })?;
    plan.validate().map_err(|e| {
        internal(&format!(
            "vkforge plan wrote a plan that does not validate: {e}"
        ))
    })?;
    let mined = read_json(&invariants, "mine", "invariants.json")?;
    let corpus_schema_sha256 = mined["corpus"]["schema_sha256"]
        .as_str()
        .ok_or_else(|| internal("vkforge mine wrote no corpus.schema_sha256"))?
        .to_string();
    let manifest_file = out.join("driver").join("manifest.json");
    let manifest: DriverManifest = serde_json::from_value(read_json(
        &manifest_file,
        "generate",
        "driver/manifest.json",
    )?)
    .map_err(|e| {
        internal(&format!(
            "vkforge generate wrote a manifest that does not parse: {e}"
        ))
    })?;
    manifest.validate().map_err(|e| {
        internal(&format!(
            "vkforge generate wrote a manifest that does not validate: {e}"
        ))
    })?;
    let excluded = read_json(
        &out.join("plan-excluded.json"),
        "plan",
        "plan-excluded.json",
    )?;
    let unbound: Vec<Value> = read_json(&binding, "bind", "the binding")?["verbs"]
        .as_array()
        .map_or(&[][..], |v| v.as_slice())
        .iter()
        .filter(|v| v["status"] == "unbound")
        .map(|v| v["verb"].clone())
        .collect();
    let learned = DriverLearned {
        family,
        app,
        catalogue_sha256: plan.catalogue_sha256.clone(),
        ontology_sha256: plan.ontology_sha256.clone(),
        corpus_schema_sha256,
        invariants_sha256: plan.invariants_sha256.clone(),
        plan_sha256: plan_sha256(&plan_bytes),
        driver_id: manifest.driver_id(),
        // The kernel's to set, from the principal.
        by: String::new(),
    };
    let now = Ctx {
        now_ms: vk_kernel::now_ms(),
        ..ctx.clone()
    };
    lock(kernel)?
        .driver_learned(
            &now,
            learned.clone(),
            &manifest.name,
            &out,
            plan.qualification.clone(),
        )
        .map_err(kerr)?;
    Ok(json!({
        "driver_id": learned.driver_id,
        "name": manifest.name,
        "plan_sha256": learned.plan_sha256,
        "out": out.display().to_string(),
        "unbound": unbound,
        "ambiguities": plan.ambiguities,
        "excluded": excluded,
    }))
}

/// How much of the catalogue the fallback's evidence carries, and of the arch's answer the
/// proposal keeps.
const EVIDENCE_BYTES: usize = 20_000;
const ANSWER_CHARS: usize = 2_000;

/// `driver.learn` with `deliberate: VERB` (ruling L-P3, the fallback of spec §4.10): one `Draft`
/// step of an arch proposing a projection rule for a verb the learned catalogue could not bind. It
/// proposes and nothing else — no trace of the application, no code, nothing applied: the ontology
/// is a human's to edit. The proposal is the file `<out>/proposals/<verb>.json`.
///
/// Refused before the model is called: no arch (`--deliberate needs --arch`), an `out` that is not
/// absolute, not a learned driver's name or holds no `catalogue.json` of this app, a verb not in
/// the ontology, an arch not mounted or that cannot `Generate` (the capability a `Draft` step
/// infers with), a name another `learn` or `qualify` is working on (L-P55).
///
/// OS analogy: the engineer who cannot find a register in the datasheet writes a guess on a
/// sticky note for the reviewer; the board is not reflashed with it.
#[allow(clippy::too_many_arguments)]
fn deliberate(
    kernel: &Shared,
    cfg: &ForgeConfig,
    state_dir: &Path,
    ctx: &Ctx,
    app: &str,
    out: &Path,
    arch: Option<String>,
    verb: &str,
) -> Result<Value, RpcError> {
    let arch = arch.ok_or_else(|| {
        bad("--deliberate needs --arch: a proposal is drafted by an arch; remedy: --arch NAME")
    })?;
    if !out.is_absolute() {
        return Err(bad(&format!(
            "out {} is not absolute; remedy: name it by its absolute path",
            out.display()
        )));
    }
    let name = out
        .file_name()
        .and_then(|n| n.to_str())
        .filter(|n| is_driver_name(n))
        .ok_or_else(|| {
            bad(&format!(
                "out {}: its last component is a learned driver's name (1 to 32 characters of \
                 a-z, 0-9 and -); remedy: --out names a learned driver's output",
                out.display()
            ))
        })?
        .to_string();
    let catalogue_file = out.join("catalogue.json");
    if !catalogue_file.is_file() {
        return Err(bad(&format!(
            "out {} holds no catalogue.json; remedy: --out names a learned driver's output",
            out.display()
        )));
    }
    let ontology_file = cfg
        .root
        .join("vkforge")
        .join("ontology")
        .join(format!("{FAMILY}.json"));
    let ontology = read_json(&ontology_file, "deliberate", "the ontology")?;
    let entry = ontology["verbs"]
        .as_array()
        .and_then(|verbs| verbs.iter().find(|v| v["verb"] == verb))
        .ok_or_else(|| {
            bad(&format!(
                "verb {verb} is not in the ontology; remedy: --deliberate names a verb of \
                 {FAMILY}, see {}",
                ontology_file.display()
            ))
        })?;
    let catalogue = read_json(&catalogue_file, "inventory", "catalogue.json")?;
    if catalogue["app"] != app {
        let held = catalogue["app"].as_str().unwrap_or("another application");
        return Err(bad(&format!(
            "out {} holds the catalogue of {held}, not of {app}; remedy: --app {held}",
            out.display()
        )));
    }
    // `infer` enforces no capability itself: this check is the gate. A `Draft` step infers with
    // `Capability::Generate`.
    let drafts = lock(kernel)?
        .arches()
        .into_iter()
        .find(|(id, _)| *id == arch)
        .map(|(_, m)| m.capabilities.contains(&Capability::Generate));
    match drafts {
        None => {
            return Err(bad(&format!(
                "arch {arch} is not mounted on this node; remedy: vk ls /arches lists the arches \
                 this node has, by name and id"
            )))
        }
        Some(false) => {
            return Err(bad(&format!(
                "arch {arch} cannot draft; remedy: an arch with the Generate capability"
            )))
        }
        Some(true) => {}
    }

    // Held to the end of this function, as `learn` holds it (L-P55).
    let _claim = Claim::take(state_dir, &name, "deliberate")?;
    let goal = format!(
        "propose a projection rule as JSON {{\"member\": \"Type.Member\", \"constants\": \
         \"<Enum>\" | null}} for the verb {verb} on {app}"
    );
    let constraint = serde_json::to_string(entry)
        .map_err(|e| internal(&format!("the ontology entry does not encode: {e}")))?;
    let evidence = catalogue_slice(entry, &catalogue);
    let now = Ctx {
        now_ms: vk_kernel::now_ms(),
        ..ctx.clone()
    };
    let (task, decision) = lock(kernel)?
        .deliberate(&now, &arch, &goal, &constraint, &evidence)
        .map_err(kerr)?;
    let answer: String = decision.chars().take(ANSWER_CHARS).collect();
    let proposal = json!({
        "verb": verb,
        "app": app,
        "proposed_by": format!("arch:{arch}"),
        "task": task,
        "rule": rule_of(&answer),
        "answer": answer,
    });
    let dir = out.join("proposals");
    std::fs::create_dir_all(&dir).map_err(|e| io_failed("create the proposals directory", &e))?;
    let path = dir.join(format!("{verb}.json"));
    let mut bytes = serde_json::to_vec_pretty(&proposal)
        .map_err(|e| internal(&format!("the proposal does not encode: {e}")))?;
    bytes.push(b'\n');
    std::fs::write(&path, bytes).map_err(|e| io_failed("write the proposal", &e))?;
    Ok(json!({"proposal": proposal, "path": path.display().to_string()}))
}

/// What the arch reads of the catalogue: the members of the types the verb's projections name, for
/// any application, that this catalogue has; else the catalogue's type names alone. At most
/// [`EVIDENCE_BYTES`], cut on a character boundary.
fn catalogue_slice(entry: &Value, catalogue: &Value) -> String {
    let types = catalogue["types"]
        .as_array()
        .map_or(&[][..], |t| t.as_slice());
    let heads: Vec<&str> = entry["projection"]
        .as_object()
        .into_iter()
        .flat_map(|apps| apps.values())
        .filter_map(|rule| rule["member"].as_str())
        .filter_map(|member| member.split('.').next())
        .collect();
    let named: serde_json::Map<String, Value> = types
        .iter()
        .filter(|t| t["name"].as_str().is_some_and(|n| heads.contains(&n)))
        .filter_map(|t| Some((t["name"].as_str()?.to_string(), t["members"].clone())))
        .collect();
    let slice = if named.is_empty() {
        let names: Vec<&Value> = types.iter().map(|t| &t["name"]).collect();
        json!({"types": names})
    } else {
        Value::Object(named)
    };
    let mut text = slice.to_string();
    if text.len() > EVIDENCE_BYTES {
        let mut cut = EVIDENCE_BYTES;
        while !text.is_char_boundary(cut) {
            cut -= 1;
        }
        text.truncate(cut);
    }
    text
}

/// The rule an answer carries (ruling L-P29): the first JSON object in it with exactly the keys
/// `member` (a string) and `constants` (a string or null), else `null`. The scan goes `{` by `{`,
/// inside an object that is not the rule included.
fn rule_of(answer: &str) -> Value {
    let mut at = 0;
    while let Some(offset) = answer[at..].find('{') {
        let start = at + offset;
        if let Some(Ok(Value::Object(o))) = serde_json::Deserializer::from_str(&answer[start..])
            .into_iter::<Value>()
            .next()
        {
            if o.len() == 2 && o.get("member").is_some_and(Value::is_string) {
                if let Some(constants) = o.get("constants").filter(|c| c.is_string() || c.is_null())
                {
                    return json!({"member": o["member"], "constants": constants});
                }
            }
        }
        at = start + 1;
    }
    Value::Null
}

/// What `driver.qualify` takes, and nothing else.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct QualifyParams {
    driver: PathBuf,
    corpus: PathBuf,
}

/// `driver.qualify {driver, corpus}`: the `qualify` stage replays the learned driver on copies
/// of the corpus, and `driver.qualified` records the replay (kernel-written: whatever principal
/// the caller presented, L-P25). Refused before anything is spawned: relative paths, a directory
/// that is not `<learn out>/driver` with `plan.json` and `invariants.json` beside it, a driver
/// this node did not learn, a name another `learn` or `qualify` is working on (L-P55). The
/// stage's work directory is the daemon's own scratch,
/// `<state_dir>/drivers/<name>/work/qualify`, emptied first; its logs go under
/// `<state_dir>/forge/qualify-<name>/`; its deadline is a minute and 130 s per document.
pub fn qualify(
    kernel: &Shared,
    cfg: &ForgeConfig,
    state_dir: &Path,
    ctx: &Ctx,
    p: Value,
) -> Result<Value, RpcError> {
    let QualifyParams { driver, corpus } = serde_json::from_value(p).map_err(|e| {
        bad(&format!(
            "driver.qualify takes driver and corpus, nothing else ({e}); remedy: vk driver \
             qualify --driver DIR --corpus DIR"
        ))
    })?;
    for (flag, path) in [("driver", &driver), ("corpus", &corpus)] {
        if !path.is_absolute() {
            return Err(bad(&format!(
                "{flag} {} is not absolute; remedy: name it by its absolute path",
                path.display()
            )));
        }
    }
    let not_learned_dir = || {
        bad(&format!(
            "{} is not a learned driver's directory; remedy: vk driver qualify --driver <learn \
             out>\\driver",
            driver.display()
        ))
    };
    let out = driver.parent().ok_or_else(not_learned_dir)?;
    let (plan_file, invariants) = (out.join("plan.json"), out.join("invariants.json"));
    let manifest_file = driver.join("manifest.json");
    if !(manifest_file.is_file() && plan_file.is_file() && invariants.is_file()) {
        return Err(not_learned_dir());
    }
    let manifest: DriverManifest = std::fs::read(&manifest_file)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .ok_or_else(not_learned_dir)?;
    let documents = documents_in(&corpus)?;
    let driver_id = manifest.driver_id();
    let row = lock(kernel)?
        .learned(&driver_id)
        .map_err(kerr)?
        .ok_or_else(|| kerr(vk_kernel::learn::not_learned(&driver_id)))?;
    let name = manifest.name.clone();
    if !is_driver_name(&name) {
        return Err(not_learned_dir());
    }
    // One request per driver name at a time (L-P55), held to the end of this function.
    let _claim = Claim::take(state_dir, &name, "qualify")?;
    let work_parent = state_dir.join("drivers").join(&name).join("work");
    let work = work_parent.join("qualify");
    vk_store::paths::private_dir(&work_parent)
        .map_err(|e| io_failed("create the driver's work directory", &e))?;
    if work.exists() {
        std::fs::remove_dir_all(&work)
            .map_err(|e| io_failed("empty the qualification's work directory", &e))?;
    }
    let logs = state_dir.join("forge").join(format!("qualify-{name}"));
    let mut args = vec![
        "--driver".into(),
        arg(&driver),
        "--corpus".into(),
        arg(&corpus),
        "--plan".into(),
        arg(&plan_file),
        "--invariants".into(),
        arg(&invariants),
        "--work".into(),
        arg(&work),
    ];
    if cfg.fixtures {
        args.push("--fake".into());
    }
    let deadline = QUALIFY_BASE + QUALIFY_PER_DOCUMENT * documents;
    run_stage(cfg, "qualify", &args, &logs, deadline)?;
    let record = work.join("qualification.json");
    let bytes = read_output(&record, "qualify", "qualification.json")?;
    let rec: Value = serde_json::from_slice(&bytes).map_err(|e| {
        internal(&format!(
            "vkforge qualify wrote a record that does not parse: {e}"
        ))
    })?;
    if rec["plan_sha256"].as_str() != Some(row.learned.plan_sha256.as_str()) {
        return Err(bad(
            "the plan changed since vk driver learn; remedy: learn again",
        ));
    }
    let count = |field: &str| {
        rec[field]
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .ok_or_else(|| internal(&format!("vkforge qualify wrote no {field} count")))
    };
    let (passes, failures) = (count("passes")?, count("failures")?);
    let now = Ctx {
        now_ms: vk_kernel::now_ms(),
        ..ctx.clone()
    };
    lock(kernel)?
        .driver_qualified(
            &now,
            DriverQualified {
                driver_id: driver_id.clone(),
                passes,
                failures,
                qualification_sha256: hash_bytes(&bytes),
            },
            &record,
        )
        .map_err(kerr)?;
    Ok(json!({
        "driver_id": driver_id,
        "name": name,
        "passes": passes,
        "failures": failures,
        "rate": rec["rate"],
        "fake": rec["fake"],
        "record": record.display().to_string(),
    }))
}

/// What `driver.plan_show` takes, and nothing else.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct PlanShowParams {
    dir: PathBuf,
}

/// `driver.plan_show {dir}`: a learned driver's plan, for review before `vk driver qualify` —
/// `{plan: <DriverPlan, validated>, plan_sha256, excluded: <plan-excluded.json, or []>}`. Reads
/// two files; no kernel state, no presence.
pub fn plan_show(p: Value) -> Result<Value, RpcError> {
    let PlanShowParams { dir } = serde_json::from_value(p).map_err(|e| {
        bad(&format!(
            "driver.plan_show takes dir, nothing else ({e}); remedy: vk driver plan show DIR"
        ))
    })?;
    if !dir.is_absolute() {
        return Err(bad(&format!(
            "dir {} is not absolute; remedy: name it by its absolute path",
            dir.display()
        )));
    }
    let file = dir.join("plan.json");
    let bytes = std::fs::read(&file).map_err(|e| {
        bad(&format!(
            "{} cannot be read ({}); remedy: vk driver plan show <the directory vk driver learn \
             wrote>",
            file.display(),
            e.kind()
        ))
    })?;
    let plan: DriverPlan = serde_json::from_slice(&bytes).map_err(|e| {
        bad(&format!(
            "{} does not parse ({e}); remedy: learn the driver again",
            file.display()
        ))
    })?;
    plan.validate()
        .map_err(|e| bad(&format!("{}: {e}", file.display())))?;
    let excluded_file = dir.join("plan-excluded.json");
    let excluded = match std::fs::read(&excluded_file) {
        Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| {
            bad(&format!(
                "{} does not parse ({e}); remedy: learn the driver again",
                excluded_file.display()
            ))
        })?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => json!([]),
        Err(e) => {
            return Err(bad(&format!(
                "{} cannot be read ({}); remedy: learn the driver again",
                excluded_file.display(),
                e.kind()
            )))
        }
    };
    Ok(json!({
        "plan": plan,
        "plan_sha256": plan_sha256(&bytes),
        "excluded": excluded,
    }))
}

/// How long a debrief may run: it reads a run's files, a few megabytes.
pub const DEBRIEF_DEADLINE: Duration = Duration::from_secs(120);

/// What `driver.debrief` takes, and nothing else.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct DebriefParams {
    run: PathBuf,
    out: PathBuf,
    #[serde(default)]
    until: Option<String>,
}

/// `driver.debrief {run, out, until?}` (founder decision 2026-10-17): vkforge's `debrief` stage
/// on a run directory the operator assembled, under the same governor as `learn`'s stages, its
/// logs under `<state_dir>/forge/debrief/<run name>/`. A machine act: no presence proof and no
/// ledger row (ruling L-P5) — the answer carries the hashes of `debrief.json` and
/// `proposals.json`, so whoever applies a proposal can name the evidence that motivated it.
/// Nothing is applied.
///
/// Refused before spawning, in the kernel's voice, what the stage would refuse: a relative path
/// (the stage runs in the vkforge root, not in the daemon's working directory), a run without
/// `journal.jsonl`, an `out` that is a file, is not empty, lies inside the run directory or inside
/// a driver's directory (its files are the driver's identity); and a second debrief of a run of
/// the same name while one runs.
pub fn debrief(cfg: &ForgeConfig, state_dir: &Path, p: Value) -> Result<Value, RpcError> {
    let DebriefParams { run, out, until } = serde_json::from_value(p).map_err(|e| {
        bad(&format!(
            "driver.debrief takes run, out and until, nothing else ({e}); remedy: vk driver \
             debrief --run DIR --out DIR"
        ))
    })?;
    for (flag, path) in [("run", &run), ("out", &out)] {
        if !path.is_absolute() {
            return Err(bad(&format!(
                "{flag} {} is not absolute; remedy: name it by its absolute path",
                path.display()
            )));
        }
    }
    if !run.join("journal.jsonl").is_file() {
        return Err(bad(&format!(
            "no journal.jsonl under {}; remedy: copy the driver's journal (its work directory) \
             into the run directory",
            run.display()
        )));
    }
    if out.exists() && !out.is_dir() {
        return Err(bad(&format!(
            "out {} is not a directory; remedy: a new directory: a debrief never overwrites one",
            out.display()
        )));
    }
    if !empty_or_absent(&out) {
        return Err(bad(&format!(
            "out {} is not empty; remedy: a new directory: a debrief never overwrites one",
            out.display()
        )));
    }
    // The containment `learn` checks for its corpus, on canonical paths.
    if inside_corpus(&out, &run) {
        return Err(bad(&format!(
            "out {} is inside the run directory; remedy: a new directory outside it: the run \
             directory is read, never written",
            out.display()
        )));
    }
    // Three new files in a driver's directory would change its `files_sha256`: the next spawn of
    // a mounted driver would be refused, and the TCB says the debrief never writes into a driver.
    if driver_above(&out, state_dir).is_some() {
        return Err(bad(&format!(
            "out {} is inside a driver; remedy: an output directory of its own",
            out.display()
        )));
    }
    // One debrief per run name at a time (L-P55's lock): two would write the same stage logs. The
    // key holds a `/`, which no driver name does, so it never meets a `learn` or a `qualify`.
    let run_name = run
        .file_name()
        .map_or_else(|| "run".to_string(), |n| n.to_string_lossy().into_owned());
    let _claim = Claim::take(state_dir, &format!("debrief/{run_name}"), "debrief")?;
    let mut args = vec!["--run".into(), arg(&run), "--out".into(), arg(&out)];
    if let Some(task) = until {
        // One argument, so a value that starts with `-` is still the value.
        args.push(format!("--until={task}"));
    }
    let logs = state_dir.join("forge").join("debrief").join(&run_name);
    run_stage(cfg, "debrief", &args, &logs, DEBRIEF_DEADLINE)?;
    let signals = read_output(&out.join("debrief.json"), "debrief", "debrief.json")?;
    let proposals = read_output(&out.join("proposals.json"), "debrief", "proposals.json")?;
    let list: Vec<Value> = serde_json::from_slice(&proposals).map_err(|e| {
        internal(&format!(
            "vkforge debrief wrote a proposals.json that does not parse ({e})"
        ))
    })?;
    let list: Vec<Value> = list
        .iter()
        .map(|p| {
            json!({
                "kind": p["kind"],
                "target": p["target"],
                "confidence": p["confidence"],
                "proposal": p["proposal"],
            })
        })
        .collect();
    Ok(json!({
        "out": arg(&out),
        "proposals": list.len(),
        "list": list,
        "debrief_sha256": hash_bytes(&signals),
        "proposals_sha256": hash_bytes(&proposals),
    }))
}

/// The driver directory `out` would land in: `out` itself or an ancestor holding a `driver.py` or
/// a `manifest.json`, looked for on canonical paths up to the state directory (excluded).
fn driver_above(out: &Path, state_dir: &Path) -> Option<PathBuf> {
    let state = std::fs::canonicalize(state_dir).ok();
    for dir in out
        .ancestors()
        .filter_map(|a| std::fs::canonicalize(a).ok())
    {
        if state.as_ref() == Some(&dir) {
            break;
        }
        if dir.join("driver.py").is_file() || dir.join("manifest.json").is_file() {
            return Some(dir);
        }
    }
    None
}

/// A path as a stage argument. The paths came in as JSON strings, so they are UTF-8.
fn arg(p: &Path) -> String {
    p.to_string_lossy().into_owned()
}

/// A JSON file a stage wrote, or the internal error naming the stage that should have.
fn read_json(file: &Path, stage: &str, what: &str) -> Result<Value, RpcError> {
    let bytes = read_output(file, stage, what)?;
    serde_json::from_slice(&bytes).map_err(|e| {
        internal(&format!(
            "vkforge {stage} wrote a {what} that does not parse ({e})"
        ))
    })
}

/// The bytes of a file a stage wrote, or the internal error naming the stage that should have:
/// one failure class — a stage's output missing or unreadable — one code (review fix round 1).
/// `io_failed` stays for the daemon's own directories.
fn read_output(file: &Path, stage: &str, what: &str) -> Result<Vec<u8>, RpcError> {
    std::fs::read(file)
        .map_err(|e| internal(&format!("vkforge {stage} wrote no readable {what} ({e})")))
}

/// Does `out` — absent, or an empty directory — lie inside `corpus`? Decided on canonical paths
/// (review fix round 1): case, `..` and junctions resolved as the file system resolves them, `out`
/// standing for its nearest existing ancestor, so a spelling that differs only lexically is still
/// refused before inventory and bind run. A corpus that cannot be resolved falls back to the
/// lexical comparison.
fn inside_corpus(out: &Path, corpus: &Path) -> bool {
    let Ok(corpus) = std::fs::canonicalize(corpus) else {
        return out.starts_with(corpus);
    };
    out.ancestors()
        .find_map(|a| std::fs::canonicalize(a).ok())
        .is_some_and(|a| a.starts_with(&corpus))
}

/// The driver names a vkforge request is working on, per state directory (ruling L-P55): two
/// requests for one name would share `<state>/forge/<name>` or
/// `<state>/drivers/<name>/work/qualify` and clear each other's files mid-run. One daemon serves one state directory (the
/// store's lock), so the key is the daemon's; a test process running several daemons keeps them
/// apart by their directories.
static IN_FLIGHT: Mutex<BTreeMap<(PathBuf, String), &'static str>> = Mutex::new(BTreeMap::new());

/// One request's hold on a driver name: taken before anything of that name is cleared or
/// spawned, released on every way out — a refusal, an error, a panic.
struct Claim {
    key: (PathBuf, String),
}

impl Claim {
    /// The name for `verb`, or the refusal naming what holds it.
    fn take(state_dir: &Path, name: &str, verb: &'static str) -> Result<Claim, RpcError> {
        let key = (state_dir.to_path_buf(), name.to_string());
        let mut held = IN_FLIGHT.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(busy) = held.get(&key) {
            return Err(bad(&format!(
                "{busy} of {name} already in progress; remedy: wait for it"
            )));
        }
        held.insert(key.clone(), verb);
        Ok(Claim { key })
    }
}

impl Drop for Claim {
    fn drop(&mut self) {
        IN_FLIGHT
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .remove(&self.key);
    }
}

/// The documents of a corpus, as the miner counts them: the `.docx` and `.pptx` files directly
/// in it, an Office lock file (`~$…`) aside. The qualification budget is per document.
fn documents_in(corpus: &Path) -> Result<u32, RpcError> {
    let entries = std::fs::read_dir(corpus).map_err(|e| {
        bad(&format!(
            "corpus {} cannot be read ({}); remedy: --corpus names the folder the driver was \
             learned from",
            corpus.display(),
            e.kind()
        ))
    })?;
    let mut n = 0u32;
    for entry in entries.flatten() {
        let path = entry.path();
        let named = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
        let document = path
            .extension()
            .and_then(|e| e.to_str())
            .is_some_and(|e| e.eq_ignore_ascii_case("docx") || e.eq_ignore_ascii_case("pptx"));
        if document && !named.starts_with("~$") && path.is_file() {
            n = n.saturating_add(1);
        }
    }
    Ok(n)
}

/// Absent, or an empty directory.
fn empty_or_absent(dir: &Path) -> bool {
    match std::fs::read_dir(dir) {
        Ok(mut entries) => entries.next().is_none(),
        Err(e) => e.kind() == std::io::ErrorKind::NotFound && !dir.exists(),
    }
}

fn io_failed(what: &str, e: &std::io::Error) -> RpcError {
    RpcError {
        code: E_STORE,
        message: format!("{what}: {e}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The rule of an answer is the first `{member, constants}` object in it (L-P29), else null.
    #[test]
    fn a_rule_is_the_first_object_with_exactly_member_and_constants() {
        assert_eq!(
            rule_of(r#"draft: use {"member": "Slide.Layout", "constants": "PpSlideLayout"} here"#),
            json!({"member": "Slide.Layout", "constants": "PpSlideLayout"})
        );
        assert_eq!(
            rule_of(r#"draft: {"member": "Slide.Layout", "constants": null}"#),
            json!({"member": "Slide.Layout", "constants": null})
        );
        for none in [
            r#"draft: {"member": "A.B", "constants": null, "why": "x"}"#,
            r#"draft: {"member": "A.B"}"#,
            r#"draft: {"member": 3, "constants": null}"#,
            r#"draft: {"member": "Type.Member", "constants": "<Enum>" | null}"#,
            "draft: no rule, a {brace",
        ] {
            assert_eq!(rule_of(none), Value::Null, "{none}");
        }
    }

    /// The evidence is the members of the types the verb names when the catalogue has them, else
    /// the type names alone, and never more than 20 kB.
    #[test]
    fn the_evidence_slice_names_members_or_types_and_is_bounded() {
        let entry = json!({"projection": {"powerpoint": {"member": "Slide.Layout"},
                                           "word": {"member": "Document.Save"}}});
        let catalogue = json!({"types": [
            {"name": "Document", "members": [{"name": "Save"}]},
            {"name": "Range", "members": []}]});
        assert_eq!(
            catalogue_slice(&entry, &catalogue),
            json!({"Document": [{"name": "Save"}]}).to_string()
        );
        let entry = json!({"projection": {"powerpoint": {"member": "Slide.Layout"}}});
        assert_eq!(
            catalogue_slice(&entry, &catalogue),
            json!({"types": ["Document", "Range"]}).to_string()
        );
        let big = json!({"types": [{"name": "\u{e9}".repeat(30_000), "members": []}]});
        assert!(catalogue_slice(&entry, &big).len() <= EVIDENCE_BYTES);
    }

    /// A relative `--vkforge-root` is made absolute at start (review fix round 1), so the stage —
    /// run in the root — is handed a script path and a `--fixture` path that name the same files.
    #[test]
    fn a_relative_root_is_made_absolute_at_start() {
        let cfg = ForgeConfig::at("drivers".into());
        assert!(cfg.root.is_absolute(), "{}", cfg.root.display());
        assert_eq!(
            cfg.root,
            std::env::current_dir().unwrap().join("drivers"),
            "against the daemon's working directory at start"
        );
    }

    /// `out` inside the corpus is decided on canonical paths: a `..` spelling, and on Windows a
    /// difference of case, is still inside; a sibling is not.
    #[test]
    fn out_inside_the_corpus_is_decided_on_canonical_paths() {
        let t = tempfile::tempdir().unwrap();
        let corpus = t.path().join("Corpus");
        std::fs::create_dir_all(&corpus).unwrap();
        std::fs::create_dir_all(t.path().join("other")).unwrap();
        let dotted = t
            .path()
            .join("other")
            .join("..")
            .join("Corpus")
            .join("word-maison");
        assert!(!dotted.starts_with(&corpus), "lexically outside");
        assert!(inside_corpus(&dotted, &corpus), "canonically inside");
        assert!(inside_corpus(
            &corpus.join("a").join("word-maison"),
            &corpus
        ));
        assert!(!inside_corpus(&t.path().join("word-maison"), &corpus));
        if cfg!(windows) {
            let cased = t.path().join("CORPUS").join("word-maison");
            assert!(
                inside_corpus(&cased, &corpus),
                "Windows names are case-blind"
            );
        }
    }

    /// One `learn` or `qualify` per driver name and state directory at a time (L-P55): a second
    /// claim on the name is refused with what holds it, another name or another state directory
    /// is not, and the name is free again once the claim is dropped.
    #[test]
    fn one_request_per_driver_name_at_a_time() {
        let t = tempfile::tempdir().unwrap();
        let (a, b) = (t.path().join("a"), t.path().join("b"));
        let held = Claim::take(&a, "word-maison", "learn").unwrap();
        let e = Claim::take(&a, "word-maison", "qualify")
            .err()
            .expect("the name is held");
        assert_eq!(
            e.message,
            "learn of word-maison already in progress; remedy: wait for it"
        );
        let other_name = Claim::take(&a, "ppt-maison", "qualify").unwrap();
        let other_node = Claim::take(&b, "word-maison", "qualify").unwrap();
        drop(held);
        Claim::take(&a, "word-maison", "qualify").expect("free once released");
        drop((other_name, other_node));
    }

    /// One debrief per run name and state directory at a time, refused before the stage is
    /// spawned; a `learn` of a driver named `debrief` holds another key, and another run name too.
    #[test]
    fn a_debrief_waits_for_the_one_in_progress_on_its_run() {
        let t = tempfile::tempdir().unwrap();
        let (state, run) = (t.path().join("state"), t.path().join("run"));
        std::fs::create_dir_all(&run).unwrap();
        std::fs::write(run.join("journal.jsonl"), "").unwrap();
        let cfg = ForgeConfig::at(t.path().join("nowhere"));
        let params = json!({"run": run, "out": t.path().join("out")});
        let held = Claim::take(&state, "debrief/run", "debrief").unwrap();
        let e = debrief(&cfg, &state, params.clone()).unwrap_err();
        assert_eq!(
            e.message,
            "debrief of debrief/run already in progress; remedy: wait for it"
        );
        assert!(!state.join("forge").exists(), "refused before a spawn");
        drop(held);
        let others = (
            Claim::take(&state, "debrief", "learn").unwrap(),
            Claim::take(&state, "debrief/other-run", "debrief").unwrap(),
        );
        let e = debrief(&cfg, &state, params).unwrap_err();
        assert!(
            e.message.starts_with("vkforge root not found"),
            "{}",
            e.message
        );
        drop(others);
    }

    /// An `out` in a driver's directory, or below one, is refused: three new files there would
    /// change the driver's `files_sha256`. The search stops at the state directory.
    #[test]
    fn out_inside_a_driver_is_refused() {
        let t = tempfile::tempdir().unwrap();
        let (state, run) = (t.path().join("state"), t.path().join("run"));
        for d in [
            &state,
            &run,
            &t.path().join("drivers").join("word-maison").join("sub"),
        ] {
            std::fs::create_dir_all(d).unwrap();
        }
        std::fs::write(run.join("journal.jsonl"), "{}").unwrap();
        let driver = t.path().join("drivers").join("word-maison");
        std::fs::write(driver.join("driver.py"), "").unwrap();
        std::fs::write(state.join("manifest.json"), "{}").unwrap();
        let cfg = ForgeConfig::at(t.path().join("nowhere"));
        for out in [driver.join("debrief"), driver.join("sub").join("debrief")] {
            let e = debrief(&cfg, &state, json!({"run": run, "out": out})).unwrap_err();
            assert_eq!(
                e.message,
                format!(
                    "out {} is inside a driver; remedy: an output directory of its own",
                    out.display()
                )
            );
        }
        assert_eq!(
            driver_above(&state.join("debrief").join("out"), &state),
            None,
            "the state directory itself is not searched"
        );
    }

    /// A root without `vkforge/__main__.py` is refused at the first spawn, before anything is
    /// written (L-P26): `ForgeConfig::at` checks nothing.
    #[test]
    fn a_root_without_vkforge_is_refused_at_the_first_spawn() {
        let t = tempfile::tempdir().unwrap();
        let cfg = ForgeConfig::at(t.path().join("nowhere"));
        let logs = t.path().join("logs");
        let e = run_stage(&cfg, "inventory", &[], &logs, STAGE_DEADLINE).unwrap_err();
        assert_eq!(
            e.message,
            "vkforge root not found; remedy: start vkd with --vkforge-root <drivers/>"
        );
        assert!(!logs.exists(), "refused before the log directory was made");
    }

    /// The qualification budget counts the documents the miner would, and nothing else.
    #[test]
    fn the_qualification_budget_counts_documents() {
        let t = tempfile::tempdir().unwrap();
        for f in [
            "a.docx",
            "b.DOCX",
            "c.pptx",
            "a.pdf",
            "~$a.docx",
            "notes.txt",
        ] {
            std::fs::write(t.path().join(f), b"x").unwrap();
        }
        std::fs::create_dir(t.path().join("sub.docx")).unwrap();
        assert_eq!(documents_in(t.path()).unwrap(), 3);
    }
}
