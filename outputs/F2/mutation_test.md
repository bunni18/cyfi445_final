# Graph-mutation test: F2

Unmodified graph: **PASS**. Each row damages a copy of the graph on purpose and re-runs the validator against the same reference record.

| Change made to the copy | Validator | Checks that failed | Node precision | Node recall |
|---|---|---|---|---|
| remove node tool_call:call_1278436:read:1 | **DETECTED** | 1_node_coverage, 1b_nested_count_matches_telemetry, 3_edge_correctness, 4_boundary_cases | 1.0 | 0.8333 |
| add a fabricated node call_FAKE_999 | **DETECTED** | 1_node_coverage | 0.8571 | 1.0 |
| change status of call_510042 to success | **DETECTED** | 2_identity_and_attributes | 1.0 | 1.0 |
| reverse edge call_510042 -> call_1278436 (next) | **DETECTED** | 3_edge_correctness | 1.0 | 1.0 |
| delete edge call_510042 -> call_1278436 (next) | **DETECTED** | 3_edge_correctness | 1.0 | 1.0 |
| merge repeated call tool_call:call_1278436:read:1 into tool_call:call_510042:read:1 (same tool and file) | **DETECTED** | 1_node_coverage, 1b_nested_count_matches_telemetry, 3_edge_correctness, 4_boundary_cases | 1.0 | 0.8333 |

All mutations detected: **yes**
