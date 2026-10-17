# Driver packaging and deployment — Design

**Status:** approved section by section in brainstorming (2026-10-16), pending founder review of this document.
**Date:** 2026-10-16
**Scope:** how a driver leaves the development tree and becomes an installed, signed, fully hashed artefact a node mounts; the manifest fields that say where a driver runs; the interface of the session agent that the next chantier implements. One implementation plan follows this spec.
**Inputs:** founder decisions of 2026-10-15/16 (below); the pilot spec `2026-10-14-device-driver-contract-and-recette-pilot-design.md` and its rulings R1–R28 (notably R5 hash exclusions, R15 the unhashed interpreter, R25 unmount is a human act, R27 the Store Python gap, R28 the five refusal words); the session-agent spike note `docs/strategy/decisions/2026-10-15-user-session-agent-dependencies.md` (private); the capital spike I1 (`.superpowers/spikes/2026-10-10-capital/report-I.md`, private: OCI layout + offline signature, tamper detection, restore on a fresh node); `contracts/tcb.md` §Drivers.

---

## 1. Why this, now

The pilot's driver lives in the repository tree, under the founder's user profile, and is hashed only in part: `files_sha256` skips `.venv`, so the interpreter and every library the driver runs on are outside its identity (R15), and a Store Python under that `.venv` silently breaks the Job Object (R27). The spike of 2026-10-15 settled the execution model the service mode needs: a keyless agent in the user's session, a service that keeps the keys and verifies containment itself, **drivers installed where the user cannot write and the service can read**. That installation is a packaging problem before it is an agent problem, and the founder asked for the deployment method to be industrialised before promotion on evidence.

OS analogy, kept: a package is a firmware image — everything the supervisor will hash and run, nothing that depends on the board. Installing it flashes it to a read-only region. Mounting it is the supervisor's act of loading a driver from that region, after checking the image's signature and checksum.

## 2. Decisions log (founder, 2026-10-15/16)

| # | Decision | Notes |
|---|---|---|
| D1 | Execution model: a **keyless remote I/O agent** in the user's session, the **service** holds every key and decision, verifies containment through a named Job and holds the kill switch | spike verdicts 1–5; agent launched by an AtLogOn task plus a 1-minute repeating trigger; evidence pushed over the pipe; `vk-agent` in Rust, signed |
| D2 | Drivers install under **`%ProgramFiles%`** with an embedded CPython; the hash covers interpreter and libraries (closes R15); profile, work dir and evidence live under **`%LOCALAPPDATA%`** | the service cannot read a user profile and the user cannot write Program Files |
| D3 | After an agent restart (crash, logoff) drivers stay **degraded until a human remount** | no mount lease; I1/R12 kept |
| D4 | Package form: **OCI image layout on disk + Ed25519 signature by a publisher key** of the organisation | the capital's format (spike I1); no Sigstore in the kernel; a cosign bundle may be emitted later from the same digest |
| D5 | Package content: manifest, driver files, lockfile, **the wheels** and **a standalone non-packaged CPython** — everything embedded, offline install, ~70 MB | the browser stays the system's (`browser_channel`) |
| D6 | Packing is a **human act**, signed with the node's device key, on the ledger as **`driver.packed`** (new kind) | provenance of a package traces to a person and a node |
| D7 | Install is **elevated and off the ledger**; mount stays the human act of today, from the installed tree, with the hash over the whole tree; a **development root** stays allowed in interactive mode only | `source: installed \| development` in the manifest; `tampered` joins the unmount vocabulary |
| D8 | Publisher key pinned by **TOFU at first install**, confirmed by the admin on screen | an explicit `vk capital trust` import was declined for now |
| D9 | Versions are **immutable, side by side**; switching is unmount then mount of the other version; the same gesture rolls back | `uninstall` is elevated and refused while mounted |
| D10 | `vk-agent` and the service path are **specified as an interface here and implemented in the next chantier**; interactive mode is the only execution mode this chantier ships | the same manifest runs in both modes |

## 3. Architecture

### 3.1 Components

