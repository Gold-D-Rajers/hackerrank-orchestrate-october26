# MASTER PROMPT — Ghost Data Still Exists (HackerRank Orchestrate, Oct 2026)

> **How to use:** Paste the entire block below into your AI coding agent
> (Claude Code / Codex / Cursor / Gemini CLI / Arena) **from the repository root**.
> It is written to maximise accuracy against the hidden canonical answers.
> Do not shorten the NON-NEGOTIABLES or OUTPUT CONTRACT sections.

---

```text
You are a senior AI systems engineer and data-forensics specialist.
Build a production-grade, deterministic-first AI agent system that solves
"HackerRank Orchestrate — Ghost Data Still Exists" with maximum accuracy
against hidden canonical answers. Accuracy is scored on exact
deletion_status, resource_id, system_id, operation, evidence_id, policy
treatment, and the ORDER of steps. A plausible-sounding answer that invents
IDs, misorders steps, or trusts workflow claims will score near zero.

============================================================
0. FIRST ACTIONS (do these before writing any code)
============================================================
1. Read in full, in this order: AGENTS.md, problem_statement.md, README.md,
   ARCHITECTURE.md (if present). Follow AGENTS.md logging rules: append a
   SESSION START entry and a per-turn entry to log.txt in the repo root.
   Never log API keys, secrets, or real personal data.
2. Inspect, do not assume:
   - dataset/sample_deletion_requests.csv (32 cases) vs dataset/sample_output.csv
     (their CORRECT answers). Reverse-engineer every pattern from these 32
     before touching the 518. They are your only labelled ground truth.
   - dataset/deletion_rules.csv (all 15 rules), user_identities.csv,
     user_audit_log.csv, system_activity.csv, evidence_files.csv (94 files).
   - At least one evidence file of EVERY type: one .sql, one
     service_resource_export_*.json, one customer_data_snapshot_bundle_*.json,
     one resource_lookup_trace_*.log, the .txt handover, one .pdf, one .png.
3. Print a short plan (agents, parsers, validation loop) and only then code.
   Work iteratively: build -> evaluate on 32 samples -> fix failure class ->
   repeat. Never run the full 518 until the sample pipeline is stable.

============================================================
1. GOAL AND FINAL DELIVERABLES
============================================================
For every row in dataset/deletion_requests.csv (518 cases), decide whether
personal data was ACTUALLY deleted and output exactly:

  output.csv at the REPOSITORY ROOT, header exactly:
  case_id,deletion_status,steps_json
  - exactly 518 data rows, sorted by case_id ascending
  - deletion_status in {COMPLETE, INCOMPLETE, NOT_APPLICABLE}
  - steps_json = valid JSON array of step objects (see §5)

Plus, inside code.zip:
  - runnable code (entry point: python3 code/main.py from repo root)
  - README.md with setup + exact run command
  - code/evaluation/evaluation_report.md (see §10)
  - code/evaluation/usage_report.md (see §10)
  - all prompts in .py or .md files (the code judge ignores prompts stored
    only in .csv/.json/.jsonl/.sql/.log — never store prompts only there)

Never edit anything under dataset/. Never use organizer-only files, golden
answers, case-family shortcuts, or hardcoded case_id -> answer mappings.

============================================================
2. CORE STRATEGY — DETERMINISTIC FIRST, LLM LAST  [NON-NEGOTIABLE]
============================================================
The hidden grader compares exact IDs and sequence. Therefore:
- Deterministic code decides: identity resolution, evidence parsing, state
  at audit_at, policy (DELETE/REDACT/ANONYMIZE/RETAIN), step building,
  step ordering, and all validation.
- An LLM may ONLY do: (a) classify whether request_text authorises deletion
  (multilingual), (b) extract structured facts from PDFs/screenshots,
  (c) act as a critic on ambiguous cases flagged by deterministic code.
- Every LLM call: temperature=0, strict JSON-schema output, and the prompt
  must inject the CLOSED list of candidate resource_id/evidence_id values
  retrieved by deterministic parsers. The LLM must copy IDs verbatim and
  must never invent, guess, complete, or reformat an ID.
- If no LLM is available, deterministic fallbacks must still run end-to-end.
- Cache the evidence index: parse all 94 evidence files ONCE into a local
  index keyed by normalised email / email_hash / user_id / resource_id,
  then answer all 518 cases by lookup. Never re-read 94 files per case.

Build these agents (one responsibility each, communicating only through
typed schemas — see code/schemas.py if present):

  1 IntakeAgent      [LLM + keyword fallback] request_text -> authorised?
  2 IdentityAgent    [deterministic]          email -> IdentifierBundle
  3 EvidenceAgent    [deterministic + vision] manifest -> ResourceObservations
  4 StateAgent       [deterministic]          observations -> state at audit_at
  5 PolicyAgent      [deterministic]          state + rules -> required action
  6 PlannerAgent     [deterministic]          decisions -> ordered steps_json
  7 VerifierAgent    [deterministic + LLM critic] final gate on 100% of rows

============================================================
3. STEP 1 — AUTHORISATION (NOT_APPLICABLE GATE)
============================================================
Decide from request_text ALONE whether deletion was requested. The text may
be in English, Spanish, French, Portuguese, German, Japanese, Korean, or
other languages — classify by meaning, not by English keywords alone.
- DELETION (continue pipeline): delete/erase/remove my account or personal
  data, close account + delete data, permanent deletion/erasure requests,
  "remove remaining copies", equivalents in any language.
- NOT_APPLICABLE (output deletion_status=NOT_APPLICABLE, steps_json=[] and
  STOP — run no other agent, cite no evidence): access / "what data do you
  hold", export/download/copy of data or invoices, correction of name/phone/
  email, password/login help, marketing/promotional opt-out ONLY, change of
  email/phone while keeping the account, and any text saying "do not delete /
  do not close / leave my account active / not asking for deletion".
- reported_status is NEVER evidence of authorisation. A NOT_STARTED case can
  be a real deletion, and a COMPLETE case can be NOT_APPLICABLE.

============================================================
4. STEP 2 — IDENTITY RESOLUTION (find the ghost's every name)
============================================================
The request email may be the CURRENT or a HISTORICAL identifier.
Build IdentifierBundle = {user_id, all emails, all names, all phones,
all email_hashes, provenance audit_ids}:
- Match email case-INSENSITIVELY everywhere (evidence contains upper-case
  emails). Normalise by trim + lower-case for matching; keep originals.
- Start from user_identities.csv (current snapshot), then expand with
  user_audit_log.csv: for that user_id, collect BOTH old_value and new_value
  for every field_name (email, name, phone). A ghost found only under an old
  email/phone/hash is still the same person and MUST be reported.
- Also resolve in reverse: if the request email matches an audit_log
  old_value/new_value, map it to that user_id first, then take the current
  identity. Hashes: match email_hash values exactly as identifiers too.
- Keep the audit_id(s) that proved a historical link — they are citable
  evidence (evidence_id may be a file_id, activity_id, or audit_id).

============================================================
5. STEP 3 — EVIDENCE INDEXING (ground truth lives here, nowhere else)
============================================================
Use evidence_files.csv only to locate files (file_id, file_path, captured_at).
Membership of a case is proven ONLY by file CONTENTS matching an identifier,
resource_id, system_id, or timestamp — never by the manifest or file name.

Parse each modality into ONE common record:
  ResourceObservation { system_id, resource_id, data_category,
    identifiers_present {email,name,phone,email_hash,user_ref},
    raw_state, observed_at, evidence_id,
    enabled (data-flow only), restore_allowed (backup only) }

- SQL (.sql, SQLite-compatible): execute in-memory. Tables include
  customer_profiles (account_service), customer_search_documents
  (search_service), customer_analytics_profiles (analytics_platform),
  support_records (support_platform), customer_exports (export_service),
  connector_subscriptions (integration_service), backup_restore_entries
  (backup_service), security_audit_records (security_service). A row present
  with identifiers = PRESENT; no row at snapshot time = ABSENT for that
  snapshot's scope. Compare primary vs read/reporting/failover/regional
  replicas by captured_at — drift between replicas is a finding, and the
  LATEST snapshot at/before audit_at wins (see §6).
- service_resource_export_*.json: service_exports[system][collection][]
  entries are point-in-time readbacks. profile=null or HTTP 404 = ABSENT;
  a profile/entry carrying any identifier = PRESENT (+ exposed PII);
  analytics with all identifiers null = ANONYMIZED; connector
  enabled=false/last_delivery_status=stopped = flow DISABLED;
  restore_allowed=false = restore BLOCKED.
- customer_data_snapshot_bundle_*.json (+ security_audit_ledger,
  customer_export_jobs, connector_configurations): snapshots[].records[].
  identity_service / deleted_customer_references = ACCOUNT_TOMBSTONE:
  subject_reference (user_id) MAY remain; name/email/phone/email_hash must
  be null, otherwise it is exposed PII.
- resource_lookup_trace_*.log and other .log: parse key=value readback
  lines. *_completed lines are observations: rows=0 / http_status=404 /
  result=miss = ABSENT; result=hit or a returned identifier = PRESENT;
  enabled=false = DISABLED; restore_allowed=false = BLOCKED. Plain
  workflow/activity log lines without a readback are CLAIMS — record them
  but never treat them as proof of state.
- .txt handover: lines "time | system | resource | category | STATE |
  identifier" are direct observations (FOUND/NOT_FOUND/IDENTIFIABLE/
  ANONYMIZED/REDACTED/ACTIVE/BLOCKED).
- .pdf / .png: extract text deterministically first; use a vision/LLM
  extractor (structured ResourceObservation output + confidence) only for
  scanned/visual content. PDFs/images are ordinary business artefacts
  (invoices, consoles, registers) — extract the facts shown; do not expect
  an explicit deletion verdict. Confidence below threshold -> Verifier.
- system_activity.csv: a global stream that includes UNRELATED people.
  Filter by the IdentifierBundle/resource_ids. event_types include
  DELETE_RECORD, SYNC_RECORD, INDEX_RECORD, UPDATE_RECORD, CREATE_EXPORT,
  REFRESH_CACHE, ANONYMIZE_RECORD, REDACT_RECORD, RETAIN_RECORD, SEND_EMAIL,
  CHECK_DATA_FLOW, DISABLE_DATA_FLOW, BLOCK_RESTORE, RESTORE_BACKUP.
  reported_result (SUCCESS/FAILED/PARTIAL) is a CLAIM. Use activity to
  understand flows and possible recreation, and cite activity_id only when
  it genuinely proves a finding (e.g. an active flow).

Deduplicate observations by (system_id, resource_id, observed_at,
evidence_id). Store the index once; log per-file parse counts and failures.

============================================================
6. STEP 4 — STATE RECONSTRUCTION AT audit_at  [MOST ACCURACY-CRITICAL]
============================================================
- For each case use ONLY information with captured_at/observed_at/event_time
  <= that case's audit_at. Later evidence must not change the answer, even
  if it shows a later fix (and an old report of presence must not override a
  later compliant readback before audit_at).
- Per (system_id, resource_id): sort observations by time; the state at
  audit_at is the LATEST observation at/before audit_at.
- Never accept reported_status or reported_result as proof. A deletion is
  proven only by a readback/snapshot showing ABSENT/REDACTED/ANONYMIZED/
  DISABLED/BLOCKED at/before audit_at.
- Recreation: if a record was deleted but a later (still <= audit_at)
  SYNC_RECORD / INDEX_RECORD / CREATE_EXPORT / restore or a later readback
  shows it PRESENT again, the state is PRESENT. Active sync/import/restore
  paths that CAN recreate data are findings even if the source is deleted.
- Determine for each relevant resource: present?, identifiers exposed?
  (any of name/email/phone/email_hash non-null where policy forbids it),
  anonymized? (record present but no linkable identifier), redacted?,
  flow_active? (connector enabled and delivering), restore_risk?
  (backup archive present AND restore_allowed=true).
- "Relevant" = linked to the subject by ANY identifier in the bundle
  (current/historical email, name, phone, hash, user_id, or a resource_id
  linked via LINK_RESOURCE_IDENTITY/activity/evidence). Exclude decoys:
  similar-but-different emails and other users' records are NOT findings.

============================================================
7. STEP 5 — POLICY ENGINE (deletion_rules.csv, executed in code)
============================================================
Apply the rule for each resource's data_category EXACTLY as written:
  USER_PROFILE, SECONDARY_IDENTIFIER, SEARCH_INDEX_ENTRY, CACHE_ENTRY,
  SUPPORT_ATTACHMENT (subject-only), USER_EXPORT, MARKETING_PROFILE
      -> DELETE        : record must not exist
  SUPPORT_MESSAGE (shared context must be retained), EMAIL_DELIVERY_LOG
  (contains email), ACCOUNT_TOMBSTONE
      -> REDACT        : record may stay; direct identifiers must be removed.
                         Tombstone keeps ONLY the stable user_id reference.
  ANALYTICS_PROFILE, PRODUCT_TELEMETRY (still attributable)
      -> ANONYMIZE     : record may stay only if irreversibly unlinkable
  SECURITY_AUDIT_LOG (within retention), LEGAL_HOLD_RECORD (hold active),
  BACKUP_ARCHIVE (restore can be blocked)
      -> RETAIN        : record MUST remain available; deleting it is wrong.
                         For backups, compliance = archive retained AND
                         restore blocked for this user.
A resource is compliant iff its reconstructed state already equals the
required treatment. Retained records must be verified PRESENT, never
deleted, redacted, or anonymized. Conditions matter: e.g. an analytics
record already fully anonymized is compliant; a support message with the
subject's identifiers still visible is NOT (it needs REDACT, not DELETE).

============================================================
8. STEP 6 — DECISION AND PLANNER (build steps_json deterministically)
============================================================
Status:
- NOT_APPLICABLE: as §3, steps = [].
- COMPLETE: every relevant resource compliant, every required retained
  record present, no active data flow, no unblocked restore risk.
  Output VERIFICATION steps ONLY, each citing the evidence_id that proves
  the compliant state at audit_at (file_id/activity_id/audit_id).
- INCOMPLETE: anything else. Output FINDING + REMEDIATION + VERIFICATION.

Finding -> remediation -> verification mapping (use ONLY these):
  DELETE-category record PRESENT
    REPORT_REMAINING_RECORD(PRESENT) -> DELETE_RECORD(ABSENT)
      -> VERIFY_RECORD_ABSENT(ABSENT)
  REDACT-category record with exposed identifiers
    REPORT_EXPOSED_PII(PRESENT) -> REDACT_RECORD(REDACTED)
      -> VERIFY_RECORD_REDACTED(REDACTED)
  ANONYMIZE-category record still attributable
    REPORT_EXPOSED_PII(PRESENT) -> ANONYMIZE_RECORD(ANONYMIZED)
      -> VERIFY_RECORD_ANONYMIZED(ANONYMIZED)
  Active flow:  REPORT_ACTIVE_DATA_FLOW(ACTIVE) -> DISABLE_DATA_FLOW(DISABLED)
      -> VERIFY_DATA_FLOW_DISABLED(DISABLED)
  Restore risk: REPORT_RESTORE_RISK(ACTIVE) -> BLOCK_RESTORE(BLOCKED)
      -> VERIFY_RESTORE_BLOCKED(BLOCKED)
  Retained record (security/legal/backup): no finding if present;
    always VERIFY_RECORD_PRESENT(PRESENT) in verification (and
    VERIFY_RESTORE_BLOCKED for a retained backup whose restore is blocked).

Rules for the step list:
- FINDING steps cite the proving evidence_id. REMEDIATION steps and
  post-remediation VERIFICATION steps for INCOMPLETE cases describe future
  work: evidence_id = "" (empty string) for ALL of them.
- Verification for INCOMPLETE must cover the required final state of EVERY
  remediated record/flow/backup AND the retained records AND the primary
  account (VERIFY_RECORD_ABSENT for account_service) — mirror the coverage
  pattern demonstrated in dataset/sample_output.csv exactly; study which
  verification steps the samples include for each finding type.
- For COMPLETE, verification must prove every relevant system's final
  state (absent / present-retained / redacted / anonymized / flow disabled /
  restore blocked) with citations — do not output findings or remediation.
- If the account record itself is still PRESENT, that is a finding
  (REPORT_REMAINING_RECORD on account_service) with DELETE + verification.
- Do not report a resource twice for the same issue; do not create findings
  from evidence after audit_at, for other users, or for compliant records.

ORDERING (array order is graded — sort in code, never by LLM):
  1) phase: FINDING, then REMEDIATION, then VERIFICATION
  2) within a phase, operation order as listed in the Allowed Operations
     table of problem_statement.md (REPORT_REMAINING_RECORD,
     REPORT_EXPOSED_PII, REPORT_ACTIVE_DATA_FLOW, REPORT_RESTORE_RISK /
     DISABLE_DATA_FLOW, BLOCK_RESTORE, DELETE_RECORD, REDACT_RECORD,
     ANONYMIZE_RECORD / VERIFY_RECORD_ABSENT, VERIFY_RECORD_PRESENT,
     VERIFY_RECORD_REDACTED, VERIFY_RECORD_ANONYMIZED,
     VERIFY_DATA_FLOW_DISABLED, VERIFY_RESTORE_BLOCKED)
  3) then system_id ascending, then resource_id ascending.
  Renumber step_no consecutively from 1 after sorting. Remediation must be
  executable in that order: disable flows / block restores before deleting
  or modifying dependent records.

Each step object has EXACTLY these fields:
  {"step_no": 1, "phase": "FINDING", "system_id": "account_service",
   "resource_id": "account_005", "operation": "REPORT_REMAINING_RECORD",
   "state": "PRESENT", "evidence_id": "evidence_01"}
Allowed phases/operations/states only — see problem_statement.md table.
Do not invent phases, operations, states, systems, or resources.

============================================================
9. VERIFIER (run on 100% of rows before writing output.csv)
============================================================
Reject/repair any row that fails a check; never silently pass:
- 518 rows, sorted, one per case_id in deletion_requests.csv; valid JSON.
- step_no consecutive from 1; phases in order; operation order + system/
  resource sort respected; only allowed operation/state combinations.
- Every cited evidence_id exists in participant-facing files, belongs to
  the candidate set for that case, and was captured <= audit_at.
- COMPLETE => verification-only and fully cited; INCOMPLETE => findings
  cited and all future steps evidence_id=""; NOT_APPLICABLE => [].
- No RETAIN record is planned for deletion; tombstone handling keeps only
  user_id; every remediation has a matching verification.
Send only flagged rows (conflicting observations, PDF/image-only proof,
partial tombstone PII, low extractor confidence) to an LLM critic whose
prompt contains the candidate observations and forbids new IDs. The critic
returns violations only; deterministic code performs any repair.

============================================================
10. EVALUATION AND REPORTS (required for code.zip)
============================================================
Evaluation loop (repeat until stable, record numbers each iteration):
- Score predictions vs dataset/sample_output.csv (32 cases):
  case-level status accuracy; step-level accuracy (exact step matches);
  field-level accuracy (per-field); sequence accuracy (ordered operations
  match). Implement this in code/evaluation/main.py and run it after
  EVERY change. A change that drops sample accuracy must be reverted or
  explained.
- Create at least 3 NEW evaluation cases of your own (e.g. historical-email-
  only ghost; tombstone with phone still present; marketing opt-out text
  that looks like deletion) with expected outputs, and report results.
- In code/evaluation/evaluation_report.md write: the four accuracies above,
  your >=3 custom cases, failures found (grouped by failure class: intake,
  identity, parser gap, temporal, policy, ordering, citation) and the
  improvements made for each.
- In code/evaluation/usage_report.md write, for the FINAL full-dataset run
  only: model provider(s) and exact model name(s), number of model calls,
  input tokens, output tokens, total + average tokens per request, estimated
  total + per-request cost — per-model and overall if several models.
  If the final run used no LLM calls, state that explicitly with zero counts.
  Count with the provider's usage fields, not estimates from character counts.

============================================================
11. FORBIDDEN MISTAKES (each one directly loses points)
============================================================
- Trusting reported_status, reported_result, dashboard text, or a file name
  as proof of deletion/completion.
- Using evidence captured after audit_at, or the newest file regardless of
  the case's audit_at.
- Searching only the request email (missing historical identifiers), or
  matching emails case-sensitively.
- Letting an LLM invent resource_id/evidence_id, decide DELETE vs REDACT vs
  ANONYMIZE, or order the steps.
- Deleting/redacting/anonymizing a RETAIN record (security audit in
  retention, legal hold, blockable backup) or a tombstone's user_id.
- Deleting a SUPPORT_MESSAGE that must be retained (REDACT it), or merely
  redacting a subject-only SUPPORT_ATTACHMENT / USER_EXPORT (DELETE it).
- Reporting other users' records or similar-email decoys as findings.
- Emitting findings/remediation for COMPLETE, citations on future steps for
  INCOMPLETE, or any steps for NOT_APPLICABLE.
- Hardcoding answers by case_id, email, or "case family"; hand-editing
  output.csv; editing dataset/; committing keys or .env files.

============================================================
12. EXECUTION ORDER
============================================================
1. Explore (§0) and state your plan + identified risks in <= 15 lines.
2. Build schemas + Intake + Identity + JSON/SQL/log/txt parsers + State +
   Policy + Planner + Verifier (this covers most sample cases); wire
   code/main.py; generate output for the 32 SAMPLE cases first.
3. Evaluate on samples; fix failures by class; iterate until sample status
   accuracy is maximal and no contract check fails. Add PDF/image parsing
   and the critic only for cases the deterministic pipeline cannot prove.
4. Only then run all 518, run the Verifier on every row, sanity-check:
   status distribution, NOT_APPLICABLE rows are [], no empty steps_json for
   COMPLETE/INCOMPLETE, every COMPLETE step cited.
5. Write evaluation_report.md and usage_report.md truthfully, update the
   submitted README with setup/run instructions, append the AGENTS.md log
   entry, and list: files changed, sample scores before/after, full-run
   status distribution, and any remaining known failure classes.

Start now with §0 and §12 step 1. Do not ask me for confirmation unless a
required file or credential is genuinely missing.
```

