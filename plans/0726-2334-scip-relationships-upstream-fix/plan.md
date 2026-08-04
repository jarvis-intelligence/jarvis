# Upstream Fix: Populate `global_symbols.relationships` in `scip expt-convert`

> **STATUS: Tasks 1-4 executed.** PR opened as
> [scip-code/scip#465](https://github.com/scip-code/scip/pull/465) — 12/12 CI checks
> green, both commits signed and authored as `phuongddx <95doanphuong@gmail.com>`.
> Local clone: `~/Projects/scip-upstream`, branch
> `fix/convert-populate-relationships`. Task 5 (link the PR from the jarvis
> plan) is done. Remaining: await maintainer review.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land a PR on [scip-code/scip](https://github.com/scip-code/scip) making `scip expt-convert` write `global_symbols.relationships`, closing the half of [#464](https://github.com/scip-code/scip/issues/464) that unblocks type-hierarchy consumers.

**Architecture:** One narrow change to `cmd/scip/convert.go`: extend `insertGlobalSymbols()` to serialize `SymbolInformation.relationships` into the already-declared `relationships BLOB` column, framed identically to how `chunks.occurrences` is framed today (zstd-compressed `proto.Marshal` of a wrapper message). Test coverage follows the module's existing `TestConvert_SmokeTest` + `check*` helper pattern.

**Tech Stack:** Go (upstream repo, `go1.25` in their CI), `zombiezen.com/go/sqlite` + `sqlitex`, `google.golang.org/protobuf`, `klauspost/compress/zstd`, `stretchr/testify/require`.

## Global Constraints

- **This is a third-party public repo.** Work on a fork under `phuongddx`, never push to `scip-code/scip` directly. One focused PR; no drive-by refactoring.
- **Scope is `relationships` only — deliberately not `signature`.** The column is `signature`, but the proto field is `Signature signature_documentation = 7` (verified in scip.proto v0.9.0); there is no `signature` field on `SymbolInformation`. That naming divergence means the intended mapping is a maintainer decision, so the PR asks rather than assumes.
- **Match the file's existing blob framing.** `chunks.occurrences` is `zstd(proto.Marshal(&scip.Document{Occurrences: ...}))` (`Chunk.toDBFormat`). Relationships must be `zstd(proto.Marshal(&scip.SymbolInformation{Relationships: ...}))` — a wrapper message, not a bare repeated field.
- **Do not change `scip.proto`.** No schema change, therefore no `nix run .#proto-generate` and no regenerated bindings.
- **Do not change the SQLite schema.** `relationships BLOB` already exists at `convert.go:200`; only the INSERT changes.
- **`insertGlobalSymbols` is called with synthetic symbols.** `convert.go:345` passes `&scip.SymbolInformation{Symbol: occ.Symbol}` for occurrence-only symbols. Empty relationships must bind SQL NULL, never a zstd frame wrapping an empty message.
- **No CLA, DCO, or PR template exists** in that repo (verified) — a plain PR is correct.
- **Snapshot regeneration is `go test ./cmd/scip -update-snapshots`** per `docs/Development.md`. Run it only if snapshots actually drift; an unnecessary snapshot churn weakens the PR.

## File Structure

| File | Change |
|---|---|
| `cmd/scip/convert.go` (modify) | `insertGlobalSymbols()`: add the `relationships` column to the INSERT and bind it; new `marshalRelationships()` helper mirroring `Chunk.toDBFormat` |
| `cmd/scip/convert_test.go` (modify) | Give `testIndex1()` a symbol with relationships; add a `checkRelationships()` helper and call it from `TestConvert_SmokeTest` |

Two files, one behavior change. Everything else in that repo stays untouched.

---

### Task 1: Fork, clone, and establish a green baseline

**Files:** none modified (environment setup)

**Interfaces:**
- Consumes: nothing
- Produces: a local clone at `~/Projects/scip-upstream` on a feature branch, with `go test ./cmd/scip` passing *before* any change — so a later failure is unambiguously ours.

- [ ] **Step 1: Fork and clone**

```bash
gh repo fork scip-code/scip --clone=false --remote=false
cd ~/Projects && rm -rf scip-upstream
gh repo clone phuongddx/scip scip-upstream -- --depth=50
cd ~/Projects/scip-upstream
git remote add upstream https://github.com/scip-code/scip.git
git remote -v
```

- [ ] **Step 2: Confirm the defect exists on this checkout**

```bash
cd ~/Projects/scip-upstream
grep -n "relationships BLOB" cmd/scip/convert.go
grep -n "INSERT INTO global_symbols" -A2 cmd/scip/convert.go
```
Expected: the column is declared (~line 200) and the INSERT names only five columns (`symbol, display_name, kind, documentation, enclosing_symbol`). If the INSERT already includes `relationships`, the bug was fixed upstream — stop and close #464 instead of opening a PR.

- [ ] **Step 3: Establish the green baseline**

```bash
cd ~/Projects/scip-upstream
go version
go build ./... 2>&1 | tail -5
go test ./cmd/scip 2>&1 | tail -15
```
Expected: build succeeds and `go test ./cmd/scip` passes. If the baseline is already red, record exactly which tests fail before touching anything — do not attempt to fix unrelated pre-existing failures in this PR.

- [ ] **Step 4: Branch**

```bash
cd ~/Projects/scip-upstream
git checkout -b fix/convert-populate-relationships
```

- [ ] **Step 5: No commit yet**

Nothing to commit — this task only establishes the workspace and the baseline. Proceed to Task 2.

---

### Task 2: Write the failing test

**Files:**
- Modify: `cmd/scip/convert_test.go`

**Interfaces:**
- Consumes: `testIndex1()`, `TestConvert_SmokeTest`, and the `check*(t, index, db)` helper convention already in the file
- Produces: `checkRelationships(t *testing.T, index *scip.Index, db *sqlite.Conn)`; `testIndex1()` gains a second symbol carrying a relationship

The file's conventions, verified: `stretchr/testify/require` for assertions, and queries run through `sqlitex.ExecuteTransient(db, query, &sqlitex.ExecOptions{ResultFunc: ...})`.

- [ ] **Step 1: Give the fixture a relationship**

In `cmd/scip/convert_test.go`, replace `testIndex1()` with a version that adds an implementation edge. Keep the existing symbol and document shape so the other `check*` helpers keep passing:

```go
func testIndex1() *scip.Index {
	pkg1S1Sym := "scip-go go . . pkg1/S1#"
	pkg1I1Sym := "scip-go go . . pkg1/I1#"
	return &scip.Index{
		Documents: []*scip.Document{
			{
				RelativePath: "a.go",
				Occurrences: []*scip.Occurrence{
					{Symbol: pkg1S1Sym, Range: []int32{10, 3, 6}, SymbolRoles: int32(scip.SymbolRole_Definition)},
					{Symbol: pkg1I1Sym, Range: []int32{20, 3, 6}, SymbolRoles: int32(scip.SymbolRole_Definition)},
				},
				Symbols: []*scip.SymbolInformation{
					// S1 implements I1 — the relationship the converter must persist.
					{
						Symbol: pkg1S1Sym,
						Relationships: []*scip.Relationship{
							{Symbol: pkg1I1Sym, IsImplementation: true},
						},
					},
					{Symbol: pkg1I1Sym},
				},
			},
			{
				RelativePath: "b.go",
				Occurrences: []*scip.Occurrence{
					{Symbol: pkg1S1Sym, Range: []int32{15, 9, 12}},
				},
			},
		},
	}
}
```

- [ ] **Step 2: Add the check helper**

Append to `cmd/scip/convert_test.go`:

```go
// checkRelationships asserts that SymbolInformation.relationships survives the
// round-trip into global_symbols.relationships. The column is declared in the
// schema, so a converter that never writes it produces a database that queries
// cleanly while silently reporting every symbol as having no relationships.
func checkRelationships(t *testing.T, index *scip.Index, db *sqlite.Conn) {
	expected := map[string][]*scip.Relationship{}
	for _, doc := range index.Documents {
		for _, sym := range doc.Symbols {
			if len(sym.Relationships) > 0 {
				expected[sym.Symbol] = sym.Relationships
			}
		}
	}
	require.NotEmpty(t, expected, "fixture must exercise at least one relationship")

	decoder, err := zstd.NewReader(nil)
	require.NoError(t, err)
	defer decoder.Close()

	found := map[string][]*scip.Relationship{}
	query := "SELECT symbol, relationships FROM global_symbols WHERE relationships IS NOT NULL"
	err = sqlitex.ExecuteTransient(db, query, &sqlitex.ExecOptions{
		ResultFunc: func(stmt *sqlite.Stmt) error {
			symbol := stmt.ColumnText(0)
			blob := make([]byte, stmt.ColumnLen(1))
			stmt.ColumnBytes(1, blob)

			raw, err := decoder.DecodeAll(blob, nil)
			if err != nil {
				return err
			}
			var info scip.SymbolInformation
			if err := proto.Unmarshal(raw, &info); err != nil {
				return err
			}
			found[symbol] = info.Relationships
			return nil
		},
	})
	require.NoError(t, err)

	require.Len(t, found, len(expected))
	for symbol, want := range expected {
		got, ok := found[symbol]
		require.True(t, ok, "no relationships stored for %s", symbol)
		require.Len(t, got, len(want))
		for i := range want {
			require.Equal(t, want[i].Symbol, got[i].Symbol)
			require.Equal(t, want[i].IsImplementation, got[i].IsImplementation)
		}
	}
}

// Symbols carrying no relationships must store SQL NULL rather than a
// compressed frame wrapping an empty message — insertGlobalSymbols is also
// called with synthetic SymbolInformation values that only have a Symbol.
func checkRelationshipsNullWhenAbsent(t *testing.T, db *sqlite.Conn) {
	var nullCount int
	query := "SELECT COUNT(*) FROM global_symbols WHERE relationships IS NULL"
	err := sqlitex.ExecuteTransient(db, query, &sqlitex.ExecOptions{
		ResultFunc: func(stmt *sqlite.Stmt) error {
			nullCount = stmt.ColumnInt(0)
			return nil
		},
	})
	require.NoError(t, err)
	require.Greater(t, nullCount, 0, "symbols without relationships must be NULL, not an empty blob")
}
```

Add the imports this needs to the file's import block — `github.com/klauspost/compress/zstd` and `google.golang.org/protobuf/proto`. Check what is already imported first:
```bash
sed -n '1,20p' cmd/scip/convert_test.go
```
and add only what is missing.

- [ ] **Step 3: Call the helpers from the smoke test**

In `TestConvert_SmokeTest`, alongside the existing `checkDocuments`/`checkSymbols`/`checkOccurrences` calls, add:

```go
	checkRelationships(t, index, db)
	checkRelationshipsNullWhenAbsent(t, db)
```

Read the surrounding lines first so the call site matches how `index` and `db` are named there:
```bash
sed -n '18,52p' cmd/scip/convert_test.go
```

- [ ] **Step 4: Run the test to verify it fails**

```bash
cd ~/Projects/scip-upstream
go test ./cmd/scip -run TestConvert_SmokeTest -v 2>&1 | tail -25
```
Expected: FAIL in `checkRelationships` — `found` is empty (`require.Len(t, found, len(expected))` gets 0 vs 1), because the converter never writes the column. This failure *is* the reproduction of #464.

- [ ] **Step 5: Commit the failing test**

```bash
cd ~/Projects/scip-upstream
git add cmd/scip/convert_test.go
git commit -m "test: cover global_symbols.relationships round-trip

Currently failing: insertGlobalSymbols never writes the relationships
column, so the value is NULL for every symbol."
```

---

### Task 3: Populate the column

**Files:**
- Modify: `cmd/scip/convert.go`

**Interfaces:**
- Consumes: `Converter.zstdWriter` (already a field), `Chunk.toDBFormat`'s framing convention
- Produces: `(*Converter).marshalRelationships(rels []*scip.Relationship) ([]byte, error)`; `insertGlobalSymbols` binds a sixth parameter

- [ ] **Step 1: Add the serialization helper**

In `cmd/scip/convert.go`, add immediately above `insertGlobalSymbols` (~line 423):

```go
// marshalRelationships serializes a symbol's relationships for the
// global_symbols.relationships column, framed the same way
// Chunk.toDBFormat frames chunks.occurrences: a zstd-compressed wrapper
// message rather than a bare repeated field, so the blob stays
// self-describing and forward-compatible.
func (c *Converter) marshalRelationships(rels []*scip.Relationship) ([]byte, error) {
	blob, err := proto.Marshal(&scip.SymbolInformation{Relationships: rels})
	if err != nil {
		return nil, fmt.Errorf("failed to serialize relationships: %w", err)
	}

	var buf bytes.Buffer
	c.zstdWriter.Reset(&buf)
	if _, err = c.zstdWriter.Write(blob); err != nil {
		return nil, fmt.Errorf("compression error: %w", err)
	}
	if err = c.zstdWriter.Close(); err != nil {
		return nil, fmt.Errorf("flushing encoder: %w", err)
	}
	return buf.Bytes(), nil
}
```

`bytes`, `fmt`, and `proto` are already imported by this file (`Chunk.toDBFormat` uses all three) — verify rather than assume:
```bash
sed -n '1,25p' cmd/scip/convert.go
```

- [ ] **Step 2: Extend the INSERT and bind the value**

In `insertGlobalSymbols`, change the prepared statement to include the column:

```go
	insertStmt, err := c.conn.Prepare(
		`INSERT INTO global_symbols (symbol, display_name, kind, documentation, enclosing_symbol, relationships)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT(symbol) DO NOTHING
		RETURNING id`)
```

Then, after the existing `enclosing_symbol` bind (parameter 5) and before `insertStmt.Step()`, add:

```go
	// Bind NULL rather than an empty frame when there are no relationships:
	// insertGlobalSymbols is also called with synthetic SymbolInformation
	// values that carry only a Symbol, and a compressed empty message would
	// make "no relationships" indistinguishable from "relationships present
	// but empty" for consumers.
	if len(symbol.Relationships) == 0 {
		insertStmt.BindNull(6)
	} else {
		relationshipsBlob, err := c.marshalRelationships(symbol.Relationships)
		if err != nil {
			return 0, err
		}
		insertStmt.BindBytes(6, relationshipsBlob)
	}
```

- [ ] **Step 3: Run the test to verify it passes**

```bash
cd ~/Projects/scip-upstream
go test ./cmd/scip -run TestConvert_SmokeTest -v 2>&1 | tail -20
```
Expected: PASS.

- [ ] **Step 4: Run the full package suite**

```bash
cd ~/Projects/scip-upstream
go test ./cmd/scip 2>&1 | tail -20
```
Expected: PASS. If a snapshot test drifts, regenerate per `docs/Development.md` and inspect the diff before accepting it:
```bash
go test ./cmd/scip -update-snapshots
git diff --stat
```
A snapshot change here would be surprising — this touches only the SQLite writer, not `scip print`/`snapshot` output. If snapshots do change, understand why before committing; unexplained churn will sink the PR.

- [ ] **Step 5: Build everything and vet**

```bash
cd ~/Projects/scip-upstream
go build ./... 2>&1 | tail -5
go vet ./cmd/scip 2>&1 | tail -10
gofmt -l cmd/scip/convert.go cmd/scip/convert_test.go
```
Expected: no output from any of them (`gofmt -l` listing a file means it needs formatting — run `gofmt -w` on it).

- [ ] **Step 6: Verify against a real index end-to-end**

Prove it on actual data, not just the fixture:

Build a real index whose symbols carry relationships. `scip-typescript` emits an
`is_implementation` edge for a class implementing an interface — verified to work:

```bash
rm -rf /tmp/relsrc && mkdir /tmp/relsrc && cd /tmp/relsrc
cat > tsconfig.json <<'EOF'
{"compilerOptions":{"target":"ES2020","module":"commonjs","strict":true,"outDir":"out"},"include":["*.ts"]}
EOF
cat > a.ts <<'EOF'
export interface Animal { sound(): string }
export class Dog implements Animal { sound(): string { return "woof" } }
EOF
npm init -y >/dev/null 2>&1
scip-typescript index --no-progress-bar
# Confirm the .scip really carries a relationship (strip ANSI before grepping --
# `scip print` colorizes field names, so a naive grep for "Relationships:" misses):
scip print index.scip | sed 's/\x1b\[[0-9;]*m//g' | grep -c "Relationships: \[\]\*scip.Relationship{$"
```
Expected: at least `1`.

Now compare unpatched against patched:

```bash
cd ~/Projects/scip-upstream
go build -o /tmp/scip-patched ./cmd/scip
/tmp/scip-patched expt-convert --output /tmp/relsrc/after.db /tmp/relsrc/index.scip
sqlite3 /tmp/relsrc/after.db \
  "SELECT COUNT(*) FROM global_symbols; SELECT COUNT(*) FROM global_symbols WHERE relationships IS NOT NULL;"
```
Expected: 5 symbols and **at least 1** with relationships. Measured before the fix on this exact index: 5 symbols, **0** with relationships — so a non-zero second number is the proof.

- [ ] **Step 7: Commit**

```bash
cd ~/Projects/scip-upstream
git add cmd/scip/convert.go
git commit -m "fix: populate global_symbols.relationships in expt-convert

The relationships column was declared in the schema but never written, so
every symbol reported no relationships while queries still succeeded --
making type-hierarchy features silently impossible to build on the SQLite
output. Serialized like chunks.occurrences: a zstd-compressed wrapper
message. Symbols without relationships bind NULL rather than an empty frame.

Refs #464"
```

---

### Task 4: Open the pull request

**Files:** none modified

**Interfaces:**
- Consumes: the branch from Tasks 1-3
- Produces: a PR on `scip-code/scip` referencing #464

- [ ] **Step 1: Review the complete diff before publishing**

```bash
cd ~/Projects/scip-upstream
git diff upstream/main...HEAD
git diff --stat upstream/main...HEAD
```
Expected: exactly two files changed. Confirm there is no stray formatting churn, no unrelated file, and no `scip.proto`/bindings change.

- [ ] **Step 2: Push the branch to the fork**

```bash
cd ~/Projects/scip-upstream
git push -u origin fix/convert-populate-relationships
```

- [ ] **Step 3: Open the PR**

```bash
cd ~/Projects/scip-upstream
cat > /tmp/pr-body.md <<'BODY'
Fixes half of #464: `global_symbols.relationships` is declared in the
`expt-convert` schema but never written, so it is `NULL` for every row in every
generated database.

Because the column exists and queries against it succeed, the omission is
silently lossy — a consumer cannot distinguish "this symbol has no
relationships" from "the converter never stored any." `relationships` is the
only source for implementation and type-definition edges, so type-hierarchy
features built on the SQLite output cannot work at all, and the failure is easy
to misattribute to the language indexer that produced the `.scip`.

## Change

`insertGlobalSymbols()` now serializes `SymbolInformation.relationships` into the
existing column. The blob is framed exactly like `chunks.occurrences` in
`Chunk.toDBFormat` — a zstd-compressed `proto.Marshal` of a wrapper message
(`SymbolInformation` here, `Document` there) — so the encoding stays consistent
within the file and remains self-describing.

Symbols with no relationships bind SQL `NULL` rather than a compressed empty
message. That matters because `insertGlobalSymbols` is also called with synthetic
`&scip.SymbolInformation{Symbol: occ.Symbol}` values for occurrence-only symbols;
storing an empty frame for those would make "absent" and "present but empty"
indistinguishable.

No schema change and no `scip.proto` change, so no regenerated bindings.

## Testing

`TestConvert_SmokeTest`'s fixture now includes a symbol with an
`is_implementation` relationship, and two new helpers follow the existing
`check*` pattern:

- `checkRelationships` decompresses and unmarshals the stored blob and asserts a
  full round-trip of the relationship data.
- `checkRelationshipsNullWhenAbsent` asserts symbols without relationships store
  `NULL`.

The test fails on `main` (nothing is written) and passes with this change.

## Not included: `signature`

#464 also notes that `global_symbols.signature` is never populated. I left it
alone deliberately: the column is named `signature`, but `SymbolInformation` has
no such field — the nearest is `Signature signature_documentation = 7`. Mapping
one onto the other looks like a decision for maintainers rather than something to
guess at in this PR. Happy to add it in a follow-up (or here) given a preference.
BODY

gh pr create --repo scip-code/scip \
  --base main \
  --head "phuongddx:fix/convert-populate-relationships" \
  --title "fix: populate global_symbols.relationships in expt-convert" \
  --body-file /tmp/pr-body.md
```

- [ ] **Step 4: Confirm the PR and watch CI**

```bash
gh pr view --repo scip-code/scip --json number,url,state,author --jq '{number, url, state, author: .author.login}'
gh pr checks --repo scip-code/scip <PR_NUMBER> --watch 2>&1 | tail -20
```
Expected: the PR is open and its checks pass. If CI fails, read the log before pushing a fix — do not force-push blindly:
```bash
gh run view --repo scip-code/scip --log-failed | head -40
```

- [ ] **Step 5: Cross-link the issue**

```bash
gh issue comment 464 --repo scip-code/scip \
  --body "Opened a PR for the \`relationships\` half: <PR_URL>. Left \`signature\` out because the column name and the proto field (\`signature_documentation\`) diverge — see the PR description."
```

---

### Task 5: Record the outcome locally

**Files:**
- Modify: `plans/0726-2306-index-search-consistency/plan.md`

**Interfaces:**
- Consumes: the merged-or-open PR from Task 4
- Produces: an accurate cross-reference in the jarvis plan

- [ ] **Step 1: Link the PR from the jarvis plan**

The jarvis plan's Task 7 and Task 8 currently cite issue #464 as the upstream status. Add the PR alongside it so a future reader knows a fix is in flight:

```bash
cd /Users/ddphuong/Projects/jarvis
grep -n "scip-code/scip/issues/464" plans/0726-2306-index-search-consistency/plan.md
```

For each hit, extend the reference to name the PR too, e.g.:

```markdown
Reported upstream as [scip-code/scip#464](https://github.com/scip-code/scip/issues/464),
with a fix proposed in [scip-code/scip#<PR>](https://github.com/scip-code/scip/pull/<PR>).
```

- [ ] **Step 2: Commit**

```bash
cd /Users/ddphuong/Projects/jarvis
git add plans/0726-2306-index-search-consistency/plan.md
git commit -m "docs: link the upstream relationships PR from the jarvis plan"
```

---

## Notes for the implementer

**Do not touch `signature` in this PR.** The proto has no `signature` field; the closest is `signature_documentation` (field 7). Guessing at that mapping would invite a review objection and risk the whole PR. It is called out explicitly in the PR body as a question for maintainers.

**The blob framing is the one real design decision.** Compressing a small per-symbol blob is arguably wasteful, and a reviewer may prefer an uncompressed `proto.Marshal`, or a bare `repeated Relationship` without the wrapper. The PR chooses consistency with `chunks.occurrences` because matching in-file precedent is the most defensible default — but treat this as negotiable and change it if a maintainer asks. The `NULL`-when-empty behavior should be defended more firmly, since it preserves a real distinction.

**Never push to `scip-code/scip` directly.** All work goes through the `phuongddx` fork. Task 4 Step 1 exists to catch an accidental unrelated file before it becomes public.

**If the baseline in Task 1 Step 3 is red**, record which tests fail and stop. Fixing someone else's pre-existing failures inside this PR would make it unreviewable.
