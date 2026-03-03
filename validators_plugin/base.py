# validators_plugin/base.py

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Type, Any, Union
import json
import os
import config  # <--- Imported to access global DEBUG_MODE


@dataclass
class ValidationResult:
    source_name: str
    status: str
    confidence_score: int
    details: str
    evidence_links: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


def deep_research_validator(cls: Type):
    cls.is_deep_research = True
    return cls


class BaseValidator(ABC):
    def __init__(self):
        # 1. Load Defaults defined by the child class
        self.config = self.get_default_settings()

        # 2. Attempt to load overrides from JSON
        self._load_settings_from_file()

    # --- Configuration Mandates ---

    @abstractmethod
    def get_default_settings(self) -> Dict[str, Any]:
        """
        Must return a dictionary of default configuration values.
        Example: {'TIMEOUT': 10, 'API_KEY': ''}
        """
        pass

    @property
    def config_filename(self) -> str:
        """
        Generates a standardized filename based on the class name.
        Example: CrossrefValidator -> CrossrefValidator_Config.json
        """
        return f"{self.__class__.__name__}_Config.json"

    def _get_config_path(self):
        """
        Determines the path for the config file.
        Prioritizes user-accessible locations over internal source paths.
        """
        # 1. Determine the application root directory
        if getattr(sys, 'frozen', False):
            # If running as a compiled .exe, look in the folder containing the executable
            app_root = os.path.dirname(sys.executable)
        else:
            # If running from source, use the current working directory
            app_root = os.getcwd()

        # 2. Check if the config exists in an external 'validators_plugin' folder
        #    Structure: App_Root/validators_plugin/CrossrefValidator_Config.json
        external_path = os.path.join(app_root, 'validators_plugin', self.config_filename)

        if os.path.exists(external_path):
            return external_path

        # 3. Fallback: Look in the directory where this script (base.py) lives
        #    This is the default for fresh installs or internal bundled configs
        internal_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(internal_dir, self.config_filename)

    def _load_settings_from_file(self):
        """Loads JSON settings with error handling."""
        path = self._get_config_path()

        # DEBUG: Only print if DEBUG_MODE is True in config
        if config.DEBUG_MODE:
            print(f"[Config] Loading settings for {self.name} from: {path}")

        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    saved_settings = json.load(f)
                    self.config.update(saved_settings)

                # DEBUG: Print full settings if enabled
                if config.DEBUG_MODE:
                    print(f"[Config] Full Settings for {self.name}:")
                    print(json.dumps(self.config, indent=4))

            except json.JSONDecodeError as e:
                print(f"[Config] JSON Syntax Error in {self.config_filename}: {e}")
                print(f"[Config] Reverting to default settings for {self.name}.")
            except Exception as e:
                print(f"[Config] Error loading {self.config_filename}: {e}")
        else:
            # Only create the file if it doesn't exist
            self.save_settings()

            # DEBUG: Print defaults if created
            if config.DEBUG_MODE:
                print(f"[Config] Created default settings for {self.name}:")
                print(json.dumps(self.config, indent=4))

    def save_settings(self):
        """Persists current self.config to JSON."""
        path = self._get_config_path()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"[Config] Failed to save {self.config_filename}: {e}")

    # --- Future Proofing: GUI Interface ---

    def get_current_settings(self) -> Dict[str, Any]:
        """Returns the current configuration for GUI inspection."""
        return self.config

    def update_setting(self, key: str, value: Any):
        """Updates a specific setting and saves to disk."""
        if key in self.config:
            self.config[key] = value
            self.save_settings()
        else:
            print(f"Warning: Attempted to update unknown setting '{key}' in {self.name}")

    # --- Existing Abstract Methods ---

    # Default behavior: Not deep research
    is_deep_research = False

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def validate(self, citation_data: Dict[str, str]) -> 'Union[ValidationResult, MatchCandidate]':
        """
        Validate a citation against this plugin's data source.

        Returns either:
          - ValidationResult: For errors, skips, not-found cases, or LLM
            validators that produce their own holistic assessment.
          - MatchCandidate: Raw match signals (title similarity, author
            overlap, year match, etc.) that the ScoringPipeline will
            convert into a ValidationResult using centralized weights.

        Existing validators returning ValidationResult continue to work
        unchanged. New or migrated validators can return MatchCandidate
        to benefit from consistent, comparable scoring.

        The ValidatorManager handles both types transparently via
        _resolve_result().
        """
        pass