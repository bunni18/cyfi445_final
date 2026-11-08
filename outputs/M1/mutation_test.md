# Graph-mutation test: M1

Unmodified graph: **PASS**. Each row damages a copy of the graph on purpose and re-runs the validator against the same reference record.

| Change made to the copy | Validator | Checks that failed | Node precision | Node recall |
|---|---|---|---|---|
| remove node call_1152410 | **DETECTED** | 1_node_coverage, 3_edge_correctness | 1.0 | 0.8 |
| add a fabricated node call_FAKE_999 | **DETECTED** | 1_node_coverage | 0.8333 | 1.0 |
| change status of call_1161847 to success | **DETECTED** | 2_identity_and_attributes | 1.0 | 1.0 |
| reverse edge call_1161847 -> call_1227404 (next) | **DETECTED** | 3_edge_correctness | 1.0 | 1.0 |
| delete edge call_1161847 -> call_1227404 (next) | **DETECTED** | 3_edge_correctness | 1.0 | 1.0 |
| merge repeated call tool_call:call_1183670:read:1 into call_1227404 (same tool and file) | **DETECTED** | 1_node_coverage, 1b_nested_count_matches_telemetry, 3_edge_correctness, 4_boundary_cases | 1.0 | 0.8 |

All mutations detected: **yes**
