# VerticalAI contracts

`schemas/*.schema.json` are GENERATED from `crates/vk-contracts` — do not edit by hand.
Regenerate with `cargo run -p vk-contracts --bin gen-schemas`; the test
`schema_drift` fails if the committed files differ from the code.

Versioning rule: any change to a struct's field order, names or types is a
schema change and bumps `vk-contracts`' minor version. Hashes are computed over
canonical JSON, so field order is part of the contract.

`examples/<type>/valid/*.json` must validate; `examples/<type>/invalid/*.json` must not.

## Drivers (pilot)

Five schemas came with the pilot, all generated like the rest:

- `driver_manifest` — a driver's identity and contract: its program, the hosts it may navigate to, and every action with its argument schema and evidence file names;
- `driver_mounted`, `driver_acted`, `driver_unmounted` — the payloads of the `driver.mounted`, `driver.acted` and `driver.unmounted` ledger events (`driver_acted` carries hashes and sizes, never text);
- `task_recipe` — a task whose steps a file lists, submitted with `vk task submit --steps`; a recipe names its arches by manifest name.

Two schemas came with driver packaging: `driver_package` is the OCI image manifest of a packed driver (one layer per component: manifest, files, lock, wheels, python), and `driver_packed` is the payload of the `driver.packed` ledger event, a human act that names the package by digest. `driver_mounted` gains `package_digest` and `source`; a row written before reads with the defaults (no digest, `development`).

Three schemas came with driver learning: `driver_plan` is the plan a human reads and approves before a driver is generated from a firm's documents (actions as ordered ontology verbs, the invariants each must leave true, the qualification it must reach; `plan_sha256` is the hash of its bytes on disk), and `driver_learned` and `driver_qualified` are the payloads of the `driver.learned` and `driver.qualified` ledger events (hashes and counts, never document text).

`driver_manifest.program.files_sha256` is `hash_dir` of the directory holding the driver's first `.py` command element (`drivers/vkdriver/hash_dir.py` computes it, and the kernel recomputes it at mount and before every spawn). `hash_dir` skips `__pycache__`, `.venv`, `tests` and `fixture` at any depth and a top-level `manifest*.json` (pilot ruling R5). `program.command` stays relative to the manifest's directory. The Python interpreter and the libraries in the driver's `.venv` are not hashed (`tcb.md`, "Drivers (pilot)").
