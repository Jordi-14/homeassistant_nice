"""Load the protocol client without importing Home Assistant integration setup."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_client_module(repo_root: Path) -> ModuleType:
    """Return the client module, falling back to a standalone file import."""
    try:
        return importlib.import_module("custom_components.nice_bidiwifi.client")
    except ModuleNotFoundError as err:
        if err.name != "homeassistant" and not (err.name or "").startswith("homeassistant."):
            raise

    module_name = "nice_bidiwifi_standalone_client"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    client_path = repo_root / "custom_components" / "nice_bidiwifi" / "client.py"
    spec = importlib.util.spec_from_file_location(module_name, client_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load Nice client from {client_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
