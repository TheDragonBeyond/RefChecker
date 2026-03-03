# validators_plugin/llm_gemini.py
#
# Tier 2 (Deep Research) validator using Google Gemini.
# Only invoked when Tier 1 validators fail to meet the confidence threshold.

import json
import requests
from typing import Dict, Any, List, Optional
from google import genai
from validators_plugin.base import BaseValidator, ValidationResult, deep_research_validator
# FIX (LG1): Added register_validator import — required for Plugin Installer
from validators_plugin.manager import register_validator


# FIX (LG1): Added @register_validator — Tier 2 validators MUST have BOTH decorators
# for the Plugin Installer's AST check to accept the file (Pitfall 5).
@register_validator
@deep_research_validator
class GeminiResearchValidator(BaseValidator):

    # FIX (LG2): Added DEPENDENCIES — google-genai is not a stdlib package
    DEPENDENCIES: List[str] = ["google-genai"]

    # --- Configuration ---
    def get_default_settings(self) -> Dict[str, Any]:
        return {
            "API_KEY": "YOUR_KEY_HERE",
            "MODEL_NAME": "gemini-2.5-flash",
            "TEMPERATURE": 0.0,
            "TIMEOUT": 30
        }

    @property
    def name(self) -> str:
        return "Gemini Research Agent"

    # FIX (LG3): Added is_configured — checks for valid API key
    def is_configured(self) -> bool:
        api_key = self.config.get("API_KEY")
        return bool(api_key and api_key != "YOUR_KEY_HERE")

    # --- Main Validation Logic ---
    def validate(self, citation_data: Dict[str, str]) -> ValidationResult:
        # 1. Load Config
        api_key = self.config.get("API_KEY")
        model_name = self.config.get("MODEL_NAME")
        temperature = self.config.get("TEMPERATURE", 0.0)

        if not api_key or api_key == "YOUR_KEY_HERE":
            return ValidationResult(self.name, "Error", 0, "Missing API Key in GeminiResearchValidator config")

        # 2. Initialize Client Directly
        try:
            client = genai.Client(api_key=api_key)
        except Exception as e:
            return ValidationResult(self.name, "Error", 0, f"Client Init Failed: {e}")

        # 3. Prepare Context
        search_results = self._search_web_context(citation_data)

        # 4. Construct Prompt
        prompt = self._create_validation_prompt(citation_data, search_results)

        # 5. Generate
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "temperature": temperature,
                    "response_mime_type": "application/json"
                }
            )
            response_text = response.text
        except Exception as e:
            return ValidationResult(self.name, "Error", 0, f"Generation Failed: {str(e)}")

        # 6. Parse & Return
        parsed = self._parse_json_response(response_text)

        if parsed.get("error"):
            return ValidationResult(self.name, "Error", 0, parsed.get("message", "Parse Error"))

        status = parsed.get("recommendation", "Not Validated")
        confidence = parsed.get("confidence", 0)

        # Extract evidence links
        evidence_links = []
        evidence = parsed.get("evidence", {})
        if evidence.get("doi_url"): evidence_links.append(evidence["doi_url"])
        if evidence.get("google_scholar_link"): evidence_links.append(evidence["google_scholar_link"])
        if evidence.get("publisher_link"): evidence_links.append(evidence["publisher_link"])
        if evidence.get("additional_urls"): evidence_links.extend(evidence["additional_urls"])

        # Format details
        details = f"Reasoning: {parsed.get('reasoning', 'N/A')}\n"
        if parsed.get("verification_note"):
            details += f"Note: {parsed['verification_note']}"

        return ValidationResult(
            source_name=self.name,
            status=status,
            confidence_score=confidence,
            details=details,
            evidence_links=evidence_links,
            metadata=parsed
        )

    # --- Helper Methods ---

    def _search_web_context(self, citation_data: Dict[str, str]) -> List[Dict]:
        results = []
        title = citation_data.get('Article Title', citation_data.get('Title', ''))
        authors = citation_data.get('Author', citation_data.get('Authors', ''))
        year = citation_data.get('Year', '')
        doi = citation_data.get('DOI', '')

        if doi:
            clean_doi = doi.strip().replace('https://doi.org/', '').replace('http://dx.doi.org/', '')
            results.append({
                'type': 'doi_check',
                'info': f"DOI provided: {clean_doi}. Verify if this resolves to the title '{title}'."
            })

        if title:
            import urllib.parse
            q = f'"{title}"'
            if authors:
                q += f" {authors.split(',')[0]}"
            encoded = urllib.parse.quote(q)
            url = f"https://scholar.google.com/scholar?q={encoded}"
            results.append({
                'type': 'google_scholar_link',
                'url': url,
                'info': "Use this link to verify existence."
            })

        return results

    def _create_validation_prompt(self, citation_data: Dict, search_context: List[Dict]) -> str:
        citation_str = "\n".join([f"{k}: {v}" for k, v in citation_data.items() if v])
        context_str = "\n".join([f"- {item['info']} (URL: {item.get('url', 'N/A')})" for item in search_context])

        return f"""You are an expert academic citation validator.

CITATION TO CHECK:
{citation_str}

AUTOMATED CONTEXT:
{context_str}

TASK:
Verify if this citation exists and classify it.

Categories:
1. "Validated": Formal academic publication (Book, Journal, Paper) found.
2. "Ambiguous": Non-standard source (Blog, Repo, Product Page) but exists and matches description.
3. "Not Validated": Cannot find or dead link.

RESPONSE JSON FORMAT ONLY:
{{
    "recommendation": "Validated" | "Ambiguous" | "Not Validated",
    "confidence": <0-100>,
    "reasoning": "<brief explanation>",
    "verification_note": "<how to verify>",
    "evidence": {{
        "doi_url": "<url>",
        "google_scholar_link": "<url>",
        "publisher_link": "<url>",
        "additional_urls": ["<url>"]
    }}
}}
"""

    def _parse_json_response(self, text: str) -> Dict:
        try:
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            return json.loads(text.strip())
        except Exception as e:
            return {"error": True, "message": f"JSON Parse Error: {e}"}