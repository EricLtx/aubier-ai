# Documentation — map and filing rule

Three kinds of documents, one folder each, and an archive with the same three-way split. A
document lives in exactly one place; what is not recent is archived, what is superfluous is
deleted. Every session that writes or moves a document follows this page (the rule is also
in `CLAUDE.md`, so it is loaded at every start).

## The tree

| Folder | What goes there | Published |
|---|---|---|
| `docs/technical/` | the **current** technical documentation: `technical-readme.md` (how the kernel and the node work), `specs/` (design specs of open or load-bearing chantiers), `plans/` (implementation plans of chantiers in progress), `runbooks/` (operator procedures) | yes |
| `docs/technical/reviews/` | review records, SDD ledgers and rulings of chantiers in progress | **no** (git-ignored) |
| `docs/functional/` | what the product does for its users: `use-cases/` (procedures, corpora, oracles, public reports), `demo/` (the demo brief, script and recorded runs) | yes |
| `docs/functional/private/` | functional material that must not leave the node: recette reports naming exposed infrastructure or a test account, test documents | **no** |
| `docs/strategy/` | framing and strategy: `business/` (business case), `decisions/` (founder decision notes that still bind), `research/` (literature notes) | **no** |
| `docs/archive/{technical,functional,strategy}/` | everything above once it is no longer recent, same split, same publication rule (`archive/strategy/`, `archive/technical/reviews/` and any `private/` stay git-ignored) | as above |
| `contracts/` | the contracts (schemas, examples, `tcb.md`): code-adjacent, generated and checked by the test suite; not a documentation folder | yes |

Outside `docs/`: `README.md` (the front page), `CLAUDE.md` (working rules), `01_Architectural_Documentation/` and `02_Consulting_Toolkit/` (external corpora, never published, not part of this tree).

## The filing rule

1. **Three questions decide the folder.** Does it describe *how the system is built or operated*? → technical. Does it describe *what the system does for a user* (a procedure, a use case, a corpus, a demo)? → functional. Does it frame *why and where the project goes* (business case, decision, research)? → strategy.
2. **Recent means load-bearing.** A spec stays current while a merged chantier still points to it as its contract reference (the founding design and the driver-contract spec stay current). A plan is current while the chantier is open; it is archived at the merge. A runbook is current while the procedure it describes is the one to follow. A review record is archived with its plan.
3. **Archive, do not delete, what carries a decision or a measurement** (a ruling, a benchmark baseline, a signed decision record). **Delete, do not archive, what carries neither** (a transient subagent report, a duplicate, a handoff superseded by a newer one).
4. **Private stays private across moves.** A document that was git-ignored before a move is git-ignored after it; the patterns in `.gitignore` are by folder, so filing it in the right folder is the whole mechanism. Never widen exposure without the founder's decision.
5. **Paths are contracts.** Code, skills, runbooks and manifests reference documents by path (`drivers/maskai-recette/driver.py` reads the recette corpus by path and hashes it into the driver's identity). Moving a document means updating every reference and, for a driver, regenerating `files_sha256`. `scripts/docs/check_docs.py` fails CI when a referenced path no longer exists or a document sits outside the tree.
6. **Specs and plans are named `YYYY-MM-DD-<topic>-design.md` and `YYYY-MM-DD-<topic>.md`** and written under `docs/technical/specs/` and `docs/technical/plans/`, not under the skills' default `docs/superpowers/`.

## Current documents (2026-10-17)

- Technical: `technical/technical-readme.md`; specs `2026-09-22-verticalai-design.md` (the founding design), `2026-10-14-device-driver-contract-and-recette-pilot-design.md` (the driver contract, rulings R1–R28), `2026-10-16-driver-packaging-and-deployment-design.md`, `2026-10-17-driver-learning-office-design.md`; runbook `recette-maskai.md`.
- Functional: `use-cases/recettage-chat-ia/` (procedure, corpora v1/v2, oracles, the 2026-10-13 benchmark baseline, journal); `demo/` (brief, README, runs of 2026-09-25).
- Strategy (private): business case; decision note of 2026-10-15 on the user-session agent; research notes on AI evolution and value.
- Archive: plans SP0, SP1a, SP1b and the driver pilot; the SP1 gate checklist; review records and ledgers of SP1a, SP1b, claude-bin, liveness; the decision record of 2026-09-23.
