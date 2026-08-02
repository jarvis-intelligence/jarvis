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
3. **How it works** — the **three verified SVG diagrams** committed in
   `aaa1ccc` (see "Diagram regeneration" below). Do **not** use the two old
   PNGs; they are factually stale. Each diagram gets a 1–2 sentence caption.
   - `codeintel-core-components.svg` — pipeline, embedding, storage
   - `codeintel-query-engine.svg` — SCIP path vs. hybrid path, no router
   - `codeintel-agent-integration.svg` — MCP over stdio, 9 flat tools
4. **Tools** — the 9 MCP tools table (name + what it does), condensed from
   the README table. `typeHierarchy` must be marked non-functional here,
   matching the README.
5. **Requirements & limits** — a compact, honest version of the README's
   limits section: macOS/Linux only; one language per repo (detected by
   git-tracked extension plurality); SCIP navigation covers 4 language
   families (TypeScript/TSX, Python, Java/Kotlin, Swift), `--search-only`
   covers 10 more for search/semantic only; navigation and search only —
   codeintel never edits code; **`typeHierarchy` is non-functional** pending
   upstream [scip#465](https://github.com/scip-code/scip/pull/465). Placed
   *before* install, matching the "understand first" choice.

### Honesty constraint on tool count

The hero may say "9 MCP tools" only if the limits section names
`typeHierarchy` as non-functional. 8 of 9 work; the README is scrupulous
about this and the landing page must not quietly overclaim. This applies to
the regenerated diagrams too — diagram 1's tool list currently presents
`typeHierarchy` as working.
6. **Install** — the CTA: `setup.sh` → `uv tool install codeintel-navigation-mcp`
   → `codeintel index`, plus the `/plugin marketplace add phuongddx/codeintel`
   path for Claude Code users.
7. **Footer** — links to the GitHub repo, docs, PyPI package, license.

All copy is sourced directly from `README.md` and
`docs/project-overview-pdr.md` — no invented marketing claims beyond what's
already documented there.

## Diagram regeneration — RESOLVED (commit `aaa1ccc`)

**Superseded by three purpose-built diagrams**, each fact checked against
`src/codeintel/` and each visually verified after export:

| File | Covers |
|---|---|
| `codeintel-core-components.svg` (43KB) | 6-stage pipeline, embedding generation, 4 storage stores |
| `codeintel-query-engine.svg` (28KB) | SCIP structural path vs. hybrid vector+lexical path, RRF k=60 |
| `codeintel-agent-integration.svg` (27KB) | stdio JSON-RPC, 9 flat tools, response contract |

Sources are the matching `.excalidraw` files; export was via the Kroki API
(`curl -X POST https://kroki.io/excalidraw/svg -H "Content-Type: text/plain"`),
verified by rendering to PNG with `rsvg-convert` and inspecting.

This closed the finding **without** needing the local export toolchain, so
the Playwright-Firefox install is no longer a blocker for this page.

**Still open (not blocking the page):** `README.md` continues to embed the
two stale PNGs. Repointing it at these three and deleting
`codeintel-architecture.png` / `codeintel-system-architecture.png` is a
separate cleanup, pending explicit go-ahead (deletion is hard to reverse).

<details>
<summary>Original staleness findings, kept for the record</summary>

The two committed PNGs are **factually stale** and must be regenerated from
their `.excalidraw` sources before the page ships. Verified inaccuracies:

**`codeintel-architecture.excalidraw`**
| Element | Says | Must say |
|---|---|---|
| `sub2` | `8 MCP tools` · `SCIP v0.7.0` | `9 MCP tools` · `SCIP v0.9.0` |
| `tools_h` | `8 MCP tools` | `9 MCP tools` |
| `tools` | list omits `semanticSearch`; shows `typeHierarchy` as working | add `semanticSearch`; mark `typeHierarchy` non-functional |

Structural gap: the diagram shows 3 engines (Query / Search / Graph) but the
architecture has 4 — Semantic (`semantic.py` → LanceDB) is absent. Adding it
is bounded but not free; the geometry is:

- Engine column x=1110, boxes at y=240/490/740 (250 pitch, 200 tall) → 4th at
  y=990. Zone `z` bottom is 1135, so 990+200=1190 **overflows by 55px**.
- Plan: grow `z` height 960→1120; add `semantic` rect at (1110, 990) and
  `lancedb` rect at (1620, 990) + arrow; relocate `readonly_h`/`readonly`
  (currently x=1110 y=985/1013, would collide) to the free area at ~x=620
  y=700; move `footer` y=1090→1250. ≈9 element operations.

**`codeintel-system-architecture.excalidraw`**
| Element | Says | Must say |
|---|---|---|
| `zA_s` line 1 | `.ts/.tsx`, `.py`, `.java/.kt` only | add `.swift -> scip-swift` |
| `zA_s` line 2 | `skips .git · node_modules · .venv …` (implies filesystem walk) | detection counts `git ls-files`; `IGNORED_DIRS` applied on top |

Structural gap: no semantic-indexing stage. **Recommendation: annotate it as
an explicitly optional step rather than adding a 6th peer stage.** Rationale
is correctness, not laziness — semantic indexing is gated behind the
`semantic` extra and is *non-fatal* (a failure there never blocks the
SCIP/Zoekt publish), so drawing it as a peer of the 5 mandatory stages would
overstate it. A 6th peer stage would also need re-pitching all 5 existing
stages (x-centers 370/766/1162/1558/1954, pitch 396; a 6th at 2350 overflows
zone `zA` right=2260) — ~18 coordinate edits, or widening `zA`/`zC`/`zD`.

**Export toolchain.** `excalidraw-brute-export-cli` v0.4.0 is installed but
**blocked**: its Playwright Firefox browser is missing
(`npx playwright install firefox`, ~90MB). Required flags include `-s/--scale`
(undocumented in the short help; omitting it fails). Verified invocation
shape:

```
excalidraw-brute-export-cli -i <in>.excalidraw -f svg -b true -d false -e false -s 1 -o <out>.svg
```

**Export as SVG, not PNG.** The current PNGs are 883KB + 906KB (≈1.8MB) at
8560×4460 and 8880×5120 — self-defeating on a page whose pitch is
minimalism, and illegible on mobile. SVG is a fraction of the size and scales
cleanly. The CLI also has `--dark-mode`, so a dark page is viable (optionally
export both variants and swap via `prefers-color-scheme`); light-vs-dark is
therefore *not* a constraint on the visual direction.

**Bonus:** `README.md` embeds these same stale PNGs, so regenerating fixes
the README too. Keep the PNG filenames the README references, or update the
README's image links in the same change.

</details>

## GitHub Pages exposure (accepted decision)

Pages source = `main` / `/docs`. **Accepted consequence:** the repo is public,
so publishing `/docs` also publishes `docs/journals/`,
`docs/superpowers/plans/` (11 internal plan files), and
`docs/superpowers/specs/` (including this spec) as browsable web pages. This
was considered and accepted — do not silently relocate those files or switch
the Pages source without asking.

Required: add an empty `.nojekyll` at the Pages root (`docs/.nojekyll`) so
Jekyll does not process the tree and mangle paths/underscored names.

## Acceptance criteria

The page is done when all of the following hold:

1. `docs/index.html` opens standalone in a browser with no console errors and
   no network requests to third-party hosts.
2. Both embedded diagrams are the **regenerated** SVGs, and every fact in
   them matches the current README (tool count 9, SCIP v0.9.0, Swift present,
   git-based language detection).
3. Every claim on the page traces to `README.md` or
   `docs/project-overview-pdr.md`. No invented benchmarks, testimonials,
   user counts, or performance numbers.
4. `typeHierarchy`'s non-functional status appears wherever the tool set is
   enumerated.
5. All install commands are copy-pasteable and byte-match the README Quick
   Start.
6. Every link resolves (GitHub repo, docs files, PyPI, LICENSE).
7. Readable at 375px viewport width — no horizontal scroll, diagrams remain
   legible or are made zoomable/linked at full size.
8. Renders correctly in both light and dark `prefers-color-scheme`.
9. Semantic headings in order, `alt` text on both diagrams, visible focus
   states, body text contrast ≥ 4.5:1.
10. `docs/.nojekyll` exists.

## Verification

Before commit: open the page in a browser at 375px and desktop widths, in
both colour schemes, and confirm criteria 1–10 by inspection. Diagram facts
(criterion 2) are checked by reading the regenerated SVG text against the
README, not by trusting the export.

## Visual craft & implementation

Implementation is handed to the `design-taste-frontend` skill (the
"taste-skill") against this spec as its brief, rather than prescribing exact
colors/type here — that skill infers direction from the brief and runs its
own pre-flight/anti-slop process. Suggested direction: technical /
terminal-adjacent rather than generic SaaS-gradient, matching the "single
stdio process, no server, no network" positioning — but the final call is
that skill's, not fixed in this spec. Light *or* dark is open, since the
diagrams can be exported either way (see Diagram regeneration).

Non-negotiable regardless of visual direction (these are requirements, not
taste): responsive to 375px, both colour schemes, and the accessibility items
in the acceptance criteria.

This is also where this design deviates from the default brainstorming
handoff: instead of `writing-plans` (a multi-phase engineering plan), the
next step is invoking `design-taste-frontend` directly, since this is one
static page, not a phased build.

## Out of scope

- No JS framework, no build pipeline, no routing/multi-page site.
- No new marketing copy beyond what's in the README/PDR.
- No automatic GitHub Pages settings change — the user enables it manually.
- No relocation of `docs/journals` or `docs/superpowers` (exposure accepted).
- No redesign of the diagrams' visual style — correct the facts, keep the look.

## Open questions

1. Install Playwright Firefox (~90MB) to unblock diagram export? Blocking.
2. Diagram 1: add the 4th (Semantic) engine box per the geometry plan, or
   ship a 3-engine diagram with a text annotation? Recommendation: add it —
   a 3-engine "Overview" understates the architecture.
3. Keep the regenerated files at the existing `.png` paths the README links,
   or switch the README to the new `.svg` files too?
