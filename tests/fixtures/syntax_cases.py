"""Cross-language declaration extraction corpus for `tests/test_syntax.py`
(spec TSI-03 "Declaration contract"). One tiny source snippet per parser
selection with its complete expected qualified-name set. Source analysis
in the originating design doc is not a claim that these snippets had
already executed successfully -- `test_syntax.py` is the actual proof.
"""
from __future__ import annotations

CASES: tuple[tuple[str, str, str, frozenset[str]], ...] = (
    ("python", "a.py", "class Box:\n    def run(self):\n        return 1\ntype Alias = Box\n", frozenset({"Box", "Box.run", "Alias"})),
    ("javascript", "a.jsx", "const View = () => <div/>; class Box { field = () => 1; run() {} }", frozenset({"View", "Box", "Box.field", "Box.run"})),
    ("typescript", "a.ts", "namespace N { export interface P { call(): void; } export type Alias = P; }", frozenset({"N", "N.P", "N.P.call", "N.Alias"})),
    ("tsx", "a.tsx", "namespace UI { export const View = () => <div/>; }", frozenset({"UI", "UI.View"})),
    ("java", "A.java", "class Box { Box() {} void run() {} } record R(int x) {}", frozenset({"Box", "Box.Box", "Box.run", "R"})),
    ("kotlin", "a.kt", "class Box { fun run() {} }\ntypealias Alias = Box\n", frozenset({"Box", "Box.run", "Alias"})),
    ("swift", "a.swift", "struct Box { func run() {} }\nprotocol P { associatedtype Item; func call() }\ntypealias Alias = Box\n", frozenset({"Box", "Box.run", "P", "P.Item", "P.call", "Alias"})),
    ("go", "a.go", "package p\ntype Box struct{}\ntype Alias = Box\ntype P interface { Run() }\nfunc outer() {}\n", frozenset({"p", "p.Box", "p.Alias", "p.P", "p.P.Run", "p.outer"})),
    ("ruby", "a.rb", "module N\n class Box\n  def run; end\n  def self.make; end\n end\nend\n", frozenset({"N", "N.Box", "N.Box.run", "N.Box.make"})),
    ("rust", "a.rs", "mod n { struct Box; type Alias = Box; trait P { type Item; fn run(&self); } }", frozenset({"n", "n.Box", "n.Alias", "n.P", "n.P.Item", "n.P.run"})),
    ("c", "a.c", "struct Box { int value; }; typedef int (*Handler)(void); int (*factory(void))(int); int (*slot)(int);", frozenset({"Box", "Handler", "factory"})),
    ("cpp", "a.cpp", "namespace N { using Alias = int; class Box { public: int run(); }; }", frozenset({"N", "N.Alias", "N.Box", "N.Box.run"})),
    ("csharp", "a.cs", "namespace N { class Box { void Run() { void Inner() {} } } record R(int X); delegate void D(); }", frozenset({"N", "N.Box", "N.Box.Run", "N.Box.Run.Inner", "N.R", "N.D"})),
    ("php", "a.php", "<?php namespace N { class Box { function run() {} } enum E { case A; } }", frozenset({"N", "N.Box", "N.Box.run", "N.E"})),
    ("scala", "a.scala", "package n { object Box { type Alias = Int; def run(): Unit = () }; trait P { def call(): Unit } }", frozenset({"n", "n.Box", "n.Box.Alias", "n.Box.run", "n.P", "n.P.call"})),
    ("bash", "a.sh", "outer() { function inner { :; }; }\nfunction other() { :; }\n", frozenset({"outer", "outer.inner", "other"})),
    ("sql", "a.sql", "CREATE TABLE users (id integer); CREATE VIEW active_users AS SELECT id FROM users; CREATE FUNCTION answer() RETURNS integer LANGUAGE SQL AS 'SELECT 1';", frozenset({"users", "active_users", "answer"})),
)
