# Landing page design

## Goal

Give codeintel a single-page landing page that lets a skeptical developer
understand what it is and why it's trustworthy *before* asking them to
install it. Optimizes for "understand first, then install," not raw
install-conversion or GitHub-star growth.

## Location & delivery

- File: `docs/index.html` — one self-contained static HTML file, inline
  CSS/JS, no build step, no framework.
- Reuses the existing diagrams already in `docs/assets/`:
  `codeintel-architecture.png`, `codeintel-system-architecture.png`.
- Hosting: GitHub Pages, source = `main` branch, `/docs` folder. This is a
  one-time repo Settings toggle the user does themselves (a repo-visibility
  change, not something to flip unattended).
- Existing markdown files in `docs/` are untouched and remain reachable as
  plain files alongside `index.html`.

## Content structure (single scrolling page)

1. **Hero** — name, one-line value prop ("Local-first code intelligence for
   coding agents"), the "no server, no auth, no network" guarantee up front,
   secondary line noting the MCP tool count (9 tools).
2. **Why it's built this way** — the three architectural guarantees from the
   README: read-only runtime, atomic publish, rebuild-not-accumulate graph.
   This is the trust-building section that justifies "understand first."
3. **How it works** — the two existing architecture diagrams (client/server
   + 3 engines overview, then index pipeline + package graph), each with the
   1–2 sentence captions already written in the README.
4. **Tools** — the 9 MCP tools table (name + what it does), condensed from
   the README table.
5. **Requirements & limits** — a compact, honest version of the README's
   limits section: macOS/Linux only; one language per repo (detected by
   git-tracked extension plurality); SCIP navigation covers 4 language
   families (TypeScript/TSX, Python, Java/Kotlin, Swift), `--search-only`
   covers 10 more for search/semantic only; navigation and search only —
   codeintel never edits code. Placed *before* install, matching the
   "understand first" choice.
6. **Install** — the CTA: `setup.sh` → `uv tool install codeintel-navigation-mcp`
   → `codeintel index`, plus the `/plugin marketplace add phuongddx/codeintel`
   path for Claude Code users.
7. **Footer** — links to the GitHub repo, docs, PyPI package, license.

All copy is sourced directly from `README.md` and
`docs/project-overview-pdr.md` — no invented marketing claims beyond what's
already documented there.

## Visual craft & implementation

Implementation is handed to the `design-taste-frontend` skill (the
"taste-skill") against this spec as its brief, rather than prescribing exact
colors/type here — that skill infers direction from the brief and runs its
own pre-flight/anti-slop process. Suggested direction: technical /
terminal-adjacent rather than generic SaaS-gradient, matching the "single
stdio process, no server, no network" positioning — but the final call is
that skill's, not fixed in this spec.

This is also where this design deviates from the default brainstorming
handoff: instead of `writing-plans` (a multi-phase engineering plan), the
next step is invoking `design-taste-frontend` directly, since this is one
static page, not a phased build.

## Out of scope

- No JS framework, no build pipeline, no routing/multi-page site.
- No new marketing copy beyond what's in the README/PDR.
- No automatic GitHub Pages settings change — the user enables it manually.
