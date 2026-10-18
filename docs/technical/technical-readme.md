# VerticalAI

Self-improving, peer-to-peer, local-first orchestration runtime for businesses.
Design: `docs/technical/specs/2026-09-22-verticalai-design.md`.
Plan for this stage: `docs/archive/technical/plans/2026-09-22-sp0-contracts-and-kernel-boundary.md`.

## SP0 — contracts and kernel boundary

- `contracts/` — language-neutral JSON Schemas (generated from `crates/vk-contracts`) and protocol documents.
- `crates/vk-contracts` — the contract types; the schemas are generated from them.
- `crates/vk-stub` — in-memory stub kernel with the interceptor chain, used to test invariants.
- `crates/vk-props` — property tests for invariants I1–I4′.

### Contracts index

| Schema | Type | Spec |
|---|---|---|
| `label`, `clearance` | information-flow lattice | §3.2, §4.2 |
| `register` | task-state IR | §3.2 |
| `arch_manifest` | arch descriptor with content-addressed identity | §3.7 |
| `principal`, `challenge`, `approval` | principals, human ceremony, approval records (I1) | §3.6 |
| `lease` | work-in-progress lease with fence | §3.4 |
| `stop_event`, `resume_event`, `liveness_lease` | STOP primitive and autonomy liveness (I4) | §3.5 |
| `liveness_renewed` | payload of the `liveness.renewed` ledger event: who renewed which business's lease, until when, and the expiry it replaced | §3.5 |
| `ledger_event` | hash-chained ledger event with three stamps | §3.9 |
| `blob_envelope`, `shred_event` | storage tiering and erasure | §3.9 |
| `module_manifest`, `validator_manifest`, `gate_verdict` | userland object formats | §4.1, §4.3 |
| `syscall_ctx` | authenticated call context | §3.11 |
| `tuf_root` | federation root role with threshold signatures | §4.4 |

Protocol documents: `contracts/identity/phone-bootstrap.md`, `contracts/federation/tuf-roles.md`, `contracts/tcb.md`.

## Build

MSRV: Rust 1.98.1 (stable, 2026-09-01), MSVC toolchain on Windows (`rust-toolchain.toml` pins `stable`).

```
cargo test --workspace                          # 121 tests: unit, schema drift, examples, stub, properties, end-to-end shell
cargo run -p vk-contracts --bin gen-schemas     # regenerate contracts/schemas/ after changing a contract type
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all -- --check
```

Windows notes:
- Install Visual Studio 2022 Build Tools with the "Desktop development with C++" workload before `rustup`. The GNU host toolchain does not work here: rustup's self-contained `dlltool` cannot build the import libraries that `raw-dylib` crates (`getrandom`, `windows-sys`) need.
- Build outside OneDrive-synced folders: `set CARGO_TARGET_DIR=%USERPROFILE%\.cargo-target\verticalai`.
- The passkey verifier (`vk-web`, SP1b) is `webauthn-rs`, which verifies with OpenSSL built from source (`openssl/vendored`). That build needs a **Windows-native Perl**: install [Strawberry Perl](https://strawberryperl.com/) (`winget install StrawberryPerl.StrawberryPerl`) or point `OPENSSL_SRC_PERL` at a portable one's `perl.exe`. Git for Windows' Perl does not do — OpenSSL's `Configure` cannot even load under it. The first build takes about fifteen minutes (no assembler: `nasm` is optional and speeds it up); every later build reuses it. GitHub's Windows runners have Strawberry Perl already; Linux and macOS need only `perl` and a C compiler.

## Running the kernel (SP1a)

`vkd` is the kernel daemon — one per user account, one endpoint, one store.
`vk` is the shell: every verb is one syscall over that endpoint, printed as a
table or, with `--json`, as the daemon's own answer.

### The OS surface

| `vk` verb | OS concept |
|---|---|
| `vk boot [--force]` | `init`: start `vkd` over a state directory and wait until it answers |
| `vk status` | `uname`: what this node is, and what its boot sequence found |
| `vk ls PATH` | the namespace: `/arches`, `/drivers`, `/tasks`, `/artefacts`, `/devices`, `/ledger` as directories |
| `vk ps`, `vk top [--calls]` | the scheduler: what every task is doing, and per arch where it runs, under whose law, whether this kernel governs it, its calls, tokens, measured tokens and cost — `--calls` adds one row per call, naming the step that spent it |
| `vk mount mock NAME`, `vk mount claude-code`, `vk mount anthropic`, `vk mount bedrock`, `vk mount ollama --model TAG`, `vk umount ID` | drivers: an arch is a device this kernel drives — and, for `ollama`, a process it starts and caps |
| `vk arch show ID` | one arch in full: its manifest, the identity tuple the arch id hashes, the clearance it may be handed, and what the next boot would make it from |
| `vk secret set NAME` | the credential store: a secret this node's daemon reads, put in this account's OS keyring without ever being echoed or printed |
| `vk task submit` / `step` / `show` | processes: a task is the unit of work, its register is its address space. `vk task submit --steps FILE` submits a **recipe**, a task whose steps a file lists, arches named by manifest name (pilot ruling R21) |
| `vk mount driver --manifest FILE`, `vk driver ls\|show\|umount` | device drivers: a browser behind a Python process this kernel starts, caps and lets a harness step act through ([Drivers](#drivers-a-browser-this-kernel-controls)) |
| `vk artefact cat HASH [--out FILE]` | `cat` of what a task attached, as a human act: signed with this node's device key and written to the ledger as an `artefact.released` event, destination `terminal:<device_id>` (pilot rulings R22, R24) |
| `vk stop [SCOPE]`, `vk resume ID` | signals: a STOP halts a scope until a human lifts it |
| `vk liveness renew --business ID [--until +30d\|RFC3339]` | the liveness lease (I4): a human opens or extends the window in which automatic runs are allowed (default `+7d`, at most thirty days). Signed with this node's device key like `vk approve` — refused with the remedy when this account has no enrolled key — and recorded on the ledger as one `liveness.renewed` event (`business`, `device_id`, `until_ms`, `previous_until_ms`, `scope: "automatic"`); `vk top` shows the expiry |
| `vk approve ID [--passkey]` | the human ceremony: an approval of a kernel-minted challenge, signed by this node's device key or by a passkey in the browser (invariant I1). **The trust model:** the node's device key sits in this account's OS keyring, so every process running as this user can sign with it — every same-user process counts as "the human" for as long as the node key is enrolled. A passkey (Windows Hello, a phone) adds a **second** human key; it does not retire the first, and nothing revokes the node key yet — the revocation gap is stated in `contracts/tcb.md` (SP1a's stand-in, Rulings 17/20) |
| `vk passkey enroll [--open]`, `vk passkey ls` | the human's own device: a passkey (Windows Hello, a phone) enrolled through the browser |
| `vk dmesg -n N` | the kernel ring buffer: the tail of the hash-chained ledger |
| `vk ledger verify` | `fsck` for the record: does the hash chain recompute |
| `vk fsck [--json]` | `fsck` for the **whole store**: the chain, the recorded head, every blob against its own address, every wrapped key, and the mounts. Exits non-zero if any tier fails |
| `vk man [NAME]` | the contracts this kernel speaks — the syscall ABI, out of `contracts/schemas/` |

### Arches: this machine's Claude, and a customer's

`vk mount claude-code` mounts two arches — a draft one and a judge one —
through the Claude Code already installed on this machine, driven as a pure
completion engine: every built-in tool removed, MCP off, one turn, no session
on disk, and the prompt on stdin so no process list shows it.

```
vk mount claude-code                                    # sonnet to draft, opus to judge
vk mount claude-code --draft-model claude-sonnet-5 --judge-model claude-opus-5 \
                     --timeout 180
```

**Which `claude` runs is the daemon's to say, not the mount's.** By default it
is `claude` on **the daemon's** `PATH` (`claude.exe` on Windows, never the
`.cmd` shim), found when the daemon starts; another one is daemon
configuration:

```
vkd --claude-bin /opt/claude/bin/claude                 # or:
vk boot --claude-bin /opt/claude/bin/claude             # passed on to vkd
vkd-service install --claude-bin "C:\Program Files\Claude\claude.exe"   # the service
```

The daemon checks it **once, when it starts**, and a path it refuses is a
start that fails, naming `--claude-bin` and the rule: it runs that file at
mount, at every boot and at every call, so it must be absolute, have no `..`,
and resolve to a file that exists; links are followed, and what is checked and
run is the path they resolve to. Under the Windows service, which runs it as
`NT SERVICE\vkd`, it must also be on a local fixed drive and the resolved
path is refused inside a synced folder or under a user profile — the rule
`vkd-service install` applies to its own binary, and applies to
`--claude-bin` before the service exists — so a service runs a `claude`
installed somewhere only administrators may write, or one on the service's
own `PATH`. A daemon you started yourself may run the `claude` in your profile
(`contracts/tcb.md`, Claude Code adapter). `vk mount claude-code` has no
`--bin`, and a mount request that names a `binary` is refused (founder
decision 2026-10-13).

It runs on **the founder's own claude.ai subscription**, which is a person's
and not a product's. Nothing is billed per call, so the manifest's
`cost_per_1k_tokens_eur` is `0`; what the same call would have cost on the
meter is recorded beside it, as `cost_list_usd` on the `infer` event and on the
arch's running counters. **Customer nodes do not use this arch**: they mount
API arches, which carry a key, a per-token price and — for the EU jurisdiction
— a different host.

Because this arch counts its own prompts, `vk top` shows what the calls
actually cost rather than what this node guessed they would:

```
ARCH            STATE  GOVERNED  CALLS  TOKENS  MEASURED  COST (LIST USD)  EUR/1K  PROJECTED
sha256:2ee2...  ready  no            2   28870     28870          0.02964       -          0
sha256:fd4b...  ready  yes           1      74        74                -       -          0
sha256:91ac...  ready  no            0       0         -                -  0.0046          0
sha256:c30f...  ready  no            0       0         -                -       ?          0
```

`TOKENS` is the best number available per call, `MEASURED` how much of it the
arch itself counted — a dash where an arch reports no usage, so an estimate is
never mistaken for a figure anyone could bill against. `EUR/1K` is the price
the arch's manifest states: a dash where nothing is billed (the subscription
arch, a local model) and `?` where this node has no price list for it (the
Bedrock arch, priced by AWS), which is never printed as `0`.

Inference happens on Anthropic's servers, so the manifest is honest about it:
`governed: false`, `locality: cloud`, `jurisdiction: US`, 30-day retention, and
a clearance that stops at Business and refuses third-party data. I2 will not
lower anything above that into it. `contracts/tcb.md` says the same in prose.

### Arches: Claude through the API, US and EU

`vk mount claude-code` is the founder's. A **customer** node mounts the API
arches instead: they carry a key, a per-token price, and — for the EU
jurisdiction — a different host.

```
vk secret set anthropic                                 # prompts, no echo, keyring only
vk mount anthropic                                      # claude-opus-5, US
vk mount anthropic --model claude-sonnet-5 --ctx 200000 --max-tokens 2048
vk mount bedrock --region eu-central-1                  # EU-hosted, Frankfurt or Dublin
```

**Mounting a cloud arch is a human act.** It authorises this node's registers
to leave the machine for a third party — and durably, because the mount is
replayed at every restart — so `vk mount anthropic`, `vk mount bedrock` and
`vk mount claude-code` carry a presence proof signed by this node's device
key, exactly as `vk stop` and `vk approve` do, and the daemon refuses them
from a bare connection with `I1` — with the proof verified before it reads a
keyring or walks a credential chain, so a forged one buys nothing.
`vk mount ollama` and `vk mount mock` are unchanged: nothing leaves the
machine. A **restart** asks nobody: the mount is written down and re-created
from the record, so the human authorises it once and that authorisation is as
durable as the arch.

**Where the calls go is not the caller's to say.** The first-party origin is
`https://api.anthropic.com`, fixed in the daemon; a mount config naming
`base_url`, `endpoint` or anything else nobody declared is refused as a bad
parameter rather than honoured. Only `vkd --anthropic-base-url` moves it, it
is for tests, and even it must be `https://` or this machine's own loopback —
because whatever it names is where this node puts its API key. Redirects are
not followed for the same reason: `x-api-key` is a custom header that
`reqwest` would carry across a `307` to another host.

The key lives in this account's OS keyring under `vk`/`anthropic` and nowhere
else: not in the mount spec (which is stored unencrypted, and which the kernel
refuses to build out of anything credential-shaped), not in a manifest, not in
a log line, not in an error — every message these adapters produce has the key
scrubbed out of it on the way. The daemon reads it at `vk mount anthropic` and
again each time it re-creates that arch at boot, so rotating the key is `vk
secret set anthropic` and a restart. A daemon that finds no key refuses the
mount and prints the one line that fixes it. (`vkd --anthropic-key-file` reads
it from a file instead, for CI and for a headless node whose OS has no
credential store — and for the Windows **service** account, whose keyring is
not the interactive user's; the file must be readable by its owner alone
(`chmod 600`, or a DACL naming only this account, SYSTEM and the
administrators), and the daemon refuses to start on one that is not.)

The two differ in exactly the things a manifest exists to say:

| | `vk mount anthropic` | `vk mount bedrock` |
|---|---|---|
| host | `api.anthropic.com`, raw HTTPS | Amazon Bedrock `Converse` |
| `jurisdiction` | `US` | `EU` — `eu-central-1` or `eu-west-1`, and no other |
| `retention_days` | 30 | none: AWS keeps neither input nor output |
| price | Anthropic's list price, in the manifest and per call | AWS's, which this node has not read, so none is claimed |

Both are `governed: false`, `locality: cloud`, clearance capped at Business
with third-party data refused, neither streams, and both bound one answer at
4096 tokens. The first-party one sends `anthropic-version: 2023-06-01` and
`thinking: {"type":"adaptive"}`; the Bedrock one sends the smallest `Converse`
request that works, because it is the one that has never been exercised.

Their **context ceiling is the model's documented window** — 1 000 000 tokens
for the Claude 5 family — and a prompt past nine tenths of it is refused before
anything is sent (I4′). The first-party arch counts the prompt with the API's
own `/v1/messages/count_tokens` and falls back to the `bytes/3 + 64` estimate
when that call fails; afterwards both check the answer's own
`usage.input_tokens` against the ceiling. Unlike Ollama, this provider
*refuses* an oversize prompt rather than truncating it, so the post-check is a
belt to that suspender rather than the load-bearing one. `--ctx` pins a smaller
ceiling for a node that wants one.

`vk mount bedrock`'s transport is the AWS SDK, behind the `bedrock` cargo
feature, which is **off by default**. Not for the build time — measured at 83
extra crates and about a minute on a clean Windows build, 15 s to 1 m 15 s,
which is affordable — but for the supply chain: 83 transitive dependencies do
not belong in every node's build for an arch most nodes never mount. A node
that wants the EU arch asks for it (`cargo build -p vkd --features bedrock`);
a daemon without it refuses the mount by name and says how to get one that
has it.

`vk top`'s `EUR/1K` column is the manifest's own price, which is a different
number from the `COST (LIST USD)` beside it: that one is what the calls did
cost, this is what the arch charges. A dash means nothing is billed (a local
model, a subscription); a **`?`** means this node has no price list for that
arch, which is the Bedrock case — AWS prices it and nobody here has read that
table. `?` is never rendered as `0`, because `0` would read as free.

### Arches: Mistral (EU), Gemini (US) and any OpenAI-compatible server

Three more API arches, built the Anthropic way: the key in this account's
keyring and nowhere else, the origin fixed in the daemon, a closed mount
config, a presence proof to mount, every error scrubbed of the key, no
redirect followed, one answer bounded at 4096 tokens, I4′ before and after
the call. A response is read to 4 MiB and no further: a far end that declares
more, or sends more, fails the call with `answer larger than 4194304 bytes`.

```
vk secret set mistral                                   # prompts, no echo, keyring only
vk mount mistral                                        # mistral-large-2512, EU endpoint
vk mount mistral --model mistral-medium-3-5 --ctx 128000

vk secret set gemini                                    # a billing-enabled key
vk mount gemini                                         # gemini-3.8-flash, US
vk mount gemini --model gemini-4-argon --max-tokens 2048

vkd --openai-compat-base-url https://api.mammouth.ai    # the daemon names the one server
vk secret set openai-compat                             # optional for a loopback server
vk mount openai-compat --model mistral-medium-3 --ctx 131072 --jurisdiction FR --retention-days 30
```

| | `mistral` | `gemini` | `openai-compat` |
|---|---|---|---|
| origin | `https://api.eu.mistral.ai` (fixed) | `https://generativelanguage.googleapis.com` (fixed) | `vkd --openai-compat-base-url` |
| wire | `POST /v1/chat/completions`, `Authorization: Bearer` | `v1beta/models/{m}:generateContent` and `:countTokens`, `x-goog-api-key` | `POST /v1/chat/completions`, `Authorization: Bearer` |
| `jurisdiction` | `EU` | `US` | stated: `--jurisdiction` |
| `retention_days` | 30 (abuse monitoring) | 55 (paid tier, abuse detection) | stated: `--retention-days` |
| `locality` | cloud | cloud | cloud; local on a loopback address |
| context ceiling | the table's documented window | the table's documented window | stated: `--ctx` |
| I4′ pre-check count | `bytes/3 + 64` (no count endpoint) | `countTokens`, the estimate if it fails | `bytes/3 + 64` |
| price | the table's, ×1.1 for the EU endpoint | the table's, long-prompt tier included | none claimed (`?`) |

