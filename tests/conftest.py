"""Shared test helpers."""


class BlockImportFinder:
    """Meta-path finder that fails one import as if the module were absent.

    `monkeypatch.setitem(sys.modules, name, None)` simulates "not installed"
    via CPython's None-in-sys.modules sentinel, but Cython's compiled `import
    x` takes a fast path that reads sys.modules directly and does not
    replicate that sentinel check -- it returns the cached None object
    instead of raising ImportError. Raising from find_spec() instead forces
    a real import-machinery failure that both CPython and Cython consult
    identically, so this exercises the same failure real users hit when the
    package genuinely isn't installed.
    """

    def __init__(self, blocked_name):
        self._blocked_name = blocked_name

    def find_spec(self, fullname, path, target=None):
        if fullname == self._blocked_name:
            raise ModuleNotFoundError(f"No module named {fullname!r}")
        return None
