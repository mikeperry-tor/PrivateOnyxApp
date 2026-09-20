from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_ORDER = [
    "apply_embedding_tokenizer_alias_patch", "_apply_sleepy_background_patch",
    "apply_playwright_helper_proxy_patch", "apply_configured_inference_proxy_patch",
    "_apply_web_connector_egress_patch", "_apply_web_connector_http_freshness_patch",
]


class BootstrapProcessTests(unittest.TestCase):
    def run_bootstrap(self, service, *, strict=True, fail="", import_fail="", missing_package=False):
        bootstrap = ROOT / f"onyx/patches/sitecustomize_{service}/sitecustomize.py"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copyfile(bootstrap, root / "sitecustomize.py")
            if not missing_package:
                imports = {}
                for node in ast.walk(ast.parse(bootstrap.read_text())):
                    if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("onyx_wrapper_patches."):
                        imports.setdefault(node.module, set()).update(alias.name for alias in node.names)
                for module, functions in imports.items():
                    path = root.joinpath(*module.split(".")).with_suffix(".py")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    for parent in path.parents:
                        if parent == root:
                            break
                        (parent / "__init__.py").touch()
                    source = "import os\n"
                    if module == import_fail:
                        source += "raise ImportError('controlled implementation import failure')\n"
                    for function in sorted(functions):
                        source += f"def {function}():\n"
                        if function == "use_obscura_browser":
                            source += "    return False\n"
                        else:
                            source += f"    print('CALL:{function}', flush=True)\n"
                            source += f"    if os.environ.get('FAIL') == {function!r}: raise RuntimeError('controlled installer failure')\n"
                    path.write_text(source)
            cwd = root / "unrelated"
            cwd.mkdir()
            return subprocess.run(
                [sys.executable, "-c", "print('APPLICATION_BODY_EXECUTED')"],
                cwd=cwd, env={"PATH": os.environ["PATH"], "PYTHONPATH": str(root),
                              "WRAPPER_PATCH_STRICT": str(strict).lower(), "FAIL": fail},
                capture_output=True, text=True, timeout=30,
            )

    @staticmethod
    def calls(completed):
        return [line.removeprefix("CALL:") for line in completed.stderr.splitlines() if line.startswith("CALL:")]

    def test_background_order_and_stderr_only_automatic_startup(self):
        completed = self.run_bootstrap("background")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "APPLICATION_BODY_EXECUTED\n")
        self.assertEqual(self.calls(completed), BACKGROUND_ORDER)

    def test_background_shared_failure_continues_only_in_nonstrict_mode(self):
        for failure in (BACKGROUND_ORDER[0], BACKGROUND_ORDER[2], BACKGROUND_ORDER[3]):
            for strict in (False, True):
                with self.subTest(failure=failure, strict=strict):
                    completed = self.run_bootstrap("background", strict=strict, fail=failure)
                    expected = BACKGROUND_ORDER[:BACKGROUND_ORDER.index(failure) + 1] if strict else BACKGROUND_ORDER
                    self.assertEqual(self.calls(completed), expected)
                    self.assertEqual(completed.returncode, 78 if strict else 0)
                    self.assertEqual(bool(completed.stdout), not strict)

    def test_api_outer_failure_stops_remaining_sequence_even_nonstrict(self):
        for strict in (False, True):
            completed = self.run_bootstrap("api_server", strict=strict, fail="apply_llm_max_tokens_override_patch")
            self.assertEqual(self.calls(completed), ["apply_embedding_tokenizer_alias_patch", "apply_llm_max_tokens_override_patch"])
            self.assertEqual(completed.returncode, 78 if strict else 0)
            self.assertEqual(bool(completed.stdout), not strict)

    def test_import_failure_preserves_api_stop_and_background_shared_continuation(self):
        completed = self.run_bootstrap("api_server", strict=False, import_fail="onyx_wrapper_patches.api.python_capabilities")
        self.assertEqual(self.calls(completed), [])
        self.assertEqual(completed.returncode, 0)
        completed = self.run_bootstrap("background", strict=False, import_fail="onyx_wrapper_patches.shared.embedding_tokenizer")
        self.assertEqual(self.calls(completed), BACKGROUND_ORDER[1:])
        self.assertEqual(completed.returncode, 0)

    def test_discovered_missing_package_is_fatal_before_application(self):
        for service in ("api_server", "background"):
            completed = self.run_bootstrap(service, missing_package=True)
            self.assertEqual(completed.returncode, 78, completed.stderr)
            self.assertEqual(completed.stdout, "")
