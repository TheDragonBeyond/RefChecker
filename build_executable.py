import PyInstaller.__main__
import os
import sys
import shutil
import glob

# --- Configuration ---
APP_NAME = "CitationUnified"
ENTRY_POINT = "app.py"
ICON_SOURCE = "Citation_validation_icon.png"

# Clean up previous builds
if os.path.exists("dist"):
    shutil.rmtree("dist")
if os.path.exists("build"):
    shutil.rmtree("build")

print("--- Building Executable ---")

# ── 0. Convert icon to platform-appropriate format ────────────────────
icon_arg = []
try:
    from PIL import Image
    img = Image.open(ICON_SOURCE)

    if sys.platform == "win32":
        # Windows needs .ico — include multiple sizes for best results
        ico_path = "app_icon.ico"
        icon_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img.save(ico_path, format="ICO", sizes=icon_sizes)
        icon_arg = [f"--icon={ico_path}"]
        print(f"  [icon] Generated {ico_path} for Windows")

    elif sys.platform == "darwin":
        # macOS needs .icns — create via iconutil or fall back to PNG
        # PyInstaller on macOS also accepts .png directly in many versions
        icns_path = "app_icon.icns"
        iconset_dir = "app_icon.iconset"

        os.makedirs(iconset_dir, exist_ok=True)
        # macOS iconset requires specific named sizes
        icns_sizes = {
            "icon_16x16.png": 16,
            "icon_16x16@2x.png": 32,
            "icon_32x32.png": 32,
            "icon_32x32@2x.png": 64,
            "icon_128x128.png": 128,
            "icon_128x128@2x.png": 256,
            "icon_256x256.png": 256,
            "icon_256x256@2x.png": 512,
            "icon_512x512.png": 512,
        }
        for name, size in icns_sizes.items():
            resized = img.resize((size, size), Image.LANCZOS)
            resized.save(os.path.join(iconset_dir, name))

        # Try iconutil (macOS native tool) to create .icns
        ret = os.system(f"iconutil -c icns {iconset_dir} -o {icns_path}")
        if ret == 0 and os.path.exists(icns_path):
            icon_arg = [f"--icon={icns_path}"]
            print(f"  [icon] Generated {icns_path} for macOS")
        else:
            # Fallback: PyInstaller accepts .png on macOS too
            icon_arg = [f"--icon={ICON_SOURCE}"]
            print(f"  [icon] Using {ICON_SOURCE} directly (iconutil not available)")

        shutil.rmtree(iconset_dir, ignore_errors=True)

    else:
        # Linux: PyInstaller accepts .png
        icon_arg = [f"--icon={ICON_SOURCE}"]
        print(f"  [icon] Using {ICON_SOURCE} for Linux")

except ImportError:
    print("  [WARNING] Pillow not installed — cannot convert icon.")
    print("            Install with: pip install Pillow")
    print("            Falling back to raw PNG (may not work on Windows).")
    if os.path.exists(ICON_SOURCE):
        icon_arg = [f"--icon={ICON_SOURCE}"]
except Exception as e:
    print(f"  [WARNING] Icon conversion failed: {e}")
    print("            Building without custom icon.")

# ── 1. Dynamically discover all internal plugins for --hidden-import ──
# PyInstaller can't detect dynamically loaded modules (importlib.import_module).
# We must declare every plugin file as a hidden import.

hidden_imports = []
SKIP_FILES = {"__init__.py", "base.py", "manager.py"}

for plugin_dir, prefix in [
    ("extraction_plugins", "extraction_plugins"),
    ("validators_plugin", "validators_plugin"),
]:
    if os.path.isdir(plugin_dir):
        for filename in os.listdir(plugin_dir):
            if filename.endswith(".py") and filename not in SKIP_FILES:
                # Also skip prompt files (not importable plugins)
                if filename.endswith("_prompts.py"):
                    continue
                module_name = f"{prefix}.{filename[:-3]}"
                hidden_imports.append(f"--hidden-import={module_name}")
                print(f"  [hidden-import] {module_name}")

# Always include tkinter (not auto-detected on some platforms)
hidden_imports.append("--hidden-import=tkinter")

# ── 2. Collect --add-data entries for config JSON files ──────────────
add_data = []
SEP = os.pathsep  # ';' on Windows, ':' on Unix

# Bundle all JSON config files from plugin directories
for plugin_dir in ["extraction_plugins", "validators_plugin"]:
    json_files = glob.glob(os.path.join(plugin_dir, "*.json"))
    if json_files:
        add_data.append(f"--add-data={plugin_dir}{os.sep}*.json{SEP}{plugin_dir}")

# Bundle the global settings file
if os.path.exists("settings.json"):
    add_data.append(f"--add-data=settings.json{SEP}.")

# Bundle the icon so the running app can reference it if needed
if os.path.exists(ICON_SOURCE):
    add_data.append(f"--add-data={ICON_SOURCE}{SEP}.")

# ── 3. Run PyInstaller ───────────────────────────────────────────────
pyinstaller_args = [
    ENTRY_POINT,
    f"--name={APP_NAME}",
    "--windowed",       # No console window
    "--onedir",         # Creates a directory, not a single file
    "--clean",
] + icon_arg + add_data + hidden_imports

print(f"\nPyInstaller args ({len(pyinstaller_args)}):")
for arg in pyinstaller_args:
    print(f"  {arg}")
print()

PyInstaller.__main__.run(pyinstaller_args)

# ── 4. Post-Build: Create the "Drop-in" Plugin Structure ────────────
print("\n--- Setting up Plugin Folders ---")

dist_path = os.path.join("dist", APP_NAME)
plugins_path = os.path.join(dist_path, "plugins")
ext_plugins = os.path.join(plugins_path, "extraction")
val_plugins = os.path.join(plugins_path, "validators")

# Create directories
os.makedirs(ext_plugins, exist_ok=True)
os.makedirs(val_plugins, exist_ok=True)

# Create README in the plugins folder so users understand its purpose
readme_text = """# Plugin Drop-in Directory

Place custom extractor plugins (.py) in the 'extraction/' subfolder.
Place custom validator plugins (.py) in the 'validators/' subfolder.

Plugins are loaded automatically on application startup.
See the Installer_Pattern.txt file for plugin development documentation.

NOTE: The built-in plugins are bundled inside the application.
      Only place NEW or CUSTOM plugins here — do NOT copy the built-in
      plugins, as this will cause duplicate loading warnings.
"""

with open(os.path.join(plugins_path, "README.md"), "w") as f:
    f.write(readme_text)

print(f"\nBuild Complete!")
print(f"Your app is ready in: {dist_path}")
print(f"Users can drop new .py files into: {plugins_path}")
print(f"\nBuilt-in plugins are bundled inside the executable.")
print(f"The plugins/ folder is for user-added extensions only.")