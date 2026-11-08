# Tracing and Verifying AI Agent Tool Calls (OpenClaw)

CYFI 445 final project by Brenda Bailey and Kelley Jorge.

This repository turns a saved OpenClaw run into a **tool-call graph** and then **checks the graph against an independent record** of the same run. It answers three questions about each run: which tool calls really happened, in what order, and how each one turned out.

| Item | Value |
|---|---|
| Platform | OpenClaw 2026.10.4 (383c124) |
| Model | `google/gemini-3.5-flash-lite` through the Gemini API (free tier) |
| Host | University of Baltimore course VM (Linux x64), Node.js 24.22.0 |
| Observation point | OpenClaw trajectory bundle (`openclaw sessions export-trajectory`) |
| Dependencies | Python 3.8+ standard library. Optional: `cairosvg` or `rsvg-convert` for `graph.png` |

## Quick start

```bash
git clone <this repo> && cd <repo>
./run_all.sh                                # every run in evidence/: reference, graph, picture, validation, mutation test
python3 -m unittest discover -s tests -v    # 15 automated tests, including graph-mutation tests
```

## The one command (per run)

```bash
python3 tracegraph.py process evidence/M1/events.jsonl \
        --reference evidence/M1/reference.json --out outputs/M1 --run M1
```

It accepts a saved run (`events.jsonl`) and its independent reference record (`reference.json`). It writes:

| File | What it is |
|---|---|
| `graph.json` | machine-readable graph: run context, question, answer, nodes, edges, edge meanings |
| `graph.dot` | Graphviz version of the same graph |
| `graph.svg` / `graph.png` | the readable picture |
| `validation.json` / `validation.md` | comparison with the reference: counts, precision/recall, checks, discrepancies |

The reference record is made by a separate program from a different part of the trace:

```bash
python3 make_reference.py evidence/M1/events.jsonl --out evidence/M1/reference.json \
        --annotations evidence/M1/annotations.json
```

Neither command needs code edits or hand-drawing for a new run. To add a run, copy its redacted bundle into `evidence/<NAME>/` and run `./run_all.sh <NAME>`. On the VM, `./run_case.sh <NAME> <tui-session-id>` exports, redacts and processes in one step.

Other commands:

```bash
python3 tracegraph.py validate outputs/F1/graph.json evidence/F1/reference.json   # exit code 1 if it fails
python3 tracegraph.py mutate   outputs/F1/graph.json evidence/F1/reference.json   # damage copies, show each is caught
python3 make_summary.py                                                          # outputs/summary.md, all runs
```

## Results

All five runs were recorded on 1 November 2026, each in its own new session. See `prompts.md` for the exact prompts and the run log.

| Run | Calls (ref / graph) | Matched | Missing / extra | Edges correct | Node P / R | Outcomes (success / error / blocked) | Result |
|---|---|---|---|---|---|---|---|
| M0 | 0 / 0 | 0 | 0 / 0 | 0 / 0 | n/a | 0 / 0 / 0 | PASS |
| M1 | 5 / 5 | 5 | 0 / 0 | 5 / 5 | 1.0 / 1.0 | 3 / 1 / 1 | PASS |
| M2 | 6 / 6 | 6 | 0 / 0 | 7 / 7 | 1.0 / 1.0 | 4 / 1 / 1 | PASS |
| F1 | 18 / 18 | 18 | 0 / 0 | 24 / 24 | 1.0 / 1.0 | 16 / 2 / 0 | PASS |
| F2 | 6 / 6 | 6 | 0 / 0 | 7 / 7 | 1.0 / 1.0 | 0 / 6 / 0 | PASS |

The mutation test (`outputs/<RUN>/mutation_test.md`) detects every deliberate change: a removed node, a fabricated node, a wrong status, a reversed edge, a deleted edge, and two repeated calls merged into one.

## Design in brief

