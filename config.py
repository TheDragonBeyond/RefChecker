# config.py
import os
import json
import sys

# --- Constants ---
SETTINGS_FILENAME = "settings.json"

# --- Default Configuration (Fallback) ---
DEFAULTS = {
    "DEBUG_MODE": False,
    "OUTPUT_ENCODING": "cp1252",
    "SATISFIED_THRESHOLD": 1,
    "ADAPTIVE_ORDERING": True,
    "LLM_ENABLED": True,
    "LLM_CONFIDENCE_THRESHOLD": 40,
    "ACTIVE_EXTRACTOR": "Google Gemini API",
    "OUTPUT_SUFFIX": "_references.csv",
    "REPORT_SPLIT_MODE": "merged",
    "ENABLED_VALIDATORS": None,
    # --- Directory Memory Defaults (relative to app base) ---
    "LAST_DIR_EXTRACTION_INPUT": "source",   # Remembers where you pull docs from
    "LAST_DIR_EXTRACTION_OUTPUT": "output",  # Remembers where you save docs to
    "LAST_DIR_VALIDATION_INPUT": "output",   # Remembers where you pull CSVs from
    "LAST_DIR_VALIDATION_OUTPUT": "output",  # Remembers where you save reports to
    # --------------------------------------
    "CSV_HEADERS": [
        "Citation Number", "Type", "Authors", "Article Title",
        "Publication Title", "Series", "Editor", "Volume", "Issue",
        "Publisher", "Publication Location", "Year", "Month", "Day",
        "Pages", "Edition", "Institution", "DOI", "ISBN", "ISSN",
        "URL", "Date Accessed"
    ],
    "SUPPORTED_EXTENSIONS": {
        ".pdf": "PDF Files",
        ".txt": "Text Files",
        ".rtf": "Rich Text Files",
        ".csv": "CSV Files",
        ".html": "HTML Files",
        ".htm": "HTML Files"
    }
}

# --- Internal State ---
_current_settings = DEFAULTS.copy()


def get_base_path():
    """
    Returns the application's base directory. Works for both normal scripts
    and PyInstaller executables (which set sys._MEIPASS).
    """
    if getattr(sys, 'frozen', False):
        # Running as a PyInstaller bundle — use the directory containing the .exe
        return os.path.dirname(sys.executable)
    else:
        # Running as a normal script
        return os.path.dirname(os.path.abspath(__file__))


def resolve_path(relative_path):
    """
    Resolves a stored relative path to an absolute path based on the app's
    base directory. Also ensures the resolved directory exists (creates it
    if needed).

    If the path is already absolute, it is validated: absolute paths that
    do NOT fall under the current base directory are treated as stale
    (e.g. leftover from a different installation) and are discarded in
    favour of the base directory.
    """
    base = get_base_path()

    if os.path.isabs(relative_path):
        # Check whether this absolute path lives under the current base.
        # If it does, trust it; if not, it's stale — fall back to base.
        try:
            norm_path = os.path.normpath(relative_path)
            norm_base = os.path.normpath(base)
            if norm_path.startswith(norm_base + os.sep) or norm_path == norm_base:
                resolved = norm_path
            else:
                if _current_settings.get("DEBUG_MODE"):
                    print(f"[Config] Stale absolute path ignored: {relative_path}")
                resolved = base
        except (ValueError, TypeError):
            resolved = base
    else:
        resolved = os.path.normpath(os.path.join(base, relative_path))

    # Create the directory if it doesn't exist yet
    if not os.path.exists(resolved):
        try:
            os.makedirs(resolved, exist_ok=True)
            if _current_settings.get("DEBUG_MODE"):
                print(f"[Config] Created directory: {resolved}")
        except OSError as e:
            print(f"[Config] Could not create directory {resolved}: {e}")
            return base  # Fall back to base directory

    return resolved


def to_relative_path(absolute_path):
    """
    Converts an absolute path back to a relative path based on the app's
    base directory. If the path is not under the base directory, stores it
    as-is (absolute) so external locations still work.
    """
    base = get_base_path()
    try:
        rel = os.path.relpath(absolute_path, base)
        # If relpath goes above the base (starts with '..'), keep it relative
        # but still store it — this allows paths like '../some_other_folder'
        return rel
    except ValueError:
        # On Windows, relpath raises ValueError if paths are on different drives
        return absolute_path


