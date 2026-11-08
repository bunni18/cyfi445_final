# Configuration used (no secrets)

| Setting | Value |
|---|---|
| OpenClaw | 2026.10.4 (383c124), installed with npm, SHA-512 checked against the npm registry |
| Node.js | 24.22.0 (nvm) |
| Host | University of Baltimore course VM, Linux x64, reached through Omnissa Horizon + MobaXterm |
| Agent | `frankie` ("Frankie"), workspace `~/.openclaw/workspace` |
| Model | `google/gemini-3.5-flash-lite` (Gemini API free tier), thinking level medium |
| Gateway | `ws://127.0.0.1:18789`, `gateway.mode local`, token auth on, loopback only |
| Tool mode | Code mode: only `exec` and `wait` are visible to the model; catalog tools (`read`, ...) run inside `exec` |
| Trace interface | `openclaw sessions export-trajectory --session-key agent:frankie:<tui-id> --workspace . --output <name>` |

The API key is set only as an environment variable for the Gateway process:

```bash
export GEMINI_API_KEY='<your key>'          # never written to a file or committed
nohup openclaw gateway > ~/gateway.log 2>&1 &
```
