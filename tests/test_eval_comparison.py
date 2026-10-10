"""Boundaries of evals/run.py that change what a comparison reports: which answers are graded,
what execution cost is known, whether a requested method actually ran, and which source ran."""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("comparison_run", ROOT / "evals" / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
helper = {}
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile((ROOT / "skills/jevify/jevify.py").read_text(encoding="utf-8"), "jevify.py", "exec"), helper)
jv = helper["jv"]
CLEAN_ENV = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
CLEAN_ENV.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def _row(entry_id, *, scope="case:1:0", purpose="judge_batch", cost=1.25, unknown=False, cache="miss",
         questions=2, cached=0):
    usage = {"input": 10, "output": 4, "cacheRead": 3, "cacheWrite": 1}
    if cost is not None:
        usage["cost"] = {"total": cost}
    raw = {"id": entry_id, "type": "model_usage", "purpose": purpose, "usage": usage, "cache": cache,
           "questions": questions, "cachedQuestions": cached, "costUnknown": unknown,
           "selectedModel": "openrouter/typesafe/jev-1", "primaryModel": "openrouter/typesafe/jev-1"}
    return run.ledger_row(raw, "auxiliary", scope)


class AccountingTests(unittest.TestCase):
    def test_real_judge_purposes_prove_judging_and_share_the_judgment_cost_bucket(self):
        for purpose in ("judge", "judge_batch"):
            with self.subTest(purpose=purpose):
                row = _row("j1", purpose=purpose)
                accounting = run.finalize_accounting([row], [], ["complete"])
                method = run.method_evidence([], [], accounting, {"planned_ids": None, "rubric_hash": None}, "auto")
                self.assertEqual(row["purpose"], purpose)
                self.assertEqual(row["bucket"], "judgment")
                self.assertEqual(accounting["totals"]["judgment"]["reported_cost"], 1.25)
                self.assertEqual(accounting["totals"]["other"]["reported_cost"], 0)
                self.assertIsNone(accounting["totals"]["total"]["reported_cost"])
                self.assertEqual(accounting["totals"]["total"]["reported_subtotal"], 1.25)
                self.assertIs(method["judged"], True)
                self.assertEqual(method["judged_evidence_class"], "persisted_model_usage")
                self.assertEqual(method["task_judgment_ledger_rows"], 1)
                self.assertEqual(method["runtime_auxiliary_ledger_rows"], 0)

    def test_runtime_and_unsupported_judgment_purposes_are_other_not_judging(self):
        for purpose in ("auto-thinking", "title", "judgment"):
            with self.subTest(purpose=purpose):
                row = _row("r1", purpose=purpose)
                accounting = run.finalize_accounting([row], [], ["complete"])
                method = run.method_evidence([], [], accounting, {"planned_ids": None, "rubric_hash": None},
                                             "direct-script")
                self.assertEqual(row["bucket"], "other")
                self.assertEqual(accounting["totals"]["judgment"]["reported_cost"], 0)
                self.assertEqual(accounting["totals"]["other"]["reported_cost"], 1.25)
                self.assertIsNone(method["judged"])
                self.assertEqual(method["judged_evidence_class"], "unverified")
                self.assertEqual(method["task_judgment_ledger_rows"], 0)
                self.assertEqual(method["runtime_auxiliary_purposes"], [purpose])
                verdict = run.adherence("direct-script", method)
                self.assertEqual(verdict["status"], "unverified")
                self.assertIsNone(verdict["passed"])

    def test_duplicate_id_in_one_snapshot_counts_once_and_unknown_cost_leaves_total_unknown(self):
        result = run.finalize_accounting([_row("same"), _row("same"), _row("failed", cost=None, unknown=True)],
                                         [], ["complete"])
        self.assertEqual([row["id"] for row in result["entries"]], ["same", "failed"])
        self.assertEqual(result["monetary_status"], "incomplete")
        self.assertIsNone(result["totals"]["total"]["reported_cost"])
        self.assertEqual(result["totals"]["auxiliary"]["reported_subtotal"], 1.25)
        self.assertEqual(result["duplicate_keys_ignored"], [_row("same")["key"]])
        self.assertIn("an observed attempt has unknown cost", result["monetary_reasons"])

    def test_same_ledger_id_from_two_attempts_is_two_paid_attempts(self):
        first = run.finalize_accounting([_row("j1", scope="case:1:0")], [], ["complete"])
        retry = run.finalize_accounting([_row("j1", scope="case:1:1")], [], ["complete"])
        merged = run.merge_accounting([first, retry])
        self.assertEqual(merged["collection_status"], "complete")
        self.assertEqual(merged["monetary_status"], "incomplete")
        self.assertEqual(merged["totals"]["judgment"]["reported_cost"], 2.5)
        self.assertEqual(merged["totals"]["total"]["reported_subtotal"], 2.5)
        self.assertIsNone(merged["totals"]["total"]["reported_cost"])

    def test_conflicting_payloads_for_one_id_are_a_conflict_not_a_choice(self):
        result = run.finalize_accounting([_row("same"), _row("same", cost=4)], [], ["complete"])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["collection_status"], "incomplete")
        self.assertIn("ledger conflict", result["monetary_reasons"])
        self.assertIsNone(result["totals"]["judgment"]["reported_cost"])
        self.assertIsNone(result["totals"]["total"]["reported_cost"])

    def test_absent_or_incomplete_snapshot_leaves_total_unknown_but_keeps_observed_subtotal(self):
        for state in ("absent", "incomplete"):
            result = run.finalize_accounting([_row("j1")], [], [state])
            self.assertEqual(result["monetary_status"], "incomplete", state)
            self.assertEqual(result["collection_status"], "incomplete", state)
            self.assertTrue(result["totals"]["judgment"]["coverage_unknown"], state)
            self.assertTrue(result["totals"]["judgment"]["cost_unknown"], state)
            self.assertIsNone(result["totals"]["total"]["reported_cost"], state)
            self.assertEqual(result["totals"]["total"]["reported_subtotal"], 1.25, state)

    def test_reported_zero_is_known_for_the_observed_judgment_not_the_whole_invocation(self):
        result = run.finalize_accounting([_row("hit", cost=0, cache="hit", questions=4, cached=4)], [], ["complete"])
        self.assertEqual(result["collection_status"], "complete")
        self.assertEqual(result["monetary_status"], "incomplete")
        self.assertEqual(result["totals"]["judgment"]["reported_cost"], 0)
        self.assertFalse(result["totals"]["judgment"]["cost_unknown"])
        self.assertIsNone(result["totals"]["total"]["reported_cost"])
        self.assertTrue(result["totals"]["total"]["cost_unknown"])

    def test_complete_stock_judge_ledgers_never_seal_completion_billing_even_when_merged(self):
        records = []
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for scope in ("case:1:0", "case:1:1"):
                session = repo / scope.replace(":", "-")
                session.mkdir()
                # Real persisted shape: a session header and judge usage, no completion journal.
                (session / "session.jsonl").write_text(
                    json.dumps({"type": "session", "id": scope, "cwd": str(repo)}) + "\n"
                    + json.dumps(_judgment()) + "\n")
                entries, conflicts, state, _ = run.read_session_usage(session, scope, repo, complete=True)
                self.assertEqual(state, "complete")
                self.assertEqual(conflicts, [])
                records.append(run.finalize_accounting(entries, conflicts, [state]))
        merged = run.merge_accounting(records)
        for record, subtotal, count in (
            (records[0], 0.5, 1), (records[1], 0.5, 1), (merged, 1.0, 2),
            (run.merge_accounting([merged, records[0]]), 1.0, 2),
        ):
            with self.subTest(subtotal=subtotal, snapshots=record["snapshot_statuses"]):
                self.assertEqual(record["collection_status"], "complete")
                self.assertEqual(record["monetary_status"], "incomplete")
                self.assertEqual(record["totals"]["judgment"]["reported_cost"], subtotal)
                self.assertEqual(record["totals"]["judgment"]["entries"], count)
                self.assertEqual(record["totals"]["total"]["reported_subtotal"], subtotal)
                self.assertEqual(record["totals"]["completion"]["entries"], 0)
                for name in ("completion", "auxiliary", "total"):
                    bucket = record["totals"][name]
                    self.assertIsNone(bucket["reported_cost"], name)
                    self.assertTrue(bucket["cost_unknown"], name)
                    self.assertTrue(bucket["coverage_unknown"], name)
                    self.assertFalse(bucket["tokens_complete"], name)
                self.assertFalse(record["grading_cost"]["measured"])
                self.assertIsNone(record["grading_cost"]["reported_cost"])

    def test_empty_or_observed_completion_rows_cannot_attest_completion_coverage(self):
        for rows in ([], [_row("c1", purpose="eval_completion")]):
            result = run.finalize_accounting(rows, [], ["complete"])
            for record in (result, run.merge_accounting([result]), run.merge_accounting([])):
                for name in ("completion", "auxiliary", "total"):
                    self.assertIsNone(record["totals"][name]["reported_cost"])
                    self.assertTrue(record["totals"][name]["cost_unknown"])
                    self.assertTrue(record["totals"][name]["coverage_unknown"])
            self.assertEqual(result["totals"]["completion"]["reported_subtotal"], 1.25 if rows else 0)

    def test_merging_a_record_without_snapshots_preserves_missing_collection_evidence(self):
        missing = run.finalize_accounting([], [], [])
        complete = run.finalize_accounting([_row("j1")], [], ["complete"])
        merged = run.merge_accounting([missing, complete])
        self.assertEqual(merged["collection_status"], "incomplete")
        self.assertIn("auxiliary ledger snapshot absent", merged["monetary_reasons"])
        self.assertEqual(merged["totals"]["judgment"]["reported_subtotal"], 1.25)
        for name in ("judgment", "completion", "auxiliary", "total"):
            self.assertIsNone(merged["totals"][name]["reported_cost"])
            self.assertTrue(merged["totals"][name]["cost_unknown"])
            self.assertTrue(merged["totals"][name]["coverage_unknown"])

    def test_cache_counts_keep_a_known_zero_apart_from_missing_counts(self):
        rows = [_row("a", questions=2, cached=0), _row("b", questions=None, cached=None)]
        evidence = run.cache_evidence(rows)
        self.assertEqual((evidence["questions_known"], evidence["questions_unknown_rows"]), (2, 1))
        self.assertEqual((evidence["cached_questions_known"], evidence["cached_questions_unknown_rows"]), (0, 1))


