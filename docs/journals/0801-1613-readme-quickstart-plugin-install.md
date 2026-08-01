# README Quick Start: Plugin Install Promoted, Almost Documented Wrong

**Date**: 2026-08-01 16:12
**Severity**: Low
**Component**: README.md / docs
**Status**: Resolved

## What Happened

Commit `6c3e65e` moved the Claude Code plugin install (`/plugin marketplace add
phuongddx/codeintel` + `/plugin install codeintel@codeintel`) up into Quick
Start's step 4, replacing the raw `claude mcp add codeintel --scope user --
codeintel-server` line as the lead path. Previously this install method only
existed ~280 lines down, under "Agent skills," phrased as a footnote ("The
plugin registers the MCP server itself, so the `claude mcp add` step above is
only needed if you are not using the plugin") — true, but buried where a new
user reading top-to-bottom would never see it before typing the manual command.

Also added a `## Quick Start` heading (there wasn't one) so "Agent skills" could
link back to it instead of repeating the registration text, and re-fenced the
plugin slash-command snippet from ` ```bash ` to a plain fence in both places —
those are Claude Code UI commands, not shell, and copy-pasting them into a
terminal fails.

## The Brutal Truth

The first pass at this edit was wrong in a way that would have actively misled
users, and it almost shipped that way. The draft implied installing the plugin
lets you skip straight from `uv tool install` to using the tools — i.e., that
the plugin replaces steps 2 through 4. It doesn't. `plugin/.mcp.json` only
wires up `uvx --from codeintel-navigation-mcp codeintel-server` as an MCP
server entry; there is no MCP tool for indexing anywhere in the tool roster.
`codeintel index` is CLI-only, full stop. If that draft had gone out, someone
would have installed the plugin, opened Claude Code, asked it to search their
repo, and gotten nothing — because no repo was ever indexed, and nothing in
the docs would explain why.

## Technical Details

- `plugin/.mcp.json` registers exactly one MCP server command:
  `uvx --from codeintel-navigation-mcp codeintel-server`. No `index` tool
  exists on the MCP surface — confirmed by reading the actual tool roster, not
  assumed from the plugin name.
- Corrected README diff (`README.md`, +16/-4): step 4 is now split into
  "install the plugin" (registers itself) vs. "any other MCP client (or Claude
  Code without the plugin) registers manually" with the original
  `claude mcp add` line preserved as the fallback.
- Fence fix: two occurrences of the `/plugin marketplace add` /
  `/plugin install` pair were tagged ` ```bash `; both retagged to a bare
  ` ``` ` fence since pasting a slash command into `sh` just errors
  (`command not found: /plugin`).

## What We Tried

Traced through `plugin/.mcp.json` and the MCP tool list before writing the
corrected wording, instead of trusting the plugin's name/marketing framing.
That's the only thing that caught the step-2-through-4 overclaim before it
was committed — there was no test or lint that would have caught prose
overclaiming scope in a README.

## Root Cause Analysis

Docs drift toward the most impressive-sounding claim by default, especially
when summarizing what a new packaging feature (the plugin, shipped in PR #12
just prior) "lets you do." The honest scope of the plugin is narrow — it
saves one CLI invocation (`claude mcp add`) — and narrow scope is a less
satisfying sentence to write than "just install the plugin and you're done,"
so the draft reached for the latter before verification.

## Lessons Learned

For any doc claim of the form "X replaces steps N through M," trace the
actual mechanism (config file, tool roster, API surface) before writing the
sentence — don't infer scope from a feature's name or its own marketing copy.
This is the second time in this immediate context that verifying against the
plugin's actual contents mattered: PR #12's own review caught a broken
version-lookup command and a silently-dead `semanticSearch` for plugin users
before merge (commit `361e533`). The plugin's real capability boundary keeps
being narrower than its surface suggests, and each near-miss required
someone to actually open `plugin/.mcp.json` rather than assume.

## Next Steps

None outstanding — commit `6c3e65e` shipped the corrected version. If the
plugin's `.mcp.json` ever grows an indexing-capable tool, revisit this
section; until then, "index is CLI-only" is a hard invariant worth
re-checking before any future doc edit implies otherwise.
