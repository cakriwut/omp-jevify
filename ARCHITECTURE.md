# Architecture and maintenance

`omp-jevify` is an Oh My Pi (OMP) plugin that ships a single skill, `jevify`. The skill
guides when to use OMP's `judge_batch` classifier (Jev); it does not make Jev mandatory
and does not itself decide whether a classification is correct.

## Architecture

```text
User task
   |
   v
OMP chooses a method
   |-- deterministic rule/compiler/search/script --> direct result
   |-- small or tightly coupled task -------------> direct reading
   `-- repeated independent semantic decisions ---> optional Jev workflow
                                                        |
                         +------------------------------+
                         v
             skills/jevify/SKILL.md
             suitability, pilot/freeze/review guidance
                         |
                         v
             skills/jevify/jevify.py (loaded in OMP's Python eval kernel)
             extract units -> render bounded states -> submit Q to judge_batch
                         |                                  |
                         |                                  v
                         |                       OMP returns answer/error rows
                         v                                  |
             coverage receipt, flags, manual review <-------+
                         |
                         v
             confirm from source; act only with authority; report limits

Separate evaluation and regression path:
evals/cases*.json + evals/fixture.py
        -> evals/run.py builds synthetic repos and runs bounded OMP sessions
        -> execution results / captured answers
        -> evals/grade.py + fixture answer keys
        -> graded metrics (not proof of production behavior)
```

The helper provides source extractors (`git_units`, `rg_units`, `ast_units`,
`line_units`), state rendering/grouping, batch collection, result receipts, flags,
calibration and persistence. `judge_batch`, `judge` and `completion` are provided by the
OMP host, not by this plugin. The checked-in evaluation runner is a separate test
harness; it is not part of the installed skill's runtime.

## Where to make changes

| Change | Maintained in |
| --- | --- |
| Change general method-selection or safety workflow | [`skills/jevify/SKILL.md`](skills/jevify/SKILL.md) |
| Change detailed helper guidance, recipes or API examples | [`skills/jevify/reference.md`](skills/jevify/reference.md) |
| Change helper extraction, batching, receipts or result handling | [`skills/jevify/jevify.py`](skills/jevify/jevify.py) |
| Define criteria for a particular Jev task | The task's `Q` question passed to `jv.run`; use `jv.choice`, `jv.yes` or `jv.scale` as appropriate |
| Change when results are escalated | The caller's rule passed to `jv.flag(rows, rule=...)`; keep action thresholds separate from rubric labels |
| Change an evaluation prompt/case | The relevant `evals/cases*.json` |
| Change synthetic evaluation context or expected answers | [`evals/fixture.py`](evals/fixture.py), or [`evals/refund_retry_fixture.py`](evals/refund_retry_fixture.py) for the refund/retry dataset |
| Change answer extraction or scoring | [`evals/grade.py`](evals/grade.py) |

Task criteria and escalation thresholds are caller policy, not one global plugin
configuration. Define the rubric and action thresholds before examining pilot results.
Pilot representative inputs, inspect flags and near misses, then freeze the questions
with `jv.freeze(Q)`; a wording change changes the question hash and requires rejudging
the affected scope. Do not move thresholds to make observed outcomes look better.
Treat errors, unknown labels, incomplete coverage and unknown model provenance as
unresolved; scores are estimates, not permission to make edits.

When changing an evaluation rubric, update the public rubric and the corresponding
private expected-answer key together. Keep them distinct: the public rubric tells the
subject how to classify, while the answer key encodes the expected truth used by
`evals/grade.py`. Run the existing tests with `python3 -m unittest discover -s tests`.

## Grounded example: refund/retry review

The `refund_retry_review` case in [`evals/cases-outcomes.json`](evals/cases-outcomes.json)
selects the synthetic `refund_retry` dataset. Its public task rubric is
`RUBRIC` in [`evals/refund_retry_fixture.py`](evals/refund_retry_fixture.py): it asks
three separate questions—whether a claim is supported, what action policy warrants,
and whether the behavior was introduced at HEAD. The fixture materializes this rubric
as `review/rubric.json`; expected answers and evidence are maintained separately in
`CLAIMS` in the same fixture.

This separation matters for two similar-looking claims:

* C01 says redelivery after a provider refund ID is persisted issues another create
  under a new idempotency key. Under the fixture policy, the provider deduplicates by
  key and a reachable second settlement blocks release, so its expected facets are
  supported / fix now / introduced.
* C05 says a lost acknowledgement is followed by recovery with one provider
  settlement. The synthetic policy treats a timeout as an unknown outcome, and a
  retry with the same key is deduplicated, so its expected action is no action.

These are source- and policy-grounded **synthetic fixture expectations**, not replayed
production events. If the operating policy changes, revise the public rubric/context
and the affected `CLAIMS` expectations together, then run the tests and regenerate
evaluation results as appropriate. If only the confidence/action threshold changes,
change the caller's escalation rule instead; do not silently redefine the labels.
