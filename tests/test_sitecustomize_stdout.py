from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _redirects_install_stdout_to_stderr(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.With) or len(node.items) != 1:
            continue
        context = node.items[0].context_expr
        if not (
            isinstance(context, ast.Call)
            and isinstance(context.func, ast.Name)
            and context.func.id == "redirect_stdout"
            and len(context.args) == 1
            and isinstance(context.args[0], ast.Attribute)
            and isinstance(context.args[0].value, ast.Name)
            and context.args[0].value.id == "sys"
            and context.args[0].attr == "stderr"
        ):
            continue
        if any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_install"
            for statement in node.body
            for child in ast.walk(statement)
        ):
            return True
    return False


class SitecustomizeStdoutTests(unittest.TestCase):
    def test_onyx_bootstraps_preserve_isolated_runner_stdout(self):
        for relative in (
            "onyx/patches/sitecustomize_api_server/sitecustomize.py",
            "onyx/patches/sitecustomize_background/sitecustomize.py",
        ):
            path = ROOT / relative
            with self.subTest(path=relative):
                self.assertTrue(_redirects_install_stdout_to_stderr(path))

    def test_strict_bootstraps_exit_instead_of_raising_to_site_loader(self):
        for relative in (
            "onyx/patches/sitecustomize_api_server/sitecustomize.py",
            "onyx/patches/sitecustomize_background/sitecustomize.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIn("os._exit(78)", source)


if __name__ == "__main__":
    unittest.main()
