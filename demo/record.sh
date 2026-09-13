#!/usr/bin/env bash
# Record the grep vs findReferences demo with asciinema, convert with agg.
set -euo pipefail
cd "$(dirname "$0")"
export JARVIS_DATA_DIR="$(pwd)/.jarvis"

asciinema rec --overwrite --quiet demo.cast --command bash << 'INPUTS'
echo "# Without jarvis: grep matches strings, not symbols"
sleep 0.8
clear
grep -rn "AuthService" .
sleep 2.0
echo ""
echo "# → 20 hits: comments, markdown, AuthServiceUser, imports…"
sleep 1.2
clear
echo "# With jarvis: findReferences returns exact occurrences"
sleep 0.8
clear
../findrefs.sh fixture AuthService
sleep 2.5
exit
INPUTS

echo "--- Recording done, converting..."
agg --theme dracula --font-size 16 --speed 1 demo.cast demo.gif
echo "Done: $(ls -lh demo.gif 2>/dev/null)"
