# extraction_plugins/gemini_extractor.py
#
# REFACTORED — Changes from original:
#   [C1] extract() now catches all exceptions and returns "" instead of raising
#   [C1] Removed ValueError raise for missing API key — prints and returns ""
#   [M1] Added lineterminator='\n' to csv.writer for cross-platform consistency

import csv
import io
import json
import os
import time
import traceback
import mimetypes
from typing import Dict, Any, List
from extraction_plugins.base import BaseExtractor
from extraction_plugins.manager import register_extractor
from google import genai
from google.genai import types
from extraction_plugins.gemini_extractor_prompts import Prompts
from file_handler import FileHandler
from config import Config


@register_extractor
class GeminiExtractor(BaseExtractor):
    DEPENDENCIES = ["google-genai"]

    # ── Column Mapping ──────────────────────────────────────────────
    HEADER_TO_KEY: Dict[str, str] = {
        "Citation Number":      "citation_number",
        "Type":                 "type",
        "Authors":              "authors",
        "Article Title":        "article_title",
        "Publication Title":    "publication_title",
        "Series":               "series",
        "Editor":               "editor",
        "Volume":               "volume",
        "Issue":                "issue",
        "Publisher":            "publisher",
        "Publication Location": "publication_location",
        "Year":                 "year",
        "Month":                "month",
        "Day":                  "day",
        "Pages":                "pages",
        "Edition":              "edition",
        "Institution":          "institution",
        "DOI":                  "doi",
        "ISBN":                 "isbn",
        "ISSN":                 "issn",
        "URL":                  "url",
        "Date Accessed":        "date_accessed",
    }

    CITATION_TYPES: List[str] = [
        "Journal Article",
        "Conference Paper",
        "Book",
        "Book Chapter",
        "Thesis",
        "Report",
        "Website",
        "Magazine Article",
        "Newspaper Article",
        "Encyclopedia Entry",
        "Other",
    ]

    def get_default_settings(self) -> Dict[str, Any]:
        return {
            "API_KEY": "YOUR_KEY_HERE",
            "MODEL_NAME": "gemini-2.5-flash",
            "MODEL_NAME_TEXT": "gemini-2.5-pro",
            "TEMPERATURE": 0.0,
            "MAX_OUTPUT_TOKENS": 64000,
        }

    def is_configured(self) -> bool:
        api_key = self.config.get("API_KEY")
        return bool(api_key and api_key != "YOUR_KEY_HERE")

    @property
    def name(self) -> str:
        return "Google Gemini API"

    @property
    def is_programmatic(self) -> bool:
        return False

    # ── Schema Construction ─────────────────────────────────────────

    def _build_response_schema(self) -> dict:
        properties: Dict[str, dict] = {}

        for header, key in self.HEADER_TO_KEY.items():
            if key == "citation_number":
                properties[key] = {"type": "INTEGER"}
            elif key == "type":
                properties[key] = {
                    "type": "STRING",
                    "enum": self.CITATION_TYPES,
                }
            else:
                properties[key] = {"type": "STRING"}

        return {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": properties,
                "required": ["citation_number", "type"],
            },
        }

    # ── JSON → CSV Conversion ───────────────────────────────────────

    def _json_to_csv(self, records: list) -> str:
        headers = Config.CSV_HEADERS
        key_order = [self.HEADER_TO_KEY[h] for h in headers]

        buf = io.StringIO()
        # [M1] Added lineterminator='\n' for consistency with contract template
        writer = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator='\n')
        writer.writerow(headers)

        for rec in records:
            row = [str(rec.get(k, "")) for k in key_order]
            writer.writerow(row)

        return buf.getvalue()

    # ── Model Selection ─────────────────────────────────────────────

    def _select_model(self, file_ext: str) -> str:
        text_extensions = {'.html', '.htm', '.txt', '.md', '.xml', '.json', '.csv', '.rtf'}

        if file_ext in text_extensions:
            model = self.config.get("MODEL_NAME_TEXT", self.config["MODEL_NAME"])
            print(f"[{self.name}] Text/HTML input detected — using text model: {model}")
            return model
        else:
            model = self.config["MODEL_NAME"]
            print(f"[{self.name}] PDF/binary input detected — using PDF model: {model}")
            return model

    # ── Main Extraction ─────────────────────────────────────────────

    def extract(self, filepath: str) -> str:
        """
        Main extraction entry point.

        [C1] REFACTORED: Now catches ALL exceptions and returns "" on
        failure, per the Extractor Contract (Section 2b, Pitfall 4).
        The ExtractionManager does NOT wrap extract() in try/except,
        so unhandled exceptions would abort the entire job.
        """
        # [C1] Check API key without raising — return "" if missing
        api_key = self.config.get("API_KEY")
        if not api_key or api_key == "YOUR_KEY_HERE":
            print(f"[{self.name}] Missing API Key. Check {self.config_filename}")
            return ""

        # Initialize Client
        client = genai.Client(api_key=api_key)
        uploaded_file = None

        try:
            contents = []
            file_ext = os.path.splitext(filepath)[1].lower()

            # Select the appropriate model for this file type
            model_name = self._select_model(file_ext)

            # 1. Prepare Content
            if file_ext == '.pdf':
                print(f"[{self.name}] Uploading {filepath}...")
                uploaded_file = client.files.upload(
                    file=filepath,
                    config=types.UploadFileConfig(mime_type='application/pdf')
                )
                print(f"[{self.name}] File uploaded: {uploaded_file.name} "
                      f"(MIME: {uploaded_file.mime_type})")

                self._wait_for_file_active(client, uploaded_file.name)

                prompt_text = Prompts.get_citation_extraction_prompt()
                contents = [uploaded_file, prompt_text]

            elif file_ext in ['.txt', '.csv', '.rtf', '.html', '.htm', '.md', '.xml', '.json']:
                print(f"[{self.name}] Reading text content locally for {file_ext}...")
                raw_text = FileHandler.read_text_file(filepath)
                prompt_text = Prompts.get_text_document_prompt()
                contents = [raw_text, prompt_text]
                print(f"[{self.name}] Read {len(raw_text)} chars.")
            else:
                # Fallback for unknown types (try upload)
                print(f"[{self.name}] Attempting upload for unknown type {file_ext}...")

                mime_type, _ = mimetypes.guess_type(filepath)
                if not mime_type:
                    mime_type = 'text/plain'

                print(f"[{self.name}] Detected/Defaulted MIME: {mime_type}")

                uploaded_file = client.files.upload(
                    file=filepath,
                    config=types.UploadFileConfig(mime_type=mime_type)
                )

                self._wait_for_file_active(client, uploaded_file.name)
                prompt_text = Prompts.get_citation_extraction_prompt()
                contents = [uploaded_file, prompt_text]

            # 2. Configure Generation (with Structured Output)
            max_tokens = int(self.config.get("MAX_OUTPUT_TOKENS", 64000))
            response_schema = self._build_response_schema()

            gen_config = types.GenerateContentConfig(
                temperature=self.config.get("TEMPERATURE", 0.0),
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
                response_schema=response_schema,
                safety_settings=[
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",
                                        threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",
                                        threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                                        threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",
                                        threshold="BLOCK_NONE"),
                ],
            )

            # 3. Generate
            print(f"[{self.name}] Generating content with {model_name} "
                  f"(Max Tokens: {max_tokens}, Mode: Structured JSON)...")

            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=gen_config,
            )

            # 4. Usage Logging
            if response.usage_metadata:
                usage = response.usage_metadata
                print(f"[{self.name}] Token Usage — "
                      f"Input: {usage.prompt_token_count} | "
                      f"Output: {usage.candidates_token_count}")

            # 5. Result Handling — parse JSON, convert to CSV
            raw_text = self._get_response_text(response)

            if not raw_text:
                print(f"[{self.name}] ERROR: Response is empty.")
                print(f"[{self.name}] Raw Response Dump: {response}")
                return ""

            try:
                records = json.loads(raw_text)
            except json.JSONDecodeError as e:
                print(f"[{self.name}] ERROR: Failed to parse JSON response: {e}")
                print(f"[{self.name}] Raw text (first 500 chars): {raw_text[:500]}")
                return ""

            if not isinstance(records, list):
                print(f"[{self.name}] ERROR: Expected a JSON array, "
                      f"got {type(records).__name__}.")
                return ""

            print(f"[{self.name}] Extracted {len(records)} citations. "
                  f"Converting to CSV...")

            return self._json_to_csv(records)

        # [C1] Catch ALL exceptions and return "" — never propagate
        except Exception as e:
            print(f"[{self.name}] Extraction failed: {e}")
            traceback.print_exc()
            return ""
        finally:
            if uploaded_file:
                try:
                    client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _get_response_text(response) -> str:
        if response.text:
            return response.text

        print("[GeminiExtractor] Standard text empty. Checking candidates...")
        if response.candidates:
            candidate = response.candidates[0]
            print(f"[GeminiExtractor] Finish Reason: {candidate.finish_reason}")

            if candidate.content and candidate.content.parts:
                parts_text = [part.text for part in candidate.content.parts if part.text]
                recovered = "".join(parts_text)
                if recovered:
                    print(f"[GeminiExtractor] Recovered {len(recovered)} chars from parts.")
                    return recovered

        return ""

    def _wait_for_file_active(self, client, file_name):
        print(f"[{self.name}] Waiting for file processing...", end="", flush=True)
        for _ in range(30):
            file_obj = client.files.get(name=file_name)
            if file_obj.state == "ACTIVE":
                print(" Ready.")
                return
            elif file_obj.state == "FAILED":
                raise ValueError(f"File processing failed. State: {file_obj.state}")
            time.sleep(1)
            print(".", end="", flush=True)
        raise TimeoutError("File processing timed out.")