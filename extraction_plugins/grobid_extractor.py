# extraction_plugins/grobid_extractor.py
#
# REFACTORED — Changes from original:
#   [C2] extract() now wraps ALL logic in try/except and returns "" on failure
#   [M4] is_configured() no longer makes a network call — checks config only
#   [W1] All subprocess calls use CREATE_NO_WINDOW on Windows to prevent
#        console window flashing in frozen (PyInstaller) builds
#
# All other logic (Docker lifecycle, retry, TEI parsing) is unchanged.

import atexit
import os
import subprocess
import time
import traceback
from typing import Dict, Any, Optional

from extraction_plugins.base import BaseExtractor
from extraction_plugins.manager import register_extractor


@register_extractor
class GrobidExtractor(BaseExtractor):
    """
    Extraction plugin that uses a GROBID service to extract citations
    from PDF documents.  Returns a CSV string conforming to the
    Extraction Blueprint specification.

    Supports two operational modes controlled by AUTO_START_GROBID:

      Managed mode (AUTO_START_GROBID = true):
        The plugin starts its own Docker container on first use and
        stops it when the application exits.

      External mode (AUTO_START_GROBID = false):
        The plugin connects to an already-running GROBID instance at
        GROBID_URL.
    """

    DEPENDENCIES = ["requests"]

    # ── Managed container state (class-level, shared across instances) ─
    _managed_container_id: Optional[str] = None
    _atexit_registered: bool = False

    @staticmethod
    def _subprocess_kwargs() -> dict:
        """
        Returns extra kwargs for subprocess.run() that suppress the
        console window flash on frozen Windows builds.

        When a PyInstaller-bundled GUI app calls subprocess.run(), each
        invocation briefly spawns a visible console window.  This is
        especially disorienting during _wait_for_grobid(), which polls
        docker inspect every 2 seconds.

        CREATE_NO_WINDOW (0x08000000) prevents the child process from
        creating or inheriting a console window.

        On non-Windows platforms this returns an empty dict (no-op).
        """
        import platform
        if platform.system() == "Windows":
            return {"creationflags": subprocess.CREATE_NO_WINDOW}
        return {}

    # ── BaseExtractor interface ──────────────────────────────────────

    @property
    def name(self) -> str:
        return "GROBID Service"

    @property
    def is_programmatic(self) -> bool:
        return True

    def get_default_settings(self) -> Dict[str, Any]:
        return {
            # --- Docker lifecycle ---
            "AUTO_START_GROBID": False,
            "GROBID_DOCKER_IMAGE": "grobid/grobid:0.8.2",
            "GROBID_CONTAINER_NAME": "citation_app_grobid",
            "GROBID_STARTUP_TIMEOUT": 120,
            # --- Connection ---
            "GROBID_URL": "http://localhost:8070",
            "GROBID_MODE": "full",
            "CONSOLIDATE_CITATIONS": 0,
            "FLAVOR": "",
            "INCLUDE_RAW_CITATIONS": 1,
            "TIMEOUT": 120,
            "MAX_RETRIES": 5,
            "RETRY_DELAY": 3,
        }

    def is_configured(self) -> bool:
        """
        [M4] REFACTORED: No longer makes a network call.

        is_configured() checks whether the plugin has enough *configuration*
        to attempt an extraction — not whether the server is currently up.
        Runtime availability is checked at the start of extract().

        Managed mode: Docker CLI must be available.
        External mode: GROBID_URL must be set.
        """
        auto_start = self.config.get("AUTO_START_GROBID", False)

        if auto_start:
            return self._is_docker_available()

        # External mode: just check that the URL is set
        url = self.config.get("GROBID_URL", "").strip()
        return bool(url)

    # ── Docker lifecycle management ──────────────────────────────────

    @classmethod
    def _is_docker_available(cls) -> bool:
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True, timeout=10,
                **cls._subprocess_kwargs()
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _ensure_managed_container(self):
        auto_start = self.config.get("AUTO_START_GROBID", False)
        if not auto_start:
            return

        if GrobidExtractor._managed_container_id:
            if self._is_container_running(GrobidExtractor._managed_container_id):
                print(f"[{self.name}] Managed container already running.")
                return
            else:
                print(f"[{self.name}] Previous managed container is gone. Starting a new one.")
                GrobidExtractor._managed_container_id = None

        if not self._is_docker_available():
            raise EnvironmentError(
                "AUTO_START_GROBID is enabled but Docker is not available. "
                "Please install Docker or disable AUTO_START_GROBID and "
                "run GROBID manually."
            )

        image = self.config.get("GROBID_DOCKER_IMAGE", "grobid/grobid:0.8.2")
        container_name = self.config.get("GROBID_CONTAINER_NAME", "citation_app_grobid")
        startup_timeout = int(self.config.get("GROBID_STARTUP_TIMEOUT", 120))
        grobid_url = self.config.get("GROBID_URL", "http://localhost:8070").rstrip("/")

        host_port = self._parse_port_from_url(grobid_url)
        self._remove_container_if_exists(container_name)

        print(f"[{self.name}] Starting managed GROBID container...")
        print(f"[{self.name}]   Image : {image}")
        print(f"[{self.name}]   Name  : {container_name}")
        print(f"[{self.name}]   Port  : {host_port}:8070")

        docker_cmd = [
            "docker", "run",
            "--rm", "-d",
            "--name", container_name,
            "-p", f"{host_port}:8070",
            image,
        ]

        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True, text=True, timeout=60,
                **self._subprocess_kwargs()
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Docker command timed out while starting GROBID container. "
                f"The image '{image}' may need to be pulled first.\n"
                f"Try running:  docker pull {image}"
            )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise RuntimeError(
                f"Failed to start GROBID Docker container:\n{stderr}\n\n"
                f"If the image is not yet pulled, run:  docker pull {image}"
            )

        container_id = result.stdout.strip()[:12]
        GrobidExtractor._managed_container_id = container_id
        print(f"[{self.name}] Container started: {container_id}")

        if not GrobidExtractor._atexit_registered:
            atexit.register(GrobidExtractor._stop_managed_container)
            GrobidExtractor._atexit_registered = True

        self._wait_for_grobid(grobid_url, startup_timeout)

    def _wait_for_grobid(self, base_url: str, timeout: int):
        import requests

        print(f"[{self.name}] Waiting for GROBID to become ready (timeout={timeout}s)...")
        start = time.time()
        poll_interval = 2

        while (time.time() - start) < timeout:
            try:
                resp = requests.get(f"{base_url}/api/isalive", timeout=5)
                if resp.status_code == 200:
                    elapsed = int(time.time() - start)
                    print(f"[{self.name}] GROBID is ready. (took ~{elapsed}s)")
                    return
            except requests.exceptions.ConnectionError:
                pass
            except requests.exceptions.Timeout:
                pass

            if GrobidExtractor._managed_container_id:
                if not self._is_container_running(GrobidExtractor._managed_container_id):
                    raise RuntimeError(
                        "Managed GROBID container exited unexpectedly. "
                        "Check Docker logs:  docker logs citation_app_grobid"
                    )

            time.sleep(poll_interval)

        raise TimeoutError(
            f"GROBID did not become ready within {timeout} seconds. "
            f"You can increase GROBID_STARTUP_TIMEOUT in settings, or "
            f"check Docker logs:  docker logs {self.config.get('GROBID_CONTAINER_NAME', 'citation_app_grobid')}"
        )

    @classmethod
    def _is_container_running(cls, container_id: str) -> bool:
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
                capture_output=True, text=True, timeout=10,
                **cls._subprocess_kwargs()
            )
            return result.stdout.strip().lower() == "true"
        except Exception:
            return False

    @classmethod
    def _remove_container_if_exists(cls, container_name: str):
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True, timeout=15,
                **cls._subprocess_kwargs()
            )
        except Exception:
            pass

    @staticmethod
    def _parse_port_from_url(url: str) -> int:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if parsed.port:
                return parsed.port
        except Exception:
            pass
        return 8070

    @classmethod
    def _stop_managed_container(cls):
        container_id = cls._managed_container_id
        if not container_id:
            return

        print(f"[GROBID Service] Stopping managed container {container_id}...")
        try:
            subprocess.run(
                ["docker", "stop", container_id],
                capture_output=True, timeout=30,
                **cls._subprocess_kwargs()
            )
            print(f"[GROBID Service] Managed container stopped.")
        except subprocess.TimeoutExpired:
            print(f"[GROBID Service] Container stop timed out — killing...")
            try:
                subprocess.run(
                    ["docker", "kill", container_id],
                    capture_output=True, timeout=10,
                    **cls._subprocess_kwargs()
                )
            except Exception:
                pass
        except Exception as e:
            print(f"[GROBID Service] Error stopping container: {e}")
        finally:
            cls._managed_container_id = None

    def shutdown(self):
        GrobidExtractor._stop_managed_container()

    # ── Main extraction logic ────────────────────────────────────────

    def extract(self, filepath: str) -> str:
        """
        Submit a file to GROBID, parse the TEI-XML response, and
        return a CSV string matching the Extraction Blueprint schema.

        [C2] REFACTORED: Entire method body wrapped in try/except.
        Returns "" on any failure instead of propagating exceptions.
        The ExtractionManager does NOT catch exceptions from extract().
        """
        try:
            import requests
            from extraction_plugins.grobid_tei_parser import parse_tei_references, citations_to_csv

            # ── Step 0: Ensure GROBID is available (managed mode) ────
            self._ensure_managed_container()

            base_url = self.config.get("GROBID_URL", "http://localhost:8070").rstrip("/")
            mode = self.config.get("GROBID_MODE", "full").lower().strip()
            consolidate = int(self.config.get("CONSOLIDATE_CITATIONS", 0))
            flavor = self.config.get("FLAVOR", "").strip()
            include_raw = int(self.config.get("INCLUDE_RAW_CITATIONS", 1))
            timeout = int(self.config.get("TIMEOUT", 120))
            max_retries = int(self.config.get("MAX_RETRIES", 5))
            retry_delay = int(self.config.get("RETRY_DELAY", 3))

            file_ext = os.path.splitext(filepath)[1].lower()

            # ── Step 1: Verify GROBID is alive ───────────────────────
            print(f"[{self.name}] Checking GROBID availability at {base_url}...")
            try:
                alive_resp = requests.get(f"{base_url}/api/isalive", timeout=10)
                if alive_resp.status_code != 200:
                    print(f"[{self.name}] GROBID server at {base_url} returned status "
                          f"{alive_resp.status_code}. Is the Docker container running?")
                    return ""
                print(f"[{self.name}] GROBID is alive.")
            except requests.exceptions.ConnectionError:
                print(f"[{self.name}] Cannot connect to GROBID at {base_url}. "
                      f"Please ensure the GROBID Docker container is running.")
                return ""

            # ── Step 2: Choose endpoint and build request ────────────
            if file_ext == ".pdf":
                tei_xml = self._process_pdf(
                    filepath, base_url, mode, consolidate, flavor,
                    include_raw, timeout, max_retries, retry_delay
                )
            elif file_ext in (".txt", ".md", ".csv", ".rtf"):
                tei_xml = self._process_text_file(
                    filepath, base_url, consolidate,
                    timeout, max_retries, retry_delay
                )
            else:
                print(f"[{self.name}] Unknown extension '{file_ext}', attempting PDF processing...")
                tei_xml = self._process_pdf(
                    filepath, base_url, mode, consolidate, flavor,
                    include_raw, timeout, max_retries, retry_delay
                )

            if not tei_xml:
                print(f"[{self.name}] ERROR: GROBID returned empty response.")
                return ""

            # ── Step 3: Parse TEI-XML into citations ─────────────────
            print(f"[{self.name}] Parsing TEI-XML response ({len(tei_xml)} chars)...")

            try:
                citations = parse_tei_references(tei_xml)
            except Exception as e:
                print(f"[{self.name}] ERROR parsing TEI-XML: {e}")
                traceback.print_exc()
                self._save_debug_xml(filepath, tei_xml)
                return ""

            if not citations:
                print(f"[{self.name}] WARNING: No references found in GROBID output.")
                self._save_debug_xml(filepath, tei_xml)
                return ""

            print(f"[{self.name}] Extracted {len(citations)} citations.")

            # ── Step 4: Convert to CSV ───────────────────────────────
            csv_output = citations_to_csv(citations)
            return csv_output

        # [C2] Catch ALL exceptions — never propagate from extract()
        except Exception as e:
            print(f"[{self.name}] Extraction failed: {e}")
            traceback.print_exc()
            return ""

    def _save_debug_xml(self, filepath: str, tei_xml: str):
        """Save raw XML for debugging when parsing fails."""
        debug_path = filepath + ".grobid_debug.xml"
        try:
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(tei_xml)
            print(f"[{self.name}] Raw XML saved to: {debug_path}")
        except Exception:
            pass

    # ── PDF processing ───────────────────────────────────────────────

    def _process_pdf(
        self, filepath: str, base_url: str, mode: str,
        consolidate: int, flavor: str, include_raw: int,
        timeout: int, max_retries: int, retry_delay: int
    ) -> str:
        if mode == "light":
            endpoint = f"{base_url}/api/processReferences"
            print(f"[{self.name}] Using LIGHT mode (processReferences)")
        else:
            endpoint = f"{base_url}/api/processFulltextDocument"
            print(f"[{self.name}] Using FULL mode (processFulltextDocument)")

        form_data = {
            "consolidateCitations": str(consolidate),
            "includeRawCitations": str(include_raw),
        }

        if flavor:
            form_data["flavor"] = flavor
            print(f"[{self.name}] Using flavor: {flavor}")

        if mode != "light":
            form_data["consolidateHeader"] = "0"

        print(f"[{self.name}] Submitting PDF: {os.path.basename(filepath)} "
              f"(consolidation={consolidate})...")

        return self._submit_with_retry(
            endpoint=endpoint,
            filepath=filepath,
            form_data=form_data,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

    # ── Text file processing ─────────────────────────────────────────

    def _process_text_file(
        self, filepath: str, base_url: str, consolidate: int,
        timeout: int, max_retries: int, retry_delay: int
    ) -> str:
        print(f"[{self.name}] Reading text file for reference extraction...")

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(filepath, "r", encoding="latin-1") as f:
                content = f.read()

        ref_lines = self._extract_reference_lines(content)

        if not ref_lines:
            print(f"[{self.name}] No reference-like lines found in text file.")
            return ""

        print(f"[{self.name}] Found {len(ref_lines)} potential reference lines.")

        endpoint = f"{base_url}/api/processCitationList"
        citations_text = "\n".join(ref_lines)

        form_data = {
            "citations": citations_text,
            "consolidateCitations": str(consolidate),
        }

        print(f"[{self.name}] Submitting {len(ref_lines)} citations to processCitationList...")

        response_text = self._post_form_with_retry(
            endpoint=endpoint,
            form_data=form_data,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

        if response_text and not response_text.strip().startswith("<?xml"):
            if "<listBibl" not in response_text and "<biblStruct" in response_text:
                response_text = (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<TEI xmlns="http://www.tei-c.org/ns/1.0">\n'
                    '  <text><back><div type="references"><listBibl>\n'
                    f'    {response_text}\n'
                    '  </listBibl></div></back></text>\n'
                    '</TEI>'
                )

        return response_text

    def _extract_reference_lines(self, content: str) -> list:
        import re

        lines = content.split("\n")
        ref_section_start = None

        for i, line in enumerate(lines):
            stripped = line.strip().lower()
            if stripped in ("references", "bibliography", "literature cited",
                            "works cited", "reference list", "literature"):
                ref_section_start = i + 1
                break
            if re.match(r"^(\d+\.?\s+)?(references|bibliography|literature cited)\s*$",
                        stripped):
                ref_section_start = i + 1
                break

        if ref_section_start is not None:
            ref_lines = []
            for line in lines[ref_section_start:]:
                stripped = line.strip()
                if stripped:
                    ref_lines.append(stripped)
            return ref_lines

        ref_lines = []
        for line in lines:
            stripped = line.strip()
            if re.match(r"^\[?\d+[\].)]\s+", stripped):
                ref_lines.append(stripped)

        return ref_lines

    # ── HTTP helpers with retry logic ────────────────────────────────

    def _submit_with_retry(
        self, endpoint: str, filepath: str, form_data: dict,
        timeout: int, max_retries: int, retry_delay: int,
    ) -> str:
        import requests

        for attempt in range(1, max_retries + 1):
            try:
                with open(filepath, "rb") as f:
                    files = {"input": (os.path.basename(filepath), f, "application/pdf")}
                    response = requests.post(
                        endpoint,
                        files=files,
                        data=form_data,
                        timeout=timeout,
                    )

                if response.status_code == 200:
                    print(f"[{self.name}] GROBID response received "
                          f"({len(response.text)} chars).")
                    return response.text

                elif response.status_code == 503:
                    if attempt < max_retries:
                        wait = retry_delay * attempt
                        print(f"[{self.name}] Server busy (503). "
                              f"Retry {attempt}/{max_retries} in {wait}s...")
                        time.sleep(wait)
                        continue
                    else:
                        raise RuntimeError(
                            f"GROBID server busy after {max_retries} retries."
                        )

                elif response.status_code == 204:
                    print(f"[{self.name}] GROBID returned 204 (No Content). "
                          f"The document may not contain extractable references.")
                    return ""

                else:
                    raise RuntimeError(
                        f"GROBID returned HTTP {response.status_code}: "
                        f"{response.text[:500]}"
                    )

            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    print(f"[{self.name}] Request timed out. "
                          f"Retry {attempt}/{max_retries}...")
                    time.sleep(retry_delay)
                    continue
                else:
                    raise TimeoutError(
                        f"GROBID request timed out after {max_retries} attempts "
                        f"(timeout={timeout}s)."
                    )

            except requests.exceptions.ConnectionError as e:
                raise ConnectionError(
                    f"Lost connection to GROBID at {endpoint}: {e}"
                )

        return ""

    def _post_form_with_retry(
        self, endpoint: str, form_data: dict,
        timeout: int, max_retries: int, retry_delay: int,
    ) -> str:
        import requests

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(
                    endpoint,
                    data=form_data,
                    timeout=timeout,
                )

                if response.status_code == 200:
                    return response.text
                elif response.status_code == 503:
                    if attempt < max_retries:
                        wait = retry_delay * attempt
                        print(f"[{self.name}] Server busy (503). "
                              f"Retry {attempt}/{max_retries} in {wait}s...")
                        time.sleep(wait)
                        continue
                    else:
                        raise RuntimeError(
                            f"GROBID server busy after {max_retries} retries."
                        )
                elif response.status_code == 204:
                    return ""
                else:
                    raise RuntimeError(
                        f"GROBID returned HTTP {response.status_code}: "
                        f"{response.text[:500]}"
                    )

            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                raise TimeoutError(f"GROBID timed out after {max_retries} attempts.")

        return ""