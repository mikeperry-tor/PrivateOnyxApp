from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


PATH = Path(__file__).resolve().parents[1] / "onyx/patches/sitecustomize_api_server/model_display_name_patch.py"


class ModelDisplayNameTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("model_display_name_patch", PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_labels_preserve_exact_ids_and_avoid_duplicates(self):
        for label, model_id, expected in (
            ("Qwen 3", "mlx-community/Qwen-3-4bit", "Qwen 3 (mlx-community/Qwen-3-4bit)"),
            ("Example", "provider/model:latest", "Example (provider/model:latest)"),
            ("model-id", "model-id", "model-id"),
            ("Example (id)", "id", "Example (id)"),
            (None, "id", None), ("", "id", ""),
            (17, "id", 17), ("Example", None, "Example"),
        ):
            with self.subTest(label=label, model_id=model_id):
                self.assertEqual(self.module._display_name(label, model_id), expected)

    def test_signature_and_source_drift_are_fatal(self):
        def target(api_base, api_key=None):
            return api_base
        self.module._validate_target(target, ("api_base", "api_key"), ("return api_base",))
        with self.assertRaisesRegex(RuntimeError, "signature changed"):
            self.module._validate_target(target, ("url",), ())
        with self.assertRaisesRegex(RuntimeError, "source changed"):
            self.module._validate_target(target, ("api_base", "api_key"), ("missing",))

    def test_install_discovery_saved_views_and_failure_propagation(self):
        payload = {"data": [
            {"id": "org/model", "name": "Model", "context_length": 8192},
            {"id": "id-only"}, {"id": "null", "name": None},
            {"id": "done", "name": "Done (done)"}, "malformed",
        ], "object": "list"}
        original_response = Mock(return_value=payload)
        original_response._wrapper_model_display_names = False
        api = SimpleNamespace(
            _get_openai_compatible_server_response=original_response,
            get_openai_compatible_server_available_models=Mock(),
        )
        calls = []

        class View:
            @classmethod
            def from_model(cls, model_configuration_model, provider_name,
                           use_stored_display_name=False, custom_config=None,
                           deployment_name=None):
                calls.append((provider_name, use_stored_display_name, custom_config, deployment_name))
                return SimpleNamespace(**vars(model_configuration_model))

        modules = {
            "onyx.llm.constants": SimpleNamespace(LlmProviderNames=SimpleNamespace(OPENAI_COMPATIBLE="openai-compatible")),
            "onyx.server.manage.llm": SimpleNamespace(api=api, models=SimpleNamespace(ModelConfigurationView=View)),
        }
        with patch.dict("sys.modules", modules), patch.object(self.module, "_validate_target") as validate:
            original_view = View.from_model.__func__
            validate.side_effect = [None, None, RuntimeError("source changed")]
            with self.assertRaisesRegex(RuntimeError, "source changed"):
                self.module.install()
            self.assertIs(api._get_openai_compatible_server_response, original_response)
            self.assertIs(View.from_model.__func__, original_view)
            validate.reset_mock(side_effect=True)
            self.module.install()
            self.assertEqual(validate.call_count, 3)
            installed = api._get_openai_compatible_server_response
            self.module.install()
            self.assertIs(api._get_openai_compatible_server_response, installed)
            result = installed("http://configured/v1", "key")
            original_response.assert_called_once_with("http://configured/v1", "key")
            self.assertEqual(result["data"][0], {
                "id": "org/model", "name": "Model (org/model)", "context_length": 8192,
            })
            self.assertEqual(result["data"][1:], payload["data"][1:])
            self.assertEqual(payload["data"][0]["name"], "Model")
            row = SimpleNamespace(name="org/model", display_name="Model", custom_display_name="Admin label")
            rendered = View.from_model(row, "openai-compatible", True, {"x": "y"}, "deploy")
            self.assertEqual(rendered.display_name, "Model (org/model)")
            self.assertEqual(rendered.custom_display_name, "Admin label")
            self.assertEqual(rendered.name, "org/model")
            self.assertEqual(row.display_name, "Model")
            self.assertEqual(calls[-1], ("openai-compatible", True, {"x": "y"}, "deploy"))
            self.assertEqual(View.from_model(row, "openrouter").display_name, "Model")
            original_response.return_value = {"data": None}
            self.assertEqual(installed("base"), {"data": None})
            original_response.side_effect = RuntimeError("provider unavailable")
            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                installed("base")


if __name__ == "__main__":
    unittest.main()
