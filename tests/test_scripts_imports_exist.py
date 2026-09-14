"""P2: scripts/prep_remnawave_v3_migration.py imported
database.core.initialize_database, which does not exist (the function is
init_db) — the one-off prep script crashed on start. Every name a script
imports from a project module (also inside functions) must exist.
"""
import ast
import glob
import importlib

import pytest

PROJECT_PREFIXES = ("database", "app.", "config")


def _imports(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "config" or node.module.startswith(PROJECT_PREFIXES):
                for alias in node.names:
                    yield node.module, alias.name, node.lineno


@pytest.mark.parametrize("path", sorted(glob.glob("scripts/*.py")))
def test_script_project_imports_resolve(path):
    missing = []
    for module, name, line in _imports(path):
        mod = importlib.import_module(module)
        if name != "*" and not hasattr(mod, name):
            try:
                importlib.import_module(f"{module}.{name}")
            except ImportError:
                missing.append(f"{path}:{line} from {module} import {name}")
    assert missing == []
