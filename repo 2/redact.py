#!/usr/bin/env python3
"""redact: make a shareable copy of an OpenClaw trajectory bundle.

    python3 redact.py <bundle_dir> <out_dir>

OpenClaw already masks workspace paths ($WORKSPACE_DIR) and the code inside exec ("***").
This script also removes, from every JSON/JSONL file in the bundle:
  * the compiled system prompt (OpenClaw's private instructions and workspace files)
  * model signature blobs (thoughtSignature, textSignature, thinkingSignature)
  * model "thinking" blocks
  * the host name and local install paths
Line numbers in events.jsonl are kept the same, so evidence pointers ("line 30") still match,
and every tool.call / tool.result field used for identity and order is left untouched.
"""
import json
import os
import re
import sys

DROP_KEYS = {"systemPrompt", "thoughtSignature", "textSignature", "thinkingSignature", "signature"}
HOST_RE = re.compile(r"tsurugi\d*", re.IGNORECASE)
PATH_RE = re.compile(r"(/home/[A-Za-z0-9_.-]+|~/[A-Za-z0-9_./-]*nvm[A-Za-z0-9_./-]*|~/openclaw-install)")
FILES = ["events.jsonl", "manifest.json", "metadata.json", "prompts.json", "tools.json", "artifacts.json"]


def clean(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in DROP_KEYS:
                out[k] = "[removed by redact.py]"
            else:
                out[k] = clean(v)
        return out
    if isinstance(obj, list):
        return [clean(x) for x in obj if not (isinstance(x, dict) and x.get("type") == "thinking")]
    if isinstance(obj, str):
        return PATH_RE.sub("<local-path>", HOST_RE.sub("<host>", obj))
    return obj


def main(src, dst):
    os.makedirs(dst, exist_ok=True)
    for name in FILES:
        p = os.path.join(src, name)
        if not os.path.exists(p):
            continue
        if name.endswith(".jsonl"):
            with open(p, encoding="utf-8") as fh, open(os.path.join(dst, name), "w", encoding="utf-8") as out:
                for line in fh:
                    line = line.strip()
                    out.write((json.dumps(clean(json.loads(line)), ensure_ascii=False) if line else "") + "\n")
        else:
            json.dump(clean(json.load(open(p, encoding="utf-8"))), open(os.path.join(dst, name), "w",
                      encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"redacted copy written to {dst}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
