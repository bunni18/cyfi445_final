#!/usr/bin/env bash
# On the OpenClaw VM: export one session, make a redacted copy, and run the whole pipeline.
#   usage:   ./run_case.sh <CASE> <TUI-SESSION-ID>
#   example: ./run_case.sh M1 tui-a64c9adb-1900-4beb-9274-f2d13dbde1eb
# Writes evidence/<CASE>/ (redacted bundle + reference.json) and outputs/<CASE>/.
# Nested code-mode calls need evidence/<CASE>/annotations.json (see README) for a full reference;
# without it the validator reports them as extra calls, which is the correct, visible result.
set -euo pipefail
CASE="${1:?usage: ./run_case.sh <CASE> <TUI-SESSION-ID>}"
SID="${2:?usage: ./run_case.sh <CASE> <TUI-SESSION-ID>}"
REPO="$(cd "$(dirname "$0")" && pwd)"
WS="$HOME/.openclaw/workspace"
NAME="final_${CASE}_$(date +%Y%m%d_%H%M%S)"     # new name every time: the export refuses to overwrite
( cd "$WS" && openclaw sessions export-trajectory --session-key "agent:frankie:${SID}" --workspace . --output "$NAME" )
python3 "$REPO/redact.py" "$WS/.openclaw/trajectory-exports/$NAME" "$REPO/evidence/$CASE"
"$REPO/run_all.sh" "$CASE"
echo "Open outputs/$CASE/validation.md and outputs/$CASE/graph.svg"
