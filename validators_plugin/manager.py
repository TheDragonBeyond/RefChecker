# validators_plugin/manager.py
#
# v3 CHANGES (from v2):
#   - Internal plugin discovery: os.listdir() → pkgutil.iter_modules()
#     (matches ExtractionManager; works inside PyInstaller frozen builds
#      where .py files don't exist on disk)
#   - External plugin scan also uses pkgutil.iter_modules() for consistency
#   - Adds {base_dir}/plugins/lib/ to sys.path before loading external
#     plugins, so that dependencies installed by PluginInstaller via
#     `pip install --target` are importable in frozen builds.

import os
import sys
import pkgutil
import importlib
import traceback
from typing import Dict, List, Set, Type, Union
from validators_plugin.base import BaseValidator, ValidationResult
from scoring import MatchCandidate, ScoringPipeline
import config as cfg
import validators_plugin


def register_validator(cls):
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
            print(f"[ValidatorManager] Added plugin lib to sys.path: {lib_dir}")


class ValidatorManager:
    def __init__(self):
        self.primary_validators: List[BaseValidator] = []
        self.research_validators: List[BaseValidator] = []
        self.success_stats: Dict[str, int] = {}
        self.enabled_validators: Set[str] = set()
        self._load_and_initialize_validators()

    def _load_and_initialize_validators(self):
        # --- 0. Ensure plugin dependency directory is on sys.path ---
        _ensure_plugins_lib_on_path()

        # --- 1. Determine Paths ---
        # External path (user drop-in)
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        external_plugin_dir = os.path.join(base_dir, 'plugins', 'validators')

        # --- 2. Load Internal (Bundled) Plugins via pkgutil ---
        # Uses pkgutil.iter_modules() which hooks into PyInstaller's
        # frozen importer — unlike os.listdir(), this finds compiled
        # modules even when there are no .py files on disk.
        package_path = validators_plugin.__path__
        prefix = validators_plugin.__name__ + "."

        for _, name, _ in pkgutil.iter_modules(package_path):
            if name in ("base", "manager") or name.startswith("_"):
                continue
            full_name = prefix + name
            if full_name not in sys.modules:
                try:
                    importlib.import_module(full_name)
                    if cfg.DEBUG_MODE:
                        print(f"[ValidatorManager] Loaded internal: {name}")
                except ImportError as e:
                    if "gemini" in name or "llm" in name:
                        print(f"[ValidatorManager] Info: Skipped {name} (Missing lib): {e}")
                    else:
                        print(f"[ValidatorManager] Warning: Could not load {name}: {e}")
                except Exception as e:
                    print(f"[ValidatorManager] Error loading internal '{name}': {e}")

        # --- 3. Load External "Drop-in" Plugins ---
        if os.path.exists(external_plugin_dir):
            if external_plugin_dir not in sys.path:
                sys.path.append(external_plugin_dir)
            self._scan_external_directory(external_plugin_dir)

        # --- 4. Instantiate Subclasses ---
        found_classes = BaseValidator.__subclasses__()
        for val_cls in found_classes:
            if val_cls is BaseValidator: continue
            try:
                # Deduplicate based on class name to prevent double-loading issues
                if any(isinstance(v, val_cls) for v in self.primary_validators + self.research_validators):
                    continue

                instance = val_cls()
                self._register_instance(instance)
            except Exception as e:
                print(f"[ValidatorManager] Failed to instantiate {val_cls.__name__}: {e}")

    def _scan_external_directory(self, directory: str):
        """Scans a filesystem directory for external drop-in plugins."""
        if cfg.DEBUG_MODE:
            print(f"[ValidatorManager] Scanning external dir: {directory}...")

        for _, name, _ in pkgutil.iter_modules([directory]):
            if name.startswith("_"):
                continue
            try:
                importlib.import_module(name)
                print(f"[ValidatorManager] Loaded external plugin: {name}")
            except ImportError as e:
                print(f"[ValidatorManager] Info: Skipped external '{name}' (Missing lib): {e}")
            except Exception as e:
                print(f"[ValidatorManager] Error loading external '{name}': {e}")

    def _register_instance(self, validator: BaseValidator):
        if getattr(validator, 'is_deep_research', False):
            self.research_validators.append(validator)
        else:
            self.primary_validators.append(validator)
            self.success_stats[validator.name] = 0

    def set_enabled_validators(self, enabled_names: List[str]):
        self.enabled_validators = set(enabled_names)

    def _resolve_result(self, raw: Union[MatchCandidate, ValidationResult]) -> ValidationResult:
        """
        Normalizes a validator's return value into a ValidationResult.

        Validators may return either:
          - MatchCandidate: Raw match signals → routed through ScoringPipeline
          - ValidationResult: Already scored (errors, skips, LLM validators)

        This is the single dispatch point described in the architecture doc.
        """
        if isinstance(raw, MatchCandidate):
            return ScoringPipeline.score(raw)
        return raw

    def validate_citation(self, citation_data: Dict) -> ValidationResult:
        active_primary = [v for v in self.primary_validators if
                          not self.enabled_validators or v.name in self.enabled_validators]
        if cfg.ADAPTIVE_ORDERING:
            active_primary.sort(key=lambda v: self.success_stats.get(v.name, 0), reverse=True)

        collected_results = []
        valid_confirmations = 0
        best_primary_result = None

        for validator in active_primary:
            try:
                raw = validator.validate(citation_data)
                result = self._resolve_result(raw)

                collected_results.append(result)
                if result.status == ScoringPipeline.STATUS_VALIDATED:
                    valid_confirmations += 1
                    self.success_stats[validator.name] = self.success_stats.get(validator.name, 0) + 1
                if best_primary_result is None or result.confidence_score > best_primary_result.confidence_score:
                    best_primary_result = result
                if valid_confirmations >= cfg.SATISFIED_THRESHOLD:
                    break
            except Exception as e:
                collected_results.append(ValidationResult(validator.name, "Error", 0, str(e)))

        if not best_primary_result:
            best_primary_result = ValidationResult("None", ScoringPipeline.STATUS_NOT_VALIDATED, 0, "No validators returned a result.")

        final_result = best_primary_result
        if cfg.LLM_ENABLED and final_result.confidence_score < cfg.LLM_CONFIDENCE_THRESHOLD:
            active_research = [v for v in self.research_validators if
                               not self.enabled_validators or v.name in self.enabled_validators]
            for v in active_research:
                try:
                    raw = v.validate(citation_data)
                    research_result = self._resolve_result(raw)

                    if research_result.confidence_score >= final_result.confidence_score:
                        final_result = research_result
                except Exception as e:
                    print(f"Research Validator {v.name} failed: {e}")
        return final_result