# Jevify reference

On-demand detail subordinate to `skill://jevify`. It is not a second workflow and
does not authorize judging before the core's conditional pilot, freeze, manual
confirmation, calibration, and authority checks. Follow the core when they differ.
Scores are not proof, and numeric thresholds below are example policies, not
correctness guarantees.

Optional sections: detailed migration stages · grouping · checked-in helper API ·
dated observations · recipes A–F · write-time rule.

## Optional detailed stages

These stages illustrate one migration sweep after the core decides Jev is appropriate.
They do not require a particular item count, and a small or tightly cross-dependent
set should be read directly. Write `Q` before seeing data. A deterministic compiler,
grep count, ast-grep rule, or test is preferable when it decides the question.

### Load

In the Python kernel, `%load` defines `jv`; `judge_batch`, `completion`, and `wait`
come from the kernel. Re-`%load` resets `jv.frozen` and `jv.stats`. Do not use `%pip`
for this helper: `jv.dep` handles a missing `ast-grep-py`.

```python
%load skill://jevify/jevify.py
```

### Persisting work and recovering host batches

A kernel reset loses Python variables, helper statistics and an interrupted
coroutine. A host-owned `judge_batch` can outlive its submitting cell and kernel;
`judge_batch.attach(batch_id)` recreates a reference to a batch still registered
in that same host session. It is not a durable queue across host shutdowns, and
an ID alone is not the source-to-verdict mapping.

Before submission, persist the source revision/scope, original unit IDs and source
locations, extraction exclusions, bounded states, frozen `Q` and `qhash`, and
the full `grouping` manifest (`original_ids`, `slots`, `excluded`) for grouped
input. Record the **actual host batch ID** immediately after
raw `judge_batch` submission, not an invented run label. Preserve each retry's ID
and planned states separately. Save rows and the completed receipt before deleting
inputs; rubric, extraction and group IDs cannot safely be reconstructed from memory.

For a caller explicitly managing a raw flat batch, after the suitability/pilot
steps and with `states`, `units`, and frozen `Q` already defined:

```python
import json
from pathlib import Path
checkpoint = Path("/tmp/jevify-batch.json")  # unique path per live run
manifest = {"questions": Q, "qhash": jv.qhash(Q), "units": units,
            "states": states, "grouping": None}
checkpoint.write_text(json.dumps(manifest, ensure_ascii=False))
b = judge_batch(states, Q, intent="jevify: recoverable flat sweep")
manifest["batch_id"] = b.id
checkpoint.write_text(json.dumps(manifest, ensure_ascii=False))
```

There is a submission-to-checkpoint failure window; this is not transactional
persistence. After a reset, reload the manifest, then attach rather than blindly
resubmitting the same states:

```python
import json
from pathlib import Path
manifest = json.loads(Path("/tmp/jevify-batch.json").read_text())
b = judge_batch.attach(manifest["batch_id"])
print(b.status())
answers_so_far = b.results()  # key -> answers, not helper rows or route metadata
failures_so_far = b.failed()  # key -> error
```

All references share the host batch's **drain cursor**. Attachment does not rewind
or replay items already drained by the original handle or another consumer.
`results()` and `failed()` recover answer/error maps so far, but not drained item
provenance. Persist each consumed drain item and its metadata as you go; a reset
can otherwise lose that evidence even while the batch itself survives. Use one
drain owner, and reconcile both saved and remaining results against planned IDs.
Do not count an empty drain as proof that no work completed.

Attachment does **not** resume `jv.run`. Its coroutine owns in-memory collection,
retry decisions and decoding; its `finally` cleanup can cancel incomplete work and
close the batch. A completed helper run has already closed its host references.
Recover only batches that still exist, reconcile against saved planned IDs, and
retain unresolved work as incomplete rather than synthesizing a helper receipt.
`jv.save` persists a receipt header and rows, not live handles or a resumable coroutine.

Once terminal results and failures are saved and reconciled, call `b.close()` to
release the host batch. When abandoning active raw work, call `b.cancel()` and then
`b.close()`; do not leave orphan work billed in the background. Do not close a
batch still used by another owner. Keep evidence until handoff; cleanup is not
permission to remove another run's cache, session ledger, or files.

### Rubric

Labels are mutually exclusive. `UNKNOWN` requires investigation rather than an edit.
Fix the example escalation policy before seeing pilot scores; do not move it afterward.

```python
TARGET = "legacyApi"
paths = ["**/*.ts", "**/*.tsx"]
SKIP_PREFIX = "generated/"  # deterministic pre-filter; keep both probes aligned
STATE_CAP = jv.CAP
Q = {"migration": jv.choice(
    "For this call site, decide whether it still depends on legacyApi's old behavior. "
    "A renamed alias or wrapper is not a successful migration; missing context is UNKNOWN.",
    NEEDS_CHANGE="The old behavior or obsolete contract still reaches a consumer.",
    SAFE="The call is already migrated or is unrelated despite its name.",
    UNKNOWN="The available unit cannot prove either conclusion.")}
ACTION_AT = 0.7  # example policy, not a correctness guarantee
def need(r):
    top = max(r.get(f"migration.{k}", 0) for k in ("NEEDS_CHANGE", "SAFE", "UNKNOWN"))
    if top < ACTION_AT or r.get("migration") == "UNKNOWN": return "uncertain"
    return "obsolete call" if r.get("migration.NEEDS_CHANGE", 0) >= ACTION_AT else False
```

### Extract and spot-check

Use `git_units` for diffs, `rg_units` for hits, `ast_units` for syntax, or
`line_units` for signatures. Compare naive heads with parser units in the same paths
and inspect unmapped comments or strings versus missed calls. Equal totals prove nothing.

```python
raw = jv.ast_units(f"{TARGET}($$$ARGS)", lang="typescript", globs=paths)
units = {k: u for k, u in raw.items() if not u["file"].startswith(SKIP_PREFIX)}
heads = {k: h for k, h in jv.rg_units(TARGET, fixed=True, globs=paths).items()
         if not h["file"].startswith(SKIP_PREFIX)}
print(f"pre-filter removed={len(raw)-len(units)}")
uncovered = [(h["file"], h["line"]) for h in heads.values() if not any(
    u["file"] == h["file"] and u["line"] <= h["line"] <= u["end_line"]
    for u in units.values())]
print(f"naive={len(heads)} mapped={len(heads)-len(uncovered)} units={len(units)}", uncovered[:20])
states = jv.states(units, cap=STATE_CAP)
print(f"extracted={len(units)} states={len(states)}", jv.stats["states"])
jv.show(list(states)[:5], states, width=1200, limit=5)
```

One empty state can reject a batch. `jv.states` caps strings, drops empties, and
records states whose serialized form is large enough to measure. Use `render`, or
`jv.hunk_states` for diffs, when call text lacks the needed sibling context.

### Pilot, then freeze

Pilot a representative subset and read every flag and near miss. Name each misread
idiom—helper, span, near miss, allow-list, transition, or duplicate—and repair `Q`'s
wording or extraction, never the numeric policy. Rejudge that same pilot. A few rounds
are normal; repeated failure means the context is insufficient. Freeze only when
satisfied, then judge the complete independently judgeable set.

```python
pilot = jv.sample(states, n=min(100, len(states)))
prows = await jv.run(pilot, Q, intent="jevify pilot: migration calls")
pflags = jv.flag(prows, need)
jv.show(pflags, pilot, fields=["migration", "migration.NEEDS_CHANGE"],
        width=STATE_CAP, limit=len(pflags))
jv.show([r for r in prows if .5 <= r.get("migration.NEEDS_CHANGE", 0) < ACTION_AT], pilot, limit=20)
seal = jv.freeze(Q)
rows = await jv.run(states, Q, intent="jevify: complete migration sweep",
                    receipt_path="/tmp/jevify-migration-receipt.json")
jv.save(rows, "/tmp/jevify-migration-rows.jsonl")
print(jv.report(rows, Q, title="Migration call-site sweep"))
```

The helper drains in bounded slices, retries eligible failures once, and retains
failures as error rows. It does not retry a `max_tokens_exceeded` failure. Any
post-freeze wording change invalidates the verdicts: freeze again and rejudge all.

### Confirm, calibrate, and act only with authority

Read every flagged source in full, including semantic uncertainty, explicit
fallbacks, and unknown provenance. `jv.show` can truncate. Do not edit from an error
row or infer routing from model labels; rerun or read it. Review authority is not
edit or deletion authority. Calibrate before bulk or destructive action, then give
editors exact confirmed IDs grouped by file. They edit; they do not scan.

```python
assert set(states) == set(units) and not uncovered, "Reconcile extraction coverage first"
assert rows.receipt["complete"], rows.receipt
assert not jv.flag(rows), "Resolve coverage, failures, fallback routes, and unknown provenance first"
cal = jv.calibrate(rows, states, Q, qid="migration", label="NEEDS_CHANGE",
                   agree={"NEEDS_CHANGE"}, per_bin=20,
                   rubric="Migration requires consumer-equivalent behavior, not a rename.")
flags = jv.flag(rows, need)
candidates = {}
for r in flags:
    if r["why"] == ["obsolete call"]:
        candidates.setdefault(units[r["id"]]["file"], []).append(r["id"])
print("Review before assigning:", candidates)
```

`jv.calibrate` samples up to 20 slow opinions per probability bin. In this example,
agreement should rise toward the top bin and reach at least 80% in `[0.7, 0.9)`.
Those figures are policy targets, not guarantees. Flat bins suggest broken context;
weak top-bin agreement means return to the pilot and rejudge all. Empty bins are
forward-filled estimates. Calibration is optional for a read-only report and grants
no file authority. Deletion additionally requires explicit authority and a
`jv.delete_spans(..., dry_run=True)` inspection. Preserve dirty work, and use LSP
references before an exported-symbol refactor.

### Re-extract with the same frozen Q

After authorized edits, re-extract, remap naive heads, and rejudge with byte-identical
`Q`. Confirm `qhash` equality. Closure requires no remaining flags, errors,
fallback routes, unknown provenance, semantic unknowns, or uncovered executable hits.
Report counts before reviewed IDs, distinguishing judge coverage from manual source
review and noting any pilot or calibration actually performed. Zero extracted units
is an extraction result, not a model verdict.

```python
again_raw = jv.ast_units(f"{TARGET}($$$ARGS)", lang="typescript", globs=paths)
again = {k: u for k, u in again_raw.items() if not u["file"].startswith(SKIP_PREFIX)}
heads_after = {k: h for k, h in jv.rg_units(TARGET, fixed=True, globs=paths).items()
               if not h["file"].startswith(SKIP_PREFIX)}
uncovered_after = [(h["file"], h["line"]) for h in heads_after.values() if not any(
    u["file"] == h["file"] and u["line"] <= h["line"] <= u["end_line"]
    for u in again.values())]
now = jv.states(again)
post = await jv.run(now, Q, intent="jevify: after action",
                    receipt_path="/tmp/jevify-after-receipt.json")
jv.save(post, "/tmp/jevify-after-rows.jsonl")
assert post.receipt["qhash"] == seal
assert post.receipt["complete"], post.receipt
remaining = jv.flag(post, need)
covered = set(now) == set(again) and not uncovered_after
print(jv.report(post, Q, title="After action"),
      f"source coverage={covered}; remaining flags={len(remaining)}")
```

## Grouping

`jv.group` can batch independently judgeable units that share necessary context. It
is optional, not a mandatory optimization. Keep each group below `jv.MAX_JEV`; if a
shared item fails, the helper marks every slot failed. Pilot and calibrate grouped rows as a separate
instrument, and never mix them with flat rows in one decision.