RUBRIC = {"factual": {"type": "choice", "instructions": "Décide from the cited evidence.",
                      "criteria": {"supported": "Evidence establishes it.", "refuted": "Evidence contradicts it."}}}
PLANNED = ["C1", "C2", "C3"]


def _receipt(strategy="jev-flat", units=PLANNED, errors=(), qhash=None, slots=None, cache="miss"):
    """Schema2 helper receipt. cache: miss|hit paid/fully-cached metadata, None for stock-unknown metadata.

    batches[].items holds one returned host item per flat state or group, never per expanded slot."""
    units = list(units)
    slots = slots or {}
    answered = [uid for uid in units if uid not in errors]
    hosts = {key: list(ids) for key, ids in slots.items()} or {uid: [uid] for uid in units}
    returned = [key for key, ids in hosts.items() if set(ids) & set(answered)]
    per_item = len(RUBRIC) * (max(map(len, hosts.values())) if slots else 1)
    items = [{"id": key, "batch_id": "b1", "selected_model": "openrouter/typesafe/jev-1",
              "primary_model": "openrouter/typesafe/jev-1", "response_model": "typesafe/jev-1",
              "fallback": False, "cache": cache, "questions": per_item,
              "cached_questions": per_item if cache == "hit" else 0} for key in returned]
    if cache is None:
        for item in items:
            for field in ("selected_model", "primary_model", "response_model", "fallback",
                          "cache", "questions", "cached_questions"):
                item[field] = None
    receipt = {"schema": 2, "strategy": strategy, "batch_ids": ["b1"],
               "batches": [{"batch_id": "b1", "planned_ids": list(hosts), "answered_ids": returned,
                            "error_ids": [key for key in hosts if key not in returned],
                            "coverage_errors": [], "items": items}],
               "original_ids": units, "unit_ids": units, "excluded_ids": [], "exclusions": {},
               "answered_ids": answered, "error_ids": list(errors),
               "coverage_errors": [], "complete": not errors,
               "question_hash": qhash or jv.qhash(RUBRIC), "qhash": qhash or jv.qhash(RUBRIC),
               "group_sizes": [len(ids) for ids in slots.values()], "group_slots": slots}
    if cache is not None:
        known = len(returned)
        receipt.update(cache_counts={"hit": known if cache == "hit" else 0, "partial": 0,
                                     "miss": known if cache == "miss" else 0, "unknown": len(hosts) - known},
                       questions=per_item * known, cached_questions=per_item * known if cache == "hit" else 0,
                       question_counts_unknown=len(hosts) - known)
    return receipt


