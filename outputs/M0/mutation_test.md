# Graph-mutation test: M0

Unmodified graph: **PASS**. Each row damages a copy of the graph on purpose and re-runs the validator against the same reference record.

| Change made to the copy | Validator | Checks that failed | Node precision | Node recall |
|---|---|---|---|---|
| add a fabricated node call_FAKE_999 to the no-tool run | **DETECTED** | 1_node_coverage, 4_boundary_cases | 0.0 | 0.0 |

All mutations detected: **yes**