1. **`vk-contracts`** gains the package manifest (`DriverPackage`, §4.1), three manifest fields on `DriverManifest` (`execution_context`, `needs_input_desktop`, `source`, §4.2), the payload `driver_packed` and two fields on `driver_mounted` (§4.4), the vocabulary word `tampered` on `driver_unmounted`, and the publisher key record. Schemas generated, `schema_drift` and examples as for every contract.
2. **`vk-capital`** (new crate, library + the `pack`/`install` logic): writes and reads the OCI layout, builds the lockfile and the wheel set, embeds CPython, signs and verifies with the publisher key, computes the **installed fingerprint** (§4.3). It depends on `vk-contracts` only. Named for what it is the first component of: the capital package of spike I1 will reuse the same layout, media-type family and signature.
3. **Kernel (`vk-kernel`)**: the publisher key (create, show, pin), `driver.pack` as a human syscall, mount from an installed tree (resolves `<name>@<version>`, verifies signature and fingerprint, records `package_digest` and `source`), the fingerprint cache, the `tampered` degradation, and the `execution_context` rules of §4.5.
4. **`vk-driver`**: the host keeps its protocol; it gains the split the spike asked for — a **link** trait with the local implementation only (`LocalLink`); `AgentLink` is declared and left unimplemented (§4.6).
5. **`vk-cli`**: `vk capital key init|show`, `vk driver pack`, `vk driver install|uninstall` (elevated), `vk driver ls` extended with installed versions, `vk mount driver <name>@<version>` beside `--manifest FILE` (§4.7).
6. **Installer**: `vk driver install` is the elevated step; the existing `vkd-service install` does not change in this chantier.

### 3.2 Where things live