**Mistral** answers on its EU regional endpoint, `api.eu.mistral.ai` — "EU and
EFTA" data centres; the global `api.mistral.ai` makes no location promise and
is not used. Mistral bills the regional endpoints at 1.1× the standard list, so
that is the price claimed. Its model table carries pinned ids only (an alias
such as `mistral-large-latest` mounts but is unpriced: Mistral warns that
aliases change behaviour and price silently, and the model id is the arch's
identity). No `reasoning_effort` is sent; a reasoning answer's `thinking`
chunks are left out of the register. Turn off "Anonymous improvement data" in
the Mistral console (Admin → Privacy): training is a setting no manifest
field can say.

**Gemini** speaks Google's native `generateContent`, which Google has called
"legacy" since its Interactions API became generally available in June 2026
while keeping it "fully supported"; the API version is in the arch identity.
The key goes on the `x-goog-api-key` header, never in a `?key=` query string,
which every error and proxy log would repeat. Nearly every current Gemini
model thinks, and the answer bound — at most 1 024 tokens on every call the
kernel makes — counts the thinking. So the thinking budget is derived from
each call's bound: a `thinkingBudget` of a quarter of it (256 at the kernel's
1 024), never more than 1 024, never less than the model's floor (128 for a
model that cannot stop thinking, 0 for 2.5 Flash). It is sent to every
listed model that thinks by default, Gemini 3 and later included — a
`thinkingLevel` bounds nothing in tokens — and to none other: 2.5 Flash-Lite
does not think unless asked, and an unlisted model is sent nothing. A bound
the floor would take entirely is refused before anything is sent, and an
answer whose bound still went on thinking is refused with a message that says
so. The rule is in the arch identity; the budget each call was sent is in its
record. The retention figure is the
**paid** tier's: the unpaid tier lets Google use prompts to improve its
products, so a customer node mounts it on a billing-enabled key (in the EEA,
Google applies the paid-tier data terms to unpaid quota too).

**OpenAI-compatible** servers — Mammouth, llama-server, vLLM — have no table:
the mount states the model, the window, the jurisdiction (an ISO 3166 code,
`EU`, or `local` for a loopback server; `unknown` and the user-assigned codes
such as `ZZ` are refused) and the retention window (`0` if nothing is kept).
All four are required and all four are in the arch id, so a corrected claim is
a new arch. The base URL is the daemon's, without the trailing `/v1` an
OpenAI SDK example carries — the adapter appends `/v1/chat/completions` and a
`…/v1` base is refused at start with the fix in the message. A key is optional
on a loopback **address** and required anywhere else — and on loopback only a
keyring with **no entry** counts as no key: a keyring that cannot be read, or
an empty entry, refuses the mount with the remedy rather than calling the
server keyless (a node with no keyring names `vkd --openai-compat-key-file`).
Every mount is checked for what it says — origin, model id, claims, an answer
bound of at least 1 — before any keyring is read. The window is only as
good as `--ctx`: a server that silently truncates at a smaller window than the
one stated cannot be caught by the post-check.