```python
gstates, grouping = jv.group(units, by=lambda u: u["file"], k=8,
                             render=lambda u: u["text"], shared=lambda file: {"file": file})
print("Excluded originals:", grouping["excluded"])
grows = await jv.run(gstates, Q, intent="jevify: grouped call sites", grouping=grouping,
                     receipt_path="/tmp/jevify-grouped-receipt.json")
jv.save(grows, "/tmp/jevify-grouped-rows.jsonl")
assert grows.receipt["complete"], grows.receipt
gflags = jv.flag(grows, need)
```

Grouped rows use **original unit IDs**, whereas `gstates` uses **group IDs**.
Pass the same `gstates` used by `run` directly to `calibrate`. The helper resolves
each row's group and slot, retains the full bounded shared/sibling context, and
instructs the slow model to judge only that slot. Missing state or invalid slot
pairings raise before any slow opinions are requested; do not build unit-ID wrappers.

```python
assert grows.receipt["complete"], grows.receipt
assert not jv.flag(grows), "Resolve coverage, failures, fallback routes, and unknown provenance first"
gcal = jv.calibrate(
    grows, gstates, Q, qid="migration", label="NEEDS_CHANGE",
    agree={"NEEDS_CHANGE"}, per_bin=20,
    rubric="Migration requires consumer-equivalent behavior, not a rename.")
```

The bound receipt and base-flag guards apply before calibration or action, even if
semantic flags are expected. On stock 18.8.7 the provenance guard can block; do not
bypass it by inventing metadata. Calibration does not repair dropped originals or
grant editing authority.

Grouped/flat agreement is not a quality or cost comparison; preserve separate
pilot and calibration evidence for each representation.

## Checked-in helper API

This describes the public surface of the checked-in `skills/jevify/jevify.py`. It is
separate from upstream `judge_batch`, `completion`, notice, token, and provenance
facts. `jv` owns the public names. Units are ordered `{id: unit}`; identifier
collisions gain `#2`, `#3`, and so on.

| Member / signature | Contract |
| --- | --- |
| `jv.CAP = 12_000` | Default maximum characters per string field. |
| `jv.MAX_JEV = 32_000` | Helper token ceiling used to warn before judging. |
| `jv.SEED = 11` | Default sampling seed. |
| `jv.EDGES = (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.01)` | Default left-closed numeric bins. |
| `jv.stats = {"states": {...}, "runs": [...]}` | Latest state summary, replaced on each build, and appended run summaries. |
| `jv.frozen = None \| qhash` | Hash from the latest `jv.freeze` call. |
| `jv.choice(instructions, criteria=None, **labels) -> dict` | Choice question; at least two labels, with no duplicates. |
| `jv.yes(instructions, *, true=None, false=None) -> dict` | Bool question; criteria included only when supplied. |
| `jv.scale(instructions, *levels) -> dict` | Score question; at least two levels, low to high. |
| `jv.qhash(Q) -> str` | First 12 hex characters of SHA-256 over sorted-key JSON. |
| `jv.freeze(Q, path="/tmp/jevify-questions.json") -> str` | Write sorted JSON, set `jv.frozen`, and return its hash. |
| `jv.git_units(rev, *, by="file", cwd=".", paths=None, context=3)` | `by` accepts `file` or `hunk`; `WORKTREE`, `STAGED`, `A..B`/`A...B`, or one commit versus first parent. |
| `jv.rg_units(pattern, *, cwd=".", globs=None, context=3, fixed=False, case=True, max_units=50_000)` | `rg --json -n -C`; no matches returns `{}`; over cap raises. |
| `jv.ast_units(pattern=None, *, lang, rule=None, cwd=".", globs=None)` | Exactly one pattern or rule dict passed to ast-grep; may install `ast-grep-py` through `jv.dep`. |
| `jv.dep(dist, module=None) -> module` | Import, or install with `uv` and fall back to `python -m pip`. |
| `jv.line_units(source, *, keep=None, drop=None, normalize=True, examples=3)` | Path, text, or lines grouped into normalized signatures. |
| `jv.states(units, render=None, *, cap=None) -> {id: state}` | Render and recursively cap strings; drop empty; measure only states over `MAX_JEV` UTF-8 bytes. |
| `jv.jev_tokens(text, *, timeout=20) -> int \| None` | One `omp toks --json` count, or `None` on any failure. |
| `jv.hunk_states(units, *, siblings=6, users=3, width=700, cap=None)` | Hunk plus nearest same-file hunks and selected name users. |
| `jv.group(units, *, by, k=8, render=None, shared=None, cap=None) -> (states, grouping)` | Shared states, at most `k` units each, and full original/slot/exclusion manifest. |
| `jv.sample(states, n=60, *, seed=None) -> dict` | Seeded subset of sorted IDs. |
| `await jv.run(states, Q, *, intent, concurrency=32, retries=1, timeout=1800, grouping=None, receipt_path=None) -> rows` | List-compatible rows bound to `rows.receipt`, including empty runs. Submit, drain, retry eligible failures once, flatten item attributes, and optionally write the receipt JSON to `receipt_path`. |
| `jv.slot_questions(Q, k) -> dict` | Copy questions to `qid@sN`; absent slots are discarded by `jv.run`. |
| `jv.flag(rows, rule=None) -> list[row]` | Copy errors, incomplete coverage, explicit fallback routes, unknown provenance, and truthy rule matches with `why`; empty rows without a complete bound receipt raise. Error rows skip the rule. |
| `jv.show(rows_or_ids, states, *, fields=None, width=1200, limit=30)` | Print selected fields and truncated state text. |
| `jv.tally(rows, key, *, edges=None) -> dict` | Count labels or bin numbers, excluding error rows. |
| `jv.calibrate(rows, states, Q, *, qid, label, agree=None, per_bin=20, edges=None, model="slow", rubric="", seed=None) -> dict` | Slow-choice agreement and threshold estimates using the same states passed to `run`; grouped rows select their slot while retaining group context. Invalid pairings raise before slow calls. |
| `jv.save(rows, path="/tmp/jevify-rows.jsonl") -> path` | Write a `{"jevify_receipt": ...}` header followed by JSONL rows, preserving empty-run evidence. |
| `jv.load(path) -> rows` | Read that header and return list-compatible rows with their receipt; headerless legacy JSONL is rejected. |
| `jv.delete_spans(units, ids, *, root=".", dry_run=True) -> {file: lines_removed}` | Remove merged inclusive spans bottom-up, plus one trailing blank line. |
| `jv.report(rows, Q, *, title="jevify") -> str` | Markdown counts, hashes, labels, state summary, and the rows' own bound run receipt; never substitute the latest run. |

