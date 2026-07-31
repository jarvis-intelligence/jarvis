# codeintel Distribution Strategy — MCP Server Adoption

Date: 2026-07-31 · Research: Exa web search (Exa Agent async run tools not exposed by connected server)
Goal: take `phuongddx/codeintel` from 0 users to real, measurable installs.

---

## 1. Where codeintel stands today

| Dimension | Current | Adoption impact |
|---|---|---|
| GitHub | public, **0★ / 0 forks**, created 2026-07-25 | baseline |
| Repo metadata | **no description, no topics, no homepage**, Discussions off | blocks GitHub search + directory crawlers |
| **LICENSE** | **absent** | **hard blocker** — legally unusable by others; Glama/awesome-list scoring reads license |
| PyPI | **not published** | no `uvx` install → every user must clone + `uv sync` |
| Install | `curl setup.sh \| sh` + clone + `uv sync` + manual `claude mcp add uv --directory <abs path>` | heaviest barrier in the funnel |
| Releases | only a vendored `zoekt` binary tag; **no `v0.2.0`** | no changelog surface, no version signal |
| Assets | 9 MCP tools, ~3.9k LOC, 2 CI workflows, 2 architecture diagrams, `codeintel-setup` / `codeintel-use` skills | strong raw material |

**Two things gate everything else: no license, no zero-clone install.** No amount of channel work compensates.

---

## 2. Honest expectation setting (measured, not assumed)

Directory listings are **passive discovery, not traffic events**. A documented case: a Docker MCP server merged into `punkpeye/awesome-mcp-servers` (**91.6k★**) in 18h and measured, post-merge — 459 clones, **5 page views, zero referrers, 0 stars**. Author's conclusion: *"npm downloads are the only metric that matters. Stars are vanity."*
→ https://nova-persists.hashnode.dev/what-actually-happens-when-your-pr-merges-to-an-88k-star-awesome-list-mqrtsqtg

Hacker News, by contrast, is measurable. Study of 138 AI/LLM repo launches (2024–25): **+121★ at 24h, +189★ at 48h, +289★ at 1 week**; median far below mean (long tail). Best window **12:00–17:00 UTC**. The `Show HN` tag itself confers **no statistical advantage** after controlling for maturity — the *content* does. → https://arxiv.org/html/2511.04453v1

Implication: registries are the slow compounding floor; **one good HN/Reddit post is the step-change.** Do registries first so the post lands on a repo that converts.

---

## 3. Channel map, ordered by leverage for *this* project

### Tier 1 — Prerequisites (do before any announcement)

**1. Add a LICENSE.** MIT — what every comparable uses (Serena, awesome-mcp-servers, most MCP servers). One file, unblocks everything.

**2. Publish to PyPI → enables `uvx`.**
```toml
# pyproject.toml already has [project.scripts] — only metadata is missing
```
`uv build && uv publish` (or GitHub Actions **trusted publishing**, no token). Then the install line becomes:
```bash
claude mcp add codeintel --scope user -- uvx --from codeintel codeintel-server
```
Caveat specific to codeintel: `uvx` solves the *Python* install, **not** the external binaries (`scip`, `zoekt-index`, `zoekt-webserver`, per-language indexers). `setup.sh` remains required. This is codeintel's single largest disadvantage vs. Serena (`uv tool install serena-agent`, LSP-based, no prebuilt index). Options: keep the two-step and document it honestly, or ship a Docker/OCI image where the binaries are baked in (also unlocks the Docker MCP Catalog + `registryType: "oci"`).

**3. Repo hygiene** — description, topics (`mcp`, `mcp-server`, `code-intelligence`, `scip`, `zoekt`, `claude-code`, `code-search`, `python`), homepage, social preview image (reuse `docs/assets/codeintel-architecture.png`), enable Discussions, tag `v0.2.0` with release notes from CHANGELOG.

**4. README restructure for conversion.** Currently architecture-first — excellent engineering doc, weak funnel. Registry reviewers and users need, in the first screen: one-line value prop → copy-paste install → copy-paste client config JSON → tool table (name + one line each). Move architecture below. Add `mcp-name: io.github.phuongddx/codeintel` to the README body — **required** by the official registry to verify PyPI ownership.

### Tier 2 — Registries (~1 hour total, compounding)