def _adherence(strategy, receipts, rows, *, snapshot_statuses=None, conflicts=()):
    plan = {"planned_ids": PLANNED, "rubric_hash": jv.qhash(RUBRIC)}
    accounting = run.finalize_accounting(rows, conflicts,
                                         ["complete"] if snapshot_statuses is None else snapshot_statuses)
    method = run.method_evidence([], receipts, accounting, plan, strategy)
    return run.adherence(strategy, method), method


class AdherenceTests(unittest.TestCase):
    def test_public_plan_hash_is_the_hash_the_helper_writes_into_its_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "review").mkdir()
            (repo / "review/rubric.json").write_text(json.dumps(RUBRIC, indent=2, ensure_ascii=False))
            (repo / "review/claims.json").write_text(json.dumps({"claims": [{"id": uid} for uid in PLANNED]}))
            self.assertEqual(run.public_plan(repo), {"planned_ids": PLANNED, "rubric_hash": jv.qhash(RUBRIC)})

    def test_receipt_without_host_judgment_or_cache_evidence_is_unverified(self):
        for cache in ("miss", None):
            with self.subTest(cache=cache):
                verdict, method = _adherence("jev-flat", [_receipt(cache=cache)], [])
                self.assertEqual(verdict["status"], "unverified")
                self.assertIsNone(verdict["passed"])
                self.assertIsNone(method["judged"])
                self.assertEqual(method["judged_evidence_class"], "unverified")

    def test_explicit_fully_cached_receipt_corroborates_method_without_billed_rows(self):
        for strategy, slots in (("jev-flat", None), ("jev-grouped", {"g1": ["C1", "C2"], "g2": ["C3"]})):
            with self.subTest(strategy=strategy):
                verdict, method = _adherence(strategy, [_receipt(strategy, slots=slots, cache="hit")], [])
                self.assertEqual(verdict["status"], "pass")
                self.assertIs(verdict["passed"], True)
                self.assertEqual(method["task_judgment_ledger_rows"], 0)

    def test_cache_corroboration_needs_explicit_batches_and_a_complete_ledger(self):
        partly = _receipt(cache="hit")
        partly["batches"][0]["items"][2].update(cache="miss", cached_questions=0)
        partly["cache_counts"] = {"hit": 2, "partial": 0, "miss": 1, "unknown": 0}
        partly["cached_questions"] = 2
        unbatched = _receipt(cache="hit")
        unbatched["batches"] = []
        summary_only = _receipt(cache="hit")
        summary_only["batches"][0]["items"] = []
        mismatched = _receipt(cache="hit")
        mismatched["batches"][0]["items"][0]["batch_id"] = "b2"
        unrouted = _receipt(cache="hit")
        unrouted["batches"][0]["items"][0]["selected_model"] = ""
        miscounted = _receipt(cache="hit")
        miscounted["cached_questions"] = 2
        cases = (("partial hit", partly, ["complete"]), ("batches disagree", unbatched, ["complete"]),
                 ("summary without host items", summary_only, ["complete"]),
                 ("item names another batch", mismatched, ["complete"]),
                 ("item lacks route", unrouted, ["complete"]),
                 ("summary disagrees with items", miscounted, ["complete"]),
                 ("incomplete ledger", _receipt(cache="hit"), ["incomplete"]),
                 ("absent ledger", _receipt(cache="hit"), ["absent"]))
        for label, receipt, statuses in cases:
            with self.subTest(case=label):
                verdict, _ = _adherence("jev-flat", [receipt], [], snapshot_statuses=statuses)
                self.assertEqual(verdict["status"], "unverified")
                self.assertIsNone(verdict["passed"])

    def test_absent_receipts_and_judgment_telemetry_are_unverified_not_violations(self):
        for statuses in (["absent"], ["incomplete"], ["complete"]):
            with self.subTest(snapshot_statuses=statuses):
                verdict, _ = _adherence("jev-flat", [], [], snapshot_statuses=statuses)
                self.assertEqual(verdict["status"], "unverified")
                self.assertIsNone(verdict["passed"])

    def test_flat_receipt_must_cover_the_public_plan_with_the_unchanged_rubric(self):
        rows = [_row(f"j{i}", questions=1) for i in range(3)]
        self.assertEqual(_adherence("jev-flat", [_receipt()], rows)[0]["status"], "pass")
        self.assertEqual(_adherence("jev-flat", [_receipt(units=PLANNED[:2])], rows)[0]["status"], "violation")
        self.assertEqual(_adherence("jev-flat", [_receipt(qhash="000000000000")], rows)[0]["status"], "violation")

    def test_unit_errors_are_outcomes_not_method_violations(self):
        rows = [_row(f"j{i}", questions=2) for i in range(2)]
        verdict, method = _adherence("jev-flat", [_receipt(errors=("C3",))], rows)
        self.assertEqual(verdict["status"], "pass")
        self.assertEqual(method["unit_outcomes"]["error_ids"], ["C3"])

    def test_helper_exclusions_are_reported_separately_from_answers_and_errors(self):
        receipt = _receipt(units=PLANNED[:2])
        receipt.update(original_ids=PLANNED, excluded_ids=["C3"], exclusions={"C3": "excluded by input rule"},
                       complete=False)
        rows = [_row(f"j{i}", questions=None, cached=None) for i in range(2)]
        _, method = _adherence("jev-flat", [receipt], rows)
        outcomes = method["unit_outcomes"]
        self.assertEqual(outcomes["answered_ids"], ["C1", "C2"])
        self.assertEqual(outcomes["error_ids"], [])
        self.assertEqual(outcomes["excluded_ids"], ["C3"])

    def test_billed_rows_need_no_question_metadata_but_must_cover_uncached_host_items(self):
        unlabelled = [_row(f"j{i}", questions=None, cached=None) for i in range(3)]
        verdict, _ = _adherence("jev-flat", [_receipt()], unlabelled)
        self.assertEqual(verdict["status"], "pass")
        self.assertIs(verdict["passed"], True)
        verdict, _ = _adherence("jev-flat", [_receipt()], unlabelled[:2])
        self.assertEqual(verdict["status"], "unverified")
        self.assertIsNone(verdict["passed"])

    def test_grouped_receipt_needs_multi_slot_groups_within_k(self):
        rows = [_row(f"j{i}", questions=2) for i in range(3)]
        singles = _receipt("jev-grouped", slots={"g1": ["C1"], "g2": ["C2"], "g3": ["C3"]})
        paired = _receipt("jev-grouped", slots={"g1": ["C1", "C2"], "g2": ["C3"]})
        self.assertEqual(_adherence("jev-grouped", [singles], rows)[0]["status"], "violation")
        self.assertEqual(_adherence("jev-grouped", [paired], rows)[0]["status"], "pass")

    def test_direct_script_needs_complete_snapshot_coverage_to_verify_no_nested_attempts(self):
        for statuses in (["absent"], ["incomplete"], [], ["complete", "absent"], ["complete", "incomplete"]):
            with self.subTest(snapshot_statuses=statuses):
                verdict, method = _adherence("direct-script", [], [], snapshot_statuses=statuses)
                self.assertEqual(verdict["status"], "unverified")
                self.assertIsNone(verdict["passed"])
                self.assertEqual(method["snapshot_statuses"], statuses)
                self.assertEqual(method["conflicts"], [])

    def test_direct_script_missing_snapshot_coverage_is_unverified(self):
        _, method = _adherence("direct-script", [], [])
        method.pop("snapshot_statuses")
        verdict = run.adherence("direct-script", method)
        self.assertEqual(verdict["status"], "unverified")
        self.assertIsNone(verdict["passed"])

    def test_direct_script_complete_empty_ledger_cannot_rule_out_cache_only_judgments(self):
        verdict, method = _adherence("direct-script", [], [])
        self.assertEqual(verdict["status"], "unverified")
        self.assertIsNone(verdict["passed"])
        self.assertIsNone(method["judged"])
        self.assertEqual(method["judged_evidence_class"], "unverified")
        self.assertEqual(method["snapshot_statuses"], ["complete"])
        self.assertEqual(method["conflicts"], [])

    def test_direct_script_conflicting_ledger_is_unverified(self):
        runtime = _row("t1", purpose="title")
        conflicts = [{"key": runtime["key"], "reason": "same ledger id has conflicting payloads"}]
        for statuses in (["complete"], ["incomplete"]):
            with self.subTest(snapshot_statuses=statuses):
                verdict, method = _adherence("direct-script", [], [runtime],
                                             snapshot_statuses=statuses, conflicts=conflicts)
                self.assertEqual(verdict["status"], "unverified")
                self.assertIsNone(verdict["passed"])
                self.assertEqual(method["snapshot_statuses"], statuses)
                self.assertEqual(method["conflicts"], conflicts)

    def test_direct_script_forbidden_evidence_overrides_incomplete_or_conflicting_coverage(self):
        cases = (
            ("judgment", [], [_row("j1")]),
            ("failed judgment", [], [_row("j1", cost=None, unknown=True)]),
            ("eval_completion", [], [_row("c1", purpose="eval_completion")]),
            ("failed eval_completion", [], [_row("c1", purpose="eval_completion", cost=None, unknown=True)]),
            ("receipt", [_receipt()], []),
        )
        conflict = {"key": _row("t1", purpose="title")["key"],
                    "reason": "same ledger id has conflicting payloads"}
        for label, receipts, rows in cases:
            for statuses, conflicts in ((["complete"], []), (["incomplete"], []),
                                         (["complete"], [conflict]), (["incomplete"], [conflict])):
                with self.subTest(evidence=label, snapshot_statuses=statuses, conflicts=conflicts):
                    verdict, method = _adherence("direct-script", receipts, rows,
                                                 snapshot_statuses=statuses, conflicts=conflicts)
                    self.assertEqual(verdict["status"], "violation")
                    self.assertIs(verdict["passed"], False)
                    self.assertEqual(method["snapshot_statuses"], statuses)
                    self.assertEqual(method["conflicts"], conflicts)

    def test_direct_script_runtime_only_ledger_leaves_method_and_price_separately_unknown(self):
        rows = [_row("t1", purpose="title", cost=None, unknown=True)]
        verdict, method = _adherence("direct-script", [], rows)
        accounting = run.finalize_accounting(rows, [], ["complete"])
        self.assertEqual(verdict["status"], "unverified")
        self.assertIsNone(verdict["passed"])
        self.assertEqual(method["runtime_auxiliary_purposes"], ["title"])
        self.assertEqual(method["snapshot_statuses"], ["complete"])
        self.assertEqual(method["conflicts"], [])
        self.assertEqual(accounting["snapshot_statuses"], ["complete"])
        self.assertEqual(accounting["monetary_status"], "incomplete")
        self.assertIsNone(accounting["totals"]["total"]["reported_cost"])

    def test_schema2_receipt_printed_after_a_progress_line_is_found(self):
        output = "jevify: 3 units · 3 ok\n" + json.dumps({"jevify_receipt": _receipt()})
        receipts = run.parse_receipts([{"content": [{"type": "text", "text": output}]}])
        self.assertEqual([receipt["unit_ids"] for receipt in receipts], [PLANNED])


class IsolationTests(unittest.TestCase):

    def test_source_manifest_binds_untracked_and_deleted_files_but_not_ignored_ones(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, CLEAN_ENV, clear=True):
            root = Path(tmp)

            def git(*args):
                subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                               cwd=root, check=True, capture_output=True)
            git("init", "-q")
            (root / ".gitignore").write_text("ignored.log\n")
            (root / "kept.ts").write_text("a\n")
            (root / "gone.ts").write_text("b\n")
            git("add", ".")
            git("commit", "-q", "-m", "base")
            (root / "gone.ts").unlink()
            (root / "new.ts").write_text("c\n")
            (root / "ignored.log").write_text("noise\n")
            identity = run.source_identity(sys.executable, root)
            self.assertEqual(identity["status"], "complete")
            self.assertEqual(identity["changes"]["gone.ts"], {"status": "deleted", "sha256": None})
            self.assertEqual(identity["changes"]["new.ts"]["status"], "untracked")
            self.assertNotIn("ignored.log", identity["changes"])
            (root / "new.ts").write_text("changed\n")
            self.assertNotEqual(run.source_identity(sys.executable, root)["source_manifest_sha256"],
                                identity["source_manifest_sha256"])


FAKE_OMP = """#!{python}
import json, os, sys, time
from pathlib import Path
assert "--no-session" not in sys.argv
session_dir = Path(sys.argv[sys.argv.index("--session-dir") + 1])
session_dir.mkdir(parents=True, exist_ok=True)
record = os.environ["FAKE_OMP_RECORD"]
with open(record, "a") as sink:
    sink.write(json.dumps({{"cache": os.environ.get("OMP_JUDGMENT_CACHE_DB"),
                           "session_dir": str(session_dir)}}) + "\\n")
with open(record) as source:
    index = len(source.read().splitlines()) - 1
with open(os.environ["FAKE_OMP_EVENTS"]) as source:
    streams = json.load(source)
script = streams[min(index, len(streams) - 1)]
header = {{"type": "session", "version": 3, "id": "session-" + str(index),
          "timestamp": "2026-10-09T00:00:00.000Z", "cwd": os.getcwd()}}
ledger_path = session_dir / ("2026-10-09_session-" + str(index) + ".jsonl")
if not script.get("omit_session"):
    with ledger_path.open("w") as sink:
        for entry in [header, *script.get("session_records", []), *script.get("early_ledger", [])]:
            sink.write(json.dumps(entry) + "\\n")
if script.get("unrelated"):
    unrelated = Path(os.environ["FAKE_OMP_UNRELATED"])
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    with unrelated.open("w") as sink:
        for entry in [header, *script["unrelated"]]:
            sink.write(json.dumps(entry) + "\\n")
for event in script["events"]:
    print(json.dumps(event), flush=True)
# A stock session can finish persisting usage after the last event and stdout EOF.
sys.stdout.flush()
os.close(1)
sys.stdout = open(os.devnull, "w")
time.sleep(script.get("delay_after_stdout", 0))
if not script.get("omit_session"):
    with ledger_path.open("a") as sink:
        for entry in script.get("ledger", []):
            sink.write(json.dumps(entry) + "\\n")
        sink.write(script.get("ledger_suffix", ""))
if script.get("hang"):
    time.sleep(60)
"""


