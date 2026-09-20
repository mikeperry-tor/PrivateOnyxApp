"""Import implementations with real dependencies but without an application bootstrap."""

import importlib
from pathlib import Path
import sys


assert "sitecustomize" not in sys.modules
root = Path("/app/onyx_wrapper_patches")
for path in sorted(root.rglob("*.py")):
    if path.name == "__init__.py":
        name = ".".join(path.parent.relative_to(root.parent).parts)
    else:
        name = ".".join(path.relative_to(root.parent).with_suffix("").parts)
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve() == path
assert not any(name == "onyx" or name.startswith("onyx.") for name in sys.modules)
assert "wrapper_env_patches" not in sys.modules
print("PINNED_INERT_CANONICAL_PACKAGE_IMPORTS_OK")
