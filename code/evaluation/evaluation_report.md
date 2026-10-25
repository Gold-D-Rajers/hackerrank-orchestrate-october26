# Evaluation report — Ghost Data Still Exists

All numbers below are reproducible from the repository:

```bash
python3 code/main.py --sample                      # -> output_sample.csv
python3 code/evaluation/main.py output_sample.csv --full
python3 code/main.py                               # -> output.csv (518 rows)
python3 code/evaluation/main.py output.csv --full
```

The harness scores predictions against `dataset/sample_output.csv` (the only
provided ground truth) and additionally re-validates the output contract on any
predictions file.

## 1. Headline results (32 sample cases)

| Metric | Result |
|---|---|
| Case-level deletion-status accuracy | **100.0 %** (32/32) |
| Step-level accuracy — exact case match | **78.1 %** (25/32 cases reproduce their step list exactly) |
| Step-level accuracy — match ratio | **93.8 %** (181/193 gold steps reproduced field-for-field) |
| Field-level accuracy | **94.4 %** (of all emitted step fields) |
| Sequence accuracy (operation order) | **81.2 %** (26/32) |
| Contract violations on emitted rows | **0** |

Field-level breakdown (share of the 193 gold steps whose field matches):

| Field | Accuracy |
|---|---|
| `phase` | 96.9 % |
| `system_id` | 93.3 % |
| `resource_id` | 92.8 % |
| `operation` | 93.3 % |
| `state` | 93.3 % |
| `evidence_id` | 96.9 % |

Accuracy definitions used by `code/evaluation/main.py`:

- **status accuracy** — predicted `deletion_status` equals gold.
- **step-level (exact case)** — same step count *and* every step equal on all six
  fields.
- **step-level (match ratio)** — gold steps matched position-by-position,
  divided by total gold steps (penalises both missing and extra steps).
- **field-level accuracy** — per-field equality over the aligned step sequence,
  including positions that exist on only one side.
- **sequence accuracy** — the ordered list of `operation` values is identical.

## 2. Full-dataset run

| Property | Value |
|---|---|
| Rows written | 518 (one per `case_id`, sorted) |
| Steps emitted | 2 961 |
| Status mix | `INCOMPLETE` 322 · `COMPLETE` 188 · `NOT_APPLICABLE` 8 |
| Contract violations | 0 |
| Evidence parsed | 94 artefacts → 3 352 observations over 2 204 resources |
| Wall clock | ≈1.5 s |
| Determinism | identical bytes across repeated runs |
| Model calls | 0 (see `usage_report.md`) |

The 8 `NOT_APPLICABLE` rows (`case_533`, `535`, `536`, `537`, `538`, `540`,
`541`, `542`) are profile-update, export, login-reset, marketing opt-out and
"send me a copy" requests; each is emitted with `steps_json=[]`.

## 3. Additional (participant-created) evaluation cases

`code/evaluation/custom_cases.py` builds situations that the 32-case sample
barely exercises. They drive the real pipeline components (state → policy →
planner) instead of comparing against stored answers, so they keep working as the
implementation changes. All **11 checks pass**.

| ID | Situation | Expected behaviour | Result |
|---|---|---|---|
| C1 | "Remove me from marketing lists only… keep receipts" | intake refuses deletion → `NOT_APPLICABLE` | PASS |
| C2 | Account tombstone: stable `user_ref` kept, name/email/phone/hash gone | `COMPLETE`, single `VERIFY_RECORD_REDACTED` | PASS |
| C3 | Analytics profile still carries a raw email | `INCOMPLETE`, `REPORT_EXPOSED_PII` → `ANONYMIZE_RECORD` → `VERIFY_RECORD_ANONYMIZED` | PASS |
| C4 | Deletion reported complete, profile row still present | `INCOMPLETE`, `REPORT_REMAINING_RECORD` → `DELETE_RECORD` → `VERIFY_RECORD_ABSENT` | PASS |
| C5 | Active sync connector **and** restore-enabled archive | `INCOMPLETE`; flow remediation and `BLOCK_RESTORE` before the record delete; verifications ordered record → flow → restore | PASS |
| C6 | Only post-`audit_at` evidence describes the record | state is unprovable, no citation invented (`evidence_id` stays empty) | PASS |

C5 also asserts cross-record ordering (dependency rule in §6.5): remediation of
the processes precedes the dependent deletion, and each verification follows its
remediation.

## 4. Failures found during development, and the fixes

Every fix below is in the shipped code; each was validated by re-running the
sample and full datasets.

1. **Workflow claims were being read as state.** `log_parser` treated
   `result="IDENTIFIABLE"`, `rows="0"`, `http_status="404"` as prose. Added
   per-line state interpretation (`FOUND/PRESENT/HIT → PRESENT`,
   `IDENTIFIABLE/EXPOSED → EXPOSED`, `ANONYMIZED/DEIDENTIFIED`,
   `REDACTED`, `NOT_FOUND/ABSENT/MISS/DELETED → ABSENT`,
   `DISABLED/STOPPED`, `BLOCKED`, `ENABLED/DELIVERING`).
2. **Diagnostic/readback exports mis-attributed records to the lookup service.**
   Lines such as
   `service=search_index_service … system="analytics_platform" record_type="ANALYTICS_PROFILE" result="IDENTIFIABLE"`
   were parsed as `('unknown','UNKNOWN')` (166 observations across 12 evidence
   files). A `record_lookup` short-circuit now takes `system_id` and category from
   the embedded `system=`/`record_type=`, which also supplies
   `exists/pii_exposed/anonymized/redacted` and the `DATA_FLOW.enabled` /
   `BACKUP_ARCHIVE.archive_present+restore_allowed` flags.
3. **Ledger rows inherited the file's default category.** In facility/security
   ledgers a JSON file declares one default `record_type`, so per-row values were
   ignored and redact/retain decisions were applied to the wrong category. Fixed
   with per-row `record_type` precedence plus a category→system hint.
4. **PDF evidence produced nothing.** The four PDFs use ASCII85+Flate streams
   (Adobe `~>`), so a plain `zlib.decompress` or a plain `a85decode` both fail and
   the parser silently returned `[]`. Added a standard-library fallback
   (`a85decode(adobe=True)` → `zlib.decompress` → `BT … (text) Tj … T* ET`
   extraction) used only when `pypdf` is unavailable; the handover,
   correction-attachment, legal-retention and security-retention PDFs now yield
   text and their observations.
5. **Absence was unprovable for whole categories.** A deleted account often has
   no row at all, so no observation exists to cite. Added a four-tier
   family-aware absence citation (explicit absence line in a case-relevant
   artefact → export-level omission → …) instead of asserting an uncited
   `VERIFY_RECORD_ABSENT`.
6. **Unsupported findings were emitted.** A `REPORT_*` finding whose citation was
   inadmissible (only post-audit evidence) was still printed. The citation filter
   now drops the whole treatment rather than assert it without proof.
7. **Account binding drifted across sibling cases.** Greedy nearest-event
   assignment produced wrong accounts when several cases finished in the same
   minute. Replaced with identity claims first, then a monotone minimum-cost
   assignment over the case window; 31/32 sample accounts bind correctly (the one
   exception, `case_17`, is discussed below).
8. **Intake over-triggered on non-deletion text.** Requests mentioning "delete"
   inside an opt-out or "do not close my account" produced spurious findings.
   Added explicit non-deletion/negation patterns and an audit-question path so
   multilingual deletion-audit asks stay authorised while access/export/
   correction requests become `NOT_APPLICABLE`.
9. **Verification ordering.** Verifications were emitted in discovery order;
   they now follow the operation table (record, then flow, then restore), which
   is what the spec's step ordering implies.

## 5. Remaining discrepancies on the sample (7 of 32 cases)

| Case | Symptom | Diagnosis |
|---|---|---|
| `case_09` | extra `VERIFY_RECORD_ANONYMIZED analytics_5718` | analytics resource of another (evaluation-set) subject falls inside this window; the golden pairing is not derivable from participant data |
| `case_17` | account `account_6523` instead of `account_4092` | two sibling requests share a deletion batch; the correct pairing needs a discriminator that the artefacts do not carry |
| `case_18` | missing `VERIFY_RECORD_PRESENT audit_record_6645` | that security-ledger id appears nowhere in `dataset/` (gold cites it with an empty `evidence_id`), so it cannot be discovered |
| `case_20` | missing `VERIFY_RECORD_PRESENT audit_record_5569` | same: no participant-visible artefact names the record |
| `case_24` | extra analytics verification | same temporal ambiguity as `case_09` (the record belongs to `case_25`) |
| `case_25` | missing `VERIFY_RECORD_ANONYMIZED analytics_1895` | consequence of the shift above |
| `case_26` | missing `VERIFY_RECORD_ANONYMIZED analytics_4566` | consequence of the shift above |

These are counted against us in the table in §1; they were left unfixed rather
than patched with sample-specific logic (hardcoding gold labels is forbidden by
§6.7 and would not generalise to the 518 evaluation cases).

## 6. Contract audit

`code/evaluation/main.py --full` re-checks the submission contract on any
predictions file: exact header, one row per case sorted by `case_id`, consecutive
`step_no`, phase order FINDING→REMEDIATION→VERIFICATION, operation names and
states from the allowed tables, citations only where the spec requires them
(findings, and COMPLETE-case verifications), no citation on remediation or
post-remediation verification of an INCOMPLETE case, `NOT_APPLICABLE ⇒ []`, and
no COMPLETE case carrying non-verification steps.

Latest result: **0 problems** on both `output.csv` (518 rows) and
`output_sample.csv` (32 rows).
