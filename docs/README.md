# Documentation — map and filing rule

Seven axes of work, one folder each under `docs/`, and an archive with the same split. A
document lives in exactly one place; what is not recent is archived, what is superfluous is
deleted. Every session that writes or moves a document follows this page (the rule is also
in `CLAUDE.md`, so it is loaded at every start).

## The tree

| Axis | Folder | What goes there | Published |
|---|---|---|---|
| Technical | `docs/technical/` | how the system is built and operated: `technical-readme.md` (how the kernel and the node work), `specs/` (design specs of open or load-bearing chantiers), `plans/` (implementation plans of chantiers in progress), `runbooks/` (operator procedures), `debriefs/` (post-run debriefs of a driver or a chantier, redacted) | yes |
| Technical, private | `docs/technical/reviews/` | review records, SDD ledgers and rulings of chantiers in progress | **no** (git-ignored) |
| Functional | `docs/functional/` | what the product does for its users: `use-cases/` (procedures, corpora, oracles, public reports), `demo/` (the demo brief, script and recorded runs); `private/` for what must not leave the node (reports naming exposed infrastructure or a test account) | yes, except `private/` |
| Users | `docs/users/` | guides for the person who installs and uses Aubier: installation, first run, the CLI by example, limits that bind the user | yes |
| Strategy | `docs/strategy/` | framing: `business/` (business case), `decisions/` (founder decision notes that still bind, and the register of open decisions) | **no** |
| Marketing | `docs/marketing/` | what is said outward: positioning, messaging, deck and site copy | **no** |
| Commercial | `docs/commercial/` | offers, pricing, pilot proposals, prospects and partners | **no** |
| Research | `docs/research/` | technical watch, literature notes, R&D spikes that precede a spec | **no** |
| Archive | `docs/archive/<axis>/` | everything above once it is no longer recent, same split, same publication rule (the private axes and any `private/` or `reviews/` stay git-ignored) | as above |
| — | `contracts/` | the contracts (schemas, examples, `tcb.md`): code-adjacent, generated and checked by the test suite; not a documentation folder | yes |

Each axis folder carries a short `README.md` saying what it holds. Outside `docs/`: `README.md`
(the front page), `CLAUDE.md` (working rules), `01_Architectural_Documentation/` and
`02_Consulting_Toolkit/` (external corpora, never published, not part of this tree).

## The filing rule

1. **One question per axis decides the folder.** How is it built or operated? → technical. What does it do for a user, and how is that proven (procedure, corpus, demo, report)? → functional. How does a person install and use it? → users. Why and where does the project go, and what has the founder decided? → strategy. What do we say to the market? → marketing. What do we sell, to whom, at what price? → commercial. What have we watched, read or tried before deciding? → research. A document that answers two questions is split, or filed under the question it answers first and linked from the other axis.
2. **Recent means load-bearing.** A spec stays current while a merged chantier still points to it as its contract reference (the founding design and the driver-contract spec stay current). A plan is current while the chantier is open; it is archived at the merge. A runbook is current while the procedure it describes is the one to follow. A review record is archived with its plan. A watch is current while no decision or spec has absorbed it.
3. **Archive, do not delete, what carries a decision or a measurement** (a ruling, a benchmark baseline, a signed decision record). **Delete, do not archive, what carries neither** (a transient subagent report, a duplicate, a handoff superseded by a newer one).
4. **Private stays private across moves.** `strategy`, `marketing`, `commercial`, `research`, `technical/reviews` and every `private/` folder are git-ignored by folder; filing in the right folder is the whole mechanism. Never widen exposure without the founder's decision.
5. **Paths are contracts.** Code, skills, runbooks and manifests reference documents by path (`drivers/maskai-recette/driver.py` reads the recette corpus by path and hashes it into the driver's identity). Moving a document means updating every reference and, for a driver, regenerating `files_sha256`. `scripts/docs/check_docs.py` fails CI when a referenced path no longer exists or a document sits outside the tree.
6. **Specs and plans are named `YYYY-MM-DD-<topic>-design.md` and `YYYY-MM-DD-<topic>.md`** and written under `docs/technical/specs/` and `docs/technical/plans/`, not under the skills' default `docs/superpowers/`.
7. **Open decisions live in one register**, `docs/strategy/decisions/<date>-decisions-ouvertes.md`: a decision taken leaves the register for the chantier's ledger or a decision note.

## Current documents (2026-10-18)

- Technical: `technical/technical-readme.md`; debrief `debriefs/2026-10-17-maskai-driver.md` (with its 18/10 follow-up); specs `2026-09-22-verticalai-design.md` (the founding design), `2026-10-14-device-driver-contract-and-recette-pilot-design.md` (the driver contract, rulings R1–R28), `2026-10-16-driver-packaging-and-deployment-design.md` (merged, tag `driver-packaging`), `2026-10-17-driver-learning-office-design.md` (chantier in progress on branch `driver-learning`, with its plan); runbooks `recette-maskai.md` and `learn-office-driver.md` (learned Office drivers: setup, learn, qualify, mount, debrief).
- Functional: `use-cases/recettage-chat-ia/` (procedure, `PROCEDURE-2.md` on the pilot branch, corpora v1/v2/v3, oracles, the 2026-10-13 and 2026-10-17 benchmarks, the 2026-10-17 run, the 18/10 execution pending signature); `demo/` (brief, README, runs of 2026-09-25).
- Users: the axis map only; first guide to write (installation and first run).
- Strategy (private): business case; decision note of 2026-10-15 on the user-session agent; the register of open decisions of 2026-10-18.
- Marketing, Commercial (private): the axis maps only.
- Research (private): the literature notes on AI evolution and value; the watches of 2026-10-17 (Obsidian and LLMOps; maturity tools) and 2026-10-18 (self-healing browser agents, judge calibration, privacy-proxy harnesses).
- Archive: plans SP0, SP1a, SP1b, the driver pilot and the driver packaging; the SP1 gate checklist; review records and ledgers of SP1a, SP1b and the packaging chantier (private); the decision record of 2026-09-23 (private); the private run-1 material of the 2026-10-13 recette (report and test documents).
