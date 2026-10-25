# Ghost Data Still Exists — AI-Agent Project Structure for Accuracy

> **Core principle: Deterministic first, LLM last.**
> The grader compares `output.csv` against hidden canonical answers on exact
> `deletion_status`, `resource_id`, `system_id`, `operation`, `evidence_id` and
> **array order**. A free-form LLM that invents IDs will score near zero.
> LLMs are used only where language/vision is genuinely needed, and their
> output is **constrained to IDs retrieved by deterministic tools**.

## 1. Why this structure gets accurate results

| Accuracy risk in this challenge | Design countermeasure |
|---|---|
| LLM hallucinates `resource_id` / `evidence_id` | LLM never generates IDs. Parsers build a closed candidate set; LLM only classifies/ranks within it. Validator rejects any ID not in the set. |
| `reported_status` / `SUCCESS` are claims, not truth | State is rebuilt from **readback evidence** (resource exports, lookup logs, SQL snapshots, snapshot bundles) at `audit_at`, never from workflow claims. |
| Evidence after `audit_at` leaks into answer | Every observation carries `captured_at/observed_at`; reconstructor drops `> audit_at` per case. |
| Historical email/name/phone still in old systems | Identity graph expands current + historical identifiers (audit log old/new, hashes, case-folded email) before any search. |
| Multilingual / non-deletion requests | Intake agent is the *only* LLM gate for authorisation; deterministic keyword fallback + `NOT_APPLICABLE → []`. |
| Policy errors (delete a RETAIN record, etc.) | `deletion_rules.csv` is executed by a deterministic rules engine — not prompted. Tombstone = keep `user_id`, strip name/email/phone/hash. |
| Wrong step order loses sequence points | Planner sorts by phase → operation order (spec table) → `system_id` → `resource_id`. LLM does not order steps. |
| PDFs / screenshots misread | Vision/PDF agents return structured observations with the same schema as JSON/SQL parsers; low-confidence rows go to the Critic, not straight to output. |

## 2. System diagram

```text
dataset/deletion_requests.csv (518)
        │
        ▼
┌──────────────────┐   NOT_APPLICABLE ──► []  (stop, no other agent runs)
│ 1 Intake Agent   │   LLM (multilingual) + deterministic fallback
│  authorisation   │
└────────┬─────────┘   DELETION
         ▼
┌──────────────────┐
│ 2 Identity Agent │   DETERMINISTIC graph: identities + audit_log
│  current+history │   → IdentifierBundle {user_id, emails[], names[],
└────────┬─────────┘     phones[], hashes[]}  (case-insensitive)
         ▼
┌──────────────────────────────────────────────────────────────┐
│ 3 Evidence Agents (parallel, one per modality)               │
│  SQL │ JSON-export │ JSON-snapshot │ Log-readback │ TXT │ PDF/Image │
│  → list[ResourceObservation]  (closed candidate set)         │
└────────┬─────────────────────────────────────────────────────┘
         ▼
┌──────────────────┐   latest observation ≤ audit_at per resource;
│ 4 State Agent    │   sync/restore after delete = recreated
│  timeline rebuild│   DETERMINISTIC
└────────┬─────────┘
         ▼
┌──────────────────┐   deletion_rules.csv → DELETE/REDACT/
│ 5 Policy Agent   │   ANONYMIZE/RETAIN per data_category
└────────┬─────────┘
         ▼
┌──────────────────┐   FINDING → REMEDIATION → VERIFICATION
│ 6 Planner Agent  │   DISABLE/BLOCK before DELETE/REDACT/ANONYMIZE
└────────┬─────────┘
         ▼
┌──────────────────┐   schema, IDs, citations, ordering, COMPLETE-proof
│ 7 Verifier/Critic│   DETERMINISTIC validator; LLM critic only on
│                  │   ambiguous/low-confidence cases
└────────┬─────────┘
         ▼
   output.csv  (518 rows, sorted by case_id)
```

## 3. Folder map (what was created)

