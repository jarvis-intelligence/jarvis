#!/usr/bin/env bash
# Demo helper: pipe the MCP handshake + findReferences through jarvis-server.
set -euo pipefail
REPO="${1:-fixture}"
SYMBOL="${2:-AuthService}"
{
  echo '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"0.0.1"}}}'
  echo '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  printf '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"findReferences","arguments":{"repo":"%s","symbol":"%s"}}}\n' "$REPO" "$SYMBOL"
} | jarvis-server 2>/dev/null | tail -1 | python3 -c "
import json, sys
r = json.load(sys.stdin)
refs = r['result']['structuredContent']['references']
sym = r['result']['structuredContent']['symbol']
print(f'  {sym} → {len(refs)} exact occurrence(s):')
for ref in refs:
    s = ref['range']['start']
    print(f\"    {ref['path']}:{s['line']+1}:{s['character']}\")"
