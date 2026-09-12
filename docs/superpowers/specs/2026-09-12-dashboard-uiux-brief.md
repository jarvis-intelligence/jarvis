# jarvis console — UI/UX Design Brief

**Date:** 2026-09-12
**Companion to:** [`2026-09-12-dashboard-design.md`](2026-09-12-dashboard-design.md) (approved product spec)
**For:** visual designer — produce high-fidelity screens + component specs for implementation
**Design language:** xAI (x.ai / Grok) — near-black engineered monochrome, white outline pills, hairline borders, uppercase tracked monospace labels, restrained sunset-orange accent

---

## 1. What you are designing

**jarvis console** is a localhost operator dashboard for
[jarvis](https://github.com/phuongddx/jarvis) — a local-first code-intelligence
MCP server that indexes source repositories and serves symbol navigation,
lexical search, and semantic search to AI coding agents. The console is the
product's first human-facing surface: part **infrastructure control panel**,
part **local code-search interface**, part **API playground**.

One command launches it (`jarvis dashboard`) and it opens in the user's
browser at `http://127.0.0.1:6080`. It is used by a single developer on their
own machine — no multi-user concerns, no auth screens, no onboarding. Think
"mission control for one engineer's code indexes," not a marketing site.

### Audience

A developer who runs `jarvis index` on their repos and wants to see: what's
indexed, what's stale, what failed and why, what's running right now — and
occasionally search their code or test a tool call without opening a terminal.

### The four views (fixed scope)

1. **Repos** (home) — table of indexed repositories with health, freshness,
   live indexing status + streaming logs; actions to index/reindex/forget.
2. **Repo detail** — one repo deep-dive: snapshots, capabilities, degradation
   causes + recovery, package-graph neighbors, storage, full log.
3. **Search** — one query box, three result columns (lexical / semantic /
   symbols), click-through source viewer with range highlighting.
4. **Playground** — invoke any of the 10 MCP tools with typed inputs;
   inspect JSON response + latency.


---

## 2. Design language — xAI system

The visual system is a strict interpretation of xAI's design language
(x.ai): an engineered, research-lab restraint. Everything sits on a near-black
canvas; white carries all hierarchy; a single warm accent is spent rarely.
The register is closer to "instrument panel" than "SaaS dashboard" — no
shadows, no glassmorphism, no gradients except where explicitly specified.

### 2.1 Color tokens

**Surfaces**

| Token | Hex | Use |
|---|---|---|
| `canvas` | `#0a0a0a` | Page background — the only page-level surface |
| `canvas-card` | `#191919` | Cards, panels, table rows on hover |
| `canvas-soft` | `#1a1c20` | Inputs, tooltips, nested wells |
| `canvas-mid` | `#363a3f` | Code blocks, log viewers, JSON response blocks |
| `hairline` | `#212327` | All 1px borders and dividers — never darker, never 2px |

**Text**

| Token | Hex | Use |
|---|---|---|
| `ink` | `#ffffff` | Primary text, headings, active states |
| `body` | `#dadbdf` | Secondary/body text |
| `body-mid` | `#82878d` | Captions, table headers, metadata, fine print. (`#7d8187` lightened one step — the stock value misses WCAG AA on `canvas-card`/`canvas-soft`; verified ratios in §7) |

**Accent (spend sparingly — this is the entire accent budget)**

| Token | Hex | Use |
|---|---|---|
| `accent-sunset` | `#ff7a17` | THE accent: live/indexing states, active signals, primary emphasis moments. Also the link/hover color inside mono blocks |
| `accent-sunset-soft` | `#ffc285` | `partial` status, softer emphasis |
| `accent-breeze` | `#a0c3ec` | Semantic-search column identity, secondary data accents |
| `accent-dusk` | `#7c3aed` | Reserved: package-graph edges only |
| `accent-twilight` | `#c4b5fd` | Reserved: graph hover/neighbor highlight only |

**Semantic status — one deliberate extension beyond stock xAI tokens**

xAI's own system has no semantic palette; an operator console cannot avoid
one. Extend with exactly these, tuned to sit inside the monochrome restraint
(no saturated greens/reds from generic dashboards):

| Token | Hex (proposed — refine) | Meaning |
|---|---|---|
| `status-ok` | `ink` `#ffffff` | `indexed` — success is *calm white*, not green. Nothing is wrong; don't shout |
| `status-partial` | `#ffc285` | `partial` — published with documented gaps |
| `status-degraded` | `#ff7a17` | `degraded` — SCIP stage failed/disabled, baseline live |
| `status-failed` | `#ff5c5c` | `failed` — the single added red; failure only |
| `status-live` | `#ff7a17` | `indexing` — same orange as accent; the one place motion is allowed |
| `stale` | `#ff5c5c` | freshness drift text ("stale +12") |

Status is also encoded by a 6px status dot + uppercase mono label, never
color alone (§7 Accessibility).

### 2.2 Typography

Two voices, maximum contrast between them — this contrast **is** the brand:

1. **Interface face** — geometric sans. Spec: `Universal Sans` (xAI's face)
   → `Inter` → `system-ui` fallback chain. Weight **400 only** on the
   console surface; 500 may be used for table headers if 400 tests
   ambiguous. Negative tracking on anything ≥ 20px (`-0.6px` at 20–24px,
   `-1.2px` at 32px+).
2. **Data face** — monospace for ALL machine values: slugs, paths, SHAs,
   ports, log lines, JSON, code, versions, numbers in tables. Spec:
   `Geist Mono` → `ui-monospace, SFMono-Regular, Menlo` fallback. Uppercase
   + tracking `+1.2px` at 12–14px for eyebrows and column headers.

**Constraint — no web fonts may be downloaded.** The console runs localhost
and must make zero network requests. Design against the fallback chain
(assume `system-ui` + `ui-monospace` on most machines); if you spec
Universal Sans/Geist Mono in mockups, provide the system-stack rendering as
the reference implementation target.

| Token | Size/Line | Face | Tracking | Use |
|---|---|---|---|---|
| `display-sm` | 32/36 | sans 400 | −1.2px | View titles (one per view) |
| `display-xs` | 20/28 | sans 400 | −0.6px | Card/panel titles |
| `body-lg` | 18/28 | sans 400 | — | Search query input, empty-state copy |
| `body-md` | 16/24 | sans 400 | — | Body, buttons, table cells (prose cells) |
| `body-sm` | 14/20 | sans 400 | — | Dense table cells, secondary actions |
| `mono-md` | 14/20 | mono 400 | — | Machine values in tables, code, logs |
| `mono-eyebrow` | 12/16 | mono 400, UPPERCASE | +1.2px | Section eyebrows, column headers, chips |

### 2.3 Shape, border, elevation

- **Buttons are pills** (`border-radius: 9999px`), 1px border. Primary =
  white-filled pill with `#0a0a0a` text. Secondary = transparent with
  `rgba(255,255,255,0.25)` border. Destructive intent = same outline pill,
  `status-failed` border + text (never filled red).
- **Cards/panels**: 8px radius, `canvas-card` fill, 1px `hairline` border.
  **No shadows anywhere.** Elevation is expressed by surface lightness
  (`canvas` → `canvas-card` → `canvas-soft` → `canvas-mid`) and hairlines.
- **Inputs**: 8px radius, `canvas-soft` fill, `hairline` border; focus =
  1px `ink` border (no glow).
- **Tables**: no zebra striping; 1px `hairline` row dividers; header row on
  `canvas-soft` with `mono-eyebrow` cells; row hover → `canvas-card`.

### 2.4 Spacing & grid

4px base unit: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64. Content max-width
**1440px**, 32px page gutters. Desktop-first: design at 1440 and 1280;
usable floor 1024×720. No mobile design required (localhost operator tool) —
but nothing may hard-break below 1024.

### 2.5 Motion

Restraint: exactly two animated behaviors.

1. **Live pulse** — the `indexing` status dot and any actively-streaming log
   indicator breathe: opacity 1 → 0.45 → 1, 1.6s ease-in-out loop.
2. **Row expand/collapse** — log-tail pane under a repo row: height
   transition 200ms ease-out.

Everything else snaps (hover, tab switches, modals). All motion respects
`prefers-reduced-motion` (pulse becomes static dot + "LIVE" mono label).

---

## 3. App shell

```
┌──────────────────────────────────────────────────────────────────────────┐
│ jarvis ▮ console                                    v0.10.0  ● zoekt live │  56px top bar
├──────────────────────────────────────────────────────────────────────────┤
│  REPOS    REPO DETAIL*    SEARCH    PLAYGROUND                            │  tab nav, 44px
├──────────────────────────────────────────────────────────────────────────┤
│  ~/.jarvis · 6 repos · 2.4 GB on disk · zoekt-webserver :6070 RUNNING     │  status strip, 36px
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   (view content, 24px top padding, max-width 1440 centered)              │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Top bar** (56px, `canvas`, hairline bottom border): left = wordmark
  "jarvis ▮ console" in `mono-eyebrow` (the ▮ is `accent-sunset`); right =
  version + zoekt state (`body-mid`, dot lit when the zoekt webserver
  process is up). The top bar never scrolls.
- **Tab nav** (44px): four tabs, `body-sm` sans; active tab = `ink` text +
  2px `accent-sunset` underline; inactive = `body-mid`. "REPO DETAIL" tab
  only appears when a repo is selected (marked * above).
- **Status strip** (36px, `canvas-soft`, hairline bottom): always-visible
  environment facts in `mono-md` — data dir, repo count, disk total,
  zoekt-webserver port + state. This is the "instrument reading" at a
  glance. Machine values (`~/.jarvis`, `:6070`) in `ink`; labels in
  `body-mid`.
- Views render below; each view has one `display-sm` title and an
  `mono-eyebrow` above it (e.g. `── INDEXED REPOSITORIES`).

---

## 4. View specs

### 4.1 Repos (home)

```
── INDEXED REPOSITORIES ──────────────────────────────────────  [ + index new repo ]
Repos                                                         

┌ STATUS ── REPO ─────── LANG ─ FRESHNESS ── SCIP ── SEM ─ SIZE ─ LAST INDEXED ─ ACTIONS ┐
│ ● INDEXING  jarvis     py   ● fresh      avail  on    412 MB  2 min ago      ⟳ ⋯       │
│ ├─ live log ──────────────────────────────────────────────────────────────── │
│ │ [12:04:31] zoekt-git-index: 1,204 files · 8.9s                             │
│ │ [12:04:40] scip-python: 411 symbols · 31.2s                    ▌streaming  │
│ ├─────────────────────────────────────────────────────────────────────────── │
│ ● INDEXED   rails-app  rb   ● fresh      —      off    1.1 GB  3 days ago    ⟳ ⋯       │
│ ● STALE+12  kite       ts   ● stale      avail  on    220 MB  12 days ago    ⟳ ⋯       │
│ ● DEGRADED  payments   java ● fresh      failed off     89 MB  yesterday     ⟳ ⋯       │
│ ● FAILED    tooling    py   —            —      —      —      4 hours ago    ⟳ ⋯       │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Row anatomy** (48px): status dot + uppercase mono status label
  (§2.1 semantic colors); repo slug (`mono-md`, `ink` — slugs are machine
  values); language chip (2-letter, `mono-eyebrow`, hairline pill);
  freshness (published-commit vs working-tree: `● fresh` in `body-mid` /
  `● stale +N` in `stale` red); SCIP state (`avail | partial | failed |
  unavailable | unsupported | disabled` — `failed` in `status-degraded`);
  semantic on/off; total size; relative time.
- **Actions**: `⟳` reindex = outline pill icon-button; `⋯` overflow opens a
  small menu: *view detail*, *copy CLI command*, *forget…*.
- **Live log pane**: when status is `indexing`, the row expands (§2.5) to a
  `canvas-mid` well, 200px, `mono-md` log lines streaming (2s poll), last
  line followed by a `▌` block cursor in `accent-sunset` + `STREAMING`
  `mono-eyebrow`. Collapsed via chevron.
- **Index new repo**: `+ index new repo` outline pill (right-aligned with
  the eyebrow) opens an inline panel (not a modal): path input (mono),
  optional slug / language select / scheme / semantic toggle, and a
  white-filled `Run index` pill + `Cancel` link. Validation errors render
  inline under the input in `status-failed`, `body-sm`.
- **Empty state** (no repos yet): centered `display-xs` "No repositories
  indexed" + `body` one-liner "Run `jarvis index /path/to/repo` or use the
  button above." with the command in a `canvas-mid` mono block + copy icon.
- **Conflict state**: reindexing a locked repo surfaces an inline banner
  above the table — `canvas-soft` strip, `status-degraded` text: "already
  indexing (pid 38078)" — never a dialog.

### 4.2 Repo detail

```
── REPO · jarvis ────────────────────────────────────────────────────────────
jarvis                                    ● INDEXED · generation 14 · 412 MB

┌ SNAPSHOTS ─────────────┐ ┌ CAPABILITIES ────────────────────────────────┐
│ ▸ gen 14  current      │ │ findReferences   SCIP     ✓                  │
│   gen 13  09-10 14:02  │ │ callHierarchy    SCIP     ✓                  │
│   gen 12  09-08 21:44  │ │ goToDefinition   SCIP/TS  ✓                  │
│   …                    │ │ searchCode       Zoekt    ✓                  │
└────────────────────────┘ │ semanticSearch  vectors  ✗ no index          │
                           └──────────────────────────────────────────────┘
┌ RECOVERY ───────────────┐ ┌ PACKAGE GRAPH ──────────────────────────────┐
│ cause: scip stage fail  │ │ depends on (2):  pydantic · httpx           │
│ $ jarvis reindex jarvis │ │ depended on by (1, 2-hop): payments         │
│ [copy]                  │ │ [view graph →]                              │
└────────────────────────┘ └──────────────────────────────────────────────┘
┌ STORAGE ────────────────────────────────┐ ┌ INDEX LOG ──────────────────┐
│ scip  ▮▮▮▮▮▮▮▮▮▯▯▯  310 MB              │ │ (full log, mono, scroll)    │
│ zoekt ▮▮▮▮▯▯▯▯▯▯▯▯   98 MB              │ │                             │
│ lance ▮▮▯▯▯▯▯▯▯▯▯▯    4 MB              │ │                             │
└─────────────────────────────────────────┘ └─────────────────────────────┘
```

- Header: `mono-eyebrow` "── REPO · <slug>" + `display-xs` slug + status
  line (dot + status + generation + size).
- **Snapshots**: vertical list, newest first; current generation = `ink` +
  `accent-sunset` "current" tag; retired ones `body-mid`. Rows are
  information-only (no restore action in v1).
- **Capabilities**: one row per MCP tool — tool name (`mono-md`), provider
  (SCIP / Tree-sitter / Zoekt / vectors), and state; unavailable tools show
  `✗` + short reason. This mirrors the `getIndexStatus.capabilities`
  payload exactly.
- **Recovery** card only renders when there is something to recover
  (`degraded`/`failed`/`partial`): cause sentence (`body-sm`) + command in
  `canvas-mid` mono block + copy pill. This card is the **reason this view
  exists** — make it impossible to miss when present.
- **Package graph**: two mono lists (depends on / depended on by, hop count
  in parens); "view graph →" links to the graph overlay (§4.5).
- **Storage**: three hairline bars (scip/zoekt/lance) — bar fills in
  `canvas-mid`, the largest store's fill in `accent-sunset`; labels + sizes
  in `mono-md`.
- **Index log**: full-height `canvas-mid` well, `mono-md`, no wrap, scroll.

### 4.3 Search

```
┌──────────────────────────────────────────────────────────────────────────┐
│  where is token refresh handled?                        [all repos ▾]  ⟙ │
└──────────────────────────────────────────────────────────────────────────┘
  62 ms ── fused

┌ LEXICAL · ZOEKT ─────────┐ ┌ SEMANTIC · FUSED ────────┐ ┌ SYMBOLS ───────┐
│ src/auth/tokens.py:88    │ │ src/auth/refresh.py:12   │ │ refresh_token  │
│ def refresh_token(user): │ │ ▸ # file: refresh.py     │ │  fn · auth/    │
│ …bearer token exchange…  │ │ …rotates bearer token…   │ │ refresh.py:12  │
│                          │ │ score 0.71 · vector+lex  │ │                │
└──────────────────────────┘ └──────────────────────────┘ └────────────────┘
```

- Search bar: 56px, `body-lg` sans, `canvas-soft`, hairline border, 8px
  radius, full content width; repo filter dropdown right; `⟙` submit is a
  white pill. Search executes on Enter/click only (no as-you-type).
- Result columns have `mono-eyebrow` headers + a thin top rule in the
  column's identity (`accent-breeze` for semantic; `body-mid` for the
  others). Column widths 1:1:0.6.
- **Hit card**: path (`mono-md`, `ink`) : line, 2-line snippet
  (`canvas-mid` block) with matched terms in `accent-sunset`; semantic hits
  add a `score` + signal-provenance line (`body-mid`); symbol hits show
  kind (fn/class/method) + defining path.
- **Click any hit → source viewer overlay**: full-screen modal, 80% width —
  `mono-md` file with 48px line-number gutter (`body-mid`), the hit's
  range highlighted with an `accent-sunset` 12% alpha band + left 2px rule,
  URL-anchored (auto-scroll). Header: path + copy-path pill + close `✕`.
- Empty query state: centered ghost text "search code, symbols, or meaning"
  in `body-mid`. No-results state per column: "no matches" `body-mid` —
  columns never disappear.
- Total latency chip next to the eyebrow after a query (`62 ms`).

### 4.4 Playground

```
┌ TOOLS ────────────┐ ┌ findReferences ────────────────────────────────────┐
│ goToDefinition    │ │                                                   │
│ findReferences  ● │ │  repo      [jarvis        ]                       │
│ callHierarchy     │ │  symbol    [AuthService     ]                      │
│ typeHierarchy     │ │                                                   │
│ documentSymbols   │ │                       [ run ⏎ ]  142 ms            │
│ searchCode        │ ├───────────────────────────────────────────────────┤
│ semanticSearch    │ │ {                                                 │
│ blastRadius       │ │   "resolved": "python-project AuthService",       │
│ getIndexStatus    │ │   "occurrences": [ { "path": "src/auth.py", …     │
│ indexRepo         │ │                                                   │
└───────────────────┘ └───────────────────────────────────────────────────┘
```

- Left rail (240px): all 10 tools, `mono-md`, one per row; active = `ink` +
  `accent-sunset` left 2px rule; flat list, no grouping.
- Right panel: form fields generated from the tool's parameter docs —
  string/number/boolean inputs (`canvas-soft`, mono for machine-typed
  values), a short `body-sm` description under each; `run ⏎` white pill
  (Enter submits); latency chip appears after a run.
- Response: `canvas-mid` full-width block, `mono-md`, JSON pretty-printed,
  collapsible nodes; top-level `{"error": …}` payloads render with a
  `status-failed` left rule so failure is scannable.
- Runs are sequential; while running, the pill shows `running…` disabled.

### 4.5 Graph overlay (from repo detail "view graph →")

Full-screen modal: package nodes (hairline pill chips, `mono-md`) laid out
client-side as SVG; edges 1px `accent-dusk`; hovering a node highlights its
2-hop neighborhood (`accent-twilight`); the focal repo's chip is
white-filled. Close `✕`. Simple radial layout is fine — max ~dozens of
nodes.

---

## 5. Global components

| Component | Spec |
|---|---|
| Status dot + label | 6px dot (semantic color) + uppercase `mono-eyebrow` label; `INDEXING` dot pulses (§2.5) |
| Pill — primary | White fill, `#0a0a0a` text, 9999px radius; hover: `ink-hover` fill |
| Pill — outline | Transparent, `rgba(255,255,255,.25)` border, `ink` text; hover border `ink` |
| Pill — destructive | Outline pill, `status-failed` border + text ("forget") |
| Table | Header `canvas-soft` + `mono-eyebrow`; rows 48px, `hairline` dividers; hover `canvas-card` |
| Log/code/JSON well | `canvas-mid`, 8px radius, `mono-md` 13–14px, scroll, no wrap |
| Copy button | Ghost icon pill (borderless) → on click flashes `accent-sunset` + "copied" 1.2s |
| Inline banner | `canvas-soft` strip, 2px semantic-color left rule, `body-sm` |
| Modal | `canvas` fill, 8px radius, hairline border, 24px padding, scrim `rgba(0,0,0,.6)`; no shadow on the card |
| Confirm dialog (forget) | Modal: title "Forget <slug>?", `body` copy "Removes the index, search shards, and registry entry. The repository on disk is untouched."; mono input "type <slug> to confirm"; destructive pill disabled until input === slug |
| Toast | Bottom-right, `canvas-card`, hairline, `body-sm` + mono detail; auto-dismiss 4s; action acknowledgements only ("reindex started") |
| Skeleton | Loading tables/logs: `canvas-soft` blocks at final layout size, no shimmer |

---

## 6. Interaction & behavior notes (what "live" means)

- **Polling**: the repos table re-fetches every **2s** while any row is
  `indexing`, otherwise every **10s**. Log panes poll on the same cadence.
  Design for values that change under the user: no layout shift on refresh
  (fixed cell widths for sizes/times; relative times may tick).
- **State transitions to choreograph**: `indexing → indexed` (pulse stops,
  dot turns white, toast "indexed jarvis", log pane collapses after 1.5s
  delay); `indexing → failed` (dot red, banner with cause, recovery card
  appears on detail).
- **409 conflict** (reindex while running): inline banner, not a modal (§4.1).
- **Forget**: destructive pill → typed-confirm modal (§5) → on success the
  row leaves the table with a 150ms fade.
- **No network spinners**: everything is local. The only long-running thing
  is indexing, and it streams logs.

---

## 7. Accessibility

- **Contrast — verified pairs** (WCAG AA: 4.5:1 body text, 3:1 large text
  and UI components). Computed against the token sheet; `body-mid` was
  lightened from the stock `#7d8187` to `#82878d` specifically to clear
  AA on nested surfaces:

  | Pair | Ratio | AA body |
  |---|---|---|
  | `ink` on `canvas` | 19.8:1 | ✓ |
  | `body-mid` `#82878d` on `canvas` / `canvas-card` / `canvas-soft` | 5.5 / 4.9 / 4.7:1 | ✓ |
  | `accent-sunset` on `canvas` | 7.6:1 | ✓ |
  | `accent-sunset-soft` on `canvas-mid` | 7.3:1 | ✓ |
  | `status-failed` `#ff5c5c` on `canvas` | 6.5:1 | ✓ |
  | `status-partial` `#ffc285` on `canvas` | 12.6:1 | ✓ |

  Two rules follow from the numbers: (1) **inside `canvas-mid` wells**
  (logs, code, JSON) emphasis uses `accent-sunset-soft`, never
  `accent-sunset` (4.4:1 there) and never `body-mid` (3.2:1 there) —
  well text is `body`/`ink`; (2) `body-mid` is cleared for captions on
  all surfaces down to `canvas-soft`, but not inside wells.
- Status is **never color-only**: dot + mono label always travel together.
- Focus: 1px `ink` outline on all interactive elements via
  `:focus-visible` only.
- Hit targets ≥ 32px (icon pills get 32px click area even at 24px visual).
- `prefers-reduced-motion` disables the pulse and transitions (§2.5).
- Log/JSON wells: `line-height ≥ 1.5`, horizontal scroll (no wrap) with
  visible scrollbars (`canvas-mid` track, `body-mid` thumb).

---

## 8. Design constraints (hard)

1. **Dark-only.** No light theme, no toggle. The console lives in the
   terminal-adjacent world.
2. **Zero network**: no web fonts, no icon CDNs, no telemetry pixels. Icons
   must be inline SVG or a system glyph set. Icon style: 1.5px stroke,
   square caps, 16/20px — geometric, no rounded-corner friendly icons.
3. **No shadows, no gradients, no glass.** Elevation = surface + hairline.
4. **No illustrations or emoji.** The console is an instrument.
5. Accent budget: `accent-sunset` appears in at most **two places per
   screen** (plus live/streaming states) — enforce the restraint.
6. Framework-free implementation (vanilla JS/CSS): designs must be
   expressible as plain DOM + CSS — no parallax, no scroll-linked
   animation, no layout tricks requiring JS measurement beyond the graph
   overlay.

---

## 9. Deliverables we need from you

1. **Hi-fi screens** (1440px): all four views + graph overlay + source
   viewer, steady state with realistic data (use the §4 wireframe content
   as the copy deck).
2. **State screens**: Repos with live indexing row expanded; empty state;
   failure state with recovery banner; forget confirm modal; search
   no-results.
3. **Component sheet**: every §5 component in variants + states (rest /
   hover / focus / active / disabled).
4. **Token sheet**: final hex values (incl. semantic adjustments from §7
   verification) exportable as CSS custom properties:
   `--canvas`, `--canvas-card`, `--canvas-soft`, `--canvas-mid`,
   `--hairline`, `--ink`, `--body`, `--body-mid`, `--accent-sunset`,
   `--accent-sunset-soft`, `--accent-breeze`, `--status-failed`, …
5. **Type + spacing spec**: confirmation of §2.2/§2.4 or your correction.

Format: Figma (preferred) with dev-mode-ready spacing, or any tool that
exports precise measurements. Reference the wireframes in §4 — they are
layout-accurate to the product spec; refine proportions, not structure.