def _sanitize_directory_settings():
    """
    Converts any stale absolute directory-memory paths in _current_settings
    to relative paths. This handles settings.json files that were copied
    from a different installation and still contain old absolute paths.
    """
    dir_keys = [
        "LAST_DIR_EXTRACTION_INPUT",
        "LAST_DIR_EXTRACTION_OUTPUT",
        "LAST_DIR_VALIDATION_INPUT",
        "LAST_DIR_VALIDATION_OUTPUT",
    ]
    base = get_base_path()
    changed = False

    for key in dir_keys:
        value = _current_settings.get(key)
        if value and os.path.isabs(value):
            try:
                norm_val = os.path.normpath(value)
                norm_base = os.path.normpath(base)
                if norm_val.startswith(norm_base + os.sep) or norm_val == norm_base:
                    # Under current base — convert to relative for portability
                    _current_settings[key] = os.path.relpath(norm_val, norm_base)
                else:
                    # Stale path from a different installation — reset to default
                    _current_settings[key] = DEFAULTS.get(key, ".")
                changed = True
            except (ValueError, TypeError):
                _current_settings[key] = DEFAULTS.get(key, ".")
                changed = True

    if changed:
        save_settings()
        if _current_settings.get("DEBUG_MODE"):
            print("[Config] Sanitized stale directory paths in settings.")


def load_settings():
    """
    Loads settings from the JSON file.
    Updates the global module variables to reflect the JSON content.
    """
    global _current_settings

    base_path = get_base_path()
    settings_path = os.path.join(base_path, SETTINGS_FILENAME)

    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                user_settings = json.load(f)
                _current_settings.update(user_settings)
                if _current_settings.get("DEBUG_MODE"):
                    print(f"[Config] Loaded settings from {settings_path}")
        except Exception as e:
            print(f"[Config] Error loading {SETTINGS_FILENAME}: {e}. Using defaults.")
    else:
        print(f"[Config] {SETTINGS_FILENAME} not found. Using defaults.")
        save_settings(_current_settings)

    # Clean up any stale absolute paths from a different installation
    _sanitize_directory_settings()


def save_settings(new_settings=None):
    global _current_settings

    if new_settings:
        _current_settings.update(new_settings)

    base_path = get_base_path()
    settings_path = os.path.join(base_path, SETTINGS_FILENAME)

    try:
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(_current_settings, f, indent=4)
        print(f"[Config] Settings saved to {settings_path}")
    except Exception as e:
        print(f"[Config] Failed to save settings: {e}")


# --- Initialization ---
load_settings()

# --- Module-Level Variable Exposure ---
DEBUG_MODE = _current_settings["DEBUG_MODE"]
OUTPUT_ENCODING = _current_settings["OUTPUT_ENCODING"]
SATISFIED_THRESHOLD = _current_settings["SATISFIED_THRESHOLD"]
ADAPTIVE_ORDERING = _current_settings["ADAPTIVE_ORDERING"]
LLM_ENABLED = _current_settings["LLM_ENABLED"]
LLM_CONFIDENCE_THRESHOLD = _current_settings["LLM_CONFIDENCE_THRESHOLD"]
ACTIVE_EXTRACTOR = _current_settings["ACTIVE_EXTRACTOR"]
REPORT_SPLIT_MODE = _current_settings["REPORT_SPLIT_MODE"]
ENABLED_VALIDATORS = _current_settings["ENABLED_VALIDATORS"] # NEW


# --- Class-Level Exposure ---
class Config:
    OUTPUT_SUFFIX = _current_settings["OUTPUT_SUFFIX"]
    CSV_HEADERS = _current_settings["CSV_HEADERS"]
    SUPPORTED_EXTENSIONS = _current_settings["SUPPORTED_EXTENSIONS"]
    OUTPUT_ENCODING = _current_settings["OUTPUT_ENCODING"]

    @staticmethod
    def reload():
        """
        Helper to reload settings at runtime without restarting the app.
        """
        load_settings()

        # Update Module Globals
        global DEBUG_MODE, OUTPUT_ENCODING, SATISFIED_THRESHOLD, \
            ADAPTIVE_ORDERING, LLM_ENABLED, LLM_CONFIDENCE_THRESHOLD, \
            ACTIVE_EXTRACTOR, REPORT_SPLIT_MODE, ENABLED_VALIDATORS

        DEBUG_MODE = _current_settings["DEBUG_MODE"]
        OUTPUT_ENCODING = _current_settings["OUTPUT_ENCODING"]
        SATISFIED_THRESHOLD = _current_settings["SATISFIED_THRESHOLD"]
        ADAPTIVE_ORDERING = _current_settings["ADAPTIVE_ORDERING"]
        LLM_ENABLED = _current_settings["LLM_ENABLED"]
        LLM_CONFIDENCE_THRESHOLD = _current_settings["LLM_CONFIDENCE_THRESHOLD"]
        ACTIVE_EXTRACTOR = _current_settings["ACTIVE_EXTRACTOR"]
        REPORT_SPLIT_MODE = _current_settings["REPORT_SPLIT_MODE"]
        ENABLED_VALIDATORS = _current_settings["ENABLED_VALIDATORS"] # NEW

        # Update Class Attributes
        Config.OUTPUT_SUFFIX = _current_settings["OUTPUT_SUFFIX"]
        Config.CSV_HEADERS = _current_settings["CSV_HEADERS"]
        Config.SUPPORTED_EXTENSIONS = _current_settings["SUPPORTED_EXTENSIONS"]
        Config.OUTPUT_ENCODING = _current_settings["OUTPUT_ENCODING"]