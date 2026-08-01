---
name: codeintel-setup
description: Install and configure codeintel, the local-first code-intelligence MCP server. Use when onboarding, running setup.sh, registering the MCP server, or indexing a repo for the first time.
version: "0.1.0"
---

# codeintel setup

Part of the codeintel toolkit. Siblings: `codeintel-use` (everyday queries), `codeintel-issues` (report bugs).

To take a machine from zero to "codeintel answering queries", run these in order.

## 1. Check prerequisites

- **OS:** macOS or Linux. codeintel does not support Windows.
- **`uv`:** run `uv --version`. If missing, install from https://docs.astral.sh/uv/.
- **PATH:** after install (step 2), `~/.codeintel/bin` must be on `PATH`. Verify with `command -v scip`.

## 2. Install codeintel + external binaries

```bash
curl -fsSL https://raw.githubusercontent.com/phuongddx/codeintel/main/setup.sh | sh
uv sync
```

`setup.sh` installs every binary codeintel needs into `~/.codeintel/bin` and appends it to the shell rc. It is idempotent — re-running skips what's present. Options: `--only <name>` (one dependency), `--force` (reinstall), `--help`.

Binaries installed: `scip` (≥ v0.9.0, SQLite conversion), `zoekt-index` / `zoekt-webserver` (search), and one indexer per language: `scip-typescript`, `scip-python`, `scip-swift` (macOS arm64 only), `scip-java` (detect-only).

## 3. Register the MCP server

Claude Code:

```bash
claude mcp add codeintel --scope user -- uv --directory /path/to/codeintel run codeintel-server
```

Cursor / other MCP clients: point them at the stdio command `uv --directory /path/to/codeintel run codeintel-server`. No HTTP server, no auth, no network.

## 4. Index a repo

```bash
uv run codeintel index /path/to/your/repo            # slug = directory name
uv run codeintel index /path/to/your/repo --slug foo # explicit slug
uv run codeintel index /path/to/your/repo --scheme MyScheme  # Swift, ambiguous Xcode scheme
```

Language is detected by counting source files per extension — **one language per index** (no multi-language merge). For a Swift repo with a checked-in `.xcodeproj`/`.xcworkspace`, codeintel auto-uses `xcodebuild`; pass `--scheme` on the first index if there is more than one scheme (it's persisted, so `reindex`/`watch` reuse it).

## 5. Verify

```bash
uv run codeintel status <slug>     # expect status: indexed
```

Then call a tool through the MCP client, e.g. `goToDefinition(repo: "<slug>", symbol: "main")`. A non-error response with a `definitions` array means the pipeline works end to end.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `command not found: scip` / `zoekt-index` | `~/.codeintel/bin` not on `PATH`. Open a new shell, or `source ~/.zshrc` (or `~/.bashrc`). Still missing after that? Re-run `setup.sh --only zoekt --force` — the flag value is `zoekt` (not `zoekt-index`); it installs both `zoekt-index` and `zoekt-webserver` from the same tarball. |
| `scip` version < v0.9.0 | Re-run `setup.sh --only scip --force`. Older converters silently drop occurrence ranges; `codeintel index` refuses them. |
| Swift: "multiple schemes" / wrong build | Pass `--scheme <name>` on the first `codeintel index`. It's stored in the registry and reused by `reindex`/`watch`. |
| `status: partial` | The index published symbols but no navigable positions (indexer/converter bug). Re-read the stderr from `codeintel index`; reindex after fixing. |
| `status: failed` | Re-run `codeintel index <slug>` and read stderr; the atomic-publish guarantee means the previous good index (if any) is still live. |

## 7. Next

Onboarding done. For everyday structural queries (find references, go-to-definition, call hierarchy), see `codeintel-use`. To report a bug, see `codeintel-issues`.
