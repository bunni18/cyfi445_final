#!/usr/bin/env bash
# Rebuild the reference record, graph, picture, validation report and mutation test for every
# run folder in evidence/. Works for any new run: add evidence/<CASE>/ and run this again.
#   usage: ./run_all.sh            (all cases)     ./run_all.sh M1 F2   (only these)
set -euo pipefail
cd "$(dirname "$0")"
CASES=("$@")
if [ ${#CASES[@]} -eq 0 ]; then
  for d in evidence/*/; do [ -f "$d/events.jsonl" ] && CASES+=("$(basename "$d")"); done
fi
for CASE in "${CASES[@]}"; do
  echo "== $CASE"
  ANN=()
  [ -f "evidence/$CASE/annotations.json" ] && ANN=(--annotations "evidence/$CASE/annotations.json")
  python3 make_reference.py "evidence/$CASE/events.jsonl" --out "evidence/$CASE/reference.json" "${ANN[@]}"
  python3 tracegraph.py process "evidence/$CASE/events.jsonl" --reference "evidence/$CASE/reference.json" \
          --out "outputs/$CASE" --run "$CASE"
  python3 tracegraph.py mutate "outputs/$CASE/graph.json" "evidence/$CASE/reference.json" \
          --out "outputs/$CASE/mutation_test.md" > /dev/null && echo "mutation test: all changes detected"
done
python3 make_summary.py > /dev/null && echo "summary table: outputs/summary.md"
