#!/usr/bin/env python3
"""tracegraph: turn one saved OpenClaw run into a tool-call graph and check it.

One command per run:

    python3 tracegraph.py process <events.jsonl> --reference <reference.json> --out <dir> --run <label>

Outputs in <dir>:
    graph.json        machine-readable graph (run context, nodes, edges, edge meanings)
    graph.dot         Graphviz source of the same graph
    graph.svg         readable picture (drawn by this script; Graphviz not needed)
    graph.png         PNG of graph.svg when cairosvg or rsvg-convert is available
    validation.json   comparison with the independent reference record
    validation.md     the same comparison as a readable report

Other commands:
    validate <graph.json> <reference.json>     re-check a graph; exit code 1 if it fails
    mutate   <graph.json> <reference.json> --out <file.md>
                                              damage copies of the graph on purpose and show
                                              that the validator catches every change

The graph is built only from events with "source": "runtime". The reference record is built
by make_reference.py from the "transcript" stream plus a team-reviewed annotation file, and
is read only after the graph is finished. Standard library only (Python 3.8+).
"""
import argparse
import copy
import html
import json
import os
import re
import shutil
import subprocess
import sys

SCHEMA = "tracegraph/2"
UNKNOWN = "unknown"

EDGE_SEMANTICS = {
    "next": "Next observed call. Among calls with the same parent (or among top-level calls), the "
            "target's tool.call is the next one in event order AND it started after the source "
            "returned. Order only; it does not claim the source caused the target.",
    "overlaps": "Started while the source was still running (target's tool.call comes before the "
                "source's tool.result). Start order is shown, but the calls ran concurrently, so no "
                "completion order is claimed.",
    "parent": "Parent/child. The target was issued from inside the source (OpenClaw code mode). "
              "Established by the child call ID 'tool_call:<parent>:<tool>:<n>'.",
    "retry": "Retry. The target calls the same tool on the same file as the source, the source "
             "ended in error or blocked, and the target is the next call on that tool and file. "
             "Only drawn for calls whose arguments name a file (exec code is masked, so exec calls "
             "never get retry edges).",
}

BLOCKED_PATTERNS = [r"^Tool \S+ not found", r"\bnot allowed\b", r"\bdenied\b", r"\bnot permitted\b",
                    r"\brequires approval\b"]


# =================================================================== parsing
def load_events(path):
    events = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                print(f"warning: {path}:{lineno} is not valid JSON; skipped", file=sys.stderr)
                continue
            ev["_line"] = lineno
            events.append(ev)
    return events


def result_text(result):
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        parts = [str(i["text"]) for i in (result.get("content") or []) if isinstance(i, dict) and "text" in i]
        if parts:
            return "\n".join(parts)
    return json.dumps(result, ensure_ascii=False)


def classify(success, text):
    """success / error / blocked / unknown for one runtime tool.result."""
    if success is True:
        if re.search(r'"status"\s*:\s*"failed"', text):     # exec wrapper reports inner failure as text
            return "error"
        return "success"
    if success is False:
        for pat in BLOCKED_PATTERNS:
            if re.search(pat, text, re.IGNORECASE | re.MULTILINE):
                return "blocked"
        return "error"
    return UNKNOWN