```text
hackerrank-orchestrate-october26/
├── ARCHITECTURE.md            ← this file
├── AGENTS.md / CLAUDE.md      ← original repo instructions (unchanged)
├── problem_statement.md
├── dataset/                   ← NEVER edited, read-only input
├── output.csv                 ← generated at repo root (518 rows)
├── log.txt                    ← agent session log (see AGENTS.md §2/§5)
└── code/
    ├── main.py                ← ORCHESTRATOR ONLY: wires agents, no logic
    ├── schemas.py             ← single source of truth for all I/O types
    ├── config/
    │   ├── settings.yaml      ← paths, model names, temperature=0, cutoffs
    │   ├── operations.yaml    ← allowed phase/operation/state + sort order
    │   └── systems.yaml       ← system_id ↔ evidence table/collection map
    ├── agents/
    │   ├── intake_agent.py    ← [LLM] deletion vs NOT_APPLICABLE
    │   ├── identity_agent.py  ← [DET] identifier graph
    │   ├── evidence_agent.py  ← [DET+Vision] fan-out to parsers, dedupe
    │   ├── state_agent.py     ← [DET] state at audit_at
    │   ├── policy_agent.py    ← [DET] deletion_rules.csv engine
    │   ├── planner_agent.py   ← [DET] ordered steps_json builder
    │   └── verifier_agent.py  ← [DET + LLM critic] final gate
    ├── parsers/               ← one file per evidence modality
    │   ├── sql_parser.py      ← SQLite snapshots (replicas drift)
    │   ├── json_export_parser.py   ← service_resource_export_*.json
    │   ├── json_snapshot_parser.py ← customer_data_snapshot_bundle_*.json
    │   ├── log_parser.py      ← resource_lookup_trace_*.log + other logs
    │   ├── text_parser.py     ← handover .txt (evidence_03 style tables)
    │   ├── pdf_parser.py      ← [LLM/Vision fallback] retention/legal PDFs
    │   └── image_parser.py    ← [Vision] console screenshots
    ├── prompts/               ← versioned, kept in code.zip (judge-visible)
    │   ├── intake.md
    │   ├── pdf_image_extraction.md
    │   └── critic.md
    ├── llm/
    │   └── client.py          ← provider-agnostic, env-var keys, usage metering
    ├── evaluation/
    │   ├── main.py            ← scorer vs sample_output.csv (32 cases)
    │   ├── evaluation_report.md
    │   └── usage_report.md
    └── tests/
        ├── test_contract.py   ← 518 rows, header, step_no, ordering, citations
        └── test_sample_regression.py ← must not regress on 32 samples
```

## 4. Agent contracts (inputs → outputs, no hidden state)

| Agent | Input | Output | LLM? | Failure mode it prevents |
|---|---|---|---|---|
| Intake | `request_text` | `{authorised: bool, reason, lang}` | Yes, T=0 | Access/correction/marketing requests wrongly deleted |
| Identity | `email`, identities, audit_log | `IdentifierBundle` | No | Missed ghost under old email (sample case_10/12/29) |
| Evidence | `IdentifierBundle`, manifest | `list[ResourceObservation]` | Only PDF/Image | Un-cited or invented findings |
| State | observations, activity, `audit_at` | `list[ResourceState]` | No | Trusting `SUCCESS`/future evidence; missing recreation |
| Policy | `ResourceState`, rules | `list[PolicyDecision]` | No | Deleting RETAIN audit/legal/backup records |
| Planner | decisions + risks | `steps_json` | No | Wrong phase/operation order |
| Verifier | `steps_json`, candidates | `pass / repaired steps` | Critic only | Invalid JSON, non-consecutive steps, COMPLETE without proof |

All agents communicate **only** through `code/schemas.py` dataclasses.
No agent reads `dataset/` directly except via `EvidenceAgent` — this makes
per-agent unit tests and ablations trivial.

## 5. The accuracy playbook (run order)

1. **Golden regression first:** `python3 code/evaluation/main.py` against the
   32 sample cases must pass before touching the 518. Every change is measured
   as case / step / field / sequence accuracy (see `evaluation_report.md`).
2. **Index evidence once, reuse 518×:** parse all 94 evidence files into a
   local observation index keyed by normalised email / hash / `user_id` /
   `resource_id`. Per-case work is then a lookup, not 518 × 94 file reads.
3. **Constrain every LLM call:** JSON-schema output, `temperature=0`, candidate
   IDs injected in the prompt, `evidence_id` must be copied verbatim.
4. **Two-pass verification:** deterministic validator on 100% of rows;
   LLM critic only on rows flagged ambiguous (conflicting observations,
   PDF/Image-only evidence, tombstone with partial PII).
5. **Ablate on samples:** disable one parser at a time; the sample cases that
   break tell you exactly which modality that parser owns
   (e.g. `evidence_54` SQL = case_01/02, `evidence_03` TXT = case_11).

## 6. What NOT to do

- ❌ One mega-prompt per case with raw CSVs pasted in (unbounded IDs, cost, drift)
- ❌ Trust `reported_status`, `reported_result=SUCCESS`, or file timestamps in names over `captured_at`
- ❌ Let the LLM decide DELETE vs REDACT vs ANONYMIZE — that is `deletion_rules.csv`
- ❌ Generate `output.csv` by hand-editing — always `python3 code/main.py`
- ❌ Store prompts only in `.json/.csv/.log` — the code judge ignores those; keep prompts in `.py/.md`
