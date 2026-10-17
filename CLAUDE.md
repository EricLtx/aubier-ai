# Working rules for this repository (Aubier AI)

## Documentation filing (binding, see `docs/README.md`)

- `docs/technical/` = how the system is built and operated (technical readme, `specs/`, `plans/`, `runbooks/`, private `reviews/`). `docs/functional/` = what it does for a user (use cases, corpora, demo; private material under `private/`). `docs/strategy/` = business case, decisions, research — **private, git-ignored**. `docs/archive/{technical,functional,strategy}/` = the same split for what is no longer recent.
- New specs go to `docs/technical/specs/YYYY-MM-DD-<topic>-design.md`, new plans to `docs/technical/plans/YYYY-MM-DD-<topic>.md` — never to `docs/superpowers/`. A plan is archived when its chantier merges; its review record and ledger go to `docs/archive/technical/reviews/`.
- Archive what carries a decision or a measurement; delete what carries neither (transient reports, duplicates, superseded handoffs). Never widen the exposure of a private document.
- Moving a document means updating every path that names it (code, skills, runbooks, manifests) and, for a driver, regenerating `files_sha256`. `python -I scripts/docs/check_docs.py` must pass before a commit that touches `docs/`.

## Build and test

- MSVC toolchain only; `CARGO_TARGET_DIR` is outside OneDrive. Python drivers run on a non-packaged CPython (never the Microsoft Store Python, ruling R27); `drivers/<name>/setup.ps1` builds the venv.
- Every push gets a CI watch (`gh run watch`); the Windows job takes 25–60 min.

## Decisions and reporting

- Contract, invariant, ledger-kind, CLI-surface, merge and roadmap decisions go to the founder (batched questions). Minor rulings stay in the chantier's ledger.
- Reports to the founder are in French, in the embedded-systems register (boot, watchdog, MPU, drivers, datasheet).
- Merges are local `--no-ff` from the main checkout, then push; no pull requests on this repository.