`git_units` disables color and external diff, enables rename detection, and places
paths after `--`. A file unit is `{file,status,diff}`. A hunk ID is
`path:+NEW_START`, or `path:-OLD_START` when the new span is empty; its unit is
`{file,status,header,hunk,line,old_line}`. Binary, rename, or mode-only changes
without hunks use `path:meta`.

`rg_units` passes an explicit `.` and closed stdin. Its unit is
`{file,line,match,context}`. `ast_units` lists files with `rg --files`, parses `.tsx`
and `.jsx` with their specific languages, and still searches files containing parse
errors. Units are `{file,line,end_line,text}` with 1-based inclusive lines. The
helper does not validate rule keys or prove decorator and nested-definition
behavior; recipe F uses an explicit rule when a deletable span must include a
decorator. A pattern-only definition match is not a helper guarantee.

`hunk_states` returns capped states. Before capping, each value is
`{file,status,hunk,nearest_sibling_hunks,hunks_using_names_defined_here}`; sibling
and user entries are brief strings, not hunk objects. Its name-user selection is a
local syntactic heuristic, not semantic proof. `line_units` normalizes UUIDs, long
hex, timestamps, and numbers when enabled. Its ID is `sha1(signature)[:10]`, and
each unit is `{signature,count,examples}`, most frequent first. Examples retain the
original unnormalized lines.

`jv.states` replaces `jv.stats["states"]`; `group` and `hunk_states` therefore leave
the statistics of their latest internal build. Fields are `input`, `built`,
`dropped`, `truncated`, `cap`, `measured`, `oversize`, and `unmeasured`. The last
two are identifier lists, while `report` prints their lengths. It measures only
serialized states larger than `MAX_JEV` bytes, using at most eight threads. This
byte screen and ceiling are helper policy, not an upstream token or failure theorem.
Empty strings, `None`, and empty containers are dropped; zero and `False` remain.

`group` returns `grouping = {"original_ids": [...], "slots": {group_id: [unit_id, ...]},
"excluded": {unit_id: reason}}`. Every original occurs exactly once in either a
slot or the exclusions map. Empty renders are retained as `"empty rendered unit"`
exclusions; a removed bounded group uses `"empty group state"`. Pass the whole
manifest as `grouping=grouping` to `run`; the old `slots=` parameter is removed.
Validation requires the manifest's groups and `s0..sN` slots to match the submitted
states, unique string original IDs, and nonempty exclusion reasons. Group
statistics describe built host states, not original coverage; use the manifest
and receipt for original counts. Any exclusion makes `complete=False`, including
an all-dropped input. Exclusions must be resolved or explicitly reported outside
a clean model-coverage claim, not silently treated as safe labels.

Flat `states` still returns only surviving IDs. Its receipt covers those submitted
states, not unseen extraction inputs; persist and reconcile the source/extraction
manifest separately. Truncation, oversize and unmeasured inputs likewise need
source/context review rather than a receipt-only completeness claim.

| Flat `jv.run` row field | Meaning |
| --- | --- |
| `id: str` | Original unit ID for this extraction. |
| `model: str \| None` | Per-item compatibility model identity; not a route or fallback test. |
| `selected_model: str \| None` | Provider-qualified catalog candidate selected for this request, including cache lookup; not a reconstructed raw origin. |
| `primary_model: str \| None` | First resolved candidate, captured before cooldown and credential/request checks. |
| `fallback: bool \| None` | Explicit route comparison: `selected_model != primary_model` when both are known. |
| `response_model: str \| None` | Raw provider-reported model, without an added provider prefix; present only when all contributing answer origins are known and agree, even on a known route. |
| `cache: str \| None` | Item cache classification: `hit`, `partial`, or `miss`; absent metadata remains unknown. |
| `questions: int \| None` | Number of questions requested for the host item, not expanded unit count. |
| `cached_questions: int \| None` | Questions answered from cache for that host item. |
| `provenance_unknown: bool` | Helper validation of missing or contradictory route/cache metadata; independent of semantic confidence. |
| `batch_id: str \| None` | Actual host batch associated with the selected attempt, or `None` if unavailable. |
| `qhash: str` | Current 12-character question hash. |
| `coverage_complete: bool` | Bound run's final completeness, copied onto every returned row. |
| `coverage_errors: list[str]` | Host-key/batch-identity coverage anomalies for that run. |
| `error: str` | Present only on failure; never convert it into a safe label. |
| `group: str` | Group-state ID, present only on a grouped row. |
| `slot: str` | Original unit's `sN` slot, present only on a grouped row. |
| `row[q]` for choice `q` | Chosen label. |
| `row[f"{q}.{label}"]` | Probability copied for every choice label, missing values as `0.0`. |
| `row[f"{q}.conf"]` for choice | Choice confidence. |
| `row[q]` for bool `q` | P(yes), a float rather than a Python verdict. |
| `row[q]` for score `q` | Probability-weighted level index, not a 0–1 probability. |
| `row[f"{q}.conf"]` for score | Score confidence. |

Grouped failure handling is all-or-slot: if the shared item fails or has an invalid
required answer, every expanded unit row carries that error. Every slot inherits
the host item's route, cache, and question counts; do not sum those duplicated row
counts as independent requests.

