"""Strict main-test multiplicity and skip ledger; never converts failed logs to PASS."""

import ast
import collections
import re


class SuiteError(ValueError):
    pass


def need(v, m):
    if not v:
        raise SuiteError(m)


def validate_ledger(d):
    need(
        type(d) is dict
        and set(d) == {"schema", "occurrences", "skips", "capabilities"},
        "ledger fields",
    )
    need(d["schema"] == "qualification-suite-ledger/v1", "ledger schema")
    need(
        type(d["occurrences"]) is list
        and d["occurrences"]
        and all(
            type(x) is str and re.fullmatch(r"[A-Za-z0-9_.]+", x)
            for x in d["occurrences"]
        ),
        "ledger occurrences",
    )
    need(
        type(d["skips"]) is list
        and all(
            type(x) is dict
            and set(x) == {"id", "reason"}
            and type(x["id"]) is str
            and type(x["reason"]) is str
            for x in d["skips"]
        ),
        "skip ledger",
    )
    c = d["capabilities"]
    need(
        type(c) is dict
        and set(c) == {"supports_no_follow", "platform", "python_sha256"}
        and type(c["supports_no_follow"]) is bool
        and type(c["platform"]) is str
        and type(c["python_sha256"]) is str
        and re.fullmatch("[0-9a-f]{64}", c["python_sha256"]),
        "capability profile",
    )
    need(
        not (
            collections.Counter(x["id"] for x in d["skips"])
            - collections.Counter(d["occurrences"])
        ),
        "skip IDs outside inventory",
    )
    return d


def inspect(raw, ledger, require_success=True):
    ledger = validate_ledger(ledger)
    text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
    pattern = r"^test[^ ]+ \(([A-Za-z0-9_.]+)\)(?: \.\.\.|$)"
    ids = re.findall(pattern, text, re.M)
    need(
        collections.Counter(ids) == collections.Counter(ledger["occurrences"]),
        "exact test occurrence mismatch",
    )
    summaries = re.findall(r"^Ran (\d+) tests? in [^\n]+$", text, re.M)
    need(summaries == [str(len(ids))], "single exact summary")
    skips = []
    for m in re.finditer(
        r"^test[^ ]+ \(([A-Za-z0-9_.]+)\) \.\.\. skipped (.+)$", text, re.M
    ):
        try:
            reason = ast.literal_eval(m.group(2))
        except (ValueError, SyntaxError):
            raise SuiteError("skip reason encoding") from None
        need(type(reason) is str, "skip reason type")
        skips.append((m.group(1), reason))
    need(
        collections.Counter(skips)
        == collections.Counter((x["id"], x["reason"]) for x in ledger["skips"]),
        "exact skip identity/reason mismatch",
    )
    failures = re.findall(r"^(ERROR|FAIL): (.+)$", text, re.M)
    ending = "OK" if not skips else "OK (skipped=" + str(len(skips)) + ")"
    successful = not failures and text.rstrip().endswith(ending)
    if require_success:
        need(successful, "test suite failed")
        matches = list(re.finditer(pattern, text, re.M))
        for i, match in enumerate(matches):
            block = text[
                match.start() : matches[i + 1].start()
                if i + 1 < len(matches)
                else text.index("Ran " + str(len(ids)) + " tests")
            ]
            outcomes = re.findall(
                r"(?:\.\.\. (ok|skipped .+)|^(ok|skipped .+))$", block, re.M
            )
            need(len(outcomes) == 1, "missing/conflicting terminal outcome")
    return {
        "tests": len(ids),
        "unique": len(set(ids)),
        "skips": len(skips),
        "failures": failures,
        "success": successful,
    }


def receipt(raw, ledger):
    import hashlib
    import json

    def pairs(items):
        d = {}
        for k, v in items:
            need(k not in d, "duplicate receipt key")
            d[k] = v
        return d

    d = json.loads(raw, object_pairs_hook=pairs)
    validate_ledger(ledger)
    need(
        set(d) == {"schema", "ledger_sha256", "tests", "events", "success"}
        and d["schema"] == "qualification-suite-result/v1",
        "suite receipt shape",
    )
    need(
        d["ledger_sha256"]
        == hashlib.sha256(
            json.dumps(ledger, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "receipt ledger identity",
    )
    need(
        d["success"] is True
        and type(d["tests"]) is int
        and d["tests"] == len(ledger["occurrences"]),
        "receipt outcome",
    )
    events = d["events"]
    need(type(events) is list, "receipt events")
    for e in events:
        need(
            type(e) is dict and e.get("outcome") in ("ok", "skip"),
            "failed or malformed event",
        )
        need(
            set(e)
            == (
                {"id", "outcome", "reason"}
                if e["outcome"] == "skip"
                else {"id", "outcome"}
            ),
            "event fields",
        )
    need(
        collections.Counter(e["id"] for e in events)
        == collections.Counter(ledger["occurrences"]),
        "receipt exact occurrences",
    )
    need(
        collections.Counter(
            (e["id"], e["reason"]) for e in events if e["outcome"] == "skip"
        )
        == collections.Counter((x["id"], x["reason"]) for x in ledger["skips"]),
        "receipt skip multiset",
    )
    return d


def profile(support):
    import hashlib
    from pathlib import Path
    import sys

    return {
        "supports_no_follow": support,
        "platform": sys.platform,
        "python_sha256": hashlib.sha256(
            Path(sys.executable).resolve().read_bytes()
        ).hexdigest(),
    }