| Target | How | Notes |
|---|---|---|
| **Official MCP Registry** | `mcp-publisher init` → `login github` → `publish`. Namespace `io.github.phuongddx/codeintel`; `registryType: "pypi"`; verification = `mcp-name:` line in README | **Highest leverage single step** — downstream marketplaces increasingly sync from it. Requires PyPI published first. → https://modelcontextprotocol.io/registry/package-types |
| **Glama** (~29k–50k servers) | **auto-crawls** public repos with a proper manifest, 3–7 days; assigns A–F quality/security score | **Prerequisite for the awesome-list PR.** Manual submit: glama.ai/mcp/servers |
| **awesome-mcp-servers** (91.6k★) | Fork → edit README → PR. **Requirements: emoji title, ONE server per PR, must already be Glama-listed (`has-glama` label), Glama score badge after description, alphabetical order, language `🐍` + scope `🏠` badges** | Bot closes multi-server PRs. Approval ~22min, merge batched 1–2 days |
| **mcp.so** (~22k) | manual web form, ~2 min, auto-pulls README | high SEO ranking for "mcp server" queries |
| **PulseMCP** (~16–21k) | auto-indexes; email `hello@pulsemcp.com` to accelerate | shows weekly visitor counts = social proof once you have traction |
| **Cursor Directory / VS Code `@mcp` gallery** | client galleries, one-click add | reaches non-Claude users |
| Smithery | `smithery` CLI + hosting | **skip** — Smithery's model is hosted remote servers; codeintel is local-first by design and reads the user's filesystem. Structurally incompatible |

### Tier 3 — Claude Code plugin marketplace (underrated, codeintel is already 80% there)

You already ship `.claude/skills/codeintel-setup-workspace` and `codeintel-use-workspace`. Claude Code plugins bundle **skills + MCP server config + a `SETUP.md` skill that walks Claude through installing and configuring the server**. That converts codeintel's multi-binary setup problem from a README the user must follow into something the agent does for them — a genuine differentiator given the install weight.

- Self-pointing marketplace: add `.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` with `"source": "./"` in this same repo. Users then run two lines:
  ```
  /plugin marketplace add phuongddx/codeintel
  /plugin install codeintel@codeintel
  ```
- Run `claude plugin validate` (same check runs on submission).
- Submit to the community marketplace: **clau.de/plugin-directory-submission** → reviewed → pinned in `anthropics/claude-plugins-community`, syncs nightly, appears in the **Discover tab**. Public repo required; updates auto-mirror from GitHub, no re-submission.
- → https://code.claude.com/docs/en/plugin-marketplaces · https://claude.com/docs/plugins/submit

### Tier 4 — Launch posts (only after Tiers 1–3)

**Reddit is what actually built the closest comparable.** Serena (27.1k★, MIT, Python, created 2025-03) earned its reputation on r/ClaudeAI *before* directories listed it — thread "Try out Serena MCP. Thank me later." (500+ upvotes) by a developer who had hand-rolled grep/ast-grep scripts and found Serena solved it. The maintainer posted directly and repeatedly ("Claude and Serena MCP — a dream team for coding", "Major Serena MCP Updates").
Targets: r/ClaudeAI, r/ClaudeCode, r/cursor, r/LocalLLaMA. Framing that works: a concrete before/after on a real repo, not a feature list.

**Hacker News** — codeintel is an unusually good fit: HN favors *open-source, local-first, self-hosted developer tools*, and "no server, no auth, no network" is exactly the pitch that resonates there.
- Post **Tue/Wed/Thu, 12:00–17:00 UTC**; link the **GitHub repo**, not a landing page.
- Write the first comment in advance: how it works, what was genuinely hard, trade-offs, what you learned. HN rewards candor, punishes marketing language. Your real material: SCIP `typed_range` oneof forcing `scip >= v0.9.0`; atomic publish via `os.replace` so a reindex never interrupts a live query; why language detection reads `git ls-files` instead of walking the filesystem (the 81 tracked `.py` vs. gitignored 4782 `.ts` case is a *great* HN anecdote); the `scip-swift` `swiftpm`-vs-`xcodebuild` override.
- **Never solicit upvotes** — HN detects voting rings and will flag the post.
- Reply to every comment for the first two hours.