def _assistant(content, reason, cost, *, usage=...):
    if usage is Ellipsis:
        usage = {"input": 10, "output": 5, "cacheRead": 0, "cacheWrite": 0, "cost": {"total": cost}}
    return {"type": "message_end", "message": {
        "role": "assistant", "content": content, "stopReason": reason, "api": "openai-responses",
        "provider": "openai", "model": "gpt-test",
        "usage": usage}}


def _judgment(entry_id="j1", cost=0.5):
    return {"id": entry_id, "parentId": None, "timestamp": "2026-10-09T00:00:00.000Z",
            "type": "model_usage", "purpose": "judge_batch", "api": "openai-completions",
            "provider": "openrouter", "model": "jev", "stopReason": "stop", "cache": "miss",
            "questions": 3, "cachedQuestions": 0,
            "usage": {"input": 3, "output": 1, "cacheRead": 0, "cacheWrite": 0, "cost": {"total": cost}}}


def _session(**options):
    call = {"type": "toolCall", "id": "t1", "name": "read", "arguments": {}}
    events = [
        _assistant([{"type": "text", "text": "I'll inspect the claims first."}, call], "toolUse", 0.01),
        {"type": "tool_execution_start", "toolCallId": "t1", "toolName": "read", "args": {"path": "review"}},
        {"type": "tool_execution_end", "toolCallId": "t1", "result": {"content": []}, "isError": False},
        _assistant([call], "toolUse", 0.02),
        _assistant([call], "toolUse", 0.04),
        _assistant([{"type": "text", "text": "Every claim is reviewed."}], "stop", 0.03),
        {"type": "agent_end"},
    ]
    return {"events": events, "ledger": [_judgment()], **options}


