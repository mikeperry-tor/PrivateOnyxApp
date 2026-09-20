"""common source patch implementation."""

from __future__ import annotations

import functools
import inspect
from types import ModuleType
from typing import Any
from onyx_wrapper_patches.common.config import _warn_or_raise


def _patch_function_source(
    *,
    module: ModuleType,
    function_name: str,
    replacements: dict[str, str],
    patch_name: str,
) -> None:
    function = getattr(module, function_name)
    try:
        # ``functools.wraps`` sets ``__wrapped__`` and ``inspect.getsource``
        # follows that chain. Without retaining our generated source, a second
        # source patch for the same function recompiles the pristine upstream
        # body and silently discards the first patch.
        source = getattr(function, "_wrapper_patched_source", None)
        if isinstance(source, str):
            # A rebuilt decorated function exposes the decorator's globals.
            # Keep the execution namespace used to compile the actual body.
            rebuild_globals = getattr(
                function, "_wrapper_source_globals", function.__globals__
            )
            source_function = function
        else:
            source_function = inspect.unwrap(function)
            source = inspect.getsource(source_function)
            rebuild_globals = source_function.__globals__
        filename = inspect.getsourcefile(source_function) or f"<{patch_name}>"
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect {patch_name}: {e}")
        return

    patched_source = source
    for old, new in replacements.items():
        if old not in patched_source:
            _warn_or_raise(f"{patch_name} patch did not match expected upstream text")
            return
        patched_source = patched_source.replace(old, new, 1)

    namespace: dict[str, Any] = {}
    exec(compile(patched_source, filename, "exec"), rebuild_globals, namespace)
    patched_function = namespace.get(function_name)
    if not callable(patched_function):
        _warn_or_raise(f"{patch_name} patch did not rebuild {function_name}")
        return

    wrapped_function = functools.wraps(function)(patched_function)
    wrapped_function._wrapper_patched_source = patched_source
    wrapped_function._wrapper_source_globals = rebuild_globals
    setattr(module, function_name, wrapped_function)
    print(f"sitecustomize: patched {patch_name}", flush=True)



def _prompt_stability_replace(
    value: str, old: str, new: str, label: str, count: int = 1,
) -> str:
    if value.count(old) != count:
        raise RuntimeError(
            f"{label}: expected exactly {count} source matches, got {value.count(old)}"
        )
    return value.replace(old, new)
