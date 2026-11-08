# Validation report: M0

**Result: PASS**

- Session: `agent:frankie:tui-ea7edea9-8f6a-4455-b57d-0451cff50b0a`
- Question: Without reading any files, what is 2 + 2?
- Final answer: 4
- Reference record: OpenClaw transcript stream (tool.call / tool.result, telemetry.callCount)
- Matching rule: one-to-one exact call-ID match; then tool, outcome, parent and session key compared; expected edges recomputed from the reference with the declared edge rules

| Metric | Value |
|---|---|
| reference calls | 0 |
| graph nodes | 0 |
| matched calls | 0 |
| missing calls | 0 |
| extra calls | 0 |
| duplicate node ids | 0 |
| reference edges | 0 |
| graph edges | 0 |
| correct edges | 0 |
| incorrect edges | 0 |
| missing edges | 0 |
| node precision | n/a |
| node recall | n/a |
| edge precision | n/a |
| edge recall | n/a |

| Check | Result |
|---|---|
| 1 node coverage | pass |
| 1b nested count matches telemetry | pass |
| 2 identity and attributes | pass |
| 3 edge correctness | pass |
| 4 boundary cases | pass |

Edges by meaning: next 0, overlaps 0, parent 0, retry 0

Outcomes: success 0, error 0, blocked 0, unknown 0

## Calls in the graph

| Order | Call ID | Tool | File / title | Status | Evidence |
|---|---|---|---|---|---|
| - | - | - | - | - | no tool.call events in the run |
