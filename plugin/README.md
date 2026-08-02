# codeintel

Local-first code intelligence: SCIP navigation and Zoekt search over your own indexed repositories. Installs as a plugin for **Codex CLI** and **Claude Code**, exposing nine MCP tools and three agent skills.

## What it gives you

Nine MCP tools (all take `repo` = the slug from `codeintel index`):

- `documentSymbols` — every top-level symbol in a file, each with its range.
- `goToDefinition` — resolve a symbol's definition site(s).
- `findReferences` — every occurrence of a symbol.
- `callHierarchy` — single-level incoming + outgoing calls.
- `typeHierarchy` — super/subtypes (errors on real indexes; see the use skill's gotchas).
- `getIndexStatus` — whether a repo has a published index, plus freshness.
- `searchCode` — lexical search via Zoekt (lazy-started webserver).
- `semanticSearch` — vector + Zoekt hybrid via reciprocal rank fusion (needs the `[semantic]` extra).
- `blastRadius` — 2-hop package-dependency BFS across indexed repos.

Full signatures and return shapes: see the `codeintel-use` skill's `references/tool-roster.md`.

## Install

### Codex CLI

The plugin's bundled `plugin/.mcp.json` auto-registers the `codeintel` MCP server on install — no `codex mcp add` needed for the base case.

```bash
codex plugin marketplace add https://github.com/phuongddx/codeintel --ref main
codex plugin add codeintel
```

Then run the `codeintel-setup` skill (or follow its steps manually): install external binaries via `setup.sh`, then `codeintel index /path/to/repo`.

### Claude Code

```text
/plugin marketplace add phuongddx/codeintel
/plugin install codeintel@codeintel
```

Then the same `setup.sh` + `codeintel index` flow.

### Optional extras

- `[semantic]` for `semanticSearch` — install with `uv tool install "codeintel-navigation-mcp[semantic]"`, then register a second MCP server (the `codeintel` name is already taken by the plugin's default registration):
  ```bash
  codex mcp add codeintel-semantic -- uvx --from "codeintel-navigation-mcp[semantic]" codeintel-server
  claude mcp add codeintel-semantic --scope user -- uvx --from "codeintel-navigation-mcp[semantic]" codeintel-server
  ```
- `[watch]` for `codeintel watch` (foreground auto-reindex on file changes).

## Privacy

codeintel is local-first. The only network egress is `uvx` fetching the published wheel on first server start, and — if you install the optional `[semantic]` extra — the one-time embedding-model download by `sentence-transformers`. No telemetry, no analytics, and no outbound calls during queries. Published indexes are opened read-only (`mode=ro&immutable=1`).

## Links

- Repository: <https://github.com/phuongddx/codeintel>
- Changelog: [`CHANGELOG.md`](https://github.com/phuongddx/codeintel/blob/main/CHANGELOG.md)
- Issues: <https://github.com/phuongddx/codeintel/issues>
- Full onboarding: the `codeintel-setup` skill.