| What | Where | Who writes | Who reads |
|---|---|---|---|
| Installed driver `<name>@<version>` | `%ProgramFiles%\Aubier\drivers\<name>\<version>\` | administrators (install) | everyone (read-only ACL) |
| Publisher key | the service's keyring (service mode) or the node's keyring (interactive mode), as the device key | kernel | kernel |
| Pinned publisher public keys | `<state_dir>/capital/publishers/<key_id>.pub` | kernel at first install (TOFU) | kernel |
| Browser profile, work dir, evidence | `%LOCALAPPDATA%\Aubier\drivers\<name>\{profile,work,evidence}` | the session side (daemon today, agent tomorrow), private ACL | the driver |
| Fingerprint cache | `<state_dir>/drivers/<name>/fingerprint.json` | kernel | kernel |
| Development root | anywhere under the user's profile | the developer | the interactive daemon only |

The profile survives versions: the hand-opened session to MaskAI is not redone on every update.

### 3.3 What the kernel enforces, and what it trusts

The kernel enforces: the signature against a pinned publisher key, every layer digest at install, the installed fingerprint against `files_sha256` at mount and before every spawn, the refusal of a development root outside interactive mode, and the refusal to uninstall a mounted version. It trusts: the administrator who confirms the TOFU pin, the read-only ACL of `%ProgramFiles%` (a local administrator can rewrite it — and the fingerprint check catches that), and the embedded CPython's own integrity (hashed, but not attested beyond the package's signature).

## 4. Contracts

### 4.1 The package: OCI image layout

A directory (or a tar of it) in the standard OCI image layout: `oci-layout`, `index.json`, `blobs/sha256/<digest>`. One image manifest, artifact type `application/vnd.aubier.driver.v1`, one layer per component, each with its media type and the annotation `org.aubier.component`:

| Layer | Media type | Content |
|---|---|---|
| `manifest` | `application/vnd.aubier.driver.manifest.v1+json` | the `DriverManifest` JSON, `source: installed`, `files_sha256` = the installed fingerprint (§4.3) |
| `files` | `application/vnd.aubier.driver.files.v1.tar` | the driver directory as R5 hashes it today (no `.venv`, `__pycache__`, `tests`, `fixture`, top-level `manifest*.json`), with `requirements.txt` |
| `lock` | `application/vnd.aubier.driver.lock.v1+json` | every Python distribution with version, wheel file name and SHA-256, produced from `requirements.txt` by a resolve against the embedded CPython's tags |
| `wheels` | `application/vnd.aubier.driver.wheels.v1.tar` | the wheel files named in `lock`, byte for byte |
| `python` | `application/vnd.aubier.driver.python.v1.tar` | one python-build-standalone release (`install_only`, Windows x86_64, non-packaged), with its upstream SHA-256 recorded in the layer annotations |

Image-manifest annotations: `org.aubier.driver.name`, `org.aubier.driver.version`, `org.aubier.driver.id` (the `driver_id` = hash of the canonical `DriverManifest`), `org.aubier.package.created` (RFC 3339), `org.aubier.publisher.key_id`.

**Signature.** A referrer artifact (`subject` = the image manifest descriptor), artifact type `application/vnd.aubier.signature.v1`, one blob: `{ "key_id", "algorithm": "ed25519", "signed": "<image manifest digest>", "signature": "<base64>" }`. `package_digest` is the image manifest's digest. The layout is readable with `tar` and any OCI tool; the signature is verifiable with twenty lines of code and the publisher's public key.

**Rust type `DriverPackage`** mirrors the index and manifest for the kernel's use; the wire format is the OCI JSON, not a serde dump of the struct.

### 4.2 `DriverManifest`: three new fields

```json
{
  "execution_context": "interactive",
  "needs_input_desktop": false,
  "source": "installed"
}
```

- **`execution_context: "interactive" | "service"`** — where the driver's process must live. `interactive`: the user's desktop session (a browser, Office). `service`: session 0 under the service account, `headless: true` required, no profile under `%LOCALAPPDATA%`. In the manifest, so in `driver_id`: a driver moved from one context to the other is another driver. Default when absent: `interactive` (every existing manifest keeps its id — verified by a test that hashes the pilot's manifest before and after this change; see §7).
- **`needs_input_desktop: bool`** — the driver drives the UI through keyboard, mouse or UIA and needs the input desktop unlocked (Office). A CDP-driven Chrome does not. Default `false`. Enforcement arrives with the agent; this chantier records the claim.
- **`source: "installed" | "development"`** — written by `vk driver pack` into the packed manifest copy (`installed`) and set by the kernel at mount of a bare manifest (`development`); **verified by path, never trusted as declared** (Ruling 10's pattern): a manifest that says `installed` from a path outside `%ProgramFiles%\Aubier\drivers\` is refused with the remedy, and a bare manifest that says `installed` is refused likewise. Not part of `driver_id`: it is excluded from the canonical hash so that the same driver has the same id in development and once installed — the **package digest**, not the driver id, says which bytes ran.

**Rules by context, this chantier (interactive daemon only):**

| | `source: installed` | `source: development` |
|---|---|---|
| `execution_context: interactive` | mount from `<name>@<version>`; fingerprint over the whole tree | mount from `--manifest FILE`; R5 hash as today; **interactive daemon only** |
| `execution_context: service` | refused: `remedy: the service mode ships with vk-agent` | refused likewise |

### 4.3 The installed fingerprint

`files_sha256` of a packed driver is `hash_dir` over the **installed tree**: `<name>\<version>\{driver files, python\, venv\}`, every file at any depth, path-sorted, `__pycache__` excluded (bytecode is regenerated and not reproducible), the manifest file itself excluded as today. The install step materialises the tree (extract `files`, extract `python`, create `venv` from the embedded CPython, install the wheels from `wheels` with `pip --no-index --find-links`, never the network), then computes the fingerprint and **refuses the install if it differs from the manifest's `files_sha256`**: packing computed the same fingerprint on the packer's node by doing the same materialisation into a temporary directory. Reproducibility conditions, enforced by the packer: pinned CPython release, wheels only (no sdists), `pip` told `--no-compile`, no `.pth` written by the resolve, `pyvenv.cfg` rewritten to relative paths.

**Cache.** Hashing 70 MB takes under a second; before every spawn it is still a cost. The kernel keeps `fingerprint.json` with the fingerprint and, per file, `(relative path, size, mtime_ns)`. Before a spawn it walks the tree: an unchanged `(size, mtime)` set reuses the fingerprint; any difference rehashes everything. A rehash that differs from the manifest degrades the driver: `driver.unmounted{reason: "tampered"}` and the mount is gone; a remount is the remedy, after an admin reinstalls.

**Addendum (implementation, 2026-10-17; the text above is left as decided).** Two points of this section are implemented differently, each by a ruling of the chantier's plan or ledger. (1) **No `venv\`** (plan ruling P-1): the wheels are installed straight into the embedded interpreter's own `Lib\site-packages` (`pip --no-index --no-compile --find-links`), so the installed tree is `{driver files, python\}` with no `pyvenv.cfg` to rewrite and nothing between the interpreter and the hashed files; the packed command is `python/python.exe -E -s -B <script>` (ruling R-T6e), with no `-I` because `-I` implies `-P` and a driver of two modules could not import its own. Console-script launchers under `python\Scripts\` are deleted after the install (plan ruling P-2), since they embed the interpreter's absolute path. (2) **`__pycache__` is hashed** (ruling R-T8b): the tree is built without bytecode, every `__pycache__` the release shipped is deleted and the interpreter runs with `-B`, so a `.pyc` in an installed tree was put there afterwards and Python would load it; excluding `__pycache__` would have left that unseen. Only a top-level `manifest*.json` and the top-level `.package/` directory (plan ruling P-3) are left out of the fingerprint. The consequence is stated in `contracts/tcb.md`: an administrator who runs the installed `python.exe` without `-B` makes the tree `tampered`.

### 4.4 Ledger payloads

- **`driver.packed`** (new kind, retention class of the arch events) — `{driver_id, name, version, package_digest, files_sha256, publisher_key_id, by: device_key_id}`. A human act: signed with the node's device key, as `driver.mount` is. Written by the node that packs, which may not be the node that installs.
- **`driver.mounted`** gains `package_digest: Option<String>` (`None` for a development root) and `source`. Payload schema bumped; readers tolerate the absent fields on old rows.
- **`driver.unmounted.reason`** vocabulary (R28) gains `tampered`.
- Nothing for install or uninstall: they are OS acts by an administrator, outside the kernel's trust, and the mount that follows carries the digest.

### 4.5 Publisher key

`vk capital key init` creates an Ed25519 key pair in the kernel's keyring (`vk/publisher`), prints `key_id` (SHA-256 of the public key) and writes the public key to `<state_dir>/capital/publishers/<key_id>.pub` as **self-pinned**. `vk capital key show` prints the public key for export. There is one publisher key per node in this chantier; rotation and multi-key are out of scope (§9). The key signs packages only — never a ledger row, never a mount: the device key keeps those roles.

**TOFU at install (D8).** `vk driver install` reads the signature's `key_id`; if a pinned public key exists it must verify; if none exists under that id, the installer prints the key id and fingerprint, asks the administrator to confirm, then pins it (`<key_id>.pub`, with `pinned_at` and `pinned_by: <Windows SID>` in a sidecar). A refused confirmation installs nothing. A package whose signature does not verify against the pinned key of its `key_id` is refused with no prompt.

### 4.6 The agent interface (specified, not implemented)

So that the manifest, the ledger and `vk-driver` do not move again when the agent arrives, this spec fixes the link's shape; the next chantier implements `AgentLink` and `vk-agent`.

```rust
pub trait DriverLink: Send {
    fn spawn(&mut self, spec: SpawnSpec) -> Result<Spawned, LinkError>;   // Spawned { pid, job: Option<JobHandle> }
    fn send_line(&mut self, line: &str) -> Result<(), LinkError>;
    fn recv_line(&mut self, deadline: Instant) -> Result<Option<String>, LinkError>;
    fn evidence(&mut self, dir_token: &str) -> Result<Vec<EvidenceFile>, LinkError>; // bytes pushed, never a path the host opens
    fn kill(&mut self) -> Result<(), LinkError>;
    fn alive(&mut self) -> bool;
}
```

`LocalLink` implements it over the process the daemon spawns today. Messages the agent will speak (JSON lines over the per-user pipe, same DACL as `vk`'s): `agent.hello` / `agent.welcome{epoch}`, `driver.spawn` / `driver.spawned{mount_id, pid, job_name}`, `driver.io{mount_id, line}` both ways, `driver.evidence{mount_id, name, bytes}`, `driver.kill{mount_id}`, `agent.ping`. Admission is on OS facts (impersonated SID, session, image path), never on message fields. The service holds a handle on the named Job (`Global\vk-<mount_id>`) whose DACL grants it query and terminate: the kill switch does not depend on the agent.

### 4.7 CLI surface

| Verb | Human act | Elevated | Does |
|---|---|---|---|
| `vk capital key init` | yes (device key) | no | creates the publisher key, self-pins it |
| `vk capital key show` | no | no | prints the public key and `key_id` |
| `vk driver pack --manifest FILE --version V [--python RELEASE] --out DIR` | yes: `driver.packed` | no | resolves, embeds, materialises into a temp dir, fingerprints, writes the layout, signs |
| `vk driver install PKG` | no (OS act) | **yes** | verifies signature (TOFU), digests, materialises under `%ProgramFiles%`, fingerprints, refuses on mismatch |
| `vk driver uninstall NAME@V` | no | **yes** | removes the tree; refused while mounted |
| `vk driver ls` | no | no | installed versions (`installed`), mounted ones (`mounted`, `degraded`) |
| `vk mount driver NAME@V` | yes (as today) | no | mounts from the installed tree |
| `vk mount driver --manifest FILE` | yes (as today) | no | development root, interactive daemon only |
| `vk driver umount NAME` | yes (R25) | no | unchanged |

`vk driver pack` is the only new human act; `install`/`uninstall` print what they verified and where they wrote, and exit non-zero on any refusal with the remedy on one line.

## 5. Flows

### 5.1 Pack (developer's node)

1. `vk driver pack --manifest drivers/maskai-recette/manifest.json --version 1.0.0 --out dist/`.
2. The kernel checks the manifest (schema, `execution_context`), reads `requirements.txt`, downloads the pinned CPython release (cached under `<state_dir>/capital/python/`, SHA-256 checked against the table in `vk-capital`), resolves wheels for its tags into a temp dir (network allowed here, and only here), materialises the tree, fingerprints it, writes `files_sha256` into the packed manifest copy, writes the layout, signs the image manifest with the publisher key.
3. The human signs the act with the device key; `driver.packed` is written; the path and `package_digest` are printed.

### 5.2 Install (any node, administrator)

1. `vk driver install dist/maskai-recette-1.0.0/` in an elevated shell.
2. Signature → TOFU or pinned key; every blob digest; artifact type; `name`/`version` annotations match the manifest; the target directory does not exist.
3. Materialise under `%ProgramFiles%\Aubier\drivers\maskai-recette\1.0.0\`, fingerprint, compare; on mismatch remove the tree and refuse.
4. Set the ACL: administrators full, SYSTEM full, users read and execute. Print `installed maskai-recette@1.0.0 package=sha256:… fingerprint=sha256:…`.

### 5.3 Mount (interactive daemon)

1. `vk mount driver maskai-recette@1.0.0`, signed.
2. The kernel resolves the tree, reads its manifest, verifies `source` by path, fingerprints (or validates the cache), compares to `files_sha256`, verifies the package signature once more against the pinned key, creates the session-side directories under `%LOCALAPPDATA%` if absent, spawns through `LocalLink` under the Job governor exactly as today (the command is `python\python.exe -I driver.py` resolved in the tree, `venv` on the path through `pyvenv.cfg`), waits for the hello, writes `driver.mounted` with `package_digest` and `source: installed`.

### 5.4 Switch and roll back

`vk driver umount maskai-recette` (human), `vk mount driver maskai-recette@1.1.0` (human). The profile under `%LOCALAPPDATA%` is reused. Back: the same two commands with `1.0.0`. `vk driver uninstall maskai-recette@1.0.0` later, elevated, refused while mounted.

## 6. Failure modes

| Event | Reaction |
|---|---|
| Signature does not verify / unknown key refused by the admin | install refused, nothing written |
| Layer digest mismatch | install refused, nothing written |
| Fingerprint mismatch after materialisation | install refused, tree removed, `remedy: repack on a node with the same CPython release` |
| Tree modified after install | next spawn: rehash, `driver.unmounted{reason: tampered}`, driver degraded; `remedy: reinstall, then remount` |
| `source: installed` claimed from a user path | mount refused |
| `execution_context: service` | mount refused in this chantier |
| Development root in service mode | refused (future; the rule is stated now) |
| `uninstall` of a mounted version | refused: `umount first` |
| Store Python found as the embedded interpreter | impossible by construction (python-build-standalone only); the packer refuses any other `--python` source |
| Packer's network unavailable | pack refused before anything is written |

## 7. Testing

- **Contracts:** schemas regenerated; examples for `DriverPackage`, the three manifest fields (valid: both contexts; invalid: `source` unknown, `execution_context` unknown), `driver_packed`; **identity test**: the pilot's `manifest.json` hashes to the same `driver_id` before and after this change (`execution_context` absent = `interactive`, `source` excluded).
- **`vk-capital`:** write then read a layout; sign then verify; a flipped byte in a wheel, in `files`, in the manifest, each refused; the lockfile resolves offline from the embedded wheels; fingerprint reproducible across two materialisations in two directories; the cache validates and invalidates on `(size, mtime)`.
- **Kernel:** every refusal of §6 by name; `driver.packed` written with the right fields and signed; `driver.mounted` carries `package_digest`; `tampered` degradation after a byte change under the installed tree; development root refused when the kernel is told it runs as a service (a test seam, as `governed` has one).
- **CLI/IPC:** `vk driver pack|install|uninstall|ls`, `vk mount driver NAME@V`, `vk capital key init|show`; TOFU prompt accepted and refused.
- **Installed-tree end to end (Windows CI job `driver-install`):** pack the fixture driver, install it under a temporary directory standing in for `%ProgramFiles%` (the path rule is parameterised for tests), mount it, run the fixture recipe (8 steps) through the real harness as `driver-browser` does today, from the installed tree.
- **Founder's node:** pack `maskai-recette@1.0.0`, install under the real `%ProgramFiles%`, mount, fixture dry run; the live MaskAI run stays on the founder's signal and uses the installed driver when it comes.

## 8. Exit criteria

`maskai-recette@1.0.0` packed on the founder's node (`driver.packed` on the ledger), installed under `%ProgramFiles%\Aubier\drivers\`, mounted from the installed tree with `package_digest` on `driver.mounted`, fixture dry run passed, a byte change under the tree detected as `tampered`. CI green on every job, `driver-install` included. `contracts/tcb.md` updated (§11). R15 closed in the TCB text.

## 9. Out of scope

`vk-agent`, the service execution path and `SESSIONCHANGE` handling (next chantier, interface fixed in §4.6); evidence-gated promotion; publication to a remote OCI registry; cosign bundles; publisher key rotation and multiple publishers; automatic updates; driver packages for Linux or macOS (the layout is portable, the CPython layer and the install paths are Windows-only here); the capital package as a whole (this is its first component and reuses nothing yet).

## 10. Open items for the plan, not for this spec

The python-build-standalone release to pin (3.13.x, `install_only`, the SHA-256 table); how `vk driver install` detects elevation and what it prints when not elevated; the name of the CI job and its runner; the exact ACL string; whether `vk driver ls` shows the pinned publisher. None of these changes a contract.

## 11. TCB statement additions

`contracts/tcb.md` §Drivers: a packed driver's identity covers its interpreter and libraries (R15 closed for `source: installed`); the development root keeps R5's exclusions and is allowed only under the interactive daemon; the publisher key signs packages and nothing else; the install step trusts the administrator's TOFU confirmation and the read-only ACL of `%ProgramFiles%`, and the fingerprint before every spawn is the control that catches a rewrite; `tampered` is a degradation, not a refusal of a human act.
