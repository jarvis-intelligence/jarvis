Output demo.gif

Set Shell "bash"
Set FontSize 20
Set Width 1100
Set Height 580
Set Padding 20
Set Theme { "name": "Dracula", "background": "#282a36" }

Type "cd demo/fixture"
Enter
Sleep 500ms
Ctrl+L
Sleep 300ms

# ── Panel 1: WITHOUT jarvis — grep ─────────────────────────────────────
Type "# Without jarvis: grep matches strings, not symbols"
Enter
Sleep 800ms
Ctrl+L
Sleep 300ms

Type 'grep -rn "AuthService" .'
Enter
Sleep 2500ms

Type "# → 20 hits: comments, markdown, AuthServiceUser, imports…"
Enter
Sleep 1500ms
Ctrl+L
Sleep 300ms

# ── Panel 2: WITH jarvis — findReferences ─────────────────────────────
Type "# With jarvis: findReferences returns exact occurrences"
Enter
Sleep 800ms
Ctrl+L
Sleep 300ms

Type "../findrefs.sh fixture AuthService"
Enter
Sleep 2500ms

Sleep 2000ms
