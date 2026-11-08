#!/usr/bin/env python3
"""make_summary: one table comparing every processed run (reads outputs/*/validation.json).

    python3 make_summary.py            -> writes outputs/summary.md and prints it
"""
import glob
import json
import os

ORDER = ["M0", "M1", "M2", "F1", "F2"]
rows = []
for path in glob.glob("outputs/*/validation.json"):
    case = os.path.basename(os.path.dirname(path))
    v = json.load(open(path, encoding="utf-8"))
    c, e, s = v["counts"], v["edges_by_type"], v["status_counts"]
    rows.append((ORDER.index(case) if case in ORDER else 99, case, c, e, s, v))
rows.sort()
L = ["# Results across runs", "",
     "| Run | Ref. calls | Graph nodes | Matched | Missing | Extra | Ref. edges | Correct edges | Incorrect edges "
     "| Node P / R | Edge P / R | Success / error / blocked | Edges next / overlaps / parent / retry | Result |",
     "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
for _, case, c, e, s, v in rows:
    L.append(f"| {case} | {c['reference_calls']} | {c['graph_nodes']} | {c['matched_calls']} | {c['missing_calls']} "
             f"| {c['extra_calls']} | {c['reference_edges']} | {c['correct_edges']} | {c['incorrect_edges']} "
             f"| {v['node_precision']} / {v['node_recall']} | {v['edge_precision']} / {v['edge_recall']} "
             f"| {s['success']} / {s['error']} / {s['blocked']} | {e['next']} / {e['overlaps']} / {e['parent']} / "
             f"{e['retry']} | {'PASS' if v['passed'] else 'FAIL'} |")
text = "\n".join(L) + "\n"
open("outputs/summary.md", "w", encoding="utf-8").write(text)
print(text)
