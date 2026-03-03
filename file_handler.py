# file_handler.py
# File handling module for various document formats

import os
import sys
import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from config import Config
from striprtf.striprtf import rtf_to_text
import requests
import tempfile
import mimetypes
from urllib.parse import urlparse

RTF_SUPPORT = True


class FileHandler:
    """Handles file operations for various document formats"""

    @staticmethod
    def get_file_dialog():
        """Opens a file dialog to select a single file"""
        return filedialog.askopenfilename(
            title="Select a document file",
            filetypes=FileHandler._get_file_types()
        )

    @staticmethod
    def get_directory_dialog():
        """Opens a dialog to select a directory"""
        return filedialog.askdirectory(title="Select Directory")

    @staticmethod
    def get_multiple_files_dialog():
        """Opens a dialog to select multiple CSV files"""
        return filedialog.askopenfilenames(
            title="Select CSV files to merge",
            filetypes=[("CSV Files", "*.csv")]
        )

    @staticmethod
    def _get_file_types():
        """Helper to build file types list from config"""
        file_types = []
        # Sort so specific extensions come before *.*
        for ext, desc in Config.SUPPORTED_EXTENSIONS.items():
            if desc not in [ft[0] for ft in file_types]:
                extensions = [e for e, d in Config.SUPPORTED_EXTENSIONS.items() if d == desc]
                pattern = "*" + ";*".join(extensions)
                file_types.append((desc, pattern))
        file_types.append(("All files", "*.*"))
        return file_types

    @staticmethod
    def validate_file(filepath):
        """Validate the selected file"""
        if not filepath:
            raise ValueError("No file selected.")

        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        file_ext = Path(filepath).suffix.lower()

        if file_ext not in Config.SUPPORTED_EXTENSIONS:
            supported = ', '.join(Config.SUPPORTED_EXTENSIONS.keys())
            raise ValueError(f"Unsupported file type: {file_ext}. Supported types: {supported}")

        return True

    @staticmethod
    def scan_directory(directory_path):
        """
        Scans a directory for all files matching supported extensions.
        Returns a list of full file paths.
        """
        if not os.path.isdir(directory_path):
            raise ValueError(f"Invalid directory: {directory_path}")

        valid_files = []
        for root, _, files in os.walk(directory_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in Config.SUPPORTED_EXTENSIONS:
                    valid_files.append(os.path.join(root, file))

        return valid_files

    @staticmethod
    def read_text_file(filepath):
        """Read a text file with proper encoding detection and RTF support"""
        file_ext = Path(filepath).suffix.lower()
        raw_content = ""

        # 1. Read the raw bytes/string from disk
        encodings = ['utf-8', 'cp1252', 'iso-8859-1', 'ascii']
        read_success = False

        for encoding in encodings:
            try:
                with open(filepath, 'r', encoding=encoding) as f:
                    raw_content = f.read()
                read_success = True
                break
            except UnicodeDecodeError:
                continue

        if not read_success:
            # Fallback ignore errors
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                raw_content = f.read()

        # 2. If it is RTF, convert to plain text
        if file_ext == '.rtf':
            if RTF_SUPPORT:
                try:
                    # striprtf handles the parsing of braces and control words
                    return rtf_to_text(raw_content)
                except Exception as e:
                    print(f"[FileHandler] Warning: Failed to parse RTF with striprtf: {e}")
                    return raw_content
            else:
                print("[FileHandler] Warning: 'striprtf' library not found. Processing raw RTF (high token usage). "
                      "Run `pip install striprtf` to fix.")
                return raw_content

        return raw_content

    @staticmethod
    def get_valid_file_path():
        """Combined method to get and validate a file path"""
        filepath = FileHandler.get_file_dialog()
        if not filepath:
            raise ValueError("No file selected.")
        FileHandler.validate_file(filepath)
        return filepath

    @staticmethod
    def is_url(input_path: str) -> bool:
        """Checks if the input string looks like a URL."""
        try:
            result = urlparse(input_path)
            return all([result.scheme, result.netloc]) and result.scheme in ['http', 'https']
        except ValueError:
            return False

    @staticmethod
    def download_to_temp(url: str) -> str:
        """
        Downloads a file from a URL to a temporary file.
        Returns the path to the temporary file.
        """
        print(f"[FileHandler] Downloading from URL: {url}")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        try:
            response = requests.get(url, headers=headers, stream=True, timeout=15)
            response.raise_for_status()

            # 1. PRIORITIZE Content-Type header for extension guessing
            # (Fixes issues with arXiv URLs like '.../2602.06039' which have no real extension)
            content_type = response.headers.get('content-type', '').split(';')[0].strip()
            path_ext = mimetypes.guess_extension(content_type)

            # 2. If Content-Type didn't give a useful extension, fall back to URL
            if not path_ext:
                parsed = urlparse(url)
                path_ext = os.path.splitext(parsed.path)[1]

            # 3. Default fallback
            if not path_ext:
                path_ext = ".txt"

            # Create temp file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=path_ext)

            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    temp_file.write(chunk)

            temp_file.close()
            print(f"[FileHandler] Saved to temp file: {temp_file.name}")
            return temp_file.name

        except Exception as e:
            raise ValueError(f"Failed to download URL: {e}")