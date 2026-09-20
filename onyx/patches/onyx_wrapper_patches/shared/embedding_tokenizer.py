"""shared embedding tokenizer patch implementation."""

from __future__ import annotations

import functools
import inspect
import os
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _warn_or_raise


def apply_embedding_tokenizer_alias_patch() -> None:
    """Load the wrapper's supported nomic tokenizers without a network lookup.

    The saved v23 name activates Onyx's nomic-family RAG behavior, while the
    local embedding shim selects the real upstream model. Onyx otherwise asks
    Hugging Face for a nonexistent tokenizer and then falls back to the bundled
    v1 tokenizer. Fresh Onyx state also constructs that v1 tokenizer directly.
    Map only those two exact tokenizer names to the generated offline file; the
    saved model name and every feature gate remain unchanged.
    """

    fake_model = "nomic-ai/nomic-embed-text-v23"
    tokenizer_model = "nomic-ai/nomic-embed-text-v1"
    tokenizer_file = os.environ.get("ONYX_EMBEDDING_TOKENIZER_FILE", "").strip()

    try:
        from onyx.natural_language_processing import utils as nlp_utils
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing embedding tokenizer module: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    original_init = nlp_utils.HuggingFaceTokenizer.__init__
    init_parameters = tuple(inspect.signature(original_init).parameters)
    if init_parameters != ("self", "model_name"):
        _warn_or_raise(
            "HuggingFaceTokenizer.__init__ signature changed: "
            f"expected ('self', 'model_name'), got {init_parameters!r}"
        )
        return
    try:
        source = inspect.getsource(original_init)
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect HuggingFaceTokenizer.__init__: {e}")
        return

    if "Tokenizer.from_pretrained(model_name)" not in source:
        _warn_or_raise(
            "HuggingFaceTokenizer.__init__ no longer contains the expected "
            "from_pretrained(model_name) call"
        )
        return

    if tokenizer_file:
        if not os.path.isabs(tokenizer_file):
            _warn_or_raise("ONYX_EMBEDDING_TOKENIZER_FILE must be absolute")
            return
        if os.path.islink(tokenizer_file) or not os.path.isfile(tokenizer_file):
            _warn_or_raise(
                "offline embedding tokenizer is missing: "
                "run the supported full-stack startup flow"
            )
            return
        if not hasattr(nlp_utils.Tokenizer, "from_file"):
            _warn_or_raise("Tokenizer.from_file is unavailable")
            return

    @functools.wraps(original_init)
    def _aliased_init(self, model_name: str):
        if model_name in (fake_model, tokenizer_model) and tokenizer_file:
            self.encoder = nlp_utils.Tokenizer.from_file(tokenizer_file)
            return None
        return original_init(
            self,
            tokenizer_model if model_name == fake_model else model_name,
        )

    nlp_utils.HuggingFaceTokenizer.__init__ = _aliased_init
    print(
        "sitecustomize: mapped supported nomic tokenizers to bundled nomic v1 "
        + (
            "offline file without changing the saved embedding model name"
            if tokenizer_file
            else "without changing the saved embedding model name"
        ),
        flush=True,
    )
