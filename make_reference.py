#!/usr/bin/env python3
"""make_reference: build the independent reference record for one OpenClaw run.

    python3 make_reference.py <events.jsonl> --out reference.json [--annotations annotations.json]

Why this is independent of tracegraph.py
  * Separate code: this file imports nothing from tracegraph.py.
  * Different stream: it reads only events with "source": "transcript" (the session
    transcript OpenClaw writes for the conversation). tracegraph.py reads only the
    "runtime" stream (the tool executor's own events).
  * Nested code-mode calls (tools called from inside `exec`) never appear in the transcript.
    Two things cover them:
      1. telemetry.callCount inside each transcript `exec` result. OpenClaw keeps a running
         count of catalog tool calls per session; the increase across one exec is the number
         of nested calls it made. That count comes from the transcript, not the runtime stream.
      2. annotations.json: nested calls (ID, tool, file, outcome, event lines) that both team
         members confirmed by reading the raw events.jsonl.

Output: reference.json with
  calls[]          call_id, tool, target, status, parent, call_seq, result_seq, order, evidence, origin
  nested_counts{}  parent call ID -> number of nested calls reported by transcript telemetry
Standard library only.
"""
import argparse
import json
import re
import sys


def read_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            rec["__line"] = n
            out.append(rec)
    return out


def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    return ""


def transcript_outcome(msg):
    """Outcome of a transcript toolResult message: success / error / blocked / unknown."""
    text = content_text(msg.get("content"))
    if "isError" not in msg:
        return "unknown"
    if msg["isError"]:
        if re.search(r"^Tool \S+ not found", text, re.MULTILINE):
            return "blocked"          # runtime refused a tool that is not available to the model
        return "error"
    if re.search(r'"status"\s*:\s*"failed"', text):
        return "error"
    return "success"


def telemetry(msg):
    """(counterScope, callCount) from a code-mode exec result, or (None, None)."""
    text = content_text(msg.get("content"))
    try:
        obj = json.loads(text)
    except ValueError:
        return None, None
    t = obj.get("telemetry") if isinstance(obj, dict) else None
    if not isinstance(t, dict) or "callCount" not in t:
        return None, None
    return t.get("counterScope"), t.get("callCount")


def nested_parent(call_id):
    m = re.match(r"^tool_call:([^:]+):[^:]+:\d+$", call_id or "")
    return m.group(1) if m else None


def target_of(args):
    if isinstance(args, dict):
        v = args.get("path") or args.get("file")
        if isinstance(v, str) and v:
            return v
    return None


def from_transcript(records):
    calls, nested_counts, last_count = {}, {}, {}
    for rec in records:
        if rec.get("source") != "transcript" or rec.get("type") not in ("tool.call", "tool.result"):
            continue
        d = rec.get("data") or {}
        if rec["type"] == "tool.call":
            cid, name = d.get("toolCallId"), d.get("name")
        else:
            msg = d.get("message") or {}
            cid, name = msg.get("toolCallId"), msg.get("toolName")
        if not cid:
            continue
        c = calls.setdefault(cid, {"call_id": cid, "tool": name or "unknown", "target": None,
                                   "status": "unknown", "parent": nested_parent(cid),
                                   "call_seq": None, "result_seq": None, "evidence": [],
                                   "origin": "transcript"})
        c["evidence"].append(f"events.jsonl line {rec['__line']} (transcript {rec['type']})")
        if rec["type"] == "tool.call":
            c["call_seq"] = rec.get("seq")
            c["target"] = target_of(d.get("arguments"))
        else:
            c["result_seq"] = rec.get("seq")
            c["status"] = transcript_outcome(msg)
            scope, count = telemetry(msg)
            if count is not None:
                nested_counts[cid] = count - last_count.get(scope, 0)
                last_count[scope] = count
    return calls, nested_counts


def apply_annotations(calls, ann):
    for entry in ann.get("calls", []):
        cid = entry["call_id"]
        base = calls.get(cid, {"call_id": cid, "parent": nested_parent(cid), "evidence": [],
                               "target": None, "status": "unknown"})
        origin = "manual review" if cid not in calls else "transcript + manual review"
        base.update({k: v for k, v in entry.items() if k != "evidence"})
        base["origin"] = origin
        base["evidence"] = base.get("evidence", []) + [entry.get("evidence", "manual review")]
        calls[cid] = base
    for cid in ann.get("remove", []):
        calls.pop(cid, None)
    return calls


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("events")
    ap.add_argument("--out", required=True)
    ap.add_argument("--annotations", help="team-reviewed nested calls and corrections (JSON)")
    a = ap.parse_args(argv)

    records = read_jsonl(a.events)
    calls, nested_counts = from_transcript(records)
    session_keys = sorted({r.get("sessionKey") for r in records
                           if r.get("source") == "transcript" and r.get("sessionKey")})
    sources = ["OpenClaw transcript stream (tool.call / tool.result, telemetry.callCount)"]
    reviewers = []
    if a.annotations:
        ann = json.load(open(a.annotations, encoding="utf-8"))
        calls = apply_annotations(calls, ann)
        reviewers = ann.get("reviewed_by", [])
        sources.append("manual annotation of nested calls reviewed by " + (", ".join(reviewers) or "team"))

    ordered = sorted(calls.values(), key=lambda c: (c.get("call_seq") is None, c.get("call_seq") or 0))
    for i, c in enumerate(ordered, 1):
        c["order"] = i
    ref = {"source": " + ".join(sources), "reviewed_by": reviewers,
           "session_key": session_keys[0] if len(session_keys) == 1 else (session_keys or "unknown"),
           "nested_counts": nested_counts, "calls": ordered}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(ref, fh, indent=2)
    n_nested = sum(1 for c in ordered if c.get("parent"))
    print(f"reference: {len(ordered)} calls ({n_nested} nested, telemetry reports "
          f"{sum(nested_counts.values())}) -> {a.out}")
    unknown = [c["call_id"] for c in ordered if c.get("status") == "unknown"]
    if unknown:
        print(f"note: outcome unknown in reference for {len(unknown)} call(s): {', '.join(unknown)}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
