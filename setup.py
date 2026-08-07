import os
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_py import build_py as _build_py

# scip_pb2.py is machine-generated from the public SCIP protobuf -- nothing
# proprietary -- and __init__.py is trivial. Everything else is compiled to a
# native extension when JARVIS_COMPILE=1 (set only by release CI) so published
# wheels carry no readable source. Dev installs never set the flag.
KEEP_PLAIN = {"__init__.py", "scip_pb2.py"}
COMPILE = os.environ.get("JARVIS_COMPILE") == "1"


class StripCompiledSources(_build_py):
    # build_py packages every .py it finds even when build_ext compiles the
    # same modules; without this override the wheel would ship source AND
    # binaries, defeating the point.
    def find_package_modules(self, package, package_dir):
        modules = super().find_package_modules(package, package_dir)
        if not COMPILE:
            return modules
        return [
            (pkg, mod, path)
            for pkg, mod, path in modules
            if Path(path).name in KEEP_PLAIN
        ]


if COMPILE:
    from Cython.Build import cythonize

    # Explicit Extension names: cythonize's dotted-name inference does not
    # understand src-layout and would emit `src.jarvis.query`. build_dir
    # isolates generated .c files to a temporary build directory so they
    # don't pollute src/jarvis/ or end up in wheels.
    ext_modules = cythonize(
        [
            Extension(f"jarvis.{path.stem}", [str(path)])
            for path in sorted(Path("src/jarvis").glob("*.py"))
            if path.name not in KEEP_PLAIN
        ],
        compiler_directives={"language_level": "3"},
        nthreads=os.cpu_count() or 1,
        build_dir="build/cython",
    )
else:
    ext_modules = []

setup(ext_modules=ext_modules, cmdclass={"build_py": StripCompiledSources})