def short_detail(text):
    m = re.search(r'"code"\s*:\s*"([a-z_]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r"(Tool \S+ not found|File not found|ENOENT|No such file)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    if "Malformed diagnostic" in text:
        return "message redacted"
    return ""


def parent_of(call_id):
    """'tool_call:call_103245:read:1' -> 'call_103245'; top-level IDs -> None."""
    if call_id and call_id.startswith("tool_call:"):
        parts = call_id.split(":")
        if len(parts) >= 4:
            return parts[1]
    return None


def norm_target(path):
    if not isinstance(path, str) or not path:
        return None
    p = path.replace("$WORKSPACE_DIR/", "").replace("$OPENCLAW_STATE_DIR/workspace/", "")
    return p[2:] if p.startswith("./") else p


def args_info(args):
    """(label, file target) from tool arguments; exec code is masked by OpenClaw, so only its title is used."""
    if not isinstance(args, dict):
        return "", None
    target = args.get("path") or args.get("file")
    label = args.get("title") or target or args.get("query") or args.get("command") or ""
    if label == "***":
        label = ""
    return str(label), target if isinstance(target, str) else None


def message_text(msg):
    content = (msg or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def run_context(events, bundle_dir=None):
    """User question and final assistant answer for the caption; 'unknown' when not recorded."""
    question = answer = None
    for ev in events:
        if ev.get("type") == "prompt.submitted" and isinstance((ev.get("data") or {}).get("prompt"), str):
            question = ev["data"]["prompt"]
            break
    if not question:
        for ev in events:
            if ev.get("type") == "user.message":
                question = message_text((ev.get("data") or {}).get("message")) or None
                if question:
                    break
    for ev in reversed(events):
        if ev.get("type") == "assistant.message":
            txt = message_text((ev.get("data") or {}).get("message")).strip()
            if txt:
                answer = txt
                break
    if not question and bundle_dir and os.path.exists(os.path.join(bundle_dir, "prompts.json")):
        try:
            question = json.load(open(os.path.join(bundle_dir, "prompts.json"), encoding="utf-8")).get(
                "latestSubmittedPrompt")
        except (OSError, ValueError, AttributeError):
            pass
    return question or UNKNOWN, answer or UNKNOWN


# =================================================================== graph
def build_graph(events, events_path="events.jsonl", run_label=None):
    calls, meta = {}, {"session_id": None, "session_key": None, "run_ids": [], "provider": None, "model": None}
    evname = os.path.basename(events_path)
    for ev in events:
        if ev.get("source") != "runtime":
            continue
        for src, dst in (("sessionId", "session_id"), ("sessionKey", "session_key"),
                         ("provider", "provider"), ("modelId", "model")):
            if ev.get(src) and not meta[dst]:
                meta[dst] = ev[src]
        if ev.get("runId") and ev["runId"] not in meta["run_ids"]:
            meta["run_ids"].append(ev["runId"])
        typ, data = ev.get("type"), ev.get("data") or {}
        cid = data.get("toolCallId")
        if typ not in ("tool.call", "tool.result") or not cid:
            continue
        n = calls.get(cid)
        if n is None:
            n = calls[cid] = {"id": cid, "tool": data.get("name") or UNKNOWN, "parent": parent_of(cid),
                              "label": "", "target": None, "call_seq": UNKNOWN, "call_ts": UNKNOWN,
                              "result_seq": UNKNOWN, "result_ts": UNKNOWN, "status": UNKNOWN,
                              "detail": "", "evidence": []}
        n["evidence"].append(f"{evname} line {ev['_line']} (seq {ev.get('seq', UNKNOWN)}, runtime {typ})")
        if typ == "tool.call":
            n["call_seq"], n["call_ts"] = ev.get("seq", UNKNOWN), ev.get("ts", UNKNOWN)
            n["label"], n["target"] = args_info(data.get("args"))
            if data.get("name"):
                n["tool"] = data["name"]
        else:
            text = result_text(data.get("result"))
            n["result_seq"], n["result_ts"] = ev.get("seq", UNKNOWN), ev.get("ts", UNKNOWN)
            n["status"] = classify(data.get("success"), text)
            n["detail"] = short_detail(text) if n["status"] != "success" else ""

    def seq_key(n):
        s = n["call_seq"] if n["call_seq"] != UNKNOWN else n["result_seq"]
        return s if isinstance(s, int) else 10 ** 9

    nodes = sorted(calls.values(), key=seq_key)
    seen = {}
    for i, n in enumerate(nodes, 1):
        n["order"] = i
        n["run"] = run_label or UNKNOWN
        n["session_key"] = meta["session_key"] or UNKNOWN
        key = (n["tool"], norm_target(n["target"])) if n["target"] else None
        if key:
            seen[key] = seen.get(key, 0) + 1
            n["repeat"] = seen[key]            # 2 = second call to this tool on this file
        else:
            n["repeat"] = None
        if n["call_seq"] == UNKNOWN:
            n["detail"] = (n["detail"] + " (no tool.call event recorded)").strip()
        if n["target"] is None:
            n["target"] = UNKNOWN if n["tool"] != "exec" else None
    edges = compute_edges([{"id": n["id"], "tool": n["tool"], "parent": n["parent"], "target": n["target"],
                            "status": n["status"], "call_seq": n["call_seq"], "result_seq": n["result_seq"]}
                           for n in nodes])
    question, answer = run_context(events, os.path.dirname(os.path.abspath(events_path)))
    return {"schema": SCHEMA, "run": run_label or meta["session_key"] or UNKNOWN,
            "trace_boundary": "one exported OpenClaw session containing exactly one user prompt",
            "session": meta, "question": question, "answer": answer,
            "edge_semantics": EDGE_SEMANTICS, "nodes": nodes, "edges": edges}


def compute_edges(calls):
    """Edges from a list of calls (dicts with id, tool, parent, target, status, call_seq, result_seq).

    Shared by the graph builder and the validator so both apply the same declared rules;
    the validator feeds it the reference record's calls, not the graph's.
    """
    def num(v):
        return v if isinstance(v, int) else None

    calls = sorted(calls, key=lambda c: num(c.get("call_seq")) if num(c.get("call_seq")) is not None else 10 ** 9)
    ids = {c["id"] for c in calls}
    edges, groups = [], {}
    for c in calls:
        groups.setdefault(c.get("parent"), []).append(c)
    for parent, members in groups.items():
        for a, b in zip(members, members[1:]):
            a_end, b_start = num(a.get("result_seq")), num(b.get("call_seq"))
            kind = "overlaps" if (a_end is not None and b_start is not None and b_start < a_end) else "next"
            edges.append({"source": a["id"], "target": b["id"], "type": kind})
        if parent is not None and parent in ids:
            for m in members:
                edges.append({"source": parent, "target": m["id"], "type": "parent"})
    last = {}
    for c in calls:
        t = norm_target(c.get("target")) if c.get("target") not in (None, UNKNOWN) else None
        if not t:
            continue
        key = (c["tool"], t)
        prev = last.get(key)
        if prev is not None and prev.get("status") in ("error", "blocked"):
            edges.append({"source": prev["id"], "target": c["id"], "type": "retry"})
        last[key] = c
    return edges


# =================================================================== rendering
STATUS_STYLE = {"success": ("#E6F4EC", "#23824A"), "error": ("#FBE6E6", "#C43B3B"),
                "blocked": ("#FFF2D9", "#A86A0B"), UNKNOWN: ("#EEF0F4", "#5B6B85")}
EDGE_STYLE = {"next": ("#1C2A44", "", "next"), "overlaps": ("#1C2A44", "2 3", "overlaps"),
              "parent": ("#5B6B85", "6 4", "parent"), "retry": ("#B04A9A", "", "retry")}


def trim(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def esc(s):
    return html.escape(str(s), quote=True)


def short_target(t):
    t = norm_target(t) if t and t != UNKNOWN else None
    return os.path.basename(t) if t else None


def node_lines(n):
    st = n["status"] + (f" ({n['detail']})" if n["detail"] else "")
    what = short_target(n["target"]) or n["label"] or "-"
    if n.get("repeat") and n["repeat"] > 1:
        what += f"  (call #{n['repeat']} on this file)"
    return [f"C{n['order']}: {n['tool']}", n["id"], what, st]


def to_dot(g):
    out = ["digraph tooltrace {", '  rankdir=TB; newrank=true; fontname="Helvetica";',
           '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];',
           f'  label="{dot_esc(g["run"] + ": " + trim(g["question"], 120))}\\nAnswer: '
           f'{dot_esc(trim(g["answer"], 140))}"; labelloc=t;']
    for n in g["nodes"]:
        fill, line = STATUS_STYLE.get(n["status"], STATUS_STYLE[UNKNOWN])
        out.append(f'  "{n["id"]}" [label="{dot_esc(chr(10).join(node_lines(n)))}", fillcolor="{fill}", color="{line}"];')
    tops = [n["id"] for n in g["nodes"] if n["parent"] is None]
    for t in tops:
        kids = [n["id"] for n in g["nodes"] if n["parent"] == t]
        if kids:
            out.append("  { rank=same; " + " ".join(f'"{i}"' for i in [t] + kids) + " }")
    for e in g["edges"]:
        color, dash, label = EDGE_STYLE[e["type"]]
        style = "dashed" if e["type"] == "parent" else ("dotted" if e["type"] == "overlaps" else "solid")
        out.append(f'  "{e["source"]}" -> "{e["target"]}" [label="{label}", color="{color}", style={style}, fontsize=9];')
    if not g["nodes"]:
        out.append('  none [label="No tool-call nodes", shape=plaintext];')
    out.append("}")
    return "\n".join(out) + "\n"


def dot_esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def wrap(text, width):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def to_svg(g):
    """Each top-level call is a row (top to bottom); its nested calls sit to its right."""
    W, H, HG, VG, X0 = 250, 78, 70, 54, 24
    tops = [n for n in g["nodes"] if n["parent"] is None]
    kids = {}
    for n in g["nodes"]:
        if n["parent"] is not None:
            kids.setdefault(n["parent"], []).append(n)
    orphans = [n for n in g["nodes"] if n["parent"] is not None and n["parent"] not in {t["id"] for t in tops}]
    plain = re.sub(r"[*`]|\$\\le\s*([^$]*)\$", lambda m: "<= " + m.group(1) if m.group(1) else "", g["answer"])
    q_lines = wrap("Question: " + g["question"], 125)[:3]
    a_lines = wrap("Answer: " + plain, 125)[:5]
    y0 = 52 + 17 * (len(q_lines) + len(a_lines)) + 22
    pos, y = {}, y0
    max_cols = 1
    for t in tops:
        pos[t["id"]] = (X0, y)
        for j, c in enumerate(kids.get(t["id"], [])):
            pos[c["id"]] = (X0 + (j + 1) * (W + HG), y + 22)
            max_cols = max(max_cols, j + 2)
        y += H + 22 + VG if kids.get(t["id"]) else H + VG
    for j, c in enumerate(orphans):
        pos[c["id"]] = (X0 + (j + 1) * (W + HG), y)
        max_cols = max(max_cols, j + 2)
    if orphans:
        y += H + VG
    width = max(X0 * 2 + max_cols * W + (max_cols - 1) * HG + 40, 900)
    legend_y = (y if g["nodes"] else y0 + 70) + 6
    height = legend_y + 66
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
         f'height="{height}" font-family="Helvetica, Arial, sans-serif">', "<defs>"]
    for k, (color, _, _) in EDGE_STYLE.items():
        o.append(f'<marker id="m-{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                 f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{color}"/></marker>')
    o.append("</defs>")
    o.append(f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>')
    o.append(f'<text x="{X0}" y="30" font-size="18" font-weight="700" fill="#0F1A2E">{esc(g["run"])}: '
             f'{len(g["nodes"])} tool-call node(s)  ·  {esc(g["session"].get("session_key") or UNKNOWN)}</text>')
    ty = 52
    for i, line in enumerate(q_lines + a_lines):
        o.append(f'<text x="{X0}" y="{ty}" font-size="13" fill="#1C2A44"'
                 f'{" font-weight=" + chr(34) + "700" + chr(34) if i == 0 else ""}>{esc(line)}</text>')
        ty += 17

    def anchor(nid, side):
        x, yy = pos[nid]
        return {"r": (x + W, yy + H / 2), "l": (x, yy + H / 2), "b": (x + W / 2, yy + H),
                "t": (x + W / 2, yy), "bl": (x + 40, yy + H), "tl": (x + 40, yy)}[side]

    labelled = set()
    for e in g["edges"]:
        if e["source"] not in pos or e["target"] not in pos:
            continue
        color, dash, label = EDGE_STYLE[e["type"]]
        (sx, sy), (tx, ty_) = pos[e["source"]], pos[e["target"]]
        d_attr = f' stroke-dasharray="{dash}"' if dash else ""
        if e["type"] == "parent":
            x1, y1 = sx + W, sy + 18
            x2, y2 = tx + W / 2, ty_
            path = f"M{x1},{y1} C{x1 + 40},{y1 - 34} {x2},{y2 - 40} {x2},{y2}"
            lx, ly = (x1 + x2) / 2, y1 - 22
        elif e["type"] in ("next", "overlaps") and sy == ty_:
            (x1, y1), (x2, y2) = anchor(e["source"], "r"), anchor(e["target"], "l")
            path = f"M{x1},{y1 + 10} L{x2},{y2 + 10}"
            lx, ly = (x1 + x2) / 2, y1 + 4
        elif e["type"] in ("next", "overlaps"):
            (x1, y1), (x2, y2) = anchor(e["source"], "bl"), anchor(e["target"], "tl")
            path = f"M{x1},{y1} L{x2},{y2}"
            lx, ly = x1 + 22, (y1 + y2) / 2 + 4
        else:  # retry: curve along the right-hand side of both boxes
            x1, y1 = sx + W - 30, sy + H
            x2, y2 = tx + W - 30, ty_
            bend = 60 if tx == sx else 0
            path = f"M{x1},{y1} C{x1 + bend + 40},{y1 + 40} {x2 + bend + 40},{y2 - 40} {x2},{y2}"
            lx, ly = (x1 + x2) / 2 + bend / 2 + 30, (y1 + y2) / 2
        o.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.7"{d_attr} '
                 f'marker-end="url(#m-{e["type"]})"/>')
        if e["type"] == "parent":
            if e["source"] in labelled:
                continue
            labelled.add(e["source"])
            lx = x1 + 46
        o.append(f'<text x="{lx}" y="{ly}" font-size="11" font-style="italic" text-anchor="middle" '
                 f'fill="{color}">{label}</text>')
    for n in g["nodes"]:
        x, yy = pos[n["id"]]
        fill, line = STATUS_STYLE.get(n["status"], STATUS_STYLE[UNKNOWN])
        l1, l2, l3, l4 = node_lines(n)
        o.append(f'<rect x="{x}" y="{yy}" width="{W}" height="{H}" rx="9" fill="{fill}" stroke="{line}" stroke-width="1.8"/>')
        o.append(f'<text x="{x + 10}" y="{yy + 19}" font-size="14" font-weight="700" fill="#0F1A2E">{esc(l1)}</text>')
        o.append(f'<text x="{x + 10}" y="{yy + 36}" font-size="10" fill="#3A4A66">{esc(trim(l2, 42))}</text>')
        o.append(f'<text x="{x + 10}" y="{yy + 52}" font-size="11" fill="#1C2A44">{esc(trim(l3, 40))}</text>')
        o.append(f'<text x="{x + 10}" y="{yy + 69}" font-size="11.5" font-weight="700" fill="{line}">{esc(trim(l4, 38))}</text>')
    if not g["nodes"]:
        o.append(f'<rect x="{X0}" y="{y0}" width="560" height="54" rx="9" fill="#F5F6F8" stroke="#9AA5B8" stroke-dasharray="5 4"/>')
        o.append(f'<text x="{X0 + 16}" y="{y0 + 33}" font-size="15" fill="#3A4A66">No tool-call nodes: '
                 f'the run contains no tool.call events.</text>')
    o.append(f'<text x="{X0}" y="{legend_y + 14}" font-size="11.5" fill="#3A4A66">Boxes: green = success, '
             f'red = error, amber = blocked (tool refused), grey = unknown. C# = order of the call\'s start event.</text>')
    o.append(f'<text x="{X0}" y="{legend_y + 32}" font-size="11.5" fill="#3A4A66">Arrows: next = next observed '
             f'call (order, not cause) · overlaps (dotted) = started before the previous call returned</text>')
    o.append(f'<text x="{X0}" y="{legend_y + 50}" font-size="11.5" fill="#3A4A66">parent (dashed) = called from '
             f'inside that exec · retry (purple) = same tool and file again after a failure</text>')
    o.append("</svg>")
    return "\n".join(o) + "\n"


def write_png(svg_path, png_path):
    try:
        import cairosvg  # optional
        cairosvg.svg2png(url=svg_path, write_to=png_path, output_width=None, scale=1.6)
        return True
    except Exception:
        pass
    if shutil.which("rsvg-convert"):
        r = subprocess.run(["rsvg-convert", "-z", "1.6", "-o", png_path, svg_path], check=False)
        return r.returncode == 0
    return False


# =================================================================== validation
def validate(graph, reference):
    """Compare a graph with the independent reference record.

    Matching rule: a reference call matches the graph node with the identical call ID (one-to-one).
    Then tool, outcome, parent and run (session key) are compared. Expected edges are computed
    from the reference calls with the same declared rules (compute_edges) and compared as sets.
    Nested-call counts are also compared with the transcript telemetry counts.
    """
    gnodes = {}
    dup_ids = []
    for n in graph["nodes"]:
        if n["id"] in gnodes:
            dup_ids.append(n["id"])
        gnodes[n["id"]] = n
    rcalls = {c["call_id"]: c for c in reference.get("calls", [])}
    matched = [c for c in rcalls if c in gnodes]
    missing = [c for c in rcalls if c not in gnodes]
    extra = [c for c in gnodes if c not in rcalls]

    mism = []
    ref_key = reference.get("session_key")
    for cid in matched:
        r, n = rcalls[cid], gnodes[cid]
        for field, rv, gv in (("tool", r.get("tool"), n.get("tool")),
                              ("status", r.get("status"), n.get("status")),
                              ("parent", r.get("parent"), n.get("parent"))):
            if rv not in (None, UNKNOWN) and rv != gv:
                mism.append({"call_id": cid, "field": field, "reference": rv, "graph": gv})
        if isinstance(ref_key, str) and ref_key != UNKNOWN and n.get("session_key") not in (ref_key,):
            mism.append({"call_id": cid, "field": "run (session key)", "reference": ref_key,
                         "graph": n.get("session_key")})

    # nested counts from transcript telemetry vs. children in the graph
    count_problems = []
    for parent, expected in (reference.get("nested_counts") or {}).items():
        got = sum(1 for n in graph["nodes"] if n.get("parent") == parent)
        if got != expected:
            count_problems.append({"parent": parent, "telemetry_count": expected, "graph_children": got})

    exp_edges = {(e["source"], e["target"], e["type"]) for e in compute_edges(
        [{"id": c["call_id"], "tool": c.get("tool"), "parent": c.get("parent"), "target": c.get("target"),
          "status": c.get("status"), "call_seq": c.get("call_seq"), "result_seq": c.get("result_seq")}
         for c in rcalls.values()])}
    act_edges = [(e["source"], e["target"], e["type"]) for e in graph["edges"]]
    act_set = set(act_edges)
    correct = sorted(act_set & exp_edges)
    incorrect = sorted(act_set - exp_edges)
    missing_edges = sorted(exp_edges - act_set)
    self_loops = [e for e in act_edges if e[0] == e[1]]
    dangling = [e for e in act_edges if e[0] not in gnodes or e[1] not in gnodes]

    n_ref, n_graph = len(rcalls), len(gnodes)
    precision = len(matched) / n_graph if n_graph else (None if n_ref == 0 else 0.0)
    recall = len(matched) / n_ref if n_ref else (None if n_graph == 0 else 0.0)
    e_prec = len(correct) / len(act_set) if act_set else (None if not exp_edges else 0.0)
    e_rec = len(correct) / len(exp_edges) if exp_edges else (None if not act_set else 0.0)
    repeated = {}
    for c in rcalls.values():
        t = norm_target(c.get("target")) if c.get("target") not in (None, UNKNOWN) else None
        if t:
            repeated.setdefault((c.get("tool"), t), []).append(c["call_id"])
    repeated = {f"{k[0]} {k[1]}": v for k, v in repeated.items() if len(v) > 1}
    checks = {
        "1_node_coverage": not missing and not extra and not dup_ids,
        "1b_nested_count_matches_telemetry": not count_problems,
        "2_identity_and_attributes": not mism,
        "3_edge_correctness": not incorrect and not missing_edges and not self_loops and not dangling,
        "4_boundary_cases": ((n_ref == 0) == (n_graph == 0))
                            and all(all(cid in gnodes for cid in ids) for ids in repeated.values()),
    }
    return {
        "run": graph["run"], "reference_source": reference.get("source", UNKNOWN),
        "counts": {"reference_calls": n_ref, "graph_nodes": n_graph, "matched_calls": len(matched),
                   "missing_calls": len(missing), "extra_calls": len(extra), "duplicate_node_ids": len(dup_ids),
                   "reference_edges": len(exp_edges), "graph_edges": len(act_set), "correct_edges": len(correct),
                   "incorrect_edges": len(incorrect), "missing_edges": len(missing_edges)},
        "node_precision": rnd(precision), "node_recall": rnd(recall),
        "edge_precision": rnd(e_prec), "edge_recall": rnd(e_rec),
        "metric_note": "n/a = undefined because both the reference and the graph are empty (0/0)",
        "edges_by_type": {t: sum(1 for e in act_set if e[2] == t) for t in EDGE_SEMANTICS},
        "status_counts": {s: sum(1 for n in graph["nodes"] if n["status"] == s)
                          for s in ("success", "error", "blocked", UNKNOWN)},
        "repeated_calls_same_tool_and_file": repeated,
        "checks": checks, "passed": all(checks.values()),
        "missing_calls": missing, "extra_calls": extra, "duplicate_ids": dup_ids,
        "attribute_mismatches": mism, "nested_count_problems": count_problems,
        "incorrect_edges": [list(e) for e in incorrect], "missing_edges": [list(e) for e in missing_edges],
        "self_loops": [list(e) for e in self_loops],
        "matching_rule": "one-to-one exact call-ID match; then tool, outcome, parent and session key compared; "
                         "expected edges recomputed from the reference with the declared edge rules",
    }


def rnd(x):
    return "n/a" if x is None else round(x, 4)


def validation_md(v, graph):
    c = v["counts"]
    L = [f"# Validation report: {v['run']}", "", f"**Result: {'PASS' if v['passed'] else 'FAIL'}**", "",
         f"- Session: `{graph['session'].get('session_key') or UNKNOWN}`",
         f"- Question: {graph['question']}", f"- Final answer: {trim(graph['answer'], 600)}",
         f"- Reference record: {v['reference_source']}", f"- Matching rule: {v['matching_rule']}", "",
         "| Metric | Value |", "|---|---|"]
    L += [f"| {k.replace('_', ' ')} | {val} |" for k, val in c.items()]
    L += [f"| node precision | {v['node_precision']} |", f"| node recall | {v['node_recall']} |",
          f"| edge precision | {v['edge_precision']} |", f"| edge recall | {v['edge_recall']} |", "",
          "| Check | Result |", "|---|---|"]
    L += [f"| {k.replace('_', ' ')} | {'pass' if ok else 'FAIL'} |" for k, ok in v["checks"].items()]
    L += ["", "Edges by meaning: " + ", ".join(f"{k} {n}" for k, n in v["edges_by_type"].items()),
          "", "Outcomes: " + ", ".join(f"{k} {n}" for k, n in v["status_counts"].items())]
    if v["repeated_calls_same_tool_and_file"]:
        L += ["", "Repeated calls to the same tool and file (each kept as its own node):"]
        L += [f"- {k}: {', '.join('`' + i + '`' for i in ids)}" for k, ids in v["repeated_calls_same_tool_and_file"].items()]
    for title, key in (("Missing calls", "missing_calls"), ("Extra calls", "extra_calls"),
                       ("Duplicate node IDs", "duplicate_ids"), ("Attribute mismatches", "attribute_mismatches"),
                       ("Nested-count problems", "nested_count_problems"), ("Incorrect edges", "incorrect_edges"),
                       ("Missing edges", "missing_edges"), ("Self-loops", "self_loops")):
        if v[key]:
            L += ["", f"## {title}", ""] + [f"- `{json.dumps(x)}`" for x in v[key]]
    L += ["", "## Calls in the graph", "", "| Order | Call ID | Tool | File / title | Status | Evidence |",
          "|---|---|---|---|---|---|"]
    for n in graph["nodes"]:
        st = n["status"] + (f" ({n['detail']})" if n["detail"] else "")
        what = short_target(n["target"]) or n["label"] or "-"
        L.append(f"| C{n['order']} | `{n['id']}` | {n['tool']} | {what} | {st} | {'; '.join(n['evidence'])} |")
    if not graph["nodes"]:
        L.append("| - | - | - | - | - | no tool.call events in the run |")
    return "\n".join(L) + "\n"


# =================================================================== mutation test
def mutations(graph):
    """Yield (description, damaged copy of the graph)."""
    nodes, edges = graph["nodes"], graph["edges"]
    if not nodes:
        g = copy.deepcopy(graph)
        g["nodes"].append({"id": "call_FAKE_999", "tool": "read", "parent": None, "label": "", "target": None,
                           "status": "success", "detail": "", "order": 1, "session_key": UNKNOWN,
                           "call_seq": UNKNOWN, "result_seq": UNKNOWN, "evidence": [], "repeat": None})
        yield "add a fabricated node call_FAKE_999 to the no-tool run", g
    if nodes:
        g = copy.deepcopy(graph)
        victim = g["nodes"][len(nodes) // 2]
        g["nodes"] = [n for n in g["nodes"] if n["id"] != victim["id"]]
        g["edges"] = [e for e in g["edges"] if victim["id"] not in (e["source"], e["target"])]
        yield f"remove node {victim['id']}", g
        g = copy.deepcopy(graph)
        g["nodes"].append(dict(copy.deepcopy(nodes[0]), id="call_FAKE_999", order=len(nodes) + 1))
        yield "add a fabricated node call_FAKE_999", g
        g = copy.deepcopy(graph)
        flip = {"success": "error", "error": "success", "blocked": "success", UNKNOWN: "success"}
        g["nodes"][0]["status"] = flip[g["nodes"][0]["status"]]
        yield f"change status of {nodes[0]['id']} to {g['nodes'][0]['status']}", g
    if edges:
        g = copy.deepcopy(graph)
        e = g["edges"][0]
        e["source"], e["target"] = e["target"], e["source"]
        yield f"reverse edge {edges[0]['source']} -> {edges[0]['target']} ({edges[0]['type']})", g
        g = copy.deepcopy(graph)
        g["edges"] = g["edges"][1:]
        yield f"delete edge {edges[0]['source']} -> {edges[0]['target']} ({edges[0]['type']})", g
    reps = {}
    for n in nodes:
        if n.get("target") not in (None, UNKNOWN):
            reps.setdefault((n["tool"], norm_target(n["target"])), []).append(n)
    reps = [v for v in reps.values() if len(v) > 1]
    if reps:
        a, b = reps[0][0], reps[0][1]
        g = copy.deepcopy(graph)
        g["nodes"] = [n for n in g["nodes"] if n["id"] != b["id"]]
        g["edges"] = [dict(e, source=a["id"] if e["source"] == b["id"] else e["source"],
                           target=a["id"] if e["target"] == b["id"] else e["target"]) for e in g["edges"]]
        yield f"merge repeated call {b['id']} into {a['id']} (same tool and file)", g


def mutation_report(graph, reference):
    base = validate(graph, reference)
    rows, all_caught = [], True
    for desc, g in mutations(graph):
        v = validate(g, reference)
        failed = [k for k, ok in v["checks"].items() if not ok]
        caught = not v["passed"]
        all_caught &= caught
        rows.append((desc, "DETECTED" if caught else "NOT DETECTED", ", ".join(failed) or "-",
                     v["node_precision"], v["node_recall"]))
    L = [f"# Graph-mutation test: {graph['run']}", "",
         f"Unmodified graph: **{'PASS' if base['passed'] else 'FAIL'}**. Each row damages a copy of the "
         f"graph on purpose and re-runs the validator against the same reference record.", "",
         "| Change made to the copy | Validator | Checks that failed | Node precision | Node recall |",
         "|---|---|---|---|---|"]
    L += [f"| {d} | **{r}** | {f} | {p} | {rc} |" for d, r, f, p, rc in rows]
    if not rows:
        L.append("| (no nodes or edges to change) | - | - | - | - |")
    L += ["", f"All mutations detected: **{'yes' if all_caught else 'NO'}**"]
    return "\n".join(L) + "\n", base["passed"] and all_caught


# =================================================================== commands
def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)


def cmd_process(a):
    events = load_events(a.events)
    graph = build_graph(events, events_path=a.events, run_label=a.run)
    os.makedirs(a.out, exist_ok=True)
    write_json(os.path.join(a.out, "graph.json"), graph)
    open(os.path.join(a.out, "graph.dot"), "w", encoding="utf-8").write(to_dot(graph))
    svg = os.path.join(a.out, "graph.svg")
    open(svg, "w", encoding="utf-8").write(to_svg(graph))
    write_png(svg, os.path.join(a.out, "graph.png"))
    msg = f"{graph['run']}: {len(graph['nodes'])} call nodes, {len(graph['edges'])} edges"
    if a.reference:
        reference = json.load(open(a.reference, encoding="utf-8"))
        v = validate(graph, reference)
        write_json(os.path.join(a.out, "validation.json"), v)
        open(os.path.join(a.out, "validation.md"), "w", encoding="utf-8").write(validation_md(v, graph))
        msg += (f" | validation {'PASS' if v['passed'] else 'FAIL'} (nodes P={v['node_precision']} "
                f"R={v['node_recall']}, edges P={v['edge_precision']} R={v['edge_recall']})")
    print(msg)
    print(f"outputs written to {a.out}")
    return 0


def cmd_validate(a):
    v = validate(json.load(open(a.graph, encoding="utf-8")), json.load(open(a.reference, encoding="utf-8")))
    print(json.dumps({"passed": v["passed"], "counts": v["counts"], "checks": v["checks"]}, indent=2))
    return 0 if v["passed"] else 1


def cmd_mutate(a):
    text, ok = mutation_report(json.load(open(a.graph, encoding="utf-8")),
                               json.load(open(a.reference, encoding="utf-8")))
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
    print(text)
    return 0 if ok else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="tracegraph", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("process", help="events.jsonl + reference -> graph, picture, validation report")
    pp.add_argument("events")
    pp.add_argument("--reference")
    pp.add_argument("--out", required=True)
    pp.add_argument("--run", help="label for the run, e.g. M1")
    pp.set_defaults(func=cmd_process)
    pv = sub.add_parser("validate", help="re-check graph.json against reference.json (exit 1 on failure)")
    pv.add_argument("graph")
    pv.add_argument("reference")
    pv.set_defaults(func=cmd_validate)
    pm = sub.add_parser("mutate", help="damage copies of a graph and show the validator catches each change")
    pm.add_argument("graph")
    pm.add_argument("reference")
    pm.add_argument("--out")
    pm.set_defaults(func=cmd_mutate)
    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
