# plugin_installer.py
#
# v3 CHANGES (from v1):
#   - Frozen builds: dependencies are installed to {exe_dir}/plugins/lib/
#     via `pip install --target`, a directory that the plugin managers add
#     to sys.path before discovery.  This means third-party plugins can
#     bring their own dependencies even in compiled builds.
#   - File copy happens FIRST, before dependency installation, so a pip
#     failure never prevents the plugin from being placed correctly.
#   - Windows Store Python stubs (exit code 9009) are detected and skipped.
#   - Dependency installation is best-effort: failures produce warnings
#     instead of aborting the entire install.
#   - analyze_plugin no longer requires @register decorators (v2 managers
#     use __subclasses__() for discovery).

import ast
import shutil
import os
import sys
import subprocess
from pathlib import Path


class PluginInstaller:
    """
    Analyzes python files to determine if they are valid Extractors or Validators,
    installs them, and automatically resolves declared dependencies.
    """

    # ── Path Resolution ──────────────────────────────────────────────────

    @staticmethod
    def _get_base_dir() -> Path:
        """
        Returns the application root directory, matching the logic used
        by ExtractionManager and ValidatorManager.
        """
        if getattr(sys, 'frozen', False):
            return Path(os.path.dirname(sys.executable))
        else:
            return Path(os.path.dirname(os.path.abspath(__file__)))

    @staticmethod
    def _get_install_dirs():
        """
        Returns (extractor_dir, validator_dir) — the directories where
        plugins should be copied so that the managers will discover them.

        Frozen:  {exe_dir}/plugins/extraction|validators/
        Source:  {project}/extraction_plugins|validators_plugin/
        """
        base = PluginInstaller._get_base_dir()

        if getattr(sys, 'frozen', False):
            return (
                base / "plugins" / "extraction",
                base / "plugins" / "validators",
            )
        else:
            return (
                base / "extraction_plugins",
                base / "validators_plugin",
            )

    @staticmethod
    def _get_plugins_lib_dir() -> Path:
        """
        Returns the directory where plugin dependencies are installed
        in frozen builds: {exe_dir}/plugins/lib/

        The ExtractionManager and ValidatorManager add this to sys.path
        before loading external plugins, so anything installed here is
        importable by the frozen application.
        """
        return PluginInstaller._get_base_dir() / "plugins" / "lib"

    # ── AST Analysis ─────────────────────────────────────────────────────

    @staticmethod
    def analyze_plugin(filepath: str):
        """
        Parses the AST to identify:
        1. Class inheritance (BaseExtractor vs BaseValidator)
        2. Presence of registration decorators (optional since v2)
        3. Required dependencies
        """
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                tree = ast.parse(f.read())
            except SyntaxError:
                return {"valid": False, "error": "Syntax Error in file"}

        plugin_type = None
        class_name = None
        has_decorator = False
        dependencies = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        if base.id == "BaseExtractor":
                            plugin_type = "extractor"
                            class_name = node.name
                        elif base.id == "BaseValidator":
                            plugin_type = "validator"
                            class_name = node.name

                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Name):
                        if decorator.id in ["register_extractor", "register_validator"]:
                            has_decorator = True

                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name) and target.id == "DEPENDENCIES":
                                if isinstance(item.value, ast.List):
                                    for elt in item.value.elts:
                                        if hasattr(elt, 'value'):
                                            dependencies.append(elt.value)
                                        elif hasattr(elt, 's'):
                                            dependencies.append(elt.s)

        if plugin_type:
            return {
                "valid": True,
                "type": plugin_type,
                "class": class_name,
                "has_decorator": has_decorator,
                "dependencies": dependencies,
            }

        return {
            "valid": False,
            "error": (
                "File does not contain a class inheriting from "
                "BaseExtractor or BaseValidator."
            ),
        }

    # ── Dependency Installation ──────────────────────────────────────────

    @staticmethod
    def _is_real_executable(executable: str, test_arg: str = "--version") -> bool:
        """
        Probes an executable to confirm it's real and functional.

        Filters out Windows Store stubs (which live in WindowsApps/ and
        return exit code 9009) and broken symlinks.
        """
        try:
            result = subprocess.run(
                [executable, test_arg],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    @staticmethod
    def _find_pip_command() -> list:
        """
        Returns a command list for invoking pip, or an empty list if
        no working pip can be found.

        Search order:
        1. Direct pip/pip3 executables on PATH (fastest)
        2. python -m pip using a validated Python interpreter

        Each candidate is tested with --version before being accepted.
        """
        # 1. Try direct pip executables
        for name in ("pip3", "pip"):
            found = shutil.which(name)
            if found and PluginInstaller._is_real_executable(found):
                return [found]

        # 2. Try python -m pip with a validated interpreter
        python_names = ["python3", "python"]
        if sys.platform == "win32":
            python_names.append("py")

        for name in python_names:
            found = shutil.which(name)
            if found and PluginInstaller._is_real_executable(found):
                # Verify this Python actually has pip
                if PluginInstaller._is_real_executable(found, "-m pip --version"):
                    return [found, "-m", "pip"]

        # 3. Check common Windows install locations
        if sys.platform == "win32":
            import glob
            patterns = [
                os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python*\python.exe"),
                r"C:\Python*\python.exe",
            ]
            for pattern in patterns:
                for match in sorted(glob.glob(pattern), reverse=True):
                    if PluginInstaller._is_real_executable(match):
                        return [match, "-m", "pip"]

        return []

    @staticmethod
    def _install_dependencies(dependencies: list) -> str:
        """
        Installs pip dependencies.  Returns a status message (never raises).

        Source mode:
            Standard `pip install` into the current environment.

        Frozen mode:
            `pip install --target={exe_dir}/plugins/lib/` so that
            packages land in a directory the plugin managers add to
            sys.path.  Uses a system Python/pip since the frozen app's
            own executable cannot run pip.
        """
        if not dependencies:
            return ""

        dep_str = ", ".join(dependencies)

        if getattr(sys, 'frozen', False):
            # ── Frozen build: pip install --target ────────────────────
            pip_cmd = PluginInstaller._find_pip_command()

            if not pip_cmd:
                return (
                    f"\n⚠️ Could not find a working pip to install: {dep_str}\n"
                    f"  The plugin file has been copied, but its dependencies\n"
                    f"  are missing.  Please install them manually:\n\n"
                    f"    pip install --target=\"{PluginInstaller._get_plugins_lib_dir()}\" "
                    f"{' '.join(dependencies)}\n"
                )

            target_dir = str(PluginInstaller._get_plugins_lib_dir())
            cmd = pip_cmd + ["install", "--target", target_dir] + dependencies

            print(f"Installing dependencies: {dep_str}")
            print(f"  Command: {' '.join(cmd)}")
            print(f"  Target:  {target_dir}")

            try:
                subprocess.check_call(cmd)
                return f"Dependencies installed successfully: {dep_str}"
            except subprocess.CalledProcessError as e:
                return (
                    f"\n⚠️ pip install failed (exit code {e.returncode}).\n"
                    f"  The plugin file has been copied, but dependencies\n"
                    f"  may be missing.  Install them manually:\n\n"
                    f"    pip install --target=\"{target_dir}\" "
                    f"{' '.join(dependencies)}\n"
                )

        else:
            # ── Source build: standard pip install ────────────────────
            python = sys.executable
            print(f"Installing dependencies: {dep_str}")
            print(f"  Using Python: {python}")

            try:
                subprocess.check_call(
                    [python, "-m", "pip", "install"] + dependencies
                )
                return f"Dependencies installed successfully: {dep_str}"
            except subprocess.CalledProcessError as e:
                return (
                    f"\n⚠️ pip install failed (exit code {e.returncode}).\n"
                    f"  Please install manually:\n"
                    f"    pip install {' '.join(dependencies)}"
                )

    # ── Main Entry Point ─────────────────────────────────────────────────

    @staticmethod
    def install_plugin(source_path: str):
        analysis = PluginInstaller.analyze_plugin(source_path)

        if not analysis["valid"]:
            raise ValueError(f"Invalid Plugin: {analysis.get('error')}")

        # ── 1. Copy the plugin file FIRST ────────────────────────────
        extractor_dir, validator_dir = PluginInstaller._get_install_dirs()
        source = Path(source_path)

        if analysis["type"] == "extractor":
            dest_dir = extractor_dir
        else:
            dest_dir = validator_dir

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / source.name

        if dest_file.exists():
            raise FileExistsError(
                f"A plugin named '{source.name}' already exists in:\n"
                f"  {dest_dir}\n\n"
                f"Delete the existing file first if you want to replace it."
            )

        shutil.copy2(source, dest_file)

        # ── 2. Handle dependencies (best-effort, never fatal) ────────
        dep_message = ""
        if analysis.get("dependencies"):
            dep_message = PluginInstaller._install_dependencies(
                analysis["dependencies"]
            )

        # ── 3. Build result message ──────────────────────────────────
        result = (
            f"✅ Successfully installed {analysis['type']} "
            f"'{analysis['class']}'\n"
            f"   → {dest_file}"
        )

        if dep_message:
            result += f"\n\n{dep_message}"

        result += "\n\nRestart the application to activate the plugin."

        return result