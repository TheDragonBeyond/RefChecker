# extraction_plugins/manager.py
#
# v2 CHANGES (from v1):
#   - Discovery: __subclasses__() instead of module-level list
#     (matches ValidatorManager, catches plugins that forget @register_extractor)
#   - Deduplication: prevents double-instantiation when internal and external
#     paths find the same class
#   - @register_extractor: now a backward-compatible no-op decorator
#     (existing plugins that use it continue to work unchanged)
#
# v3 CHANGES (from v2):
#   - Adds {base_dir}/plugins/lib/ to sys.path before loading external
#     plugins, so that dependencies installed by PluginInstaller via
#     `pip install --target` are importable in frozen builds.

import pkgutil
import importlib
import sys
import os
import config as cfg
from typing import List, Type, Dict
from extraction_plugins.base import BaseExtractor
import extraction_plugins


def register_extractor(cls):
    """
    Backward-compatible decorator for extraction plugins.

    In v1, this appended to a module-level list used for discovery.
    In v2, discovery uses BaseExtractor.__subclasses__() instead,
    so this decorator simply returns the class unchanged.

    Existing plugins that use @register_extractor continue to work.
    New plugins may omit it — inheriting from BaseExtractor is sufficient.
    """
    return cls


def _ensure_plugins_lib_on_path():
    """
    Adds {base_dir}/plugins/lib/ to sys.path if it exists.

    This directory is where PluginInstaller places pip dependencies
    for external plugins in frozen (PyInstaller) builds, using
    `pip install --target`.  Adding it to sys.path allows those
    packages to be imported by the frozen application.

    In source/development mode, this is a no-op (the directory
    typically doesn't exist, and pip installs to the normal
    site-packages).
    """
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    lib_dir = os.path.join(base_dir, 'plugins', 'lib')

    if os.path.isdir(lib_dir) and lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)
        if cfg.DEBUG_MODE:
            print(f"[ExtractionManager] Added plugin lib to sys.path: {lib_dir}")


class ExtractionManager:
    def __init__(self):
        self.available_extractors: Dict[str, BaseExtractor] = {}
        self._load_plugins()

    def _load_plugins(self):
        """
        Loads built-in plugins AND external 'drop-in' plugins, then
        instantiates all discovered BaseExtractor subclasses.
        """
        # --- 0. Ensure plugin dependency directory is on sys.path ---
        _ensure_plugins_lib_on_path()

        # --- 1. Load Built-in Plugins (Internal) ---
        package_path = extraction_plugins.__path__
        prefix = extraction_plugins.__name__ + "."

        for _, name, _ in pkgutil.iter_modules(package_path):
            if name.endswith("_prompts") or name in ["manager", "base"]:
                continue
            if prefix + name not in sys.modules:
                try:
                    importlib.import_module(prefix + name)
                except ImportError as e:
                    # Graceful skip for plugins with missing optional deps
                    if "gemini" in name or "chatgpt" in name:
                        print(f"[ExtractionManager] Info: Skipped {name} (Missing lib): {e}")
                    else:
                        print(f"[ExtractionManager] Warning: Could not load '{name}': {e}")
                except Exception as e:
                    print(f"[ExtractionManager] Failed to load internal '{name}': {e}")

        # --- 2. Load External "Drop-in" Plugins ---
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        external_dir = os.path.join(base_dir, 'plugins', 'extraction')

        if os.path.exists(external_dir):
            if external_dir not in sys.path:
                sys.path.append(external_dir)

            for _, name, _ in pkgutil.iter_modules([external_dir]):
                try:
                    importlib.import_module(name)
                    print(f"[ExtractionManager] Loaded external plugin: {name}")
                except ImportError as e:
                    print(f"[ExtractionManager] Info: Skipped external '{name}' (Missing lib): {e}")
                except Exception as e:
                    print(f"[ExtractionManager] Failed to load external '{name}': {e}")

        # --- 3. Discover & Instantiate via __subclasses__() ---
        # This catches ALL classes that inherit BaseExtractor, regardless
        # of whether they used @register_extractor.
        found_classes = BaseExtractor.__subclasses__()

        for cls in found_classes:
            # Skip the abstract base itself (safety check)
            if cls is BaseExtractor:
                continue

            # Deduplication: prevent double-instantiation if the same class
            # was found via both internal and external paths
            if any(isinstance(v, cls) for v in self.available_extractors.values()):
                continue

            try:
                instance = cls()
                self.available_extractors[instance.name] = instance

                if cfg.DEBUG_MODE:
                    print(f"[ExtractionManager] Registered: {instance.name} ({cls.__name__})")
            except Exception as e:
                print(f"[ExtractionManager] Error instantiating {cls.__name__}: {e}")

    def get_active_extractor(self) -> BaseExtractor:
        active_name = cfg.ACTIVE_EXTRACTOR
        extractor = self.available_extractors.get(active_name)
        if not extractor:
            valid_keys = list(self.available_extractors.keys())
            if valid_keys:
                print(f"Warning: Configured extractor '{active_name}' not found. Falling back to '{valid_keys[0]}'")
                return self.available_extractors[valid_keys[0]]
            raise ValueError("No extraction plugins available.")
        return extractor

    def run_extraction(self, filepath: str) -> str:
        extractor = self.get_active_extractor()
        print(f"Step 1: Extraction (Using {extractor.name})")
        return extractor.extract(filepath)