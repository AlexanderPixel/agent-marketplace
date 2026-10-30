"""Discovers connectors: every module in tenderhub.connectors defining a Connector subclass with a `name`."""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from . import connectors as _pkg
from .connector import Connector

log = logging.getLogger("tenderhub.registry")
_cache: dict[str, type[Connector]] | None = None
import_errors: dict[str, str] = {}


def all_connectors() -> dict[str, type[Connector]]:
    global _cache
    if _cache is not None:
        return _cache
    found: dict[str, type[Connector]] = {}
    for mod in pkgutil.iter_modules(_pkg.__path__):
        if mod.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{_pkg.__name__}.{mod.name}")
        except Exception as e:  # one broken connector must not take down the rest
            import_errors[mod.name] = repr(e)
            log.error("failed to import connector module %s: %r", mod.name, e)
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Connector) and obj is not Connector and not inspect.isabstract(obj) \
                    and obj.__module__ == module.__name__ and getattr(obj, "name", None):
                if obj.name in found:
                    raise ValueError(f"duplicate connector name {obj.name!r} in {module.__name__}")
                found[obj.name] = obj
    _cache = dict(sorted(found.items()))
    return _cache


def get(name: str) -> type[Connector]:
    try:
        return all_connectors()[name]
    except KeyError:
        raise KeyError(f"unknown connector {name!r}; known: {', '.join(all_connectors())}") from None
