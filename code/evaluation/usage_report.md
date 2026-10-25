# Usage report — final full-dataset run

Scope: the run that produced the submitted root `output.csv`

```bash
python3 code/main.py     # 518 cases -> ./output.csv
```

## 1. Model usage of the submitted solution

The shipped pipeline makes **no model calls**. It is a deterministic rule engine:
intake classification, identifier reconstruction, evidence parsing, record
binding, policy application, citation and planning are all implemented as Python
logic over the participant-visible files in `dataset/`.

| Item | Value |
|---|---|
| Model providers | none (no external inference provider is contacted at run time) |
| Model names | none — no LLM is invoked |
| Model-call count | **0** |
| Input tokens | **0** |
| Output tokens | **0** |
| Total tokens | **0** |
| Average tokens per request (518 requests) | **0** |
| Estimated total cost | **$0.00** |
| Estimated cost per request | **$0.00** |
| API keys required | none (the code reads none and stores none) |
| Network access required | none |

Per-model totals: not applicable — a single configuration with zero model calls.
Overall totals equal the per-model totals: 0 calls, 0 tokens, $0.00.

Rationale: the task is a closed-world audit over a fixed dataset whose correct
answers depend on exact identifier matching, timestamps and policy rows. Every
question the agent must answer is decided by data that is already in `dataset/`,
so a deterministic implementation is both cheaper and more reproducible than an
LLM loop; it also removes any risk of the model inventing a resource id or a
citation. The pipeline therefore treats "AI agent" as an *architecture* of
specialised agents (intake, identity, evidence, binding, state, policy, citation,
planner, verifier) with explicit, auditable rules.

## 2. Development-time assistant usage (for transparency)

The code was built with an AI coding assistant inside an agentic IDE session.
The harness does not expose a per-session token counter to the workspace, so no
exact input/output token figures can be quoted for the build itself; none are
estimated here rather than reporting invented numbers.

| Item | Value |
|---|---|
| Assistant | Arena Agent Mode coding agent (harness-exposed name; the underlying model id is not exposed to the sandbox) |
| Providers billed for the submitted run | none |
| Model calls made by `code/main.py` | 0 |
| Model calls made by `code/evaluation/main.py` | 0 |

Development prompts that were *considered* and rejected: an optional LLM intake
hook (removed — the multilingual rule classifier already reaches 100 % status
accuracy and the hook added a silent dependency on an API key) and an
LLM-written step rationale (removed — remediation text must be deterministic and
citation-free).

## 3. Local compute cost of the submitted run

Measured on the evaluation sandbox (Python 3.11, single core, standard library
only):

| Property | Value |
|---|---|
| Cases processed | 518 |
| Evidence artefacts parsed | 94 (24 SQL, 28 JSON, 26 log, 4 PDF, 11 PNG, 1 TXT) |
| Observations built | 3 352 |
| Distinct resources | 2 204 |
| Emitted steps | 2 961 |
| Wall-clock time | ≈1.5 s |
| Peak memory | < 150 MB |
| Monetary cost | $0.00 (no metered service) |

Average per request: ≈2.9 ms of CPU, 0 tokens, $0.00.

## 4. Reproducing the figures

```bash
python3 code/main.py            # prints rows written, status mix, contract result, elapsed time
python3 code/evaluation/main.py output.csv --full
```

Both commands are deterministic; repeated runs on the same `dataset/` produce
byte-identical `output.csv`, which is the reason no per-request token accounting
exists for the submission.
