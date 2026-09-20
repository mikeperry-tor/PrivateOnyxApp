from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "onyx/patches/onyx_wrapper_patches"


class PatchPackageTests(unittest.TestCase):
    def test_inert_initializers_and_dependency_direction(self):
        graph = {}
        for path in ROOT.rglob("*.py"):
            name = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            tree = ast.parse(path.read_text())
            if path.name == "__init__.py":
                self.assertTrue(all(isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) for node in tree.body), path)
            graph[name] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertNotEqual(module, "wrapper_env_patches")
                    if module.startswith("onyx_wrapper_patches."):
                        dependency = module.removeprefix("onyx_wrapper_patches.")
                        graph[name].add(dependency)
                        if name.startswith(("common.", "shared.")):
                            self.assertFalse(dependency.startswith(("api.", "background.")), (name, dependency))
                        if name.startswith("api."):
                            self.assertFalse(dependency.startswith("background."), (name, dependency))
                        if name.startswith("background."):
                            self.assertFalse(dependency.startswith("api."), (name, dependency))
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse((node.module or "").startswith("onyx."), path)

        def visit(name, stack):
            self.assertNotIn(name, stack, f"cyclic patch imports: {stack} -> {name}")
            for dependency in graph.get(name, ()):
                visit(dependency, (*stack, name))

        for name in graph:
            visit(name, ())

    def test_old_implementation_paths_are_removed(self):
        patch_root = ROOT.parent
        self.assertFalse((patch_root / "shared/wrapper_env_patches.py").exists())
        self.assertEqual({path.name for path in (patch_root / "sitecustomize_api_server").glob("*.py")}, {"sitecustomize.py"})