**Product Hunt** — secondary for dev tools. Comparative data from one team launching two dev tools on both: HN gave 107 points / 50+ stars / **100+ installs**; PH gave 193 votes / **~30 installs**. HN converts better; PH is more gameable. Launch 12:01 PT if you do it.

**Skip for now:** dev.to / Hashnode (SEO-only, no immediate audience), paid anything.

---

## 4. Competitive positioning — what to actually claim

Serena is the category incumbent and the honest reference point. Do not pitch codeintel as a Serena replacement; pitch the axis where it wins.

| | Serena (27.1k★) | codeintel |
|---|---|---|
| Mechanism | live LSP per project | precomputed SCIP index, read-only SQLite |
| Editing | yes — rename/move/inline/safe-delete | **no** — navigation + search only |
| Languages | 40+ via LSP | 4 indexers (TS, Python, Java/Kotlin, Swift), **one language per repo** |
| Cross-repo/package | no | **`blastRadius` — package dependency graph, 2-hop BFS** |
| Lexical search at scale | `search_for_pattern` | **Zoekt (trigram index)** |
| Semantic search | none (explicitly no embeddings) | **LanceDB + RRF fusion with Zoekt** |
| Query cost | spawns/holds language servers | opens an immutable file `mode=ro&immutable=1` |
| Reindex safety | n/a | **atomic publish — a failure leaves the previous index live** |

Defensible claims: **blastRadius (nothing comparable in the OSS tier), Zoekt-grade lexical search, semantic+lexical RRF fusion, and a query path that never writes.** The narrow-language, no-editing, index-required tradeoffs are real — state them in the README. HN will find them anyway, and pre-empting reads as confidence.

**Disclose before launch** (all currently in CLAUDE.md but not surfaced in README):
- `typeHierarchy` returns empty on real indexes — `scip expt-convert` v0.7.0 never populates `relationships`. Either fix, hide the tool, or document loudly. Shipping a tool that always returns empty is the fastest way to lose a first-time user.
- Swift indexing: **macOS arm64 only**. Java/Kotlin: detect-only, requires a Docker pull.
- **Windows unsupported.**

---

## 5. Execution sequence

**Week 1 — unblock (nothing ships publicly without these)**
1. `LICENSE` (MIT)
2. PyPI metadata + classifiers + `mcp-name:` line → `uv build` / `uv publish` → verify `uvx --from codeintel codeintel-server`
3. README restructure (value prop → install → client config JSON → tool table → architecture)
4. Repo description, topics, homepage, social preview, Discussions on, tag `v0.2.0`
5. Decide `typeHierarchy`: fix, hide, or document

**Week 2 — registries + plugin**
6. `mcp-publisher publish` to the official MCP Registry
7. `.claude-plugin/{plugin.json,marketplace.json}` + `SETUP.md` skill → `claude plugin validate` → submit to community marketplace
8. Wait for Glama auto-crawl (3–7d); manual submit if absent
9. mcp.so form; PulseMCP email; Cursor Directory
10. awesome-mcp-servers PR **only after Glama listing exists** (emoji title, one server, score badge)

**Week 3 — launch**
11. Write the HN first comment before the post exists
12. Reddit r/ClaudeAI first (lower stakes, real feedback, and the channel that actually built Serena)
13. HN Show HN, Tue–Thu 12:00–17:00 UTC, linking the repo; reply to everything for 2h
14. Optional Product Hunt, 12:01 PT

**Measure PyPI downloads, not stars.** `pypistats recent codeintel` weekly. Clone counts and directory listings are noise; a download means someone actually ran it.

---

## Unresolved questions

1. **Docker/OCI image?** It removes the multi-binary barrier and unlocks the Docker MCP Catalog — but codeintel must read the user's real filesystem and spawn `zoekt-webserver`, so a container needs volume mounts and complicates the atomic-publish path. Worth it, or does it break the local-first model?
2. **`typeHierarchy` disposition** — fix upstream, hide the tool, or ship documented-as-broken? This is a launch blocker either way.
3. **Semantic extra at launch** — promote it (differentiator vs. Serena) or hide it (heavy deps: lancedb + sentence-transformers + tree-sitter, and a first-run model download)?
4. **Linux-only Swift / no Windows** — does the target audience make the macOS-first framing a liability on HN?
5. Is `codeintel` available on PyPI as a name? Not verified — check before committing to it in `server.json`.
