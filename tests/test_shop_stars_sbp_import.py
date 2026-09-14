"""stars_pay:sbp imported platega_service from a package where it doesn't exist
(the module lives in the repo root), so every SBP click for Stars failed."""
import ast
from pathlib import Path

import platega_service  # noqa: F401  (the module the handler must import)

SRC = Path(__file__).resolve().parents[1] / "app/handlers/payments/telegram_stars_purchase.py"


def test_stars_sbp_imports_root_platega_service():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    bad = [n.lineno for n in ast.walk(tree)
           if isinstance(n, ast.ImportFrom) and n.module == "app.services.payments"
           and any(a.name == "platega_service" for a in n.names)]
    assert bad == []
    assert any(isinstance(n, ast.Import) and any(a.name == "platega_service" for a in n.names)
               for n in ast.walk(tree))