- **Trace boundary:** one exported session that contains exactly one user prompt.
- **What counts as a tool call:** every `tool.call` event with a `toolCallId` in the runtime stream. That includes failed calls, blocked calls (for example `Tool read not found`), retries, and nested code-mode calls. Each call ID is its own node, so repeated calls to the same tool are never merged.
- **Node fields:** run label, session key, call ID, tool, file or title, order, start and end sequence numbers and timestamps, status (`success`, `error`, `blocked` or `unknown`), and evidence pointers (`events.jsonl line N`). A field missing from the trace is written as `unknown`, never guessed.
- **Edges and their meanings** (stored in `graph.json` as `edge_semantics`):

| Edge | Meaning | Drawn as |
|---|---|---|
| `next` | next observed call at the same level, which started after the previous call returned. Order only, not cause. | solid arrow |
| `overlaps` | started before the previous call returned (concurrent calls inside one `exec`). No completion order is claimed. | dotted arrow |
| `parent` | called from inside that `exec` (child ID `tool_call:<parent>:<tool>:<n>`) | dashed arrow |
| `retry` | same tool and same file called again after that call failed or was blocked | purple arrow |

- **Graph source:** `source: "runtime"` events only (`tracegraph.py`).
- **Reference source:** `source: "transcript"` events only (`make_reference.py`). The reference also uses the transcript's `telemetry.callCount`: OpenClaw's running count of nested calls, read independently of the runtime stream. Nested calls do not appear in the transcript, so `annotations.json` lists them by call ID, file and outcome, with the raw event lines both team members checked.
- **Matching:** one-to-one on exact call ID. Then tool, outcome, parent and session key are compared. Expected edges are recomputed from the reference with the same declared rules. Nested-call counts must equal the telemetry counts.

`docs/dataflow.png` shows the data flow, from agent event to trace, graph and validation result.

## Repository layout

```
tracegraph.py        graph builder, renderer, validator, mutation test
make_reference.py    independent reference record (transcript stream + telemetry + annotations)
redact.py            makes the shareable copy of a trajectory bundle
run_all.sh           runs the pipeline for every run in evidence/
run_case.sh          on the VM: export + redact + process one session
make_summary.py      cross-run results table
evidence/<RUN>/      redacted bundle (events.jsonl, manifest, metadata, prompts, tools, artifacts),
                     annotations.json (nested calls, team-reviewed), reference.json
outputs/<RUN>/       graph.json, graph.dot, graph.svg, graph.png, validation.json, validation.md, mutation_test.md
outputs/summary.md   all runs in one table
tests/               unittest suite on the real runs, including mutation tests
data/                the unchanged starter files
docs/                data-flow diagram
prompts.md           exact prompts, run log, revisions
config.example.md    platform and model configuration (no secrets)
```

## Reproduce a run

1. Install OpenClaw 2026.10.4 and connect a model (`config.example.md`).
2. Copy `data/` to `~/.openclaw/workspace/midterm_data/` and check the SHA-256 values:
   ```
   0f19f59480e1a291058757e9ad0236ea66a187f4bb1ba9c5a42020a9acd8efec  devices.csv
   126296a31f97285a04aa3249fbf5bc35b812c6b3c7cc8583187dd78ff70da54c  auth_events.csv
   71eeb5c53b9905f114b63c7e120055ca9154cac6ae8e5fcd766bc60dc8c34a49  alert_rule.txt
   ```
3. Start `openclaw`, open a **new** session, and paste one prompt from `prompts.md`.
4. Copy the `tui-...` session ID from the status bar, then run `./run_case.sh <NAME> <tui-id>`.

The model is not deterministic, so a rerun can produce a different number of calls. The pipeline handles that; the saved evidence here is what the reported results came from.

## Privacy

OpenClaw's export already replaces workspace paths with `$WORKSPACE_DIR` and masks the code inside `exec` as `***`. `redact.py` also removes the compiled system prompt, model signature blobs, thinking blocks, the host name and local install paths. Line numbers stay the same, so evidence pointers still match. The data files are synthetic. No API key, Gateway token or private prompt is in this repository.