### Route and cache provenance

**Capability boundary (2026-10-09):** stock OMP **18.8.7** (`f261ed9faf16b61880b544f599876bface4ded0d`)
exposes `JudgmentItem` key, answers, error, and model, but not the route/cache/count
fields below. The checked-in helper reads those proposed attributes defensively;
on stock, missing metadata remains `None` and successful rows are flagged
`provenance_unknown`. Model aliases, frequencies, zero batch cost, or internal host
telemetry do not fill that gap. The metadata-rich contract below belongs to the
**local upstream companion patch** on `fix/jevify-current-surface` in
`../oh-my-pi.jevify-current`, not a released, published, installed or deployed OMP
capability. Legacy cache origins may still be unknown with that patch. Source
contracts are not live-host proof. Direct `await judge(...)` remains answers-only;
these fields belong to batch drain items, with no `metadata()` method.

`selected_model` identifies the routing candidate, while `response_model` is the
raw response identity. An alias candidate can return a concrete model name without
being fallback. Missing raw `response_model` alone does not mean fallback or
`provenance_unknown`; neither `model` labels nor their frequencies decide routing.
`fallback=True` must escalate even when every row shares that same fallback route.

For native question-cache reuse, distinguish these cases:

- **Fresh:** `cache="miss"`, `cached_questions=0`; a provider request answers the
  questions and supplies raw `response_model` only if the payload contains it.
- **Warm:** `cache="hit"`, `cached_questions=questions`; no provider request and
  zero new usage. The display `model` may be the selected alias while raw identity
  comes from explicit cached response evidence. That difference is not fallback.
- **Mixed batch:** some items are hits and others misses; this does not make any
  individual item partial and does not imply different routing or fallback.
- **Partial item:** `0 < cached_questions < questions`; only missing questions
  are requested. Answers merge cached and fresh results. `response_model` is
  exposed only when **all cached and fresh contributing answer origins** are
  explicitly known and agree. Any missing or conflicting origin leaves it `None`;
  it must not describe only the fresh subset or borrow an identity for unknowns.

A full hit recovers raw identity only when all contributing cached answers have
explicit, nonempty, agreeing `response_model` evidence. Legacy, missing, or
conflicting evidence leaves `response_model=None`; historical display
model strings are not upgraded into raw provenance. Thus a known route/cache can
coexist with an unknown original raw model. Conversely, missing route/count fields
on an older host escalate even if a display model happens to look familiar.

For non-error rows, `jv.flag` adds `provenance_unknown` when selected or primary
identity is absent/empty, `fallback` is not a bool, or it contradicts the selected
versus primary comparison. It also requires `cache` and integer question counts
consistent with `hit` (all cached), `miss` (none cached), or `partial` (some but not
all cached), with at least one question. An already true `provenance_unknown` is
retained. It independently adds `fallback:<selected_model>` when `fallback is True`.
Error rows get only `error` and skip coverage/provenance/rule checks. Non-error
rows additionally escalate incomplete coverage, as described below. A string rule result
labels `why`; other truthy results become `"rule"`; falsy results do not flag.

Semantic uncertainty is separate: valid routing does not prove a verdict correct,
and a confident verdict does not repair missing provenance. Keep uncertain labels
or probabilities in the rubric's rule. A cache hit is reuse, not evidence that the
answer used the current primary candidate; a cache miss is not proof of fallback.

### Completed-run receipts and cost limits

Each completed `jv.run` binds its structured summary to the returned list-compatible
`rows.receipt`, appends it to `jv.stats["runs"]`, prints `{"jevify_receipt": summary}`,
and, if requested, writes **the summary itself** as JSON to `receipt_path` (not JSONL
rows or the printed wrapper). `jv.save` separately writes a receipt header followed
by rows; `jv.load` restores the binding even for an empty run. Headerless old row
files are not accepted. The receipt is built from actual submissions and returned items,
including failed or incomplete attempts; it is not a declaration that all work
succeeded. Validation errors before execution do not produce a run receipt.

The schema-2 summary records `strategy` (`jev-flat` or `jev-grouped`), `intent`,
`units`, `ok`, `errors`, `models`, `elapsed`, `qhash`, `question_hash`, and
`frozen_question_hash`. It includes actual `batch_ids` and `batches`: each attempt
records `batch_id`, host-state `planned_ids`, `answered_ids`, `error_ids`,
`coverage_errors`, and `cost_estimate`. Retry batches retain their own evidence;
final rows and their `batch_id` use the retry attempt for retried states.

Each `batches[].items` entry captures a delivered planned host key's `id`,
`batch_id`, `selected_model`, `primary_model`, `response_model`, `fallback`,
`cache`, `questions`, and `cached_questions`. Missing attributes remain `null`.
Grouped items occur once per host group per attempt, not once per expanded unit;
retry items stay in their own batches. Missing deliveries are not fabricated.
Unexpected keys and duplicate deliveries remain coverage errors; unexpected items
are excluded and the first planned-key delivery is preserved. This is helper-captured
host-item evidence printed by model-controlled code, **not independent trusted host
attestation**. Persist it with the receipt; an answer-only recovery cannot recreate
lost drain metadata.

Top-level `unit_ids`, `answered_ids`, `error_ids`, `error_details`, `group_sizes`,
and `group_slots` describe final expanded submitted units. `original_ids`,
`excluded_ids`, and `exclusions` retain grouped originals and drop reasons.
`coverage_errors` includes unexpected/duplicate host keys or a missing host batch
ID. `complete` means no final errors, coverage errors, **or exclusions**; it does
not mean semantic correctness, complete provenance, or independent extraction
coverage. `provenance_complete` checks final rows separately. Truly empty input
has no host batches or judge verdict, even when its receipt is complete; an
all-dropped grouped input is incomplete.

