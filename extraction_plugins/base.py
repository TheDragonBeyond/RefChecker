# extraction_plugins/base.py
from abc import ABC, abstractmethod
from typing import Dict, Any, List
import json
import os
import sys
import config  # Global DEBUG_MODE access


class BaseExtractor(ABC):
    """
    Base class for all extraction plugins.

    Subclasses must implement:
      - name (property)        — display name used in UI and config selection
      - extract(filepath)      — perform extraction, return CSV string
      - get_default_settings() — return dict of default config values

    Optional overrides:
      - is_configured()    — return True if the plugin has valid credentials
      - is_programmatic    — True for regex/parsing, False for LLM-based

    Plugin Installation:
      External plugins can declare pip dependencies via a class-level
      DEPENDENCIES list.  The PluginInstaller reads this via AST analysis
      and runs ``pip install`` automatically before copying the file::

          class MyExtractor(BaseExtractor):
              DEPENDENCIES = ["some-library", "another-lib>=2.0"]
              ...

      If your plugin has no external dependencies, you can omit the
      attribute entirely or set it to an empty list.
    """

    # Subclasses may declare pip dependencies for automatic installation.
    # The PluginInstaller reads this via AST — it does NOT import the file.
    DEPENDENCIES: List[str] = []

    def __init__(self):
        # 1. Load defaults defined by the child class
        self.config = self.get_default_settings()

        # 2. Attempt to load overrides from JSON
        self._load_settings_from_file()

    # --- Configuration ---

    def is_configured(self) -> bool:
        """
        Checks if the plugin has been properly configured (e.g., API keys set).
        Defaults to False — subclasses should override with real checks.
        """
        return False

    @abstractmethod
    def get_default_settings(self) -> Dict[str, Any]:
        """
        Must return a dictionary of default configuration values.
        Example: {'API_KEY': 'YOUR_KEY_HERE', 'MODEL_NAME': 'gemini-2.5-flash'}
        """
        pass

    @property
    def config_filename(self) -> str:
        """
        Generates a standardized filename based on the class name.
        Example: GeminiExtractor -> GeminiExtractor_Config.json
        """
        return f"{self.__class__.__name__}_Config.json"

    def _get_config_path(self):
        """
        Determines the path for the config file.
        Prioritizes user-accessible locations over internal source paths.

        Resolution order:
          1. External: {app_root}/extraction_plugins/{ConfigName}.json
             (user-editable, survives updates, works for frozen builds)
          2. Internal: {this_script_dir}/{ConfigName}.json
             (default for fresh installs or development)
        """
        # 1. Determine the application root directory
        if getattr(sys, 'frozen', False):
            # Running as a compiled .exe — use the folder containing the executable
            app_root = os.path.dirname(sys.executable)
        else:
            # Running from source — use the current working directory
            app_root = os.getcwd()

        # 2. Check if the config exists in an external 'extraction_plugins' folder
        #    Structure: App_Root/extraction_plugins/GeminiExtractor_Config.json
        external_path = os.path.join(app_root, 'extraction_plugins', self.config_filename)

        if os.path.exists(external_path):
            return external_path

        # 3. Fallback: Look in the directory where this script (base.py) lives
        #    This is the default for fresh installs or internal bundled configs
        internal_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(internal_dir, self.config_filename)

    def _load_settings_from_file(self):
        """Loads JSON settings with robust error handling."""
        path = self._get_config_path()

        if config.DEBUG_MODE:
            print(f"[Config] Loading settings for Extractor {self.name} from: {path}")

        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    saved_settings = json.load(f)
                    self.config.update(saved_settings)

                if config.DEBUG_MODE:
                    print(f"[Config] Full Settings for {self.name}:")
                    print(json.dumps(self.config, indent=4))

            except json.JSONDecodeError as e:
                print(f"[Config] JSON Syntax Error in {self.config_filename}: {e}")
                print(f"[Config] Reverting to default settings for {self.name}.")
            except Exception as e:
                print(f"[Config] Error loading {self.config_filename}: {e}")
        else:
            # Create the file with defaults so the user can edit it
            self.save_settings()

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

    # --- GUI Interface Helpers ---

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

    # --- Abstract Interface ---

    @property
    @abstractmethod
    def name(self) -> str:
        """The specific key used in settings.json to select this plugin."""
        pass

    @property
    def is_programmatic(self) -> bool:
        """
        True = Fast, deterministic (Regex, BeautifulSoup).
        False = Slow, stochastic (LLM, AI models).
        Defaults to False (LLM). Override in subclass if programmatic.
        """
        return False

    @abstractmethod
    def extract(self, filepath: str) -> str:
        """
        Perform the extraction.

        Input: Path to the source file.
        Output: A valid CSV string (with headers matching config.CSV_HEADERS).
        """
        pass