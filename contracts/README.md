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

`driver_manifest.program.files_sha256` is `hash_dir` of the directory holding the driver's first `.py` command element (`drivers/vkdriver/hash_dir.py` computes it, and the kernel recomputes it at mount and before every spawn). `hash_dir` skips `__pycache__`, `.venv`, `tests` and `fixture` at any depth and a top-level `manifest*.json` (pilot ruling R5). `program.command` stays relative to the manifest's directory. The Python interpreter and the libraries in the driver's `.venv` are not hashed (`tcb.md`, "Drivers (pilot)").