Use `rows.receipt["complete"]` and `jv.flag(rows, rule)` together before closure
or action. A later run must not supply an earlier run's receipt. `jv.flag` adds
`coverage_incomplete` to non-error rows if their bound receipt is incomplete or
`coverage_complete` is not true. Empty rows with no complete bound receipt raise
instead of quietly returning an empty flag list. Errors remain errors; rule checks
cannot turn them safe. Plain list conversion or slicing discards the receipt
binding, so retain the original returned collection (and persisted header) for
run-level claims; `jv.report` reports unavailable coverage rather than borrowing
`jv.stats["runs"][-1]`. The receipt is not a tamper-proof attestation.

`cache_counts` (`hit`, `partial`, `miss`, `unknown`), `questions`,
`cached_questions`, and `question_counts_unknown` count final **host items**, not
expanded slots or all retry attempts. Recognized integer counts are aggregated;
missing/invalid counts increment `question_counts_unknown`, not an invented zero
origin. `models`, `selected_models`, and `primary_models` count final expanded rows
with non-null identities, including error rows. Provenance validation remains
stricter than aggregate count inclusion.

Receipt `cost` sums the observed status costs of initial and helper-retry batches,
and `cost_kind` is `batch_estimate`. Displayed costs and `jv.report` are estimates,
not billing or end-to-end task totals. Stock 18.8.7 persists auxiliary accounting
in session JSONL entries with
`type: "model_usage"`, not a print-mode `model_usage_summary` event. These entries
carry identity, purpose, optional role, API/provider/model, usage, stop reason and
optional error message. Stock 18.8.7 does not journal eval `completion` calls in
this ledger: a completely collected file with judge rows cannot prove completion
cost is zero or overall billing is complete. Fully cached judgments can also skip
billed usage entries; absence of ledger rows does not prove no judging. Preserve
unavailable cost and coverage as unknown. Deduplicate by entry `id` within its
invocation, not across retries; outer assistant usage stays on transcript messages
and grading overhead stays separate and unmeasured at the session-total level.

