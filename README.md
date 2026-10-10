# omp-jevify

An [Oh My Pi](https://github.com/can1357/oh-my-pi) plugin that ships one skill, `jevify`:
method-first suitability guidance and optional rubric-based classification in the eval
kernel with `judge_batch` (Jev).

Choose the method before judging:

- Prefer a compiler, search, AST rule or short script when a deterministic rule decides
  the answer.
- Prefer direct reading for small jobs or tightly cross-dependent reasoning.
- Consider Jev for repeated, independently judgeable semantic decisions with sufficient
  context, such as whether changed hunks match a stated contract or review findings
  match the cited code.

There is no item-count mandate. Loading the skill, repeating a task or explicitly asking
for Jev does not make judgment the right method. When choosing Jev, define and pilot the
rubric, freeze it, then cover the declared scope. Read flagged sources and resolve errors,
unknowns and coverage gaps before claiming closure. Calibrate before bulk or destructive
action; read-only calibration is optional. Scores and model agreement are not proof or
permission to edit. It generalises Can Bölük's Jevict
([thread](https://x.com/_can1357/status/2102524777776677104)) beyond test pruning.

## Install

```sh
omp plugin install 'git+https://github.com/ericjuta/omp-jevify.git#v0.2.0'
```

The install command pins the published tag. The helper and eval contracts below
describe this checked-out source; local changes and the separate host companion
patch are not made available by reinstalling that tag.

Start a new OMP session afterwards:

- Ask for the kind of job it is for ("go through every changed hunk in this commit and tell
  me which aren't explained by the message", "check every call site of X…") and the model
  picks the skill from its description.
- Or include the word `jevify` in the prompt. OMP's built-in `jevify` magic keyword adds
  its own instructions for that turn, and the skill is picked up alongside.
- Or type `/skill:jevify` to inject it directly.

Stock OMP 18.8.7 still injects an item-count-based mandatory `jevify` keyword notice
that conflicts with this skill's method-first guidance. Installing the plugin alone
does not change that host notice. The separate, unreleased local companion host patch
targets method-first guidance: Jev stays optional, there is no size requirement, and
context gathering is allowed before choosing the method.

The model loads the helpers itself; by hand, in the Python eval kernel:

```python
%load skill://jevify/jevify.py
```

This defines `jv` (extraction from diffs, `rg` and ast-grep; batch handling;
Jev-token state sizing; error, fallback and unknown-provenance flags; slow-model
calibration; dry-run-by-default span deletion).
See [`skills/jevify/SKILL.md`](skills/jevify/SKILL.md) for suitability and workflow;
read [`skills/jevify/reference.md`](skills/jevify/reference.md) on demand for helper APIs
and recipes.

### Provenance is not inferred

Stock OMP 18.8.7 exposes only `key`, `answers`, `error` and `model` on judgment items.
It does not expose selected/response model, fallback or cache-hit provenance. The helper
marks missing provenance `provenance_unknown`; a model label or a change in that label
does not establish fallback or a cache hit. Treat unknown provenance as unresolved,
not as evidence that the primary model answered.

## Requirements

- OMP with the eval kernel's `judge_batch`, `judge` and `completion`.
- `omp toks` (omp ≥ 18.3.0) for sizing large states in Jev tokens; without it `jv.states`
  warns that they are unmeasured.
- `rg` and `git` for the diff and search extractors.
- `uv` (or pip) for `jv.ast_units`, which installs `ast-grep-py` on first use into
  `${XDG_CACHE_HOME:-~/.cache}/jevify/` (the kernel's Python may have no pip).

## Development and evals

Local tests: `python3 -m unittest discover -s tests`.

`evals/run.py` runs bounded OMP sessions in generated fixture repositories. Skill
availability and requested method are separate controls:

- `--arm skill` (default) pins the tested skill bundle; `--arm none` hides jevify.
- `--strategy auto` (default) leaves the case goal unchanged. `direct-script`,
  `jev-flat` and `jev-grouped` append method instructions, not expected answers.
- `--ref` tests a Git revision's skill directory; `--skill-dir` tests another complete
  bundle. With neither, the runner uses the working-tree skill.
- `--omp` selects the executable; `--omp-source` records a supplied source checkout's
  identity separately. `--model` and `--thinking` select the subject configuration.
- `--reps`, `--jobs`, `--max-time`, `--max-calls` and `--retries` bound execution.

Model runs require model access and spend real tokens. Opening the skill is activation
evidence, not evidence that classification was suitable or that an answer was correct.
Explicit strategies measure requested-method adherence separately from answer quality.

## Evaluation evidence and cache controls

Execution results use schema 4, distinct from grading and answer-key schemas. They
record skill/source, cases and fixture identities; requested configuration; `runs`;
`summary`; and a `cost_ledger`. Runs retain `attempt_records`, including failed or
retried attempts, captured answers, execution status and strategy adherence. Completed
runs are appended to `<out>.partial.jsonl`; raw artifacts are kept beside the output
unless `--raw-dir` selects another location.

The runner starts each attempt with a private `--session-dir` and reads persisted
session JSONL `type: model_usage` entries after the process settles. It does not depend
on a `model_usage_summary` print event. The retained ledger is an accounting-only
projection; the transient full session is deleted after collection. Outer assistant
usage and auxiliary usage remain separate. `snapshot_statuses` and `collection_status`
describe collection completeness, not overall billing coverage. Stock OMP 18.8.7 does
not journal eval `completion` calls in this ledger. Even a complete file containing
judge rows cannot establish zero completion cost or a complete execution total.
Completion, auxiliary and total buckets retain `coverage_unknown: true`,
`cost_unknown: true` and null `reported_cost`, including after attempts are merged.
`reported_subtotal` and token sums count only observed rows; a zero subtotal is not
proof of free execution, and unknown coverage leaves `tokens_complete` false.
Observed costs remain estimates unless attested otherwise. Unknown costs remain
unknown, not zero; execution accounting does not include grading overhead.

`judged` can be unknown. `auto_judged` uses only known observations in its denominator;
`auto_judged_unverified` reports the excluded unknowns. Fully cached judging may produce
no billed usage rows, so an empty ledger does not prove that no judgment occurred.
Helper receipts and source markers are not host-attested execution evidence. These
limits also constrain strategy-adherence claims.

### Cache controls

The host's default judgment cache is shared, not session-local. The runner provides:

- `--cache-mode cold` (default): a fresh, independent `OMP_JUDGMENT_CACHE_DB` for each
  attempt, including retries. No seed path is accepted.
- `--cache-mode warm --cache-namespace /path/to/seed.sqlite`: a read-only SQLite backup
  of an existing seed, including committed WAL contents, frozen once per eval invocation.
  Each attempt gets an independent writable clone of that snapshot. The runner records
  its starting fingerprint and does not judge against or update the original seed.
  A seed's existence does not prove cache hits.
- `--cache-mode uncontrolled`: use host cache state without asserting isolation.

For comparisons across invocations, keep the seed stable and compare the recorded seed
fingerprints. Do not hash only the main file of a live WAL database or let arms share a
single growing writable cache.

```sh
python3 evals/run.py --arm skill --strategy auto --cache-mode cold --jobs 1 --out /tmp/cand.json
python3 evals/run.py --arm none --strategy auto --cache-mode cold --jobs 1 --out /tmp/none.json --compare /tmp/cand.json
```

For an explicitly requested flat-method run against an existing seed:

```sh
python3 evals/run.py --arm skill --strategy jev-flat --cache-mode warm --cache-namespace /path/to/seed.sqlite --jobs 1 --out /tmp/warm-flat.json
```

`--compare` prints an informational comparison, not a controlled quality verdict.
Check source, cases, fixtures, model configuration, cache identities, execution status
and unknown evidence before interpreting differences. Execution results do not grade
answer correctness.

### Grading and evidence limits

`evals/grade.py` grades saved answers in OMP's Python eval kernel. Schema-2 answer keys
separate factual truth, action under the public policy and origin; historical Boolean
keys remain separate. Grading outputs use schema 2 and reference a shared grading-cost
ledger, rather than adding grading overhead to each execution arm. Batch costs are
estimates; grading-session outer cost and total billing remain unmeasured/unknown,
even for zero-cost or empty batches. Its cache-mode argument records intent; the
grading session must configure its own cache.

```python
%load evals/grade.py
await grade("/tmp/cand.json", "evals/cases.json")
```

Use the same case file that produced the captured answers. Omissions and extraction
failures remain visible; model-extracted assertions are not independent quality proof.
Recorded artifacts in `evals/results/` describe their captured revisions and conditions,
not current quality guarantees.

`evals/holdout.json` is sealed. Do not read or edit its prompts while tuning the skill
or eval machinery. Once used for tuning, it is no longer a holdout.

## License

MIT
