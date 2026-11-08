"""Automated tests for tracegraph on the five real saved runs, including graph-mutation tests.

Run from the repository root:   python3 -m unittest discover -s tests -v
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import tracegraph  # noqa: E402

CASES = ["M0", "M1", "M2", "F1", "F2"]


def build(case, tmp):
    ev = os.path.join(ROOT, "evidence", case, "events.jsonl")
    ref = os.path.join(tmp, f"{case}_reference.json")
    cmd = [sys.executable, os.path.join(ROOT, "make_reference.py"), ev, "--out", ref]
    ann = os.path.join(ROOT, "evidence", case, "annotations.json")
    if os.path.exists(ann):
        cmd += ["--annotations", ann]
    subprocess.run(cmd, check=True, capture_output=True)
    out = os.path.join(tmp, case)
    subprocess.run([sys.executable, os.path.join(ROOT, "tracegraph.py"), "process", ev, "--reference", ref,
                    "--out", out, "--run", case], check=True, capture_output=True)
    return json.load(open(os.path.join(out, "graph.json"))), json.load(open(ref)), out


class RealRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.runs = {c: build(c, cls.tmp) for c in CASES}

    def test_every_run_passes_validation(self):
        for c in CASES:
            g, r, _ = self.runs[c]
            v = tracegraph.validate(g, r)
            self.assertTrue(v["passed"], f"{c}: {v['checks']}")

    def test_command_writes_all_outputs(self):
        for c in CASES:
            out = self.runs[c][2]
            for f in ("graph.json", "graph.dot", "graph.svg", "validation.json", "validation.md"):
                self.assertTrue(os.path.exists(os.path.join(out, f)), f"{c} missing {f}")

    def test_no_tool_run_has_no_nodes(self):
        g, _, _ = self.runs["M0"]
        self.assertEqual(g["nodes"], [])
        self.assertIn("2 + 2", g["question"])
        self.assertEqual(g["answer"].strip(), "4")

    def test_blocked_call_is_kept_and_labelled(self):
        g, _, _ = self.runs["M1"]
        st = {n["id"]: n["status"] for n in g["nodes"]}
        self.assertEqual(st["call_1227404"], "blocked")      # "Tool read not found"
        self.assertEqual(st["call_1161847"], "error")
        self.assertEqual(st["tool_call:call_1183670:read:1"], "success")

    def test_repeated_calls_stay_separate_nodes(self):
        g, _, _ = self.runs["F1"]
        dev = [n for n in g["nodes"] if n["tool"] == "read" and str(n["target"]).endswith("devices.csv")]
        self.assertEqual(len(dev), 4, "devices.csv was read 4 times in F1; each read is its own node")
        self.assertEqual(len({n["id"] for n in dev}), 4)
        self.assertFalse(any(e["source"] == e["target"] for e in g["edges"]), "no self-loops")

    def test_retry_edges_only_after_failure(self):
        g, _, _ = self.runs["F2"]
        retries = [(e["source"], e["target"]) for e in g["edges"] if e["type"] == "retry"]
        self.assertEqual(retries, [("tool_call:call_510042:read:1", "tool_call:call_1278436:read:1"),
                                   ("tool_call:call_1278436:read:1", "tool_call:call_741999:read:1")])
        g1, _, _ = self.runs["F1"]
        self.assertFalse([e for e in g1["edges"] if e["type"] == "retry"],
                         "F1 repeats successful reads; those are repeats, not retries")

    def test_concurrent_calls_are_not_given_a_total_order(self):
        g, _, _ = self.runs["F1"]
        kinds = {(e["source"], e["target"]): e["type"] for e in g["edges"]}
        self.assertEqual(kinds[("tool_call:call_1224218:read:1", "tool_call:call_1224218:read:2")], "overlaps")
        self.assertEqual(kinds[("tool_call:call_1138511:read:1", "tool_call:call_1138511:read:2")], "next")

    def test_nested_counts_match_transcript_telemetry(self):
        _, r, _ = self.runs["F1"]
        self.assertEqual(r["nested_counts"], {"call_766761": 0, "call_1224218": 3, "call_972380": 3,
                                              "call_1138511": 3, "call_783962": 1, "call_1186635": 2})


class MutationTests(unittest.TestCase):
    """Damage a copy of a correct graph; the validator must fail it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.g, cls.r, _ = build("F1", cls.tmp)

    def fails(self, g, check):
        v = tracegraph.validate(g, self.r)
        self.assertFalse(v["passed"])
        self.assertFalse(v["checks"][check], f"{check} should fail: {v['checks']}")
        return v

    def test_removed_node_detected(self):
        g = copy.deepcopy(self.g)
        g["nodes"] = [n for n in g["nodes"] if n["id"] != "tool_call:call_783962:read:1"]
        g["edges"] = [e for e in g["edges"] if "tool_call:call_783962:read:1" not in (e["source"], e["target"])]
        v = self.fails(g, "1_node_coverage")
        self.assertEqual(v["missing_calls"], ["tool_call:call_783962:read:1"])
        self.assertFalse(v["checks"]["1b_nested_count_matches_telemetry"])

    def test_fabricated_node_detected(self):
        g = copy.deepcopy(self.g)
        g["nodes"].append(dict(g["nodes"][0], id="call_FAKE"))
        self.assertEqual(self.fails(g, "1_node_coverage")["extra_calls"], ["call_FAKE"])

    def test_altered_edge_detected(self):
        g = copy.deepcopy(self.g)
        e = next(e for e in g["edges"] if e["type"] == "next")
        e["source"], e["target"] = e["target"], e["source"]
        self.fails(g, "3_edge_correctness")

    def test_relabelled_edge_detected(self):
        g = copy.deepcopy(self.g)
        next(e for e in g["edges"] if e["type"] == "overlaps")["type"] = "next"
        self.fails(g, "3_edge_correctness")

    def test_wrong_status_detected(self):
        g = copy.deepcopy(self.g)
        next(n for n in g["nodes"] if n["id"] == "call_1224218")["status"] = "success"
        self.fails(g, "2_identity_and_attributes")

    def test_merged_repeat_detected(self):
        g = copy.deepcopy(self.g)
        g["nodes"] = [n for n in g["nodes"] if n["id"] != "tool_call:call_972380:read:2"]
        self.fails(g, "4_boundary_cases")

    def test_mutate_command_reports_every_change_detected(self):
        text, ok = tracegraph.mutation_report(self.g, self.r)
        self.assertTrue(ok)
        self.assertNotIn("NOT DETECTED", text)


if __name__ == "__main__":
    unittest.main()