The current companion metadata patch does not establish a custom usage-summary
dependency. The eval runner reads the stock persisted ledger after settlement;
helper receipts remain model-printable batch estimates, not independent host
accounting. Its `snapshot_statuses` and `collection_status` describe collection,
not billing completeness. Completion, auxiliary and total buckets keep unknown
coverage and null `reported_cost` through attempt merges; `reported_subtotal` and
token sums contain only observed rows, and `tokens_complete` is false when coverage
is unknown. Keep invocation accounting separate from successful-row coverage and
from provider billing. Do not invent unrecorded attempts or fill unknown costs
with zero. See [README evaluation controls](../../README.md#evaluation-evidence-and-cache-controls)
for the runner's session retention, ledger and cache isolation contract.

`jv.calibrate` returns `{bins,thresholds,verdicts}`. Bin entries contain `lo`, `hi`,
`n`, `calibrated`, `agreed`, `errors`, and `agreement` (`None` when no valid slow
verdict exists). Threshold entries contain `threshold`, `flagged`, `expected_true`,
and `expected_false` for the fixed example policies 0.9, 0.8, 0.7, 0.6, and 0.5.
Each verdict has `slow`, `reason`, and `p`; slow failures use `slow: "ERR"` and are
excluded from agreement. The threshold table is an estimate. `jv.delete_spans`
requires inclusive 1-based `{file,line,end_line}`, rejects absolute, escaping, or
symlink paths, prints missing IDs, and defaults to dry-run. Its returned line count
includes at most one immediately following blank line per merged span.

### Host keyword notice and plugin policy

Stock OMP 18.8.7's injected `jevify` keyword notice still mandates Jev for roughly
20 homogeneous items and limits direct inspection. That conflicts with this
plugin's method-first suitability guidance: list length and loading a skill do
not themselves justify judging. Installing or updating this plugin does **not**
replace the host-injected notice.

The separate local upstream companion patch aligns the notice with method-first
selection: Jev is optional, no minimum item count mandates it, necessary source
and context gathering is allowed, and pilots apply to read-only judging too.
Frozen rubrics, complete declared coverage, source confirmation and action
authority still matter. This describes a local source change, not a released or
deployed host feature. The older API-example bug (`wait([judge(...)])`) was fixed
in upstream 18.4.10; the policy conflict survived that API correction.

## Dated observations and historical notes

The retained [2026-09-27 graded artifact](../../evals/results/graded-2026-09-27.json)
records Claude Opus 5.5 with OMP 18.3.5 on the scale-12 fixture. Its
`large-none-cur` / `hunk_audit_large` precision is 0.956, versus 0.523 for
`large-skill-cur`; the later `large-skill-v014` arm records 0.831. These are
historical model-graded observations, not current compatibility or quality proof,
a controlled comparison of this working tree, or a general advantage claim.
Prefer a deterministic method when a rule decides the question.

The 2026-10-09 stock-18.8.7 audit observed two expected typed results in each of a
flat, exact-repeat, and grouped smoke. The repeat reported zero batch cost estimate;
all successful rows had unknown provenance. This establishes only that those
bounded calls worked, not quality, billing, companion-patch support, or deployment.

The following interface notes are historical observations from 2026-09-27 on omp
18.3.4 and typesafe/jev-1.13.0. They are not current API, notice, token, fallback,
or TTSR promises, and do not measure the unreleased patch's runtime or quality.

- On that build, `judge` was a coroutine: `await judge(...)` returned answers, while
  `wait([judge(...)])` raised `TypeError`. A built-in notice example using
  `wait(handles)` failed there. The notice API example was corrected in 18.4.10;
  stock 18.8.7 uses `await judge(...)` for one judgment and `judge_batch` for bulk
  work. This history does not justify a `wait([judge(...)])` compatibility path.
- The observed `judge_batch` callable accepted `concurrency`, `retries`, `min_ok`,
  and `intent`, and exposed batch ID, intent, total, status, drain, result, failure,
  cancel, and close operations. Its status included a `cost` field and one model.
  Choice results contained choice, confidence, and probabilities; bool results
  contained P(yes); score results contained a weighted level index, confidence,
  legend, and probabilities. Do not treat this shape as the current contract.
- On that build, one empty-string state rejected a batch at submission. Token probes
  judged 32,086- and 32,595-token states but failed 33,101- and 34,107-token states.
  Character ratios varied sharply: roughly 0.2 token/character for prose, 0.72 for
  dense text, and 2 for some CJK or emoji text. Oversize failure was per item, with
  `ok=False`, `model=None`, and an error ending in `max_tokens_exceeded`.
- Since omp 18.3.0 on that line, fallback was observed only among native candidates,
  never the chat model. This historical routing observation does not substitute for
  explicit per-item metadata in the patched contract above.
- `omp toks <file> --json` counted one input on that build; several paths were
  concatenated. Jev was selected by `encoding == "Jev"`. Real text took around 0.25
  seconds, while 50k repetitions of one character took around 30 seconds.
- One observed kernel was system Python 3.12 without pip, so `%pip install` failed
  with `No module named pip`. `jv.dep` installed `ast-grep-py` with `uv` in about 1
  second. `rg`, git, and `uv` were present; numpy, matplotlib, and tree-sitter were
  absent. `%load skill://jevify/jevify.py` resolved a skill created during that
  session. Current environments may differ.

## Optional recipes

Recipes A–F only specialize source, question wording, and escalation. They still
require a representative pilot, frozen hash, source confirmation, and calibration
before bulk or destructive action. Labels grant no edit or deletion authority.
Escalate errors, explicit fallback routes, unknown provenance, ambiguous probabilities,
and the named labels; the 0.7 and 0.3–0.7 ranges are example policies, not guarantees.

### A. Diff scope audit

Use `jv.git_units(rev, by="hunk")`, retaining binary and metadata units. Put the full
commit message or PR body—not its title—in the question. Give each declared purpose
an operational label, plus `undeclared` and `mixed`. `jv.hunk_states` can show that
setup, helper, or parametrization hunks serve the purpose asserted by neighboring
tests.

```python
import subprocess
REV = "HEAD"  # or a reviewed range, with MESSAGE taken from the PR body
MESSAGE = subprocess.run(["git", "log", "-1", "--format=%B", REV],
                         capture_output=True, text=True, check=True).stdout
Q = {"purpose": jv.choice(
    "Assign THIS hunk to the declared purpose it serves. Sibling hunks and hunks using names "
    "it defines are context: helpers, fixtures and parametrizations serve the purpose their "
    "tests assert; a change's own proposal/tasks/metadata serve that change.\n"
    f"MESSAGE:\n{MESSAGE}",
    feature="<declared purpose 1, operationally: its code, tests, docs and reporting>",
    validation_repair="<declared repair: formatter blank lines, type-narrowing asserts, mock fixes>",
    undeclared="Serves none of the declared purposes.",
    mixed="Separable changes serving different purposes, including an undeclared one."),
    "behaviour_undeclared": jv.yes(
        "Does this hunk change runtime (non-test) behavior for a reason OTHER than the declared "
        "purposes? A behavior change that implements a declared purpose answers no."),
    "debug_leftover": jv.yes("Does this hunk add debug output, commented-out code or TODOs?")}
units = jv.git_units(REV, by="hunk")
states = jv.hunk_states(units)
```

Escalate `undeclared`, `mixed`, weak top-label probability, either bool at or above
0.5, errors, fallback routes, and unknown provenance. Read every flag. Preserve
unrelated work, repair only authorized scope, and rejudge the same frozen `Q`.

### B. Refactor or migration completeness

For exported symbols, collect LSP references before judging. Include aliases and
wrappers. Parser matches plus a naive count still cannot prove completeness.

```python
Q = {"migration": jv.choice(
    "Classify THIS call against the new API contract; a rename alone is not migration.",
    migrated="New contract used with equivalent consumer-visible behavior.",
    needs_change="Obsolete contract or lost required behavior remains reachable.",
    not_applicable="Lookalike hit not governed by this migration.",
    unsure="Alias, type or surrounding context is insufficient.")}
units = jv.ast_units("oldApi($$$ARGS)", lang="typescript")
```

Escalate `needs_change` at or above 0.7, `unsure`, errors, fallback routes, and
unknown provenance. Read consumers before editing. After authorized edits,
re-extract and rejudge the same frozen `Q`.

### C. Existing diagnostic triage

Parse diagnostics that were already captured. Do not run a compiler solely to create
a sweep. Preserve location and neighboring source; `line_units` discards that
location. This parser is for `tsc`; adapt it for ESLint or Cargo.

```python
import re
from pathlib import Path
Q = {"origin": jv.choice(
    "Classify immediate cause, not a downstream cascade; use source context.",
    real_bug="Changed source has an observable defect needing repair.",
    type_only="Type-check contract fails, without evidence of a runtime failure.",
    stale_or_generated="Diagnostic comes from stale build state or generated output.",
    noise="Non-actionable, duplicate, or source context insufficient to decide."),
    "effort": jv.scale("Estimated repair effort given this source context?",
                       "local", "multi-file", "architecture")}
rx = re.compile(r"^(.*)\((\d+),\d+\): error (TS\d+): (.*)$")
lines = Path("/tmp/diagnostics.log").read_text().splitlines()
def context(p, n):
    f = Path(p)
    if not f.is_file(): return ""
    source = f.read_text(errors="replace").splitlines()
    return "\n".join(source[max(0, int(n)-3):int(n)+2])
units = {f"{p}:{n}:{code}:{i}": {"file": p, "line": int(n), "code": code,
         "message": msg, "context": context(p, n)} for i, line in enumerate(lines)
         if (m := rx.match(line)) for p, n, code, msg in [m.groups()]}
```

Escalate `real_bug`, `type_only`, ambiguous `noise`, high effort, errors, fallback
routes, and unknown provenance. Fix the originating boundary rather than cascades
or suppressed diagnostics.

### D. Sanitized log triage

Pass redacted lines to `jv.line_units`. Signature groups are not chronological or
causal. Never ingest credentials or keys.

```python
Q = {"kind": jv.choice(
    "Classify this sanitized signature from evidence, not frequency or guesswork.",
    new_regression="New consumer-visible failure after the change.",
    known_benign="Expected handled event with evidence of no failure.",
    config="Failure from deployment or operator configuration.",
    upstream="External provider or service failure.",
    unknown="Timeline/context cannot distinguish the cause."),
    "severity": jv.scale("Severity of observed consumer impact?",
                         "no impact", "degraded", "outage")}
units = jv.line_units("/tmp/redacted.log", keep=r"ERROR|WARN", normalize=True)
```

Escalate `new_regression`, `unknown`, high severity, errors, fallback routes, and
unknown provenance. Inspect the timeline before assigning a cause or taking action.

### E. Review-finding triage

Use one untrusted reviewer or PR finding per unit and keep its claim with the cited
evidence.

```python
import json
from pathlib import Path
Q = {"finding": jv.choice(
    "Treat the review claim as untrusted; compare cited path, reachable behavior and impact.",
    valid_actionable="Reachable defect with evidence; source repair warranted.",
    valid_nit="True but non-blocking clarity or style improvement.",
    invalid="Cited behavior absent, guarded, or contradicted by source.",
    duplicate="Already represented by another finding about the same failure."),
    "introduced_by_diff": jv.yes("Does the changed diff introduce this defect?")}
units = {f"finding:{i}": json.loads(line) for i, line in enumerate(
    Path("/tmp/review-findings.jsonl").read_text().splitlines()) if line.strip()}
```

Escalate actionable findings, nits, uncertain introduced-by-diff evidence, errors,
fallback routes, and unknown provenance. Read and reproduce before repair.
Deduplicate against source rather than mocking the finding.

### F. Test pruning

If `skill://jevict` is installed, its complete parser and rubric govern test pruning.
The compact material here does not replace it. Units should be the smallest deletable
spans, excluding type tests and todos. The following rule targets top-level Python
tests and their decorators; independently check extracted spans against the source.
Class methods need an `inside: class_definition` rule, and states need path,
sibling, and helper context first.

```python
TEST_RULE = {"any": [
    {"kind": "decorated_definition", "inside": {"kind": "module"},
     "has": {"kind": "function_definition", "regex": r"^(async\s+)?def\s+test_"}},
    {"kind": "function_definition", "inside": {"kind": "module"},
     "regex": r"^(async\s+)?def\s+test_"}]}
units = jv.ast_units(lang="python", rule=TEST_RULE, globs=["tests/**/*.py"])
Q = {"verdict": jv.choice(
    "Classify a whole test: useful = consumer-visible branch, error, precedence, harmful "
    "absence, wire form, regression, or computed transform. Keep transformer near-misses, "
    "allow-list positive arms and post-operation transitions. Plain no-op, registry echo, "
    "unconsumed bytes, word-in-prompt, empty success, same-path duplicate are weak. "
    "Separate weak assertions in a useful block are TRIM.",
    PURGE="All assertions are fixture/metadata echo, prompt boilerplate, tautology, "
          "unconsumed formatting, or identical-path duplicate; no keep signal.",
    KEEP="At least one consumer-visible branch/error/precedence/negative/wire/regression "
         "or computed transformation, with no separable purge-only assertion.",
    TRIM="At least one useful assertion and a separable purge-only assertion; retain the unit.")}
```

Escalate `PURGE` at or above 0.7, all `TRIM`, errors, fallback routes, and unknown
provenance. Read every purge candidate and calibrate before destructive action.
With explicit deletion authority, dry-run `jv.delete_spans` first and inspect the
exact selected spans, including decorators. Hand-edit `TRIM`, then rejudge the same
frozen `Q`. Assertion helpers count; an absent keyword alone is not deletion grounds.

## Optional write-time rule

Only when a write-time rule is requested, write `<repo>/.omp/rules/test-value.md` or
`~/.omp/agent/rules/test-value.md`: one yes/no question, gated by scope and condition.

```markdown
---
description: Warn when a changed test asserts only incidental implementation details
question: Does this test edit add assertions that only echo fixtures, inspect wiring, or check successful completion without pinning a plausible consumer-visible failure?
scope: "tool:edit(*.test.ts), tool:write(*.test.ts)"
condition: "expect\\(|assert"
---
Read the test: replace incidental assertions with a consumer contract, or remove them; preserve useful assertions.
```

Historical 2026-09-27 omp 18.3.4 observation, not a current TTSR promise: TTSR judged
`{output,content}` after completion, with a 60k-character limit, and did not
interrupt. P(yes) at or above 0.7 delivered the body as a hidden warning, while
failure delivered nothing. Optional globs, regex conditions, or ast-grep conditions
could gate cost. `omp ttsr list` showed questions, while `omp ttsr test/scan` did not
exercise the judge. `/omfg` could draft a rule. A rule does not replace a bulk sweep.
No current TTSR runtime, threshold, or delivery behavior is established by this note.

## Handoff checks

- Preserve the frozen hash across the full run and every after-action run.
- Retain the original-ID/grouping manifest, saved receipt header and each batch ID;
  check bound completeness separately from flags and source coverage. Reattachment
  cannot replay consumed metadata or resume the helper coroutine.
- Separate extraction counts, pilot and calibration counts, errors, fallback routes,
  and unknown provenance from source that was actually reviewed.
- Never turn a score, estimate, or model-label difference into an edit.
- Never stash, reset, commit, or touch unrelated dirty work. Dry-run every deletion
  and apply it only with explicit deletion authority.