class AttemptStreamTests(unittest.TestCase):
    """Run the real subprocess attempt loop with stock-shaped persisted session usage."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fake = self.root / "omp"
        handle = os.open(self.fake, os.O_WRONLY | os.O_CREAT, 0o755)
        with os.fdopen(handle, "w") as sink:
            sink.write(FAKE_OMP.format(python=sys.executable))
        (self.root / "raw").mkdir()
        (self.root / "work").mkdir()
        self.args = SimpleNamespace(omp=str(self.fake), model=None, thinking=None, max_time=30, max_calls=40,
                                    raw_dir=str(self.root / "raw"), strategy="auto", cache_mode="cold",
                                    cache_namespace=None, retries=1)
        self.case = {"id": "refund_retry_review", "expect": "either", "dataset": "refund_retry",
                     "prompt": "Review the claims."}

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, streams):
        events = self.root / "events.json"
        events.write_text(json.dumps([{"events": stream} if isinstance(stream, list) else stream
                                     for stream in streams]))
        environment = dict(CLEAN_ENV, FAKE_OMP_EVENTS=str(events), FAKE_OMP_RECORD=str(self.root / "record"),
                           FAKE_OMP_UNRELATED=str(self.root / "work" / "unrelated" / "session.jsonl"))
        with mock.patch.dict(os.environ, environment, clear=True):
            return run.run_one(self.case, 1, self.root / "overlay.yml", None, self.args, self.root / "work")

    def _token_alias_fixtures(self, value):
        return (
            ("input", "input", {"input": value, "output": 0, "cache_read": 0, "cache_write": 0}),
            ("inputTokens", "input", {"input": value, "output": 0, "cache_read": 0, "cache_write": 0}),
            ("prompt_tokens", "input", {"input": value, "output": 0, "cache_read": 0, "cache_write": 0}),
            ("output", "output", {"input": 0, "output": value, "cache_read": 0, "cache_write": 0}),
            ("outputTokens", "output", {"input": 0, "output": value, "cache_read": 0, "cache_write": 0}),
            ("completion_tokens", "output", {"input": 0, "output": value, "cache_read": 0, "cache_write": 0}),
            ("cache_read", "cache_read", {"input": 0, "output": 0, "cache_read": value, "cache_write": 0}),
            ("cacheRead", "cache_read", {"input": 0, "output": 0, "cache_read": value, "cache_write": 0}),
            ("cached_tokens", "cache_read", {"input": 0, "output": 0, "cache_read": value, "cache_write": 0}),
            ("cache_write", "cache_write", {"input": 0, "output": 0, "cache_read": 0, "cache_write": value}),
            ("cacheWrite", "cache_write", {"input": 0, "output": 0, "cache_read": 0, "cache_write": value}),
        )

    def _observation_stream(self, usage, reason):
        return [
            _assistant([{"type": "text", "text": "Observation answer."}], reason, 0, usage=usage),
            {"type": "agent_end"},
        ]

    def _assert_observation(self, usage, reason, *, expected_tokens, cost_unknown, cost_basis,
                            reported_cost, reported_subtotal, tokens_complete,
                            aggregate_tokens, final_answers, answer):
        with mock.patch.object(self.args, "retries", 0):
            result = self._run([self._observation_stream(usage, reason)])
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(len(result["attempt_records"]), 1)
        for observation in (result, result["attempt_records"][0]):
            self.assertEqual(observation["final_answers"], final_answers)
            self.assertEqual(observation["answer"], answer)
            accounting = observation["accounting"]
            self.assertEqual(len(accounting["entries"]), 1)
            outer = [entry for entry in accounting["entries"] if entry["origin"] == "outer"]
            self.assertEqual(len(outer), 1)
            entry = outer[0]
            self.assertEqual(entry["cost_unknown"], cost_unknown)
            self.assertEqual(entry["cost_basis"], cost_basis)
            self.assertEqual(entry["reported_cost"], reported_cost)
            self.assertEqual(entry["tokens"], expected_tokens)
            self.assertEqual(accounting["collection_status"], "complete")
            self.assertEqual(accounting["monetary_status"], "incomplete")
            self.assertEqual("an observed attempt has unknown cost" in accounting["monetary_reasons"], cost_unknown)
            for bucket in ("outer", "total"):
                totals = accounting["totals"][bucket]
                self.assertEqual(totals["reported_cost"], reported_cost if bucket == "outer" else None)
                self.assertEqual(totals["cost_unknown"], cost_unknown if bucket == "outer" else True)
                self.assertEqual(totals["coverage_unknown"], bucket == "total")
                self.assertEqual(totals["reported_subtotal"], reported_subtotal)
                self.assertEqual(totals["tokens"], aggregate_tokens)
                self.assertEqual(totals["tokens_complete"], tokens_complete if bucket == "outer" else False)
                self.assertEqual(totals["entries"], 1)

    def test_failed_positive_token_aliases_distinguish_reported_zero_from_missing_cost(self):
        for reason in ("error", "aborted"):
            for value in (7, 0.5):
                for alias, canonical, expected_tokens in self._token_alias_fixtures(value):
                    for cost_mode, cost_unknown, cost_basis, reported_cost in (
                        ("reported_zero", False, "catalog_estimate", 0),
                        ("missing", True, "unknown", None),
                    ):
                        with self.subTest(reason=reason, alias=alias, value=value, cost_mode=cost_mode):
                            usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
                            if alias != canonical:
                                del usage[canonical]
                            usage[alias] = value
                            if cost_mode == "reported_zero":
                                usage["cost"] = {"total": 0}
                            self._assert_observation(
                                usage, reason, expected_tokens=expected_tokens, cost_unknown=cost_unknown,
                                cost_basis=cost_basis, reported_cost=reported_cost, reported_subtotal=0.0,
                                tokens_complete=True,
                                aggregate_tokens=expected_tokens, final_answers=[], answer="")

    def test_failed_zero_tokens_leave_reported_zero_unknown(self):
        for reason in ("error", "aborted"):
            with self.subTest(reason=reason, alias="canonical_all", value=0, cost_mode="reported_zero"):
                usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": {"total": 0}}
                self._assert_observation(
                    usage, reason,
                    expected_tokens={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                    cost_unknown=True, cost_basis="unknown", reported_cost=None, reported_subtotal=0.0,
                    tokens_complete=True,
                    aggregate_tokens={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                    final_answers=[], answer="")

    def test_failed_absent_usage_keeps_tokens_incomplete(self):
        for reason in ("error", "aborted"):
            for usage in (None, {}):
                with self.subTest(reason=reason, alias="absent", value=usage, cost_mode="missing"):
                    self._assert_observation(
                        usage, reason,
                        expected_tokens={"input": None, "output": None, "cache_read": None, "cache_write": None},
                        cost_unknown=True, cost_basis="unknown", reported_cost=None, reported_subtotal=0.0,
                        tokens_complete=False,
                        aggregate_tokens={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                        final_answers=[], answer="")

    def test_failed_boolean_token_aliases_are_not_usage_evidence(self):
        for reason in ("error", "aborted"):
            for value in (True, False):
                for alias, canonical, ignored_tokens in self._token_alias_fixtures(value):
                    with self.subTest(reason=reason, alias=alias, value=value, cost_mode="reported_zero"):
                        usage = {alias: value, "cost": {"total": 0}}
                        self._assert_observation(
                            usage, reason,
                            expected_tokens={"input": None, "output": None, "cache_read": None, "cache_write": None},
                            cost_unknown=True, cost_basis="unknown", reported_cost=None, reported_subtotal=0.0,
                            tokens_complete=False,
                            aggregate_tokens={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                            final_answers=[], answer="")

    def test_stop_zero_cost_retains_answers_with_zero_or_positive_tokens(self):
        reason = "stop"
        for tokens in (
            {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
            {"input": 7, "output": 0, "cache_read": 0, "cache_write": 0},
        ):
            with self.subTest(reason=reason, alias="input", value=tokens["input"], cost_mode="reported_zero"):
                usage = tokens.copy()
                usage["cost"] = {"total": 0}
                self._assert_observation(
                    usage, reason, expected_tokens=tokens, cost_unknown=False, cost_basis="catalog_estimate",
                    reported_cost=0, reported_subtotal=0.0, tokens_complete=True,
                    aggregate_tokens=tokens,
                    final_answers=[{"source": "assistant_message", "text": "Observation answer."}],
                    answer="Observation answer.")

    def test_failed_positive_tokens_preserve_reported_nonzero_cost_without_grading_text(self):
        for reason in ("error", "aborted"):
            with self.subTest(reason=reason, alias="input", value=7, cost_mode="reported_nonzero"):
                usage = {"input": 7, "output": 0, "cache_read": 0, "cache_write": 0, "cost": {"total": 0.125}}
                self._assert_observation(
                    usage, reason,
                    expected_tokens={"input": 7, "output": 0, "cache_read": 0, "cache_write": 0},
                    cost_unknown=False, cost_basis="catalog_estimate", reported_cost=0.125,
                    reported_subtotal=0.125, tokens_complete=True,
                    aggregate_tokens={"input": 7, "output": 0, "cache_read": 0, "cache_write": 0},
                    final_answers=[], answer="")

    def test_interim_tool_use_text_is_paid_for_but_not_graded_as_a_final_answer(self):
        result = self._run([_session()])
        self.assertEqual(result["final_answers"], [{"source": "assistant_message", "text": "Every claim is reviewed."}])
        self.assertEqual(result["assistant_messages"][0]["text"], "I'll inspect the claims first.")
        outer = [row for row in result["accounting"]["entries"] if row["origin"] == "outer"]
        self.assertEqual(len({row["key"] for row in outer}), 4)
        self.assertTrue(all(row["estimated"] and row["provider"] == "openai" and row["api"] == "openai-responses"
                            for row in outer))
        self.assertEqual(result["accounting"]["collection_status"], "complete")
        self.assertEqual(result["accounting"]["monetary_status"], "incomplete")
        self.assertAlmostEqual(result["accounting"]["totals"]["outer"]["reported_cost"], 0.10)
        self.assertAlmostEqual(result["accounting"]["totals"]["total"]["reported_subtotal"], 0.60)
        self.assertIsNone(result["accounting"]["totals"]["total"]["reported_cost"])

    def test_usage_persisted_after_stdout_closes_is_included_after_process_settles(self):
        result = self._run([_session(delay_after_stdout=0.1)])
        self.assertEqual(result["accounting"]["collection_status"], "complete")
        self.assertEqual(result["accounting"]["monetary_status"], "incomplete")
        self.assertEqual(result["method"]["task_judgment_ledger_rows"], 1)
        self.assertIs(result["judged"], True)
        self.assertAlmostEqual(result["accounting"]["totals"]["total"]["reported_subtotal"], 0.60)
        self.assertIsNone(result["accounting"]["totals"]["total"]["reported_cost"])

    def test_missing_persisted_session_leaves_cost_and_judging_unknown(self):
        with mock.patch.object(self.args, "strategy", "jev-flat"):
            result = self._run([_session(omit_session=True)])
        self.assertEqual(result["accounting"]["monetary_status"], "incomplete")
        self.assertIsNone(result["accounting"]["totals"]["total"]["reported_cost"])
        self.assertAlmostEqual(result["accounting"]["totals"]["total"]["reported_subtotal"], 0.10)
        self.assertEqual(result["accounting"]["collection_status"], "incomplete")
        self.assertIn("auxiliary ledger snapshot absent", result["accounting"]["monetary_reasons"])
        self.assertIsNone(result["judged"])
        self.assertEqual(result["strategy_adherence"]["status"], "unverified")
        self.assertIsNone(result["strategy_adherence"]["passed"])

    def test_source_markers_do_not_prove_judging_or_method_adherence(self):
        events = [
            {"type": "tool_execution_start", "toolCallId": "e1", "toolName": "eval",
             "args": {"code": "# skill://jevify/jevify.py judge_batch jv.run( judge( completion("}},
            {"type": "tool_execution_end", "toolCallId": "e1", "result": {"content": []}, "isError": False},
            _assistant([{"type": "text", "text": "Review complete."}], "stop", 0.01),
            {"type": "agent_end"},
        ]
        for strategy in ("jev-flat", "direct-script"):
            with self.subTest(strategy=strategy), mock.patch.object(self.args, "strategy", strategy):
                result = self._run([events])
                self.assertTrue(result["helpers_loaded"])
                self.assertEqual(result["helpers_loaded_evidence_class"], "source_marker_only")
                self.assertTrue(result["method"]["source_marker_diagnostics"])
                self.assertTrue(result["method"]["source_marker_diagnostics_are_not_execution_proof"])
                self.assertEqual(result["method"]["task_judgment_ledger_rows"], 0)
                self.assertIsNone(result["judged"])
                self.assertEqual(result["strategy_adherence"]["status"], "unverified")
                self.assertIsNone(result["strategy_adherence"]["passed"])

    def _receipt_session(self, receipt):
        output = "jevify: 3 units · 3 ok\n" + json.dumps({"jevify_receipt": receipt})
        return [
            {"type": "tool_execution_start", "toolCallId": "e1", "toolName": "eval",
             "args": {"code": "rows = await jv.run(states, Q, intent='review')"}},
            {"type": "tool_execution_end", "toolCallId": "e1",
             "result": {"content": [{"type": "text", "text": output}]}, "isError": False},
            _assistant([{"type": "text", "text": "Review complete."}], "stop", 0.01),
            {"type": "agent_end"},
        ]

    def test_explicit_cache_hit_receipt_shows_judging_with_zero_billed_rows(self):
        plan = {"planned_ids": PLANNED, "rubric_hash": jv.qhash(RUBRIC)}
        with mock.patch.object(self.args, "strategy", "jev-flat"), \
                mock.patch.object(run, "public_plan", return_value=plan):
            receipt = {**_receipt(cache="hit"), "intent": "private-receipt-intent",
                       "error_details": {"C9": "private-receipt-diagnostic"}}
            result = self._run([self._receipt_session(receipt)])
        method = json.dumps(result["method"])
        self.assertNotIn("private-receipt-intent", method)
        self.assertNotIn("private-receipt-diagnostic", method)
        self.assertEqual(result["method"]["task_judgment_ledger_rows"], 0)
        self.assertIs(result["judged"], True)
        self.assertEqual(result["strategy_adherence"]["status"], "pass")
        self.assertIs(result["strategy_adherence"]["passed"], True)
        self.assertEqual(result["accounting"]["collection_status"], "complete")
        self.assertEqual(result["accounting"]["monetary_status"], "incomplete")
        self.assertAlmostEqual(result["accounting"]["totals"]["outer"]["reported_cost"], 0.01)
        self.assertAlmostEqual(result["accounting"]["totals"]["total"]["reported_subtotal"], 0.01)
        self.assertIsNone(result["accounting"]["totals"]["total"]["reported_cost"])

    def test_receipt_without_cache_corroboration_or_billed_rows_leaves_judging_unknown(self):
        plan = {"planned_ids": PLANNED, "rubric_hash": jv.qhash(RUBRIC)}
        for cache in (None, "miss"):
            with self.subTest(cache=cache), mock.patch.object(self.args, "strategy", "jev-flat"), \
                    mock.patch.object(run, "public_plan", return_value=plan):
                result = self._run([self._receipt_session(_receipt(cache=cache))])
                self.assertEqual(result["method"]["task_judgment_ledger_rows"], 0)
                self.assertIsNone(result["judged"])
                self.assertEqual(result["strategy_adherence"]["status"], "unverified")
                self.assertIsNone(result["strategy_adherence"]["passed"])

    def test_malformed_persisted_session_retains_observed_cost_without_claiming_total(self):
        for suffix in ('{"type":"model_usage",', json.dumps({"type": "model_usage", "usage": {}}) + "\n"):
            with self.subTest(suffix=suffix):
                result = self._run([_session(ledger_suffix=suffix)])
                self.assertEqual(result["accounting"]["collection_status"], "incomplete")
                self.assertTrue(result["accounting"]["conflicts"])
                self.assertEqual(result["accounting"]["monetary_status"], "incomplete")
                self.assertIsNone(result["accounting"]["totals"]["total"]["reported_cost"])
                self.assertAlmostEqual(result["accounting"]["totals"]["total"]["reported_subtotal"], 0.60)

    def test_forced_termination_keeps_observed_usage_but_cannot_seal_cost(self):
        script = _session(early_ledger=[_judgment()], ledger=[], hang=True)
        with mock.patch.object(self.args, "max_calls", 1), mock.patch.object(self.args, "retries", 0):
            result = self._run([script])
        self.assertEqual(result["stop"], "max_calls")
        self.assertEqual(result["execution_status"], "incomplete")
        self.assertEqual(result["accounting"]["collection_status"], "incomplete")
        self.assertIn("persisted auxiliary ledger coverage incomplete", result["accounting"]["monetary_reasons"])
        self.assertEqual(result["accounting"]["monetary_status"], "incomplete")
        self.assertIsNone(result["accounting"]["totals"]["total"]["reported_cost"])
        self.assertAlmostEqual(result["accounting"]["totals"]["auxiliary"]["reported_subtotal"], 0.5)

    def test_unrelated_session_usage_is_not_included(self):
        result = self._run([_session(unrelated=[_judgment("not-this-attempt", cost=91)])])
        self.assertNotIn("not-this-attempt", [row["id"] for row in result["accounting"]["entries"]])
        self.assertAlmostEqual(result["accounting"]["totals"]["total"]["reported_subtotal"], 0.60)
        self.assertIsNone(result["accounting"]["totals"]["total"]["reported_cost"])
        self.assertEqual(result["method"]["task_judgment_ledger_rows"], 1)

    def test_accounting_does_not_retain_transcripts_or_credential_pins(self):
        judgment = {**_judgment(), "prompt": "private-ledger-prompt", "apiKey": "private-api-key",
                    "credentialPin": {"token": "private-pinned-token"}, "extensionData": "private-extra-data",
                    "errorMessage": "private-provider-diagnostic"}
        transcript = {"type": "message", "id": "private-message", "parentId": None,
                      "message": {"role": "user", "content": "private-session-transcript"}}
        result = self._run([_session(ledger=[judgment], session_records=[transcript])])
        for observation in (result, result["attempt_records"][0]):
            sanitized = json.dumps({key: observation[key] for key in ("accounting", "method", "route")})
            for secret in ("private-ledger-prompt", "private-api-key", "private-pinned-token",
                           "private-extra-data", "private-session-transcript", "private-provider-diagnostic"):
                self.assertNotIn(secret, sanitized)
            self.assertAlmostEqual(observation["accounting"]["totals"]["total"]["reported_subtotal"], 0.60)
            self.assertIsNone(observation["accounting"]["totals"]["total"]["reported_cost"])
        directories = [json.loads(line)["session_dir"] for line in (self.root / "record").read_text().splitlines()]
        self.assertTrue(all(not any(Path(directory).rglob("*.jsonl")) for directory in directories))

    def test_retry_cost_includes_failed_outer_and_stock_auxiliary_usage(self):
        failed = {"events": [_assistant([], "error", 0.125), {"type": "agent_end"}],
                  "ledger": [_judgment()]}
        result = self._run([failed, _session()])
        self.assertEqual([item["execution_status"] for item in result["attempt_records"]], ["infra", "complete"])
        self.assertEqual(result["accounting"]["collection_status"], "complete")
        self.assertEqual(result["accounting"]["monetary_status"], "incomplete")
        self.assertAlmostEqual(result["accounting"]["totals"]["outer"]["reported_cost"], 0.225)
        self.assertAlmostEqual(result["accounting"]["totals"]["auxiliary"]["reported_subtotal"], 1.0)
        self.assertAlmostEqual(result["accounting"]["totals"]["judgment"]["reported_cost"], 1.0)
        self.assertAlmostEqual(result["accounting"]["totals"]["total"]["reported_subtotal"], 1.225)
        for observation in (result, *result["attempt_records"]):
            for bucket in ("completion", "auxiliary", "total"):
                totals = observation["accounting"]["totals"][bucket]
                self.assertIsNone(totals["reported_cost"])
                self.assertTrue(totals["cost_unknown"])
                self.assertTrue(totals["coverage_unknown"])
        auxiliary = [row for row in result["accounting"]["entries"] if row["origin"] == "auxiliary"]
        self.assertEqual([row["id"] for row in auxiliary], ["j1", "j1"])
        self.assertEqual(len({row["key"] for row in auxiliary}), 2)
        self.assertTrue(all(row["estimated"] and row["cost_basis"] == "catalog_estimate" for row in auxiliary))
        self.assertEqual(result["attempt_records"][0]["final_answers"], [])
        self.assertEqual(result["answer"], "Every claim is reviewed.")
        directories = [json.loads(line)["session_dir"] for line in (self.root / "record").read_text().splitlines()]
        self.assertEqual(len(set(directories)), 2)
        self.assertTrue(all(not any(Path(directory).rglob("*.jsonl")) for directory in directories))

    def test_retry_keeps_the_failed_attempt_and_gives_each_attempt_its_own_cold_cache(self):
        result = self._run([{"events": [], "omit_session": True}, _session()])
        self.assertEqual([item["execution_status"] for item in result["attempt_records"]], ["infra", "complete"])
        self.assertEqual(result["attempt_records"][0]["final_answers"], [])
        caches = [json.loads(line)["cache"] for line in (self.root / "record").read_text().splitlines()]
        self.assertEqual(len(set(caches)), 2)
        self.assertIn("auxiliary ledger snapshot absent", result["accounting"]["monetary_reasons"])
        self.assertEqual(result["accounting"]["collection_status"], "incomplete")
        self.assertIsNone(result["accounting"]["totals"]["total"]["reported_cost"])
        self.assertAlmostEqual(result["accounting"]["totals"]["total"]["reported_subtotal"], 0.60)


if __name__ == "__main__":
    unittest.main()
