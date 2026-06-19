"""Base wrapper-side runtime patches for Onyx containers.

Loaded automatically by Python when this directory is on PYTHONPATH.
"""

from __future__ import annotations

from wrapper_env_patches import apply_code_interpreter_network_description_patches
from wrapper_env_patches import apply_open_url_char_limit_patches

apply_open_url_char_limit_patches()
apply_code_interpreter_network_description_patches()
