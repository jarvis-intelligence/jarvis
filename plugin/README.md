# jarvis

Local-first code intelligence: SCIP navigation and Zoekt search over your own indexed repositories. Installs as a plugin for **Codex CLI** and **Claude Code**, exposing nine MCP tools and three agent skills.

## What it gives you

Nine MCP tools (all take `repo` = the slug from `jarvis index`):

- `documentSymbols` — every top-level symbol in a file, each with its range.
- `goToDefinition` — resolve a symbol's definition site(s).
- `findReferences` — every occurrence of a symbol.
- `callHierarchy` — single-level incoming + outgoing calls.
- `typeHierarchy` — super/subtypes (errors only on indexes built with an unpatched `scip`; reindex fixes it — see the use skill's gotchas).
- `getIndexStatus` — whether a repo has a published index, plus freshness.
- `searchCode` — lexical search via Zoekt (lazy-started webserver).
- `semanticSearch` — vector + Zoekt + SCIP symbol-definition hybrid via reciprocal rank fusion (needs the `[semantic]` extra).
- `blastRadius` — 2-hop package-dependency BFS across indexed repos.

Full signatures and return shapes: see the `jarvis-use` skill's `references/tool-roster.md`.

## Install

### Codex CLI

The plugin's bundled `plugin/.mcp.json` auto-registers the `jarvis` MCP server on install — no `codex mcp add` needed for the base case.

```bash
codex plugin marketplace add https://github.com/jarvis-intelligence/jarvis-index --ref main
codex plugin add jarvis
```

Then run the `jarvis-setup` skill (or follow its steps manually): install external binaries via `setup.sh`, then `jarvis index /path/to/repo`.

### Claude Code

```text
/plugin marketplace add jarvis-intelligence/jarvis-index
/plugin install jarvis@jarvis
```

Then the same `setup.sh` + `jarvis index` flow.

### Optional extras

- `[semantic]` for `semanticSearch` — install with `uv tool install "jarvis-mcp[semantic]"`, then register a second MCP server (the `jarvis` name is already taken by the plugin's default registration):
  ```bash
  codex mcp add jarvis-semantic -- uvx --from "jarvis-mcp[semantic]" jarvis-server
  claude mcp add jarvis-semantic --scope user -- uvx --from "jarvis-mcp[semantic]" jarvis-server
  ```
- `[watch]` for `jarvis watch` (foreground auto-reindex on file changes).

## Privacy

jarvis is local-first. The only network egress is `uvx` fetching the published wheel on first server start, and — if you install the optional `[semantic]` extra — the one-time embedding-model download by `sentence-transformers`. No telemetry, no analytics, and no outbound calls during queries. Published indexes are opened read-only (`mode=ro&immutable=1`).

## Links

- Repository: <https://github.com/jarvis-intelligence/jarvis-index>
- Changelog: [PyPI release history](https://pypi.org/project/jarvis-mcp/#history)
- Issues: <https://github.com/jarvis-intelligence/jarvis-index/issues>
- Full onboarding: the `jarvis-setup` skill.