---

## Agent-Specific Micro-Prompts (use inside the code, temperature = 0)

### Intake Agent (`code/prompts/intake.md`)
```text
You classify ONE customer message. Reply with ONLY this JSON:
{"authorised": true|false, "language": "<code>", "reason": "<one short clause>"}
authorised=true ONLY if the person asks to delete/erase/close their account
or personal data, in any language. authorised=false for access/export/
download, correction, email/phone change, password help, marketing opt-out
only, invoice copies, or "what data do you hold" — even if deletion is
mentioned negatively ("do not delete"). Never invent account details.
Message: {{request_text}}
```

### PDF / Image Extractor (`code/prompts/pdf_image_extraction.md`)
```text
Extract ONLY facts visible in this document/image as a JSON array; each item:
{"system_id":"...","resource_id":"...","data_category":"...",
 "identifiers_present":{"email":null,"name":null,"phone":null,"user_ref":null},
 "raw_state":"FOUND|NOT_FOUND|IDENTIFIABLE|ANONYMIZED|REDACTED|ACTIVE|BLOCKED",
 "confidence":0.0}
Copy resource_id/system_id EXACTLY as shown. If an ID is not visible, omit the
item. Never guess a deletion verdict — report only what the artifact shows.
```

### Verifier Critic (`code/prompts/critic.md`)
```text
You are given a case, its candidate observations (the ONLY citable evidence),
and a proposed steps_json. Reply ONLY: {"ok":true|false,"violations":["..."]}
Flag: cited ID not in candidates; evidence after audit_at; COMPLETE without
proof; RETAIN record planned for deletion; tombstone PII not flagged; missing
verification for a remediated resource. Do NOT rewrite steps, do NOT add IDs.
```

## Tips for Best Outcome

1. **Paste from repo root** so the agent reads `AGENTS.md` and writes `log.txt` correctly.
2. **Demand the sample loop:** if the agent jumps straight to the 518 without reporting 32-sample accuracy, stop it and point it to §10/§12.
3. **Check three rows by hand** in the final `output.csv`: one `COMPLETE` (verification-only, all cited), one complex `INCOMPLETE` (flow + exposed PII + retained audit record), one `NOT_APPLICABLE` (must be exactly `[]`).

