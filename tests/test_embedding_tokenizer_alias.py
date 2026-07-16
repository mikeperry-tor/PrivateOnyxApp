from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "onyx"
    / "patches"
    / "shared"
    / "wrapper_env_patches.py"
)


class Tokenizer:
    calls: list[str] = []

    @classmethod
    def from_pretrained(cls, model_name: str):
        cls.calls.append(model_name)
        return object()


class FakeHuggingFaceTokenizer:
    def __init__(self, model_name: str):
        self.encoder = Tokenizer.from_pretrained(model_name)


def _load_wrapper_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "wrapper_env_patches_tokenizer_under_test", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EmbeddingTokenizerAliasTests(unittest.TestCase):
    def test_fake_nomic_name_uses_bundled_tokenizer_only(self) -> None:
        wrapper = _load_wrapper_module()
        onyx = ModuleType("onyx")
        nlp = ModuleType("onyx.natural_language_processing")
        utils = ModuleType("onyx.natural_language_processing.utils")
        utils.HuggingFaceTokenizer = FakeHuggingFaceTokenizer
        nlp.utils = utils
        onyx.natural_language_processing = nlp
        modules = {
            "onyx": onyx,
            "onyx.natural_language_processing": nlp,
            "onyx.natural_language_processing.utils": utils,
        }
        Tokenizer.calls.clear()

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, modules):
            wrapper.apply_embedding_tokenizer_alias_patch()
            utils.HuggingFaceTokenizer("nomic-ai/nomic-embed-text-v23")
            utils.HuggingFaceTokenizer("other/model")

        self.assertEqual(
            Tokenizer.calls,
            ["nomic-ai/nomic-embed-text-v1", "other/model"],
        )


if __name__ == "__main__":
    unittest.main()
