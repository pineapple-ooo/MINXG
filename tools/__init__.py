"""tools module - Self-registering tool system."""

from tools.registry import registry, discover_builtin_tools, invalidate_check_fn_cache
import importlib, pkgutil, logging

_log = logging.getLogger(__name__)

def discover_tools():
    """Auto-import all tool modules in the tools package so they register."""
    for importer, modname, ispkg in pkgutil.iter_modules(__path__):
        if modname in ('registry', '__init__'):
            continue
        try:
            importlib.import_module(f'tools.{modname}')
        except Exception as e:
            _log.warning(f"Failed to load tool module {modname}: {e}")

# Run discovery on import
discover_tools()

__all__ = ["registry", "discover_builtin_tools", "invalidate_check_fn_cache", "discover_tools"]
