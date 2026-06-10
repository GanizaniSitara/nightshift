"""Plugin loader.

The public core ships only interfaces (the Verifier contract, the registry, the
orchestration records). Concrete plugins — especially project-specific verifiers,
rubrics, and run recipes — can live OUTSIDE this repo in a private location and be
loaded at runtime. This keeps personal/project detail out of the public OSS core
while still letting Nightshift use it.

Plugin sources are gathered in order:
  1. paths passed explicitly to ``load_plugins(...)``
  2. the ``NIGHTSHIFT_PLUGINS`` env var (os.pathsep-separated directories)
  3. ``config["plugin_paths"]``

Each source is a directory. Every top-level ``*.py`` file and every package
(a subdir with ``__init__.py``) inside it is imported. A plugin registers itself
on import — e.g. ``registry.register(MyVerifier())`` at module level — and/or by
defining a no-arg ``register()`` function, which is called after import.

Give plugin modules unique names so they don't shadow stdlib or core modules.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Iterable


def _iter_plugin_modules(path: Path) -> Iterable[str]:
    for entry in sorted(path.iterdir()):
        if entry.name.startswith(("_", ".")):
            continue
        if entry.is_dir() and (entry / "__init__.py").exists():
            yield entry.name
        elif entry.suffix == ".py":
            yield entry.stem


def load_path(path: str | Path) -> list[str]:
    """Import every plugin module/package in ``path``. Returns the names loaded."""
    p = Path(path).expanduser()
    if not p.is_dir():
        return []
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
    loaded: list[str] = []
    for name in _iter_plugin_modules(p):
        module = importlib.import_module(name)
        register = getattr(module, "register", None)
        if callable(register):
            register()
        loaded.append(name)
    return loaded


def gather_paths(
    explicit: Iterable[str] | None = None,
    config: dict | None = None,
) -> list[str]:
    paths: list[str] = list(explicit or [])
    env = os.environ.get("NIGHTSHIFT_PLUGINS")
    if env:
        paths.extend(env.split(os.pathsep))
    if config and config.get("plugin_paths"):
        paths.extend(config["plugin_paths"])
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in paths:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def load_plugins(
    explicit: Iterable[str] | None = None,
    config: dict | None = None,
) -> list[str]:
    """Load plugins from all configured sources. Returns the names loaded."""
    loaded: list[str] = []
    for path in gather_paths(explicit, config):
        loaded.extend(load_path(path))
    return loaded
