"""Synthetic refund/retry review fixture; no recorded production or model evidence.

build(root) writes a two-commit repository: HEAD~1 holds the BASE source plus the review
materials (review/), HEAD changes only source and config, so `git show HEAD` is the change
under review. public_files() is everything a subject sees; CLAIMS holds the private schema2
truth, frozen from source and policy reasoning before any model evaluation. Evidence refs are
"path:lines" into HEAD, or "HEAD~1:path:lines" into BASE.

review_units(grouped) gives explicit-strategy consumers the same context: BASE and HEAD source
plus every review file except claims.json, once per claim (flat) or once per episode (grouped).
review/replay.py is an executable synthetic scenario recipe, not a saved run.
"""

import json
import subprocess
from pathlib import Path

SUBJECT = "fix(refunds): retry connection resets before abandoning a refund"
BODY = (
    "Timeouts already enter the unknown-outcome retry path. Treat connection resets the same\n"
    "way when RESET_RETRY_ENABLED is on; enable that rollout flag in this candidate.\n"
    "Redelivery with a stored provider refund id now sends a confirmation create under the\n"
    "original key plus ':retry', then refreshes the stored id instead of returning immediately.\n"
    "Reduce the provider request timeout from 30 to 5 seconds; retain the three-attempt limit.\n"
    "Decimal booking and the ignored legacy compatibility argument are unchanged.\n"
)

POLICY = """# Refund retry operating policy

## Provider outcomes
A timeout, reset, or missing acknowledgement is an unknown outcome: the provider may have
settled the request without delivering its response. A warning alone establishes neither
rejection nor a second movement of money. A recorded provider refund id establishes acceptance.

## Idempotency and recovery
The provider deduplicates creates by account and idempotency key, not by our refund id or memo.
Every delivery of one refund must reuse its original key. Once an accepted provider id is
persisted, redelivery must not create a second settlement. A warning followed by one accepted
provider settlement is recovery. A reachable second settlement blocks release even if another
claim reports the same root cause; count one defect, retain every claim's identity.

## Currency booking
Inputs are decimal strings. Convert before half-even cent rounding. An intermediate display
is not a booking amount. Display preferences or intermediate precision do not justify changing
a correct booking; inspect the whole conversion and the amount passed to the provider.

## Compatibility debt
An unchanged, ignored compatibility argument is technical debt for the later adapter cleanup,
not a blocker for this release. Retrying existing calls does not itself introduce that argument.

## Retry bounds and rollout
Only unknown outcomes are retried, at most RETRY_LIMIT attempts. The rollout gate must actually
reach the outcome classifier; an enabled but unused flag is not evidence of a reachable retry.
A working bounded retry is not a reason to remove recovery. A logging-severity preference
without operator-impact evidence is not actionable: take no action; no downgrade is authorized.

## Incomplete timeout evidence
Before attributing a timeout to this change, obtain the request's start, applied deadline,
provider, refund correlation, deployed revision, and any later response. A bare timeout sample
cannot establish causation. Investigate missing context before changing timeout or retry knobs.
"""

RUBRIC = {
    "factual": {
        "type": "choice",
        "instructions": "Check this claim against complete base/head source, synthetic scenario expectations, and operating policy. Scenario expectations are not observed production evidence.",
        "criteria": {
            "supported": "The available source or policy establishes the claim.",
            "refuted": "The available evidence contradicts the claim.",
            "underdetermined": "Required evidence is missing or genuinely ambiguous.",
        },
    },
    "action": {
        "type": "choice",
        "instructions": "Choose the warranted action separately from factual truth. Do not invent missing operational evidence.",
        "criteria": {
            "fix_now": "A reachable contract break must block this release.",
            "defer": "Established non-blocking technical debt belongs in later cleanup.",
            "no_action": "No code or process change is warranted by this claim.",
            "investigate": "Obtain missing evidence before changing behavior.",
        },
    },
    "introduced": {
        "type": "choice",
        "instructions": "Compare the behavior at HEAD and HEAD~1. Do not confuse a supported claim with a newly introduced defect.",
        "criteria": {
            "yes": "HEAD creates or newly exposes the behavior under review.",
            "no": "The behavior predates HEAD, or the alleged behavior is refuted or absent.",
            "unknown": "Missing history or deployment evidence prevents attribution.",
        },
    },
}

# Freeze before model evaluation: facts and actions follow source and the operating policy.
# None of these truth fields, defect identifiers, evidence annotations, or rationales is public.
CLAIMS = [
    ("C01", "redelivery", "On redelivery with a persisted provider id, HEAD sends another create under a new idempotency key.", "supported", ["fix_now"], "yes", "D-DOUBLE-SETTLE",
     ["refunds/worker.py:15-22", "HEAD~1:refunds/worker.py:15-17", "review/policy.md:9-11"],
     "HEAD's stored-id branch calls create_refund with the original key plus ':retry'; at HEAD~1 that branch returned settled without any provider call. The provider deduplicates by key, so a new key after a persisted acceptance can settle a second time."),
    ("C02", "redelivery", "The bundled R-1842 redelivery scenario predicts two settled provider ids for one refund.", "supported", ["fix_now"], "yes", "D-DOUBLE-SETTLE",
     ["review/replay.py:64-69", "review/replay.py:28-30", "review/replay.md:7-10"],
     "The recipe records accepted pr_1842a, then redelivers through worker_entry. The sandbox settles each new key once, so HEAD's refund:1842:retry yields pr_1842b. The chronology labels this a source-derived expectation, not an executed replay."),
    ("C03", "redelivery", "A timeout warning by itself proves that both provider calls moved money.", "refuted", ["no_action"], "no", None,
     ["review/policy.md:4-6", "review/replay.py:28-32", "review/replay.md:13-14"],
     "Policy says a warning alone establishes neither rejection nor a second movement of money. A lost acknowledgement settles once and the same-key retry is deduplicated; R-1842's second settlement comes from a new key, not from a warning."),
    ("C04", "redelivery", "The acknowledged-redelivery confirmation path should block release even if another claim already reports it.", "supported", ["fix_now"], "yes", "D-DOUBLE-SETTLE",
     ["refunds/worker.py:16-22", "review/policy.md:10-13"],
     "A reachable second settlement after a persisted acceptance breaks the one-key contract, and policy says it blocks release even when another claim reports the same root cause. C01, C02 and C04 are one canonical defect."),
    ("C05", "lost_ack", "R-1904's lost-acknowledgement scenario emits a warning and recovers with only one provider settlement.", "supported", ["no_action"], "no", None,
     ["review/replay.py:71-73", "review/replay.py:28-33", "HEAD~1:refunds/provider.py:15-16", "HEAD~1:refunds/worker.py:18-26"],
     "The first create settles refund:1904 but returns timed_out, so the worker warns; the next same-key create returns the existing id. Timeout-as-unknown and the warning loop are unchanged from HEAD~1."),
    ("C06", "lost_ack", "R-1904's recovery is a second settlement that must be reversed.", "refuted", ["no_action"], "no", None,
     ["review/replay.py:28-33", "review/policy.md:11-12", "review/replay.md:13-15"],
     "The sandbox stores one settlement per key and both R-1904 calls use refund:1904. Policy defines a warning followed by one accepted settlement as recovery, so there is nothing extra to reverse."),
    ("C07", "lost_ack", "The recovered R-1904 job remains in an ambiguous terminal state.", "refuted", ["no_action"], "no", None,
     ["refunds/worker.py:23-32", "review/replay.py:71-73"],
     "The accepted retry records the provider id and returns settled. The loop returns unknown only after every permitted attempt lacks a definite outcome, which R-1904 does not reach."),
    ("C08", "lost_ack", "Expected recovery makes the warning severity too high for operators.", "underdetermined", ["no_action"], "no", None,
     ["review/policy.md:27-28", "refunds/worker.py:31", "HEAD~1:refunds/worker.py:26"],
     "No operator-impact evidence or severity standard is supplied, so the preference is neither established nor refuted. Policy says such a preference takes no action. The warning call is identical at HEAD~1, so HEAD did not introduce it."),
    ("C09", "conversion", "The conversion books a float product rounded to 435 cents for the 5.50 x 0.79 sample.", "refuted", ["no_action"], "no", None,
     ["refunds/money.py:4-7", "refunds/worker.py:9-12"],
     "Both inputs become Decimal before multiplication; exact 5.50 x 0.79 is 4.345, which half-even rounding books as 434 cents. create_refund passes that integer to the adapter."),
    ("C10", "conversion", "The sample's 4.345 intermediate display differs from the booked 4.34 amount.", "supported", ["no_action"], "no", None,
     ["refunds/money.py:4-11", "review/replay.py:83-84", "review/replay.md:18-19"],
     "display_amount formats the unquantized Decimal product as 4.345, while refund_cents quantizes it to 4.34. Both functions are unchanged from HEAD~1 and the difference is intentional."),
    ("C11", "conversion", "The surrounding conversion and provider-call code refute the rounding allegation.", "supported", ["no_action"], "no", None,
     ["refunds/money.py:4-7", "refunds/worker.py:9-12", "refunds/provider.py:8-9"],
     "Only the integer booked cents from refund_cents travel through create_refund and ProviderAdapter.create to the transport. No float or display value takes part in settlement."),
    ("C12", "conversion", "The display difference requires a conversion change before release.", "refuted", ["no_action"], "no", None,
     ["review/policy.md:16-18", "refunds/money.py:4-11"],
     "The booking converts before half-even rounding as policy requires. Policy says intermediate display precision does not justify changing a correct booking."),
    ("C13", "legacy_flag", "create_refund passes the pre-existing legacy flag, but the provider adapter ignores it.", "supported", ["defer"], "no", "D-LEGACY-FLAG",
     ["refunds/worker.py:6-12", "refunds/provider.py:8-9", "HEAD~1:refunds/worker.py:6-12"],
     "HEAD and HEAD~1 both pass legacy=LEGACY_REFUND_WINDOW. ProviderAdapter.create accepts legacy but forwards only key, cents and timeout_seconds to the transport, so the flag has no request effect."),
    ("C14", "legacy_flag", "HEAD introduces the ignored legacy compatibility argument.", "refuted", ["no_action"], "no", None,
     ["refunds/worker.py:6-12", "HEAD~1:refunds/worker.py:6-12", "HEAD~1:refunds/provider.py:8-9", "review/change.md:8"],
     "The constant, the create_refund call and the adapter signature are identical at HEAD~1; the change description says the argument is unchanged."),
    ("C15", "legacy_flag", "Leaving the ignored flag in place blocks this refund-retry release.", "refuted", ["no_action"], "no", None,
     ["review/policy.md:21-22"],
     "Policy assigns an unchanged ignored compatibility argument to later adapter cleanup and says it is not a blocker for this release."),
    ("C16", "legacy_flag", "The ignored flag is factual technical debt that can wait for adapter cleanup.", "supported", ["defer"], "no", "D-LEGACY-FLAG",
     ["refunds/provider.py:8-9", "review/policy.md:21-22"],
     "The argument is accepted but unused and predates HEAD; policy classifies it as deferable cleanup debt, distinct from the introduced settlement defect. Same canonical defect as C13."),
    ("C17", "reset_retry", "HEAD's enabled reset rollout reaches a new connection-reset retry from the worker entry point.", "supported", ["no_action"], "yes", None,
     ["config/rollout.py:2,9-10", "refunds/provider.py:17-18", "refunds/worker.py:23-25,35-38", "HEAD~1:config/rollout.py:2", "HEAD~1:refunds/provider.py:12-17"],
     "worker_entry calls settle_refund, whose loop calls provider_outcome; HEAD's new reset branch returns unknown while RESET_RETRY_ENABLED is True, so the next bounded attempt runs. At HEAD~1 the flag is False and resets are rejected."),
    ("C18", "reset_retry", "A connection-reset retry necessarily replaces the original provider idempotency key.", "refuted", ["no_action"], "no", None,
     ["refunds/worker.py:9-12,23-31", "review/replay.py:75-77"],
     "Without a stored id a reset stays in the ordinary loop, whose create_refund call passes no override and reuses refund['idempotency_key']. Only the stored-id branch builds a new key, a different state."),
    ("C19", "reset_retry", "The ordinary retry loop continues beyond the configured attempt limit.", "refuted", ["no_action"], "no", None,
     ["config/refunds.py:2", "refunds/worker.py:15,23,32", "review/replay.py:79-81"],
     "attempts defaults to the imported RETRY_LIMIT of 3, and range(1, attempts + 1) permits exactly three calls before returning unknown."),
    ("C20", "reset_retry", "The new reset branch should be removed because every unknown-outcome retry is unsafe.", "refuted", ["no_action"], "no", None,
     ["review/policy.md:25-27", "refunds/worker.py:23-32"],
     "Bounded same-key retries satisfy the unknown-outcome contract and recover lost acknowledgements; policy says a working bounded retry is no reason to remove recovery. The new-key redelivery is a separate defect."),
    ("C21", "timeout_sample", "The supplied operational sample identifies the refund, deadline, provider, and any later response.", "refuted", ["no_action"], "no", None,
     ["review/prod-context.md:5-8"],
     "The sample is one timeout line, and its note says no refund correlation, start, deadline, provider, revision or later response was retained."),
    ("C22", "timeout_sample", "The fictional timeout sample is insufficient to decide whether HEAD's five-second timeout introduced a regression.", "supported", ["investigate"], "unknown", "D-TIMEOUT-GAP",
     ["review/prod-context.md:5-8", "review/policy.md:31-33", "config/refunds.py:1"],
     "The five-second timeout is real, but policy requires timing, correlation and revision evidence the sample lacks before attributing a timeout. D-TIMEOUT-GAP is an evidence gap, not a proven defect."),
    ("C23", "timeout_sample", "HEAD's timeout reduction caused the sampled operational timeout.", "underdetermined", ["investigate"], "unknown", "D-TIMEOUT-GAP",
     ["config/refunds.py:1", "HEAD~1:config/refunds.py:1", "refunds/worker.py:12", "refunds/provider.py:9", "review/prod-context.md:5-8"],
     "HEAD lowers the applied timeout from 30 to 5 seconds and passes it to the transport, but nothing binds the sample to that request or revision. Neither causation nor its absence is established."),
    ("C24", "timeout_sample", "Request timing and deployment context must be obtained before attributing the sample to HEAD or changing timeout or retry behavior.", "supported", ["investigate"], "unknown", "D-TIMEOUT-GAP",
     ["review/policy.md:31-33", "review/prod-context.md:7-8"],
     "Policy names the start, applied deadline, provider, refund correlation, revision and later response as prerequisites, and the sample retains none of them."),
]


def _tree(head):
    provider = '''from config.rollout import reset_retry_enabled


class ProviderAdapter:
    def __init__(self, transport):
        self.transport = transport

    def create(self, idempotency_key, cents, *, timeout_seconds, legacy=False):
        return self.transport.create_refund(idempotency_key, cents, timeout_seconds=timeout_seconds)


def provider_outcome(response):
    if response.get("refund_id"):
        return "accepted"
    if response.get("timed_out"):
        return "unknown"
'''
    if head:
        provider += '''    if response.get("reset") and reset_retry_enabled():
        return "unknown"
'''
    provider += '''    return "rejected"
'''
    worker = '''from config.refunds import HTTP_TIMEOUT_SECONDS, RETRY_LIMIT
from config.rollout import refund_retry_enabled
from refunds.money import refund_cents
from refunds.provider import provider_outcome

LEGACY_REFUND_WINDOW = True


def create_refund(refund, provider, *, idempotency_key=None):
    cents = refund_cents(refund["amount"], refund["rate"])
    return provider.create(idempotency_key or refund["idempotency_key"], cents,
                           timeout_seconds=HTTP_TIMEOUT_SECONDS, legacy=LEGACY_REFUND_WINDOW)


def settle_refund(refund, provider, store, attempts=RETRY_LIMIT):
    if store.provider_refund_id(refund["id"]):
'''
    if head:
        worker += '''        response = create_refund(refund, provider, idempotency_key=refund["idempotency_key"] + ":retry")
        outcome = provider_outcome(response)
        if outcome == "accepted":
            store.record(refund["id"], response["refund_id"])
            return "settled"
        return outcome
'''
    else:
        worker += '''        return "settled"
'''
    worker += '''    for attempt in range(1, attempts + 1):
        response = create_refund(refund, provider)
        outcome = provider_outcome(response)
        if outcome == "accepted":
            store.record(refund["id"], response["refund_id"])
            return "settled"
        if outcome == "rejected":
            return "rejected"
        store.warning(refund["id"], "provider outcome unknown; retrying", attempt)
    return "unknown"


def worker_entry(refund, provider, store):
    if not refund_retry_enabled():
        return "disabled"
    return settle_refund(refund, provider, store)
'''
    return {
        "refunds/__init__.py": "",
        "config/__init__.py": "",
        "refunds/money.py": '''from decimal import Decimal, ROUND_HALF_EVEN


def refund_cents(amount, rate):
    converted = Decimal(amount) * Decimal(rate)
    booked = converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return int(booked * 100)


def display_amount(amount, rate):
    return format(Decimal(amount) * Decimal(rate), ".3f")
''',
        "refunds/provider.py": provider,
        "refunds/worker.py": worker,
        "config/refunds.py": f"HTTP_TIMEOUT_SECONDS = {5 if head else 30}\nRETRY_LIMIT = 3\n",
        "config/rollout.py": f'''REFUND_RETRY_ENABLED = True
RESET_RETRY_ENABLED = {head!r}


def refund_retry_enabled():
    return REFUND_RETRY_ENABLED


def reset_retry_enabled():
    return RESET_RETRY_ENABLED
''',
    }


REPLAY = '''"""Executable synthetic scenarios, not a saved or production replay.

Run from the fixture root: python3 -m review.replay
The sandbox implements provider-key deduplication and deliberately lost responses.
"""

import json

from refunds.money import display_amount, refund_cents
from refunds.provider import ProviderAdapter
from refunds.worker import create_refund, worker_entry


class SandboxTransport:
    def __init__(self, label, responses=()):
        self.label = label
        self.responses = iter(responses)
        self.settlements = {}
        self.calls = []

    def create_refund(self, key, cents, *, timeout_seconds):
        self.calls.append({"key": key, "cents": cents, "timeout_seconds": timeout_seconds})
        response = next(self.responses, "accepted")
        if response == "timeout":
            return {"timed_out": True}
        if response == "reset":
            return {"reset": True}
        if key not in self.settlements:
            refund_id = "pr_" + self.label + chr(ord("a") + len(self.settlements))
            self.settlements[key] = {"refund_id": refund_id, "cents": cents}
        if response == "lost_ack":
            return {"timed_out": True}
        return {"refund_id": self.settlements[key]["refund_id"]}


class RefundStore:
    def __init__(self):
        self.ids = {}
        self.warnings = []

    def provider_refund_id(self, refund_id):
        return self.ids.get(refund_id)

    def record(self, refund_id, provider_refund_id):
        self.ids[refund_id] = provider_refund_id

    def warning(self, refund_id, message, attempt):
        self.warnings.append({"refund_id": refund_id, "message": message, "attempt": attempt})


def refund(label):
    return {"id": "R-" + label, "idempotency_key": "refund:" + label,
            "amount": "5.50", "rate": "0.79"}


def snapshot(state, transport, store):
    return {"state": state, "calls": transport.calls,
            "settlements": transport.settlements, "warnings": store.warnings,
            "stored_ids": store.ids}


def scenarios():
    result = {}
    transport, store = SandboxTransport("1842"), RefundStore()
    provider, request = ProviderAdapter(transport), refund("1842")
    accepted = create_refund(request, provider)
    store.record(request["id"], accepted["refund_id"])
    state = worker_entry(request, provider, store)
    result["acknowledged_redelivery"] = snapshot(state, transport, store)

    transport, store = SandboxTransport("1904", ["lost_ack", "accepted"]), RefundStore()
    state = worker_entry(refund("1904"), ProviderAdapter(transport), store)
    result["lost_ack"] = snapshot(state, transport, store)

    transport, store = SandboxTransport("1910", ["reset", "accepted"]), RefundStore()
    state = worker_entry(refund("1910"), ProviderAdapter(transport), store)
    result["reset_rollout"] = snapshot(state, transport, store)

    transport, store = SandboxTransport("1911", ["timeout"] * 3), RefundStore()
    state = worker_entry(refund("1911"), ProviderAdapter(transport), store)
    result["retry_exhaustion"] = snapshot(state, transport, store)

    result["conversion"] = {"display": display_amount("5.50", "0.79"),
                            "booked_cents": refund_cents("5.50", "0.79")}
    return result


if __name__ == "__main__":
    print(json.dumps(scenarios(), indent=2))
'''


def public_files():
    claims = [{"id": cid, "group": group, "text": text} for cid, group, text, *_rest in CLAIMS]
    groups = [{"id": group, "claim_ids": [claim["id"] for claim in claims if claim["group"] == group]}
              for group in dict.fromkeys(claim["group"] for claim in claims)]
    return {
        **_tree(True),
        "review/__init__.py": "",
        "review/change.md": f"# {SUBJECT}\n\n{BODY}",
        "review/policy.md": POLICY,
        "review/rubric.json": json.dumps(RUBRIC, indent=2) + "\n",
        "review/claims.json": json.dumps({"claims": claims, "groups": groups}, indent=2) + "\n",
        "review/replay.py": REPLAY,
        "review/replay.md": """# Synthetic scenario chronology and expected outcomes

These are designed development scenarios, not captured executions or production records.
The executable recipe is review/replay.py. No replay, model evaluation, or production probe
is claimed by this document; expected outcomes follow the bundled source and provider contract.

## R-1842: acknowledged redelivery
1. The provider accepts key refund:1842 as pr_1842a; the store persists that accepted id.
2. Redelivery enters the stored-id path. At BASE it returns without another provider create.
3. At HEAD confirmation uses refund:1842:retry, creating pr_1842b: two keys, two settlements.

## R-1904: lost acknowledgement and recovery
1. The provider settles key refund:1904 but loses its acknowledgement; the worker warns.
2. The next create reuses refund:1904 and returns the same accepted id: one settlement.
3. BASE and HEAD both reach settled after this warning; no extra reversal is warranted.

## Conversion and unchanged adapter behavior
The sample has decimal inputs 5.50 and 0.79. Intermediate display is 4.345; booked cents are
434 (4.34). Only integer booked cents reach the provider. The legacy argument is accepted by
the adapter signature but does not reach the transport, at BASE or HEAD.

## Reset rollout and exhaustion
With HEAD's reset rollout enabled, a reset then acceptance reaches settled on the second
same-key call. BASE rejects a reset. Three unresolved timeouts reach unknown with exactly
three calls. BASE sends timeout_seconds=30; HEAD sends timeout_seconds=5 on each request.

## Operational sample limitations
review/prod-context.md is a separate fictional sample with incomplete provenance. It cannot
supply real deployment, timing, or later-response evidence for these development scenarios.
""",
        "review/prod-context.md": """# Fictional operational sample

Sanitized development input, not collected production evidence:

`2026-09-29T08:40:00Z WARN provider request timeout`

No refund correlation, request start, applied deadline, provider identity, deployed revision,
or later response was retained. Its relationship to the candidate deployment is unknown.
""",
        "review/README.md": """# Refund/retry review materials

Everything here is synthetic development material, not observed production evidence.
`git show HEAD` is the change under review; `git show HEAD~1:<path>` gives BASE source.
change.md describes the change, policy.md is the operating policy, rubric.json defines the
factual, action and introduced questions, replay.md and replay.py describe and reproduce
synthetic provider scenarios (`python3 -m review.replay` from the repository root), and
prod-context.md is an incomplete operational sample. claims.json lists 24 review claims
grouped into six episodes; episode membership is not a verdict.
""",
    }


def build(root):
    root = Path(root)
    public, head = public_files(), _tree(True)
    base = {**{path: text for path, text in public.items() if path not in head}, **_tree(False)}
    _write(root, base)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "feat(refunds): provider adapter with bounded timeout recovery, review materials")
    _write(root, head)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", f"{SUBJECT}\n\n{BODY}")
    return root


def _write(root, files):
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)


def _git(root, *args):
    subprocess.run(["git", "-c", "user.name=jevify-eval", "-c", "user.email=eval@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
                   cwd=root, check=True, capture_output=True, text=True)


def answer_keys():
    items = []
    for cid, group, text, factual, actions, introduced, defect, evidence, rationale in CLAIMS:
        items.append({"id": cid, "desc": f"review claim {cid} ({group}): {text}", "factual": factual,
                      "actions": actions, "introduced": introduced, "defect": defect, "group": group,
                      "evidence": evidence, "rationale": rationale})
    return {"schema": 2, "question": "For each refund/retry claim, report factual status, warranted action, and whether HEAD introduced the behavior.", "items": items}


def review_units(grouped=False):
    public = public_files()
    claims = json.loads(public["review/claims.json"])
    context = {"base": _tree(False), "head": _tree(True),
               "review": {path: text for path, text in public.items()
                          if path.startswith("review/") and path != "review/claims.json"}}
    claim_by_id = {claim["id"]: claim for claim in claims["claims"]}
    if grouped:
        return [{"id": group["id"], "claims": [claim_by_id[cid] for cid in group["claim_ids"]], "context": context}
                for group in claims["groups"]]
    return [{"id": claim["id"], "claims": [claim], "context": context} for claim in claims["claims"]]
