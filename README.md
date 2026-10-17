# Aubier AI

> **Le noyau AUBIER fait de l'IA un capital : chaque tâche apprise devient une automatisation prouvée, portable et souveraine.**
>
> *The AUBIER kernel turns AI into capital: every task it learns becomes automation that is proven, portable and sovereign.*

Aubier AI (formerly *VerticalAI*) is building **the first European ISA Model**: a kernel installed inside an organisation that orchestrates any AI model — local, cloud or aggregator — and connects natively to the organisation's applications, the way an operating system drives processors and devices.

*Aubier* is the French word for sapwood: the living layer under the bark that carries what the whole tree needs to grow.

---

## Why

Access to AI models is becoming a commodity, while what organisations build with AI keeps getting lost: models are retired every few months, workflows stay locked inside each vendor, and nobody can prove which model actually works for their own tasks. Automation, meanwhile, is still a separate project, owned by whoever hosts it.

Aubier AI starts from the opposite assumption: **models and applications change; the know-how an organisation builds with them should stay its own.**

## What the AUBIER kernel does

| | |
|---|---|
| **ISA contract for models** | One contract for every AI system, whatever its vendor or protocol. Work is routed by the organisation's rules first (which data may go where), then by what has been proven to work on its own tasks. When a model disappears, a qualified fallback is already known. |
| **Native connection to applications** | Each application is reached through a driver with a typed contract: allowed actions, data involved, reversible or not. Drivers prefer the most reliable channel the application offers (files and APIs first, the screen only as a fallback) and are compiled into replayable programs as they are learned. |
| **Automation by design** | Every task moves through three levels — assisted, supervised, automatic — promoted only on measured evidence and with human approval, and demoted automatically when a check fails. Irreversible actions always keep a human in the loop. |
| **AI capital** | Processes, drivers, knowledge, evidence, rules and provenance form a signed, versioned package in open formats. It belongs to the organisation, can be exported at any time, and is re-qualified whenever a model or an application changes. |
| **Shared proof, not shared know-how** | Organisations in the same trade can pool content-free evidence, common rules and alerts node to node, without any central cloud. Client data never leaves its owner. |

## Principles

- **Mechanism in the kernel, policy with the organisation.** The kernel enforces; the organisation decides.
- **Human oversight is a primitive, not a setting.** Approvals use a hardware-backed key; a STOP can always halt the system.
- **Everything is replayable.** Every decision lands in a hash-chained ledger.
- **Sovereign by default, portable always.** Local-first where it matters, any vendor where it is allowed, open formats throughout.
- **Build on what exists.** Proven open-source components are reused wherever they fit; the kernel adds governance, evidence and portability on top.

## Status

Early-stage, under active development.

| Stage | Content | State |
|---|---|---|
| SP0 | Language-neutral contracts and kernel boundary, invariant property tests | Done (`sp0`) |
| SP1a | Single-node kernel: tasks, scheduler, leases and approvals, STOP, encrypted storage, hash-chained ledger, local IPC, the `vk` shell | Done (`sp1a`) |
| SP1b | Real model adapters (local and cloud), confined agent harness, passkey approvals, Windows service, signed builds | Done (`sp1b`) |
| Pilot | First application driver under the driver contract (a browser behind typed business actions), task recipes, and a complete governed process run through the kernel: plan, confined execution, judgement, two signed approvals, release | Done (`pilot-driver`) |
| Driver packaging | A driver cut as a signed package that carries its own CPython and libraries, installed once under `%ProgramFiles%` by an administrator and fingerprinted whole before every start (`tampered` on any change; stat cache: an unchanged size and mtime is not re-read) | Built, under review (`driver-packaging`) |
| Next | Quota and health tracking for the model adapters, the execution context for drivers on a desktop, then evidence-gated promotion, AI capital packaging and re-qualification, peer-to-peer nodes | Being specified |

Feasibility of the next stages has been checked with throwaway prototypes before any product code is written.

## Repository layout

- `contracts/` — language-neutral JSON Schemas (generated from `crates/vk-contracts`) and protocol notes.
- `crates/` — the kernel, written in Rust. Internal codename: `vk` (`vkd` is the daemon, `vk` the shell).
  - `vk-contracts` contract types · `vk-kernel` syscalls, locks, labels, ledger · `vk-store` storage tiering · `vk-ipc` local transport · `vk-cli` the shell · `vkd` the daemon · `vk-service` the Windows service · `vk-web` the local web surface · `vk-stub` / `vk-props` invariant testing.
  - `vk-arch-ollama` · `vk-arch-claude-code` · `vk-arch-anthropic` — model adapters (local engine, the founder's coding-agent subscription, the API in US and EU hosting).
  - `vk-arch-mistral` · `vk-arch-gemini` · `vk-arch-openai-compat` — model adapters for the API providers (Mistral on its EU endpoint, Google's Gemini, any OpenAI-compatible server).
  - `vk-harness` the confined agent harness · `vk-mcp` the tools it may call · `vk-driver` the application-driver host.
- `drivers/` — application drivers (Python), each with its manifest; `recipes/` — task recipes the kernel runs end to end.
- `docs/` — technical README, runbooks and use cases; `scripts/` — recette tooling.

## Build

Rust stable (see `rust-toolchain.toml`). On Windows, use the MSVC toolchain with the Visual Studio 2022 Build Tools ("Desktop development with C++"), and build outside cloud-synced folders.

```
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all -- --check
cargo run -p vk-contracts --bin gen-schemas     # regenerate contracts/schemas/ after changing a contract type
```

## Try the kernel

A throwaway node with file-backed keys, so nothing touches the OS keyring:

```
D=${TMPDIR:-${TEMP:-/tmp}}/vk-demo
export VK_NODE_KEY_FILE=$D/node.key
vk boot --state-dir $D --master-key-file $D/master.key
vk status
vk mount mock demo-model --ctx 4096
vk task submit --goal "Draft a proposal" --artefact proposal --plan $ARCH --draft $ARCH --approve --release out
vk task step $TASK --all        # waits for a human approval
vk approve $TASK                # signed with this node's device key
vk task step $TASK --all
vk ledger verify                # the hash chain verifies
vk liveness renew --business acme --until +30d   # a human extends the automatic window; on the ledger as liveness.renewed
vk man                          # the contracts this kernel speaks
```

`$ARCH` and `$TASK` are the ids printed by the commands before them. In Git Bash on Windows, set `MSYS_NO_PATHCONV=1` first.

## Licence

Proprietary — all rights reserved. See `LICENSE`.

---

**Technical documentation (SP0 to the pilot: state directory, arches, harness, passkeys, Windows service, drivers, demo, recovery):** see `docs/technical/technical-readme.md`. **Running a governed process on a node:** `docs/technical/runbooks/`.
