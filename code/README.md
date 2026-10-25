# Ghost Data Still Exists — deletion-audit agent

Deterministic, offline audit agent for the HackerRank Orchestrate (October 2026)
challenge. It reads only the participant-visible files in `dataset/`, decides for
every request whether deletion was actually requested, reconstructs the true state
of the subject's records at the case's `audit_at`, applies `deletion_rules.csv`,
and emits the ordered finding → remediation → verification plan.

No network calls, no API keys, no LLM calls at run time: two runs on the same
inputs produce byte-identical `output.csv`.

---

## Setup

Python 3.9+ (standard library only — nothing to install):

```bash
python3 --version          # 3.9 or newer
```

PDF parsing uses `pypdf` when it happens to be installed and falls back to a
built-in ASCII85/Flate stream decoder when it is not, so the pipeline never
depends on a third-party package. `Pillow`/`tesseract` are treated the same way:
absent means "no text recovered from screenshots", never a crash.

## Run

From the repository root:

```bash
python3 code/main.py                  # all 518 cases -> ./output.csv
python3 code/main.py --sample         # 32 sample cases -> ./output_sample.csv
python3 code/main.py --out /tmp/x.csv # explicit output path
```

The run prints its status distribution and any contract violations to stderr and
takes ~1.5 s for the full dataset.

Evaluate:

```bash
python3 code/evaluation/main.py output_sample.csv --full   # sample agreement + custom cases + contract audit
python3 code/evaluation/main.py output.csv --full          # full run: custom cases + contract audit
```

---

## Pipeline

```
dataset/ ─► intake ─► identity ─► evidence ─► binding ─► state ─► policy ─► citation ─► planner ─► verifier ─► output.csv
```

| Stage | Module | What it establishes |
|---|---|---|
| intake | `agents/intake_agent.py` | Does `request_text` authorise deletion? Multilingual rule classifier; a request for access, export, correction, marketing opt-out or login help is `NOT_APPLICABLE` and gets `[]`. |
| identity | `agents/identity_agent.py` | Current **and** historical identifiers for the subject: current email/phone/hash from `user_identities.csv`, every superseded value from `user_audit_log.csv`, plus the stable `user_id`. |
| evidence | `agents/evidence_agent.py`, `parsers/*` | Parses all 94 artefacts once (SQL snapshots, JSON exports, log trails, PDFs, screenshots, plain text) into point-in-time `Observation`s keyed by `(system_id, resource_id)`. Each observation remembers the capture time and `file_id`. |
| binding | `agents/account_agent.py` | Attributes global records to individual cases: identity claims first (an artefact or activity row that names one of the subject's identifiers), then a monotone minimum-cost temporal assignment over `requested_at ≤ event ≤ audit_at`, with unusable records left unassigned. |
| state | `agents/state_agent.py` | Picks the newest observation that existed at `audit_at`; a description written after the audit cannot prove the state at the audit, so the citation is dropped instead. |
| policy | `agents/policy_agent.py` | Applies `deletion_rules.csv` per data category and condition (delete, redact, anonymize, retain, tombstone) to produce a `Decision`. |
| citation | `agents/citation_agent.py` | Attaches an admissible `file_id`/`activity_id`/`audit_id`. A finding with no admissible citation is dropped — the audit never asserts what it cannot show. |
| planner | `agents/planner_agent.py` | Orders FINDING → REMEDIATION → VERIFICATION, data-flow and restore actions before dependent record changes, and derives the deletion status. |
| verifier | `agents/verifier_agent.py` | Contract checks per row: header, sort order, step numbering, phase/operation tables, citation rules, `NOT_APPLICABLE ⇒ []`. |

Key domain decisions:

- **Workflow claims are not evidence.** `reported_status`, `reported_result` and
  log `result=` lines are treated as claims; the verdict comes from artefacts.
- **Capture-time gating.** Evidence captured after `audit_at` never decides a
  case (it may still link identifiers, which does not age).
- **Diagnostic/readback logs.** A line like
  `service=search_index_service … system="analytics_platform" record_type="ANALYTICS_PROFILE" result="IDENTIFIABLE"`
  describes the *record*, not the lookup tool: the record's own system/type/state
  are read from the embedded fields.
- **Tombstones.** `ACCOUNT_TOMBSTONE` keeps the stable `user_ref` and loses name,
  email, phone and hash → `VERIFY_RECORD_REDACTED`.
- **Absent records** (`DELETE`/`REDACT`) complete a case, but only when an artefact
  captured before `audit_at` proves the absence (direct row-level proof or a
  whole-export omission).

## Output contract

Root `output.csv`, header `case_id,deletion_status,steps_json`, 518 rows sorted by
`case_id`; steps carry exactly `step_no, phase, system_id, resource_id, operation,
state, evidence_id`; findings and COMPLETE-case verifications cite evidence;
remediation and post-remediation verification for INCOMPLETE cases use `""`.

## Layout

```
code/
├── main.py                 # entry point (argument parsing, orchestration, CSV writing)
├── schemas.py              # Case, IdentifierBundle, Observation, ResourceState, Decision, Step
├── agents/                 # intake, identity, evidence, account, state, policy, citation, planner, verifier
├── parsers/                # sql, json, log, pdf, image, text  (pure standard library)
└── evaluation/
    ├── main.py             # sample agreement, custom cases, contract audit
    ├── custom_cases.py     # hand-built cases beyond the provided sample
    ├── evaluation_report.md
    └── usage_report.md
```
