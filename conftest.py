"""Root conftest: ensure `import server` resolves to the correct
sub-project module when all tests run from the workspace root.

Each sub-project (jira-mcp, confluence-mcp, doc2md-mcp) has its own
``server.py``.  We pre-import each one under a unique alias and then
swap ``sys.modules["server"]`` to point at the right one before each
test file is collected and before each test runs.
"""

import importlib
import sys
import pathlib

_root = pathlib.Path(__file__).resolve().parent

_SUBPROJECTS = ["jira-mcp", "confluence-mcp", "doc2md-mcp"]
_SERVER_DIRS = {name: _root / name for name in _SUBPROJECTS}
_SERVER_MODULES: dict[str, object] = {}


def _preload_servers() -> None:
    """Import each sub-project's server.py under a unique key."""
    for name, dirpath in _SERVER_DIRS.items():
        ds = str(dirpath)
        if ds not in sys.path:
            sys.path.insert(0, ds)
        sys.modules.pop("server", None)
        old_path = list(sys.path)
        sys.path = [ds] + [p for p in sys.path if p != ds]
        try:
            mod = importlib.import_module("server")
            _SERVER_MODULES[name] = mod
        except Exception:
            pass
        finally:
            sys.path = old_path

    sys.modules.pop("server", None)


_preload_servers()


def _activate_subproject(test_path: pathlib.Path) -> None:
    for name, dirpath in _SERVER_DIRS.items():
        try:
            test_path.relative_to(dirpath)
        except ValueError:
            continue
        mod = _SERVER_MODULES.get(name)
        if mod is not None:
            sys.modules["server"] = mod
            ds = str(dirpath)
            if ds not in sys.path:
                sys.path.insert(0, ds)
        return


def pytest_collectstart(collector) -> None:
    if hasattr(collector, "fspath") and collector.fspath:
        _activate_subproject(pathlib.Path(collector.fspath).resolve())


def pytest_runtest_setup(item) -> None:
    _activate_subproject(pathlib.Path(item.fspath).resolve())