A failed call says `http status <code>:` first, then `rate limited` (429),
`credit or quota exhausted` (402, or OpenAI's `insufficient_quota`) or
`model not served` (404), then the provider's own words with the key removed.

### Arches: a model this kernel governs

`vk mount ollama` is the other kind. It runs a Gemma-class model in an Ollama
container **this kernel starts**, on loopback, under a memory cap and a CPU cap
it sets — so the inference is a process the node contains, and nothing about
the call leaves the machine.

```
vk mount ollama --model gemma4:e4b                      # the demo model, 12 GiB, 6 CPUs
vk mount ollama --model gemma3:1b --ctx 8192 --memory 4g --cpus 2 --max-tokens 512
vk mount ollama --model gemma4:e4b --recreate           # replace a container with other caps
vk mount ollama --model gemma3:1b --external http://127.0.0.1:11434   # ungoverned
```

`GOVERNED` on the mount line and in `vk top` is not a promise, it is a reading:
the caps are read back off the running container, and an existing `vk-ollama`
is adopted only when its image and both caps are exactly the ones asked for —
otherwise the mount is refused and names what differs, because a container
started at 4 GiB is not a 12 GiB governor. `--recreate` replaces it and keeps
the volume, so the models are not downloaded again. `--external` points at an
Ollama somebody else is running: loopback only until SP4, never `governed`, and
its clearance stops at Business, and since it is an engine this node cannot
govern, mounting it takes a presence proof as a cloud mount does. The daemon
takes the caps only in their plain forms (`--memory` digits then `k`, `m` or
`g`; `--cpus` a whole or decimal number) and the image only as the pinned one.

Its context ceiling is **half** the window asked for. Ollama 0.33.3 truncates
an oversize prompt to `num_ctx / 2 + 3` tokens and answers `200 OK` with
`done_reason: "stop"`, so this node refuses the prompt instead — before the
call from its own estimate, and after it from the server's `prompt_eval_count`.
Nothing is billed, so there is no `COST`; what `vk top` shows is the tokens the
server itself counted.

### Drivers: a browser this kernel controls

A **driver** is a Python process this kernel starts, caps and talks to, and
through which a harness step can act on a browser. It is to a thing with a
screen what an arch is to a model: the kernel holds the process, the harness
holds a tool.

The wire is JSON lines on the driver's stdin and stdout, protocol 1: a `hello`
that names the driver, its version and its actions, then one request and one
response per action, one in flight at a time. The harness never touches
Chrome. Its only way in is the `vk_driver_act` tool, which `vk-mcp` routes to
the `driver.act` syscall, and the kernel answers it with the driver's
`result` — the evidence goes to the task's register, not back to the model.
The harness fence is the one SP1b built, unchanged.

```
vk mount driver --manifest drivers/maskai-recette/manifest.json
vk driver ls                       # NAME STATE GOVERNED SOURCE VERSION ACTIONS WHY
vk driver show maskai-1            # identity, state, hosts, actions, root
vk ls /drivers                     # the names; /drivers/NAME is `vk driver show`
vk driver umount maskai-1          # signed; stop the process, whatever it is doing
```

Mounting is a human act, signed with this node's device key like the cloud
arches' mounts, because it puts a browser — with whatever session its profile
holds — under this node's control. Unmounting is one too, for the same reason
the other way round: it ends that session, and kills an action in flight
(pilot ruling R25). The kernel refuses either to a machine principal. The
manifest (`driver_manifest`) names the driver, its program, the hosts it may navigate to, and every action with its
argument schema, its evidence file names, whether it is reversible and
whether its result goes to the register.

`program.command` stays relative, and the mount carries a `root`: the
directory of the manifest file, canonical (pilot ruling R6). The first
`.py` element of the command must be a relative path without `..`, and
`hash_dir` of its directory must equal the manifest's `files_sha256`. The
check runs at mount and again before every spawn, so a file changed after the
mount is refused rather than run. `hash_dir` skips `__pycache__`, `.venv`,
`tests` and `fixture` at any depth and a top-level `manifest*.json` (pilot
ruling R5), so the manifest can sit next to the files it hashes. The driver
runs from its own `.venv` (pilot ruling R15): the manifest's command is
`.venv/Scripts/python.exe -I driver.py`, and `drivers/maskai-recette/setup.ps1`
makes that environment and checks both manifests' hash.

The driver's process gets five variables from the host and nothing it was not
given: `VK_DRIVER_PROFILE_DIR` (its browser profile, under the state
directory), `VK_DRIVER_WORK_DIR` (its working directory, where `driver.log`
is), `VK_DRIVER_HEADLESS` (`1` or `0`), `VK_DRIVER_CHANNEL` (the browser
channel, or empty) and `VK_DRIVER_HOSTS` (the allow-list, comma-joined). Each
action's request carries an absolute `evidence_dir` under
`<state_dir>/drivers/<name>/evidence/`. On Windows the tree runs inside a Job
Object, kill-on-close and capped at the manifest's `program.max_memory_bytes`
(at least 256 MiB; 6 GiB for a browser whose manifest names none — the
harness's 2 GiB starves a headed Chrome), and `vk driver ls` says `GOVERNED`
yes; elsewhere it says no. One exception, measured: under the Microsoft Store
build of Python, a packaged app, Windows starts the interpreter's
non-packaged children — Playwright's node and Chrome — outside its job, so
outside this Job: they escape the cap, kill-on-close and the Job's process
list, and only the kill by process tree reaches them. Build the driver's
`.venv` on a python.org (or other non-packaged) Python for the Job to hold
the browser: `drivers/maskai-recette/setup.ps1` refuses a base interpreter
under `WindowsApps`, refuses an existing `.venv` whose `pyvenv.cfg` names
one, and without `-Python` takes the standalone CPython 3.13 that `uv`
manages (pilot ruling R27).

Four ledger kinds record a driver, each with its own schema:

| kind | what it carries |
|---|---|
| `driver.mounted` | `driver_id`, `name`, `manifest_hash`, `profile_dir_hash`, `governed`, and `by`, the device key that signed the mount; `package_digest` when the tree was installed from a package, and `source` (`development` or `installed`; rows written before packaging read as `development`) |
| `driver.acted` | `task_id`, `step_index`, `driver_id`, `action`, `args_hash`, `result_hash`, `ok`, `reason` when it failed — a host or daemon failure's word from a closed vocabulary (`timeout`, `dead`, `protocol: malformed response`, `would not start`, …), a driver refusal's word from the protocol's five (`dialog`, `timeout`, `host`, `precondition`, `internal`; anything else is pinned to `internal`), or a kernel refusal composed of plain tokens and byte counts — `duration_ms`, `evidence` (the blob hashes), `evidence_bytes`, `hosts_seen` |
| `driver.packed` | `driver_id`, `name`, `version`, `package_digest` (the OCI image manifest's digest), `files_sha256`, `publisher_key_id`, and `by`, the node that packed — a human act |
| `driver.unmounted` | `driver_id` and the reason: the operator, what degraded it (a word of the same vocabulary), or `row unreadable at boot` |

`driver.acted` carries hashes and sizes, never text (Ruling 28): what the
harness asked, what the driver answered and what it captured are in the
store, under their labels, and not in the record. A failure's `reason` is
one of a fixed list of words; its detail — a path, a hash, an excerpt of a
response the host could not read — goes to the daemon's log and to the
driver's `driver.log`, never to the ledger (final review I1).

`hosts_seen` is the egress sampled while the action ran: the remote `ip:port`
endpoints of the established TCP connections of every process in the Job on
Windows, of the driver's leader alone when it is ungoverned, sampled every
500 ms (pilot ruling R8). It is a lower bound, in addresses, not names: a
connection that opens and closes between two samples is not seen, and an
auditor resolves the addresses to hosts by hand.

Evidence is stored as artefacts, each kind being the evidence file's name
(`answer.png`, `network.json`, `dom.html`, `timing.json`, `attach.png`). A
human reads one with `vk artefact cat HASH`, which is a human act too (pilot
rulings R22 and R24): the read signs with this node's device key, and the
ledger records it as an `artefact.released` event with destination
`terminal:<device_id>`, so reading what a task found is as visible as
releasing it. `vk task show` lists a task's artefacts as `{hash, kind}` —
metadata only (pilot ruling R23). Text goes to stdout; bytes that are not
text need `--out FILE`.

What the kernel enforces on each `driver.act`: the lease's step is running and
names the driver; the driver is mounted and not degraded; the action is in
the manifest and its arguments validate; the cadence and the step's action cap
hold; no STOP covers the task; the register's label flows to the driver's
clearance (I2). What it trusts the driver for is the host allow-list. The
driver enforces it on **navigations** — a page that goes off the list is
refused and the action answers `host` — while third-party sub-resource hosts
(a CDN, an analytics endpoint) are recorded in `network.json`, never blocked
(pilot rulings R16 and R19). The driver also services browser events between
actions, without intercepting requests, so a sign-in popup works while the
driver is idle.

A driver degrades on every host-side failure except the evidence refusals: a
deadline, a dead process, a protocol error, a STOP (pilot ruling R12). A
human STOP degrades it too, because the host kills the tree under the action
it interrupts; the remedy is a remount once the STOP is lifted. A degraded
driver refuses every act with the remedy, which is to mount it again:
`vk mount driver --manifest …` starts a fresh process and writes a new
`driver.mounted`. `vk driver umount`, signed like the mount, kills the
process even while an action is in flight, and that action's caller is told
the act is stale (pilot ruling R13). A stored driver row this binary can no
longer read — a field written by a newer one — is recorded and removed at
boot rather than failing it; the driver is then mounted again.

A driver is identified by `files_sha256` and by `inputs_sha256`: the second
covers the evaluator, the journal script, the cases, the oracles and the
corpus the verdicts depend on, which `driver_id` does not. It is in the hello's
`version` (`0.2.0+<12 hex>`) and on every line of the journal (pilot ruling
R18), so two runs judged by different rules show it. Since the driver's v2,
every line also carries `ui_profile_sha256`, the hash of the selectors, the
known modals, the stream pattern and the end-of-answer constants the page was
read with: a selector fix shows on the line, which
`inputs_sha256` cannot show (debrief of 2026-10-17, D12). A driver installed
without its repository finds `vkdriver`, `evaluer.py` and `journal.py` under
`vendor/` beside `driver.py` first, the repository second;
`drivers/maskai-recette/pack.json` names them for the packer, and
`inputs_sha256` names them by their repository path either way.

A task that uses a driver is submitted from a recipe, not from flags:

```
vk task submit --steps recipes/recette-maskai.json
```

The recipe is a `task_recipe` — the goal, the artefact kind, the constraints,
the evidence and the steps. A harness step lists the drivers it may use, and
may cap `max_actions` and `timeout_s`. A recipe names its arches by manifest
name, and `vk task submit --steps` resolves each to the one mounted arch of
that name, refusing none or several (pilot ruling R21), because an arch id is
a hash that differs by machine and by model version. `--steps` conflicts with
the flag-built task (`--goal`, `--artefact`, `--plan`, `--draft`, `--harness`,
`--judge`, `--approve`, `--release`); a harness step is then run with
`vk harness run TASK`, as before.

The first use is the MaskAI recette, sixteen steps. The procedure for the
founder, in French, from the first boot to the release, is
[`docs/technical/runbooks/recette-maskai.md`](runbooks/recette-maskai.md). The driver's
tests run in CI (`driver-browser`) against a local fixture page in headless
Chromium, with no network.

### Driver packages: sign it, ship it, install it, check it before every start

A development driver runs from a directory of the repository, under the
interactive daemon only, with an interpreter and libraries nothing hashes
(pilot ruling R15). A **driver package** is the same driver cut for a node
that is not the developer's: one signed directory holding its files, its
dependency lock, its wheels and its own CPython, that an administrator
installs once under `%ProgramFiles%` and that the kernel then mounts by
`name@version` with the whole installed tree fingerprinted before every
spawn. It is to a development driver what a flashed, signed firmware image is
to a binary loaded from the debugger: the bootloader checks the image against
the key it trusts before it jumps, and checks the flash again at every reset.
The design is
[`specs/2026-10-16-driver-packaging-and-deployment-design.md`](specs/2026-10-16-driver-packaging-and-deployment-design.md).

```
vk capital key init                      # once: the publisher key; self-pinned on this node
vk capital key show                      # its id and public half, to hand to other nodes
vk driver pack --manifest drivers/maskai-recette/manifest.json --version 1.0.0 --out dist/maskai-recette-1.0.0
vk driver install dist/maskai-recette-1.0.0      # elevated shell, offline, no daemon
vk mount driver maskai-1@1.0.0           # signed, as any mount; from the installed tree
vk driver ls                             # NAME STATE GOVERNED SOURCE VERSION ACTIONS WHY
vk driver umount maskai-1
vk driver uninstall maskai-1@1.0.0       # elevated; refused while it is mounted
```

**The package** is an OCI image layout (`oci-layout`, `index.json`,
`blobs/sha256/…`; readable with `tar`). One image manifest, artifact type
`application/vnd.aubier.driver.v1`, five layers, each by digest: `manifest`
(the `DriverManifest`, `source: installed`, `files_sha256` = the installed
fingerprint), `files` (the driver's directory and its `requirements.txt`),
`lock` (every distribution with its wheel file name and SHA-256), `wheels`
(the wheels, byte for byte) and `python` (one pinned python-build-standalone
release, `install_only`, Windows x86_64, with its upstream SHA-256). The
signature is a *referrer* — a second manifest whose `subject` is the image
manifest — carrying `{key_id, algorithm: ed25519, signed, signature}` and, as
the annotation `org.aubier.publisher.public_key`, the publisher's public key,
so that an installer that has never met the publisher can show it. That key is
untrusted text until its id is pinned (below). The package digest is the
SHA-256 of the image manifest. A packed driver has its own `driver_id` (its
`command` and `files_sha256` differ from the development manifest's); only
`source` is excluded from the id (with `governed`, what the host observed),
so the id does not depend on where the same packed manifest is mounted —
which bytes ran is the package digest's to say.

**`vk driver pack`** is a human act (signed with the node's device key; the
daemon records `driver.packed` with the package digest). It is the only step
of the whole chain that touches the network: it downloads the pinned CPython (to
`<state_dir>/capital/python/`, or `--python-cache DIR`; the SHA-256 in the
table in `crates/vk-capital/src/python.rs` is checked before a byte is read),
resolves the wheels for that interpreter's tags with `pip download` (wheels
only, no sdist), materialises the whole tree in a stage directory with a short
path (`%LOCALAPPDATA%\vk\stage\<8 hex>`, because pip and the Playwright wheel
fail under a deep one), fingerprints it, writes the fingerprint into the
packed copy of the manifest, writes the layout and signs it with the publisher
key. A stage is removed whether the pack succeeds or is refused.

**`pack.json`** — a driver whose code lives partly outside its own directory
(the `vkdriver` library, the evaluator and journal scripts of the recette)
declares it in a `pack.json` beside its manifest:
`{"vendor": ["drivers/vkdriver", "scripts/recette/evaluer.py"]}`. Paths are
relative to the repository root (the nearest ancestor of the driver
directory holding a `Cargo.toml`). A directory is copied into the files layer
as `vendor/<its name>/`, a file as `vendor/<its parent's name>/<file>`, without
`__pycache__`, `.venv`, `tests`, `fixture`, `test_*.py`, `*_test.py` and
`*.pyc`. The pack refuses, before copying anything: an absolute path, `..`, a
link resolving outside the repository, an absent path, two entries landing on
one place, a place the driver's own files already hold, an unknown key. The
driver looks in `DRIVER_DIR/vendor/` first and in the repository layout
second, so one file serves both. With no `pack.json`, a `vkdriver/` beside the
driver is copied as before. `pack.json` is part of the files layer, so it is
part of the fingerprint.

**The installed tree** is
`<install root>/<name>/<version>/`: the files layer at its root, the
interpreter in `python/` (wheels installed straight into its
`Lib\site-packages` with `--no-index`, `--no-compile`; **no `venv` is created**,
plan ruling P-1), the manifest layer verbatim as `manifest.json`, and
`.package/` holding the image manifest blob (its SHA-256 is the package digest)
and the signature. Console-script launchers pip writes under `python\Scripts\`
embed the path of the interpreter and are deleted after the install (plan
ruling P-2), as is every `__pycache__` the release shipped; the packed command
is `python/python.exe -E -s -B <script> …` (ignore the environment, no user
site, write no bytecode; not `-I`, which would drop the script's directory from
`sys.path` and a driver with two modules could not import).

**The install root** is `%ProgramFiles%\Aubier\drivers`, or the directory
named by `VK_DRIVER_INSTALL_ROOT`; the kernel reads it once when it is built
and `vk status` prints it (`install root`). The kernel refuses a manifest that
says `source: installed` outside it, and a development tree inside it, and a
root that is not exactly `<name>/<MAJOR.MINOR.PATCH>` below it, and an
installed manifest whose command is not the interpreter its package carries
(`python/python.exe`, flags with `-B`, a `.py` script under the tree, both
regular files there; ruling R-F2), at pack and at mount. `vk driver
install` writes under the same root, with no daemon, and writes nothing before
its checks pass: every digest of the layout; the annotations against
the manifest (a name and a version are path components); the signature, under
the pinned key of its `key_id` or, if the id is not pinned, under the carried
key once that key's id is shown to be the signature's — and then the
administrator is asked (`publisher key sha256:…`, the PEM re-rendered from the
key bytes, `pin this publisher on this node? [y/N]`; `--yes` answers for CI);
the tree materialised into `<version>.part`, which an elevated install creates
with its final ACL (administrators and SYSTEM full control, users read and
execute, inheritance cut) *before* the interpreter runs inside it; the
fingerprint against the manifest's; then a rename to `<version>` and, last,
the pin. An elevated install refuses a root that Users, Authenticated Users,
Everyone or INTERACTIVE may write, a root or a `<name>` that is a link, a
version already installed (a version is never overwritten) and a tree whose
deepest path exceeds 240 characters. A shell that cannot write the root is
told so in one line (`run it from an elevated shell`). Installing from a
shell that is not elevated into a root that user may write is the test seam:
the tree gets that root's own ACL. The publisher is pinned into the state
directory the daemon reads (`--state-dir`, else this user's, as `vk boot`
resolves it): `<state_dir>/capital/publishers/<key_id>.pub`, with a sidecar
recording who pinned it and when. The install names that directory on stderr
before anything else and warns when another account owns it: an
administrator who elevates with another account than the one running `vkd`
passes `--state-dir` = the `state dir` line of that daemon's `vk status`, or
the mount is refused (`is not pinned on this node: no pin in …`, which names
the directory the daemon looked in). A name binds its publisher (ruling
R-F1): while any version of `<name>` is installed, a package of that name
signed by another key is refused before the prompt (`remedy: vk driver
uninstall every version of <name> first`), and the prompt says how many
publishers the node already pins. `vk driver uninstall` moves the version
aside before deleting it, which Windows refuses while a file of it is open, and
is refused while the daemon says that version is mounted; with no daemon on
its endpoint it treats nothing as mounted and says so on stderr, so umount
before stopping the daemon.

**The fingerprint** (`hash_tree`, in `vk-capital`) is the SHA-256 over every
regular file of the installed tree at any depth, in path order, each framed
`path\0bytes\0`, links never followed. It leaves out the top-level
`manifest*.json` and `.package/` and nothing else — `__pycache__` is hashed,
because the tree is built without bytecode and runs with `-B`, so a `.pyc` that
appears later is a change (ruling R-T8b). Packing and installing both
materialise the layers and must reach the same digest; a node that reaches
another refuses the install (`repack on a node with the same CPython release`).
Before every spawn the kernel walks the tree and compares `(path, size,
mtime_ns)` with `<state_dir>/drivers/<name>/fingerprint.json`; an unchanged set
reuses the digest without a read, any difference rehashes the whole tree, and a
digest that is not the manifest's degrades the driver (`driver.unmounted`
with the reason `tampered`, remedy: reinstall, then remount). A cache that
claims a mismatch is not believed on its own: the tree is hashed first. The cache
is a speed-up, not a witness: it trusts the metadata, so a byte flipped with
its size and mtime put back is not seen by it (the documented limit, and the
reason the tree is also read-only to everyone but the administrators). A byte
changed under a driver that is running is seen at its next spawn, not at its
next act.

**Test seams**, each documented where it is read and none a production knob:

| Variable | Read by | What it does |
|---|---|---|
| `VK_DRIVER_INSTALL_ROOT` | the kernel, and `vk driver install` and `uninstall` | The install root, made absolute once. Every test daemon and test install sets its own, a directory of its own, so a machine that has real drivers installed under `%ProgramFiles%` does not change a result. |
| `VK_PUBLISHER_KEY_FILE` | the kernel | The publisher key's secret in a file instead of the OS keyring, as `VK_NODE_KEY_FILE` does for the device key. |
| `VK_TEST_PYTHON_RELEASE` | `vkd`, and the tests that gate on it | The path of an already downloaded CPython archive, in place of the download. Only the path moves: the SHA-256 is always the pinned one, so a wrong file is refused. The archive-gated tests skip, with one line on stderr, when it is not set. |

**CI** has a Windows job, `driver-install`, for what no other job can run: it
downloads the pinned release with `vk capital python fetch --cache DIR --as
FILE` (a hidden verb that calls the pack's own `fetch`, hash check included;
cached by the hash of `python.rs`), runs the real-release `vk-capital`
materialise test, the daemon's pack, mount and `tampered` tests, and the
`vk driver` smoke tests, among them the end-to-end `pack → install → mount →
act → ls → uninstall`. GitHub's Windows runner is an administrator, so that job
is where the elevated half of the install (the read-only ACL, the root check,
the interpreter spawned from the protected tree) runs for the first time.

What the kernel enforces and what it trusts is in
[`contracts/tcb.md`](../../contracts/tcb.md), §Drivers.

### The human ceremony: passkeys and kernel-minted challenges

Every human approval answers a **challenge the kernel minted** for the task:
`vk approve` asks the daemon for it (`approval.challenge`), signs its digest
with the node's device key and presents it; the daemon accepts a challenge it
minted, once, while it is unexpired — and nothing a client built itself. That
is invariant I1 made concrete: no client ever chooses the subject, the nonce or
the expiry a human signs.

The device key in the OS keyring is SP1a's stand-in for a human. The passkey
is the human's own device: Windows Hello today, a phone's passkey later.

```
vk passkey enroll                       # prints http://localhost:7734/enroll?t=…; --open opens it
vk passkey ls                           # DEVICE  ENROLLED (ms)
vk approve $TASK --passkey              # prints the approval link, waits for Windows Hello
vk approve $TASK --passkey --open --timeout 120
```

`vkd` serves the two pages on both loopback addresses, `127.0.0.1` and `[::1]`
(`--web-port`, default 7734; `0` lets the OS pick, and `vk status` shows the
`web` origin), and refuses to start if either is taken. The pages open only
from a link the daemon minted over its endpoint, good for ten minutes, for one
page and one task; `vk passkey enroll` signs a presence proof with the node's
device key to get its link, because enrolling a passkey is a human act. On the
approval page the daemon mints the task's challenge with the WebAuthn challenge
as its nonce — so what Windows Hello signs is the challenge the kernel minted —
verifies the assertion in-process with `webauthn-rs` against the enrolled
passkey, records the approval with `proof: webauthn` and the signed bytes
beside it (so the signature can be checked again from the record), and runs
the step that was waiting; `vk approve --passkey` sees the task leave
`waiting_human` and prints the result. `contracts/tcb.md` says what is and is
not claimed of it.

**Windows Hello, by hand** (the check no test can make):

1. `vk boot` (any state directory), `vk mount mock m1`, then a task with
   `--approve` stepped to `waiting_human`, as in the walk-through below.
2. `vk passkey enroll --open`. Edge or Chrome opens
   `http://localhost:7734/enroll?t=…`; press *Enrol with Windows Hello*, choose
   *This device* when the browser asks where to save the passkey, and confirm
   with your PIN, fingerprint or face. The page prints `Enrolled passkey:…`;
   `vk passkey ls` lists it and `vk dmesg` shows `device.enrolled`.
3. `vk approve $TASK --passkey --open`. The page shows the goal and the hash;
   press *Approve with Windows Hello* and confirm. The page says the task is
   `done` (or `running`, with more steps to go), the shell prints
   `approved sha256:…`, and `vk dmesg` shows `approval.recorded`.
4. Open the same link again: `404`. Run `vk approve $TASK --passkey` for a
   task that is not waiting: refused, no link minted. Open
   `http://localhost:7734/enroll` without a link: `404`.

A phone's passkey works through the same pages once the browser can reach it
(a QR code from Edge or Chrome); nothing on the daemon's side is different.

### What boot does

`vkd` verifies the ledger chain before it serves anything and reports what it
found: `ledger_ok`, `ledger_len`, `recovered_partial_line` (a crash mid-append
left an unterminated last line, which the store dropped and truncated away),
the mounted arches, the enrolled devices, the stopped scopes, and
`policies_version` — a placeholder in SP1a, where there is no policy engine
yet. The report goes to the log at info level (`<state_dir>/vkd.log` for a
detached daemon) and into the record: the `boot` event's payload is that
report's canonical hash. `vk status` shows the parts a person acts on, and
`vk status --json` carries them all.

**A chain that does not verify does not serve.** `vkd` exits non-zero and says
where the ledger is; `vkd --force` (or `vk boot --force`) serves anyway, for
recovering a node whose record was damaged — everything appended afterwards
chains onto a record already known not to hold. A `vk boot` whose daemon
refused comes back with the daemon's own last words, not with a timeout.
Serving under `--force` is itself on the record: the daemon appends a
`boot.forced` event naming the verdict it overrode, and `vk status` marks the
node `forced boot`, next to the chain line, for as long as that process runs.

### Verifying the store

`vk ledger verify` answers one question: does the hash chain recompute. A node
can pass it with every blob on disk unreadable, every wrapped key stale and
every arch unmountable — so `vk fsck` asks the other questions, over the whole
store, and prints one line per tier:

```
$ vk fsck
TIER    VERDICT  CHECKED  FAILED
ledger  ok       412      0
head    ok       1        0
blobs   ok       57 (2 skipped)  0
keys    ok       9        0
mounts  ok       3        0

the store verifies
```

| tier | what it checks | what a failure means |
|---|---|---|
| `ledger` | every link and every hash recomputes; the first event that does not is named | a line was rewritten since this node wrote it |
| `head` | the chain still reaches the head this store last recorded, **and there is one** | the tail was cut — by a restore, or by somebody. A record with *no* head recorded for it is the same failure: the head is written on every append, so the row was removed |
| `blobs` | every blob opens **at the address it is filed under**: the envelope names it, the AEAD tag binds the ciphertext to it, and the plaintext derives it again | a `.bin` was swapped, restored from the wrong copy, or has a flipped byte |
| `keys` | every wrapped DEK still unwraps under the master key | the keyring entry or key file is not the one the blobs were written with |
| `mounts` | every mounted arch still has the spec the next boot would make it from | that arch comes back `unavailable` at the next restart |

A shredded subject is **skipped**, not failed: its DEK was deleted on purpose
and its ciphertext is meant to stay unreadable for ever.

**Store-format break (SP1b, Ruling 38).** Since SP1b each subject's DEK is
wrapped with the subject id as associated data, so a `.dek` file moved between
subjects does not unwrap and a `shred` cannot be pointed at somebody else's
key. A state directory written **before** that — any SP1a-era store, and the
SP1b demo stores made before the change — does not open its subjects any more:
`get` and `put` fail, `shred` refuses, and `vk fsck` fails the `keys` tier,
each naming the three things it can be (written before SP1b's subject
binding, moved between subjects, wrong master key). There is no legacy read
path and no migration, on purpose: no production store exists, and such a
directory is recreated.

**Claude Code mount specs from SP1b (no migration).** Until 2026-10-13 the
`claude` a Claude Code arch ran was a field of its mount request, and the
spec the mount persisted carries it as `binary`. Such a spec is still read:
at every boot that re-creates the arch the field is **ignored** — the daemon's
own binary (`vkd --claude-bin`, default `claude` on its `PATH`) is what runs —
and `vkd.log` gets a warning naming the path it ignored. Nothing rewrites the
row; `vk arch show` still prints the old field. To be rid of both, start the
daemon with the `--claude-bin` you meant and mount the arch again
(`vk umount ID`, then `vk mount claude-code`): the new spec has no `binary`.

A node with a record and **no recorded head** does not serve without `--force`
either. Cutting a tail is caught by the head; cutting a tail *and* deleting the
head row would otherwise be caught by nothing, and one `DELETE` would turn a
detectable tamper into a node that attests itself clean. The only innocent
version of "no head" is a store nothing has ever been appended to, which has no
record either.

`vk fsck` exits non-zero if any tier fails, so `vk fsck && …` gates on it, and
`vk fsck --json` is the whole report for a script. Reading every blob on the
node is a human act, so the call carries a presence proof by this node's device
key, exactly as `vk stop` and `vk approve` do.

**Recovering after a legitimate restore.** Restoring an older copy of the
ledger segment leaves a record that links perfectly and is *shorter* than the
head this store recorded — which is byte for byte what a tail somebody cut
looks like. The node cannot tell them apart and refuses to serve on either
(`vkd --force` serves anyway, and says so). When it was your restore:

```
$ vk boot --force            # so the node serves and vk can reach it
$ vk fsck                    # head: FAILED — confirm this is the restore you made
$ vk fsck --rebase-head --force
Type `rebase` to confirm: rebase
```

The word is typed out, on the client, before any syscall is made; `--yes`
takes it as given for a script. The head is re-recorded from the record as it
now stands, and the node serves without `--force` from then on. Two refusals
guard it: a chain that does not itself verify is **not** rebased — re-recording
a head onto a record known to be rewritten would only make the next open call
it intact — and neither is an empty record.

The override goes on the record, and stays there. Each rebase is its own row —
append-only, never overwritten and never cleared, so a second rebase cannot
erase the first. There is no new event kind for it: the next `boot` event's
payload is that boot's report, and the report names every rebase since the
previous boot, the same way `boot.forced` names the verdict a `--force`
overrode. Because a `boot` event is a commitment to a report and not the report
itself, the rows are also on the surfaces a live operator reads — `vk status`
marks the node `head rebased`, `vk status --json` and `vk fsck --json` carry
the whole history — for as long as the node exists.

**A damaged rebase evidence row.** Because those rows are append-only
evidence, a row under `ledger.head.rebase.` that no longer parses is not
skipped: `vkd` refuses to boot (and on a daemon already running, `vk status`
and `vk fsck` fail), naming the row (`rebase evidence row ledger.head.rebase.…
cannot be read`). Nothing repairs, skips or deletes it — there is no command
for that, on purpose — and `--force` does not get past it, because `boot` reads
the rows before it decides whether to serve. The remedy is the state directory
itself: stop the daemon, keep the damaged directory as it is (it is the
evidence of what happened to it), and restore a whole copy of the state
directory taken before the damage.

### The SP1 demo

`scripts/demo-sp1.ps1` is the milestone end to end, and the shortest way to see
what this is for: a client proposal written from a two-page brief by **Gemma in
a container this kernel caps** and **Claude through the installed Claude Code**,
co-working through one register, approved by a human ceremony and released as a
file — then the same brief again with the two models' roles swapped, and ten
checks that the register did not care which was which (H1).

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\demo-sp1.ps1
```

Twelve minutes, a throwaway state directory, file keys, a private endpoint and
an OS-chosen web port, so it never touches the node you already have. The
recorded runs — both orders, the harness variant, the two proposals, the
ledgers and the H1 verdict — are in `docs/functional/demo/runs/2026-09-25/`, and
`docs/functional/demo/README.md` says what to expect, what it needs, and how to run the
Windows Hello variant by hand.

### Installing on Windows

`scripts\install-windows.ps1` is the everyday path once a build (or a signed
release download) already exists: it does not build anything, it packages
what is already built.

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

That copies `vk.exe`, `vkd.exe`, `vk-mcp.exe` and `vkd-service.exe` — found
beside the script by default, `-Source DIR` elsewhere — into
`%LOCALAPPDATA%\Programs\VerticalAI` and adds that directory to this user's
own PATH (`-Root DIR` installs somewhere else instead; running it again does
not grow a duplicate PATH entry). It also checks two things the rest of this
README assumes are already true — Docker Desktop's engine answers `docker
info`, and `claude` is on PATH — and warns rather than failing if either is
missing, because installing the node and being ready to mount every arch are
two different moments. If the binaries it is copying are not signed, it says
so loudly: Smart App Control and SmartScreen will not be quiet about it
either (`docs/archive/technical/sp1-gate-checklist.md`, item 1).

`-Service` additionally registers and starts `vkd` as the `NT SERVICE\vkd`
Windows service the next section describes — `vkd-service install`, then
`start` — and needs an elevated shell; run without one, it refuses and
prints the exact command to re-run elevated, rather than doing half the job
from the wrong account. `-Uninstall` reverses everything a plain run did,
and, if it finds one, whatever `-Service` did too: the PATH entry, the copied
binaries, the service. It never touches `%ProgramData%\VerticalAI\vk` (the
node's store) or the OS keyring — uninstalling the program is not discarding
a node. `-DryRun` (or the built-in `-WhatIf`) prints every action a run would
take without taking any of them.

### Signing release binaries

Every Windows binary this repository ships — `vk`, `vkd`, `vk-mcp`,
`vkd-service` — is signed by the `sign` job in `.github/workflows/ci.yml` (on
pushes to `master`, on tags and on `workflow_dispatch` — not on branch pushes,
which `test` covers on three operating systems) and by the Windows leg of `.github/workflows/release.yml` (on an
`sp*`/`v*` release tag), so that gate item 1 — installing without a
SmartScreen or Smart App Control prompt — has something to check. Neither
job fails for a secret that does not exist yet, because the founder has not
chosen between the two paths below; both are wired up so whichever secret
set is added first is the one that runs, in this order:

1. **Azure Trusted Signing**, when `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
   `AZURE_CLIENT_SECRET`, `AZURE_ENDPOINT`, `AZURE_CODE_SIGNING_NAME` and
   `AZURE_CERT_PROFILE_NAME` are all set as repository secrets — a per-call,
   short-lived certificate from an Azure Trusted Signing account, nothing to
   renew by hand and nothing to protect as a file.
2. **An OV (organization-validated) code-signing certificate**, when
   `SIGNING_CERT_B64` (the `.pfx`, base64-encoded) is set, plus
   `SIGNING_CERT_PASSWORD` if the `.pfx` has one. Signed with `signtool sign
   /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f cert.pfx` — the
   founder's own certificate, bought from a CA and renewed by hand every one
   to three years.
3. **Neither** (today's state): the job prints `unsigned: no signing
   secrets configured`, still succeeds, and uploads the same four binaries
   under a clearly named `unsigned-` prefix (`unsigned-vk-windows` from CI,
   `unsigned-vk-windows-latest` from a release tag) — so a run before either
   secret exists is still a green run with something installable at the end
   of it, not a red one with nothing.

`scripts/check-workflows.py` loads both workflow files with `yaml.safe_load`
and asserts their job names, so a YAML mistake — in particular a flow mapping
(`{ key: value }`) holding a `${{ }}` expression, which GitHub's own
preprocessor accepts but a standard YAML parser does not — is caught before
it reaches a runner rather than by a contributor reading the diff carefully.

### Running as a Windows service

A node that belongs to the machine rather than to a logged-in shell:
`vkd-service` registers `vkd` under the **virtual account** `NT SERVICE\vkd`,
which Windows creates and manages from the service's own name — no password to
store, no password to rotate, and a Credential Manager of its own, so the
master key and the node's device key are not in the founder's.

```
# build somewhere the synced repository is not, then put the binaries
# where only administrators may write
$env:CARGO_TARGET_DIR = "$env:USERPROFILE\.cargo-target\verticalai-sp1"
cargo build --release
mkdir "$env:ProgramFiles\VerticalAI"                       # elevated
copy "$env:CARGO_TARGET_DIR\release\vk*.exe" "$env:ProgramFiles\VerticalAI\"

# from an elevated PowerShell, once
& "$env:ProgramFiles\VerticalAI\vkd-service.exe" install   # --user-sid defaults to you
& "$env:ProgramFiles\VerticalAI\vkd-service.exe" start

# from an ordinary shell, from then on -- nothing to configure for the read
# verbs; the human ones need the key set-up under "Human ceremonies" below
vk status
```

**Where the binary lives is part of the trust boundary.** The registered
`ImagePath` runs as `NT SERVICE\vkd` at every start, so a binary under a user
profile — or inside OneDrive — is one that account can replace, and the next
start would hand it the service account's keyring. `vkd-service install`
refuses such a path outright; `%ProgramFiles%\VerticalAI\` is the answer,
because an elevated `mkdir` there is owned by the administrators. (Signing is
the SP1 gate's job; until then nothing verifies *what* is at that path, only
who may change it.)

**The service listens where that person's `vk` already looks.** `install`
resolves `--user-sid` to its account name and registers the endpoint that
name gives — `\\.\pipe\vk-<user>`, the very name a `vk boot` daemon of
theirs would take — so the shell needs no `$VK_ENDPOINT` and no `--endpoint`.
One derivation serves both (`transport::user_endpoint`), so they cannot drift.
The corollary: **a `vkd` of your own and the service cannot both run**, because
they want the same pipe; whichever starts second is refused, naming the holder.
`vkd-service install --endpoint <name>` overrides it, and then `vk` has to be
told too. The state directory is `%ProgramData%\VerticalAI\vk` rather than any
one person's app data. Stopping the service is `vkd-service stop`, and
`vkd-service uninstall` removes it — leaving the state directory and the service
account's keyring entries behind, which is where the node's whole record is.

**The endpoint's ACL is the first authentication factor, and it is written
out — for every daemon, not only the service.** The default DACL of a named
pipe grants SYSTEM, the administrators and the creator full access *and
Everyone and Anonymous read access*. So `vkd` never takes it: a daemon a person
started binds `D:(A;;GA;;;<that person's SID>)`, and the service binds two
accounts and nobody else:

```
D:(A;;GA;;;<the NT SERVICE\vkd SID>)(A;;GA;;;<the interactive user's SID>)
```

The daemon reads its own half off its process token at bind time and logs the
whole list; the other half is `--user-sid`, which `vkd-service install` fills
in from whoever ran it (an elevated shell has the same user SID as the desktop
that raised it) and which `whoami /user` prints if you want to name another. It
must be a **user** account: `S-1-1-0` is Everyone and is a perfectly
well-formed SID, so the string is resolved and its type checked before it
becomes an entry. `vkd-service install` prints the SIDs, the endpoint, the DACL
and the `ImagePath` before the service has ever run.

**The store's directory carries its own ACL too.** `%ProgramData%` hands
inheritable read *and* create rights to every local account, so the service
creates `%ProgramData%\VerticalAI\vk` — and its parent — with a protected
list, Full Control to the service account, SYSTEM and the administrators,
inherited by everything underneath, and **refuses to start** on a directory
whose owner or entries name anybody else, naming the one that stopped it. A store somebody else
created first is not adopted: they would own the ledger.

**Who is on the other end.** Windows lets any local account claim a pipe name
nobody is serving yet, so `vkd` refuses to start on a name already taken
(naming the holder's pid and account), and `vk`, after connecting, reads the
pipe object's **owner** off its own handle and refuses to speak to anything that
is neither the caller's own account nor `NT SERVICE\vkd`. The owner rather than
the server's process, because an ordinary account may not open a service's
process at all. The residual — a pipe whose owner cannot be read — is in
`contracts/tcb.md`.

#### Human ceremonies under the service

`vk status`, `vk ls` and `vk ledger verify` need nothing. The **human verbs**
— `vk approve`, `stop`, `resume`, `fsck --rebase-head`, `passkey enroll`, the
cloud and `--external` mounts — sign a presence proof with the node's device
key, `node:<node id>`, and that key is the one **the daemon enrolled when it
started with `--auto-enroll-node`**, read from `--node-key-file` or, without
it, from the daemon's own account's keyring. Under the service that account is
`NT SERVICE\vkd`, whose keyring is not yours: a plain `vkd-service install`
passes neither flag, so the service enrols no node key at all and every human
verb is refused. `vk` never creates a key to make up for it: an account with
no key, or with one the node has not enrolled, is refused before anything is
signed, with a message pointing here.

What the code supports today is one shared key file:

```
# elevated: the daemon makes the key at its first start and enrols it
& "$env:ProgramFiles\VerticalAI\vkd-service.exe" install -- `
    --node-key-file <path> --auto-enroll-node

# your shell, from then on
$env:VK_NODE_KEY_FILE = "<path>"
vk stop; vk resume <stop id>; vk approve <task>
```

The file must be readable by both `NT SERVICE\vkd` (which creates it) and
you — choose its directory and its ACL accordingly; nothing checks the
Windows ACL of that file, and anyone who can read it holds the node's human
key. With that key in place, `vk passkey enroll` enrols a passkey against the
service's daemon, and `vk approve <task> --passkey` approves with it from
then on. This path has not been run against a live service in SP1b (`scripts/spike-6a.ps1`
exercises only the read verbs); `contracts/tcb.md` says which ceremonies work
under the service.

Two things the DACL does **not** cover. The passkey pages are still served on
`127.0.0.1:7734` and `[::1]:7734`, and loopback has no ACL — what protects them is the
single-use link tokens minted only over the endpoint (`contracts/tcb.md`).
And the service has no console: its log is `%ProgramData%\VerticalAI\vk\vkd.log`,
the same `vkd.log` a detached `vk boot` daemon writes.

`scripts/spike-6a.ps1` is the whole thing end to end — stage the binaries,
install, start, `vk status`, `vk ls /arches`, `vk ledger verify`, a restart and
the same checks again, the DACL the daemon actually bound, whether Docker's
engine is reachable from the service account, a second local account's attempt,
uninstall and tidy up after itself — and it prints a summary block. It needs an
elevated PowerShell and builds nothing:

```
.\scripts\spike-6a.ps1            # binaries from %USERPROFILE%\.cargo-target\verticalai-sp1\release
```

| `vkd-service` verb | what it does |
|---|---|
| `install [--user-sid S] [--binary PATH] [--endpoint EP] [--claude-bin PATH] [--probe-docker] [-- <vkd args>]` | create the service under `NT SERVICE\vkd`, on that user's own endpoint unless one is named; print the SIDs, the account, the endpoint, the DACL and the `ImagePath`. `--claude-bin` is checked by the service's rule before anything is created and written into the `ImagePath` as `vkd --claude-bin <resolved path>`; given after `--` instead, it is refused |
| `uninstall` | stop it if it is running, delete it |
| `start` / `stop` | control it and wait for the new state |
| `run --user-sid S [--endpoint EP]` | the service control manager's own entry point; it runs `vkd --as-service --user-sid S --endpoint EP` in this process, with the endpoint `install` resolved. Not for a terminal |

`vkd --as-service --user-sid <SID>` is that daemon on its own, without the SCM:
same state directory, same DACL, and the same endpoint — it resolves the SID
to an account name and derives the pipe exactly as `install` does. It refuses without
`--user-sid`, because a service that did not know which user to admit would
serve nobody.

### Defaults

| | default | override |
|---|---|---|
| state directory | this user's local app data — `%LOCALAPPDATA%\VerticalAI\vk\data` on Windows (the `data` leaf is `directories`' own), `~/.local/share/vk` on Linux, `~/Library/Application Support/ai.VerticalAI.vk` on macOS — never a synced folder; `%ProgramData%\VerticalAI\vk` under `--as-service` | `--state-dir DIR` |
| master key | OS keyring, service `vk`, user `master` | `--master-key-file FILE` (tests and CI) |
| node device key | OS keyring | `--node-key-file FILE`, or `$VK_NODE_KEY_FILE` |
| endpoint | `\\.\pipe\vk-<user>` (Windows — under `--as-service` too, for the `--user-sid` account); `$XDG_RUNTIME_DIR/vk.sock`, else `/tmp/vk-<user>/vk.sock` (Unix) | `--endpoint EP`, or `$VK_ENDPOINT` |
| endpoint ACL | the creating account's — Unix: `0600` in a `0700` directory; Windows: `D:(A;;GA;;;<that account>)` | `--as-service`: `D:(A;;GA;;;<service SID>)(A;;GA;;;<--user-sid>)` |
| state directory ACL | Unix `0700`; Windows, the parent's (per user under `%LOCALAPPDATA%`) | `--as-service`: the service account, SYSTEM and the administrators, protected and inherited |
| passkey pages | `http://localhost:7734`, on loopback only | `--web-port PORT` (`0`: one the OS picks) |
| Claude Code binary (`claude-code` arches) | `claude` on the daemon's `PATH` (`claude.exe` on Windows), found and pinned at start; under `--as-service` a start that finds none is refused, while an interactive daemon looks again at each mount (never at call time) | `vkd --claude-bin PATH`, `vk boot --claude-bin PATH`, `vkd-service install --claude-bin PATH` |

`$VK_ENDPOINT` and `$VK_NODE_KEY_FILE` are read by `vk` only: a daemon on a
non-default endpoint is always started with `--endpoint`, and `vk boot` passes
on the endpoint it resolved. The one variable `vkd` reads is `$RUST_LOG`, and
with it unset it logs at **info** — which is why the boot report is in
`<state_dir>/vkd.log` without anybody having had to ask for it.

### A node, end to end

A throwaway state directory and file-backed keys, so nothing here touches the
keyring. Paste it a line at a time and read what each verb prints; `$ARCH`,
`$TASK` and `$STOP` are the ids the commands before them printed.

```
D=${TMPDIR:-${TEMP:-/tmp}}/vk-demo      # any local path outside a synced folder
export VK_NODE_KEY_FILE=$D/node.key

vk boot --state-dir $D --master-key-file $D/master.key
vk status                               # node-1, 2 events, chain verified, stopped nothing

vk mount mock gemma-mock --ctx 4096     # ARCH=sha256:fd4b8acf...
vk ls /arches

vk task submit --goal "Draft a proposal for Acme" --artefact proposal \
      --plan $ARCH --draft $ARCH --approve --release out      # TASK=task-node-1-2
# --judge $ARCH adds a judging step after the draft; --harness claude-code
# drafts with the confined agent instead of an arch (`vk harness run` runs it).
vk task step $TASK --all                # runs until it waits: status waiting_human
vk ps
vk task show $TASK                      # the same task in full, with where it writes

vk stop                                 # STOP=stop-node-1-3, and nothing steps
vk task step $TASK --all                # vk: scope is stopped: node (-32001), exit 1
vk resume $STOP
vk approve $TASK                        # signed with this node's device key
vk task step $TASK --all                # status done
ls $D/exports/out                       # 3c2c07fa23e9.proposal

vk top
vk dmesg -n 6
vk ledger verify                        # 18 events, chain verifies

vk man                                  # the 19 contracts
vk man clearance                        # one of them, rendered
vk man clearance --json                 # the schema itself
```

Arches and their state: `vk ls /arches` has a `STATE` column, and `vk top` has
one beside each arch's counters. An arch is `ready` when its adapter is there,
`starting` while the daemon is re-creating it, and `unavailable` when it cannot
be re-created — with the reason in the `WHY` column (under the table in `vk
top`). `vk status` says `arches  3 (1 unavailable)` when any of them is not
`ready`.

`starting` is the window just after a restart. A daemon re-creates the arches
it had mounted *behind* its endpoint, so it answers `vk status` and `vk ls`
from the moment `vk boot` returns — which it does at once, saying `; 2 arches
starting` — while a container starts or a model loads behind it. A `vk task
step` that names an arch in that window exits non-zero with `arch <id> is
starting; retry`, and the task stays `queued` with its step still `pending`:
run the same `vk task step` again when the arch is `ready` and it goes on from
where it was. Nothing is failed and nothing has to be re-submitted.

**Upgrading a store made before this:** a node records *how* to re-create each
arch from the moment it is mounted, and a store written by an earlier build has
no such record. Every arch in one of those comes back once as `unavailable  no
mount spec was recorded for this arch…`; mount each of them again (`vk mount
ollama …`, `vk mount claude-code …`) and they persist across every restart
afterwards. An arch whose engine has genuinely changed underneath — a new
image, newer weights, a newer `claude` — comes back `unavailable  manifest
changed: …` instead, and the reason names the `vk umount <id>` that retires the
old id; its replacement is the mount you make next, under a new id, because an
arch id names particular weights and is never re-pointed at different ones.

Stopping the node: the `vk boot` that starts a daemon prints its `pid` (and
`--json` gives it as a field) — `taskkill /F /PID <pid>` on Windows, `kill
<pid>` elsewhere. A later `vk boot` on the same endpoint and state directory
starts nothing, says `already_running` and has no pid to give, so find it with
`tasklist /FI "IMAGENAME eq vkd.exe"` or `pgrep vkd`. A `vk boot` naming a
*different* state directory on that endpoint is refused, because one endpoint
means one store — and it creates nothing before refusing.

Shell notes: `vk boot` without `--state-dir` and `--master-key-file` uses this
user's local app data and writes a `vk`/`master` entry in the OS keyring, which
is the intended everyday path. **Headless Linux:** a machine with no Secret
Service (no desktop session, a container, a server over SSH) has no keyring
for the daemon to put the master key or the node's device key in, and the
keyring call fails rather than falling back — start it with
`--master-key-file FILE` and `--node-key-file FILE` (both born `0600`, refused
if anybody else can read them), and keep the two files with the state
directory: a master key that is lost is every blob on the node lost. In Git Bash on Windows, set `MSYS_NO_PATHCONV=1`
first, or namespace paths like `/arches` are rewritten into filesystem paths
before `vk` ever sees them — and pass a named-pipe endpoint from PowerShell or
`cmd` rather than Git Bash, which eats the leading `\` of `\\.\pipe\...` even
with that set.

## The SP1 gate

`docs/archive/technical/sp1-gate-checklist.md` is this stage's acceptance gate: eight items, run
on a stock Windows 11 VirtualBox image with Smart App Control on, covering
signed installation, the service identity, the store surviving a restart, the
harness's fence and its network egress, the passkey ceremony under replay and
forgery, the property tests and CI, the demo script in both role orders, and
the TCB statement. SP1 is done when every row in its results table reads
PASS.
