# validators_plugin/llm_gemini.py
#
# Tier 2 (Deep Research) validator using Google Gemini.
# Only invoked when Tier 1 validators fail to meet the confidence threshold.
#
# v2 CHANGES (LLM Pipeline migration):
#   - Returns LLMCandidate instead of ValidationResult
#   - Uses CitationAccessor for uniform field access
#   - LLMScoringPipeline handles proof verification and calibrated scoring
#
# v3 CHANGES (Two-Stage JSON Architecture):
#   - PRIMARY CALL: Tool use enabled (Google Search grounding) + prompt-
#     based JSON instructions.  response_mime_type is NOT set, because
#     Gemini cannot combine structured output mode with tool use.
#     temperature: 0 for deterministic output.
#   - LOCAL PARSER: parse_llm_json() strips markdown fences and extracts
#     JSON from the response text.  Succeeds ~98-99% of the time.
#   - FALLBACK CALL: On the rare occasion parsing fails, a cheap second
#     call (Gemini Flash) with response_mime_type='application/json' and
#     a formal response_schema reformats the raw text into valid JSON.
#     This call has NO tool use, so the schema constraint is safe.
#
#   Net effect: the LLM now actually searches the web during validation
#   instead of relying on training data, while still producing structured
#   JSON output for the LLMScoringPipeline.

import json
import traceback
import urllib.parse
from typing import Dict, Any, List, Optional, Union

from google import genai
from google.genai import types

from validators_plugin.base import BaseValidator, ValidationResult, deep_research_validator
from validators_plugin.manager import register_validator
from llm_scoring import LLMCandidate
from utils import CitationAccessor


# ═══════════════════════════════════════════════════════════════════════
# Local JSON Parser — single source of truth for response cleaning
# ═══════════════════════════════════════════════════════════════════════

def parse_llm_json(text: str) -> Optional[dict]:
    """
    Attempts to parse LLM output as JSON with progressive cleanup.

    Steps:
      1. Strip leading/trailing whitespace.
      2. Remove markdown code fences (```json ... ```) if present.
      3. Try json.loads on the cleaned text.
      4. On failure, extract the substring between the first '{' and
         last '}', and try json.loads on that.
      5. Return the parsed dict on success, None on failure.

    Never raises — returns None on any parse failure so the caller
    can decide whether to trigger the fallback.
    """
    if not text:
        return None

    cleaned = text.strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        # Remove opening fence (```json or ```)
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
        # Remove closing fence
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

    # Attempt 1: Direct parse
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 2: Extract { ... } substring
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            result = json.loads(cleaned[start:end + 1])
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# ═══════════════════════════════════════════════════════════════════════
# Response Schema — single source of truth for both prompt and fallback
# ═══════════════════════════════════════════════════════════════════════

# Formal schema object used by the FALLBACK call's response_schema config.
# The primary call's prompt includes a human-readable version of this same
# schema, avoiding drift between the two paths.

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "recommendation": {
            "type": "STRING",
            "enum": ["Validated", "Ambiguous", "Not Validated"],
        },
        "confidence": {"type": "INTEGER"},
        "reasoning": {"type": "STRING"},
        "verification_note": {"type": "STRING"},
        "title_found": {"type": "STRING"},
        "authors_found": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "year_found": {"type": "STRING"},
        "identifiers_found": {
            "type": "OBJECT",
            "properties": {
                "doi": {"type": "STRING"},
                "pmid": {"type": "STRING"},
                "arxiv_id": {"type": "STRING"},
                "isbn": {"type": "STRING"},
                "urls": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                },
            },
            "required": ["doi", "pmid", "arxiv_id", "isbn", "urls"],
        },
        "evidence": {
            "type": "OBJECT",
            "properties": {
                "doi_url": {"type": "STRING"},
                "google_scholar_link": {"type": "STRING"},
                "publisher_link": {"type": "STRING"},
                "additional_urls": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                },
            },
            "required": [
                "doi_url", "google_scholar_link",
                "publisher_link", "additional_urls",
            ],
        },
    },
    "required": [
        "recommendation", "confidence", "reasoning",
        "verification_note", "title_found", "authors_found",
        "year_found", "identifiers_found", "evidence",
    ],
}

# Fallback model — cheapest available for reformatting only.
FALLBACK_MODEL = "gemini-2.0-flash"


@register_validator
@deep_research_validator
class GeminiResearchValidator(BaseValidator):

    DEPENDENCIES: List[str] = ["google-genai"]

    # ── Configuration ────────────────────────────────────────────────

    def get_default_settings(self) -> Dict[str, Any]:
        return {
            "API_KEY": "YOUR_KEY_HERE",
            "MODEL_NAME": "gemini-2.5-pro",
            "TEMPERATURE": 0.0,
            "TIMEOUT": 30,
        }

    @property
    def name(self) -> str:
        return "Gemini Research Agent"

    def is_configured(self) -> bool:
        api_key = self.config.get("API_KEY")
        return bool(api_key and api_key != "YOUR_KEY_HERE")

    # ── Main Validation Logic ────────────────────────────────────────

    def validate(
        self, citation_data: Dict[str, str]
    ) -> Union[LLMCandidate, ValidationResult]:
        """
        Two-stage validation:

          Stage 1 (Primary): Tool-use-enabled call with Google Search
            grounding.  The model searches the web and returns its
            findings.  Prompt instructions enforce JSON output format.
            response_mime_type is NOT set (incompatible with tool use).

          Stage 1b (Parse): parse_llm_json() extracts JSON from the
            response.  Succeeds ~98-99% of the time.

          Stage 2 (Fallback): If parsing fails, a cheap Gemini Flash
            call with response_mime_type='application/json' and the
            formal response_schema reformats the raw text.  This call
            has no tool use, so the schema constraint works.

        Returns LLMCandidate on success.
        Returns ValidationResult directly on errors.
        """
        # ── 1. Check Config ──────────────────────────────────────────
        api_key = self.config.get("API_KEY")
        model_name = self.config.get("MODEL_NAME")
        temperature = self.config.get("TEMPERATURE", 0.0)

        if not api_key or api_key == "YOUR_KEY_HERE":
            return ValidationResult(
                self.name, "Error", 0,
                "Missing API Key in GeminiResearchValidator config"
            )

        # ── 2. Prepare Citation Data ─────────────────────────────────
        acc = CitationAccessor(citation_data)

        # ── 3. Initialize Client ─────────────────────────────────────
        try:
            client = genai.Client(api_key=api_key)
        except Exception as e:
            return ValidationResult(
                self.name, "Error", 0, f"Client init failed: {e}"
            )

        # ── 4. Build Prompt (includes JSON schema instructions) ──────
        search_context = self._build_search_context(acc)
        prompt = self._create_validation_prompt(acc, search_context)

        # ── 5. PRIMARY CALL: Tool use + prompt-based JSON ────────────
        try:
            # Google Search grounding tool — lets the model search the
            # web during generation.  This is the whole point: the model
            # verifies citations against live web data, not training data.
            tools = [types.Tool(google_search=types.GoogleSearch())]

            gen_config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=4096,
                # NOTE: response_mime_type is intentionally NOT set.
                # Setting it would disable tool use (Gemini API limitation).
                # The prompt instructs the model to return JSON instead.
                tools=tools,
            )

            print(f"[{self.name}] Querying {model_name} (with Google Search)...")

            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=gen_config,
            )

            # Usage logging
            if response.usage_metadata:
                usage = response.usage_metadata
                print(
                    f"[{self.name}] Token Usage — "
                    f"Input: {usage.prompt_token_count} | "
                    f"Output: {usage.candidates_token_count}"
                )

            # Extract the text from the response.  With tool use, the
            # response may contain multiple parts (search results + text).
            # response.text concatenates all text parts.
            response_text = response.text if response.text else ""

            if not response_text and response.candidates:
                # Fallback: try to recover text from candidate parts
                candidate = response.candidates[0]
                if candidate.content and candidate.content.parts:
                    parts_text = [
                        part.text for part in candidate.content.parts
                        if hasattr(part, 'text') and part.text
                    ]
                    response_text = "".join(parts_text)

        except Exception as e:
            return ValidationResult(
                self.name, "Error", 0,
                f"Primary call failed: {str(e)}"
            )

        if not response_text:
            return ValidationResult(
                self.name, "Error", 0,
                "Empty response from Gemini (primary call)"
            )

        # ── 6. LOCAL PARSE: Try to extract JSON from the response ────
        parsed = parse_llm_json(response_text)

        if parsed is not None:
            print(f"[{self.name}] Primary JSON parse succeeded.")
        else:
            # ── 7. FALLBACK: Cheap formatting call with enforced JSON ─
            print(
                f"[{self.name}] WARNING: Primary JSON parse failed. "
                f"Triggering fallback formatting call."
            )
            # Log the raw text for prompt tuning
            print(
                f"[{self.name}] Raw response (first 500 chars): "
                f"{response_text[:500]}"
            )

            parsed = self._fallback_format(client, api_key, response_text)

            if parsed is None:
                return ValidationResult(
                    self.name, "Error", 0,
                    f"Both primary parse and fallback formatting failed. "
                    f"Raw response (first 300 chars): {response_text[:300]}"
                )

            print(f"[{self.name}] Fallback formatting succeeded.")

        # ── 8. Build LLMCandidate from parsed JSON ───────────────────
        return self._build_candidate(parsed, acc)

    # ── Fallback Formatting Call ─────────────────────────────────────

    def _fallback_format(
        self, client, api_key: str, raw_text: str
    ) -> Optional[dict]:
        """
        Cheap second call to reformat raw LLM text into valid JSON.

        Uses Gemini Flash (cheapest model) with:
          - response_mime_type='application/json' (guaranteed valid JSON)
          - response_schema=RESPONSE_SCHEMA (structural constraint)
          - NO tool use (so the schema constraint works)
          - temperature=0

        Only sends the raw text + a short extraction instruction.
        Does NOT resend the original citation or search context.
        """
        try:
            fallback_config = types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
            )

            fallback_prompt = (
                "Extract and format the following text into the required "
                "JSON schema. Preserve all information — identifiers, "
                "URLs, reasoning, authors, dates — exactly as stated in "
                "the text. Do not invent or fabricate any information "
                "that is not present in the text.\n\n"
                f"{raw_text}"
            )

            print(f"[{self.name}] Fallback call to {FALLBACK_MODEL}...")

            fallback_response = client.models.generate_content(
                model=FALLBACK_MODEL,
                contents=fallback_prompt,
                config=fallback_config,
            )

            if fallback_response.usage_metadata:
                usage = fallback_response.usage_metadata
                print(
                    f"[{self.name}] Fallback token usage — "
                    f"Input: {usage.prompt_token_count} | "
                    f"Output: {usage.candidates_token_count}"
                )

            fallback_text = fallback_response.text
            if not fallback_text:
                return None

            return json.loads(fallback_text)

        except Exception as e:
            print(f"[{self.name}] Fallback formatting failed: {e}")
            return None

    # ── LLMCandidate Construction ────────────────────────────────────

    def _build_candidate(
        self, parsed: dict, acc: CitationAccessor
    ) -> LLMCandidate:
        """
        Constructs an LLMCandidate from the parsed JSON response.
        Same logic as v2, extracted into its own method for reuse
        by both the primary parse path and the fallback path.
        """
        identifiers = parsed.get("identifiers_found", {})
        evidence = parsed.get("evidence", {})

        # Collect evidence links
        evidence_links = []
        if evidence.get("doi_url"):
            evidence_links.append(evidence["doi_url"])
        if evidence.get("google_scholar_link"):
            evidence_links.append(evidence["google_scholar_link"])
        if evidence.get("publisher_link"):
            evidence_links.append(evidence["publisher_link"])
        if evidence.get("additional_urls"):
            evidence_links.extend(evidence["additional_urls"])

        candidate = LLMCandidate(
            source_name=self.name,

            # Citation data (from CitationAccessor)
            citation_title=acc.title or acc.container_title,
            citation_container_title=acc.container_title,
            citation_authors=acc.authors,
            citation_year=acc.year,
            citation_author_count=acc.author_count,

            # LLM's assessment
            recommendation=parsed.get("recommendation", "Not Validated"),
            llm_confidence=int(parsed.get("confidence", 0)),
            reasoning=parsed.get("reasoning", ""),
            verification_note=parsed.get("verification_note", ""),

            # What the LLM found
            title_found=parsed.get("title_found", ""),
            authors_found=parsed.get("authors_found", []),
            year_found=parsed.get("year_found", ""),

            # Identifiers (from explicit schema fields — no regex)
            doi_found=identifiers.get("doi", ""),
            pmid_found=identifiers.get("pmid", ""),
            arxiv_id_found=identifiers.get("arxiv_id", ""),
            isbn_found=identifiers.get("isbn", ""),
            urls_found=identifiers.get("urls", []),

            # Evidence
            evidence_links=evidence_links,

            # Raw response for debugging
            raw_response=parsed,
        )

        print(
            f"[{self.name}] LLM recommendation: {candidate.recommendation} "
            f"(confidence: {candidate.llm_confidence})"
        )
        if candidate.doi_found:
            print(f"[{self.name}] DOI found: {candidate.doi_found}")
        if candidate.pmid_found:
            print(f"[{self.name}] PMID found: {candidate.pmid_found}")
        if candidate.arxiv_id_found:
            print(f"[{self.name}] ArXiv ID found: {candidate.arxiv_id_found}")
        if candidate.isbn_found:
            print(f"[{self.name}] ISBN found: {candidate.isbn_found}")

        return candidate

    # ── Context Building ─────────────────────────────────────────────

    @staticmethod
    def _build_search_context(acc: CitationAccessor) -> List[Dict]:
        """
        Build automated context hints for the LLM prompt.
        These give the LLM a head start (DOI to check, Scholar link).
        """
        results = []

        if acc.doi:
            results.append({
                'type': 'doi_check',
                'info': (
                    f"DOI provided in citation: {acc.doi}. "
                    f"Verify if this resolves to the title '{acc.title}'."
                ),
            })

        title = acc.title or acc.container_title
        if title:
            q = f'"{title}"'
            if acc.first_author_surname:
                q += f" {acc.first_author_surname}"
            encoded = urllib.parse.quote(q)
            url = f"https://scholar.google.com/scholar?q={encoded}"
            results.append({
                'type': 'google_scholar_link',
                'url': url,
                'info': "Use this link to verify existence.",
            })

        return results

    # ── Prompt Construction ──────────────────────────────────────────

    @staticmethod
    def _create_validation_prompt(
        acc: CitationAccessor, search_context: List[Dict]
    ) -> str:
        """
        Build the validation prompt.

        v3 changes: The prompt now includes an explicit JSON schema
        description and example, since we can't use response_mime_type
        with tool use.  The model is strongly instructed to return ONLY
        a JSON object with no surrounding text or markdown fences.
        """
        # Build citation summary from CitationAccessor
        cite_parts = []
        if acc.title:
            cite_parts.append(f"Article Title: {acc.title}")
        if acc.container_title:
            cite_parts.append(f"Publication/Journal: {acc.container_title}")
        if acc.authors:
            cite_parts.append(f"Authors: {acc.authors}")
        if acc.year:
            cite_parts.append(f"Year: {acc.year}")
        if acc.doi:
            cite_parts.append(f"DOI: {acc.doi}")
        if acc.isbn:
            cite_parts.append(f"ISBN: {acc.isbn}")
        if acc.url:
            cite_parts.append(f"URL: {acc.url}")
        if acc.publisher:
            cite_parts.append(f"Publisher: {acc.publisher}")
        if acc.volume:
            cite_parts.append(f"Volume: {acc.volume}")
        if acc.issue:
            cite_parts.append(f"Issue: {acc.issue}")
        if acc.pages:
            cite_parts.append(f"Pages: {acc.pages}")

        citation_str = "\n".join(cite_parts)

        context_str = "\n".join([
            f"- {item['info']} (URL: {item.get('url', 'N/A')})"
            for item in search_context
        ])

        return f"""You are an expert academic citation validator with access to Google Search.

CITATION TO CHECK:
{citation_str}

AUTOMATED CONTEXT:
{context_str}

TASK:
Use Google Search to verify if this citation refers to a real, findable work. You MUST search for the work — do not rely on your training data alone.

INSTRUCTIONS:
1. SEARCH for the exact work described in the citation using Google Search.
2. Report the exact title, authors, and year of the work you find (even if slightly different from the citation).
3. Report any identifiers you find. This is critical for verification:
   - DOI: Report the bare identifier (e.g., "10.1000/xyz"), not a URL.
   - PMID: Report just the numeric PubMed ID.
   - ArXiv ID: Report just the ID (e.g., "2410.11782"), not a URL.
   - ISBN: Report the ISBN-10 or ISBN-13.
   - URLs: Any relevant URLs (publisher page, repository, etc.).
4. If you cannot find a specific identifier, use an empty string "" for that field.
5. Provide evidence links a human reviewer could use to verify manually.

CLASSIFICATION:
- "Validated": Formal academic publication (journal article, conference paper, book) found and confirmed via search.
- "Ambiguous": Non-standard source (blog post, GitHub repository, product page, technical documentation) exists and matches the description.
- "Not Validated": Cannot find the work via search, or the link is dead, or the citation appears fabricated.

RESPONSE FORMAT:
After completing your search, return your FINAL answer as a single valid JSON object.
No markdown fences, no explanation, no text outside the JSON.

Schema:
{{
  "recommendation": "Validated" | "Ambiguous" | "Not Validated",
  "confidence": integer 0-100,
  "reasoning": "string explaining your assessment",
  "verification_note": "string with manual verification instructions",
  "title_found": "exact title you found",
  "authors_found": ["list", "of", "author", "names"],
  "year_found": "publication year you found",
  "identifiers_found": {{
    "doi": "bare DOI or empty string",
    "pmid": "PubMed ID or empty string",
    "arxiv_id": "ArXiv ID or empty string",
    "isbn": "ISBN or empty string",
    "urls": ["relevant URLs"]
  }},
  "evidence": {{
    "doi_url": "https://doi.org/... or empty string",
    "google_scholar_link": "scholar URL or empty string",
    "publisher_link": "publisher URL or empty string",
    "additional_urls": ["any other evidence URLs"]
  }}
}}

Example of a correct response:
{{"recommendation": "Validated", "confidence": 95, "reasoning": "Found the exact paper on IEEE Xplore via Google Search. Title, authors, and year all match.", "verification_note": "Check the DOI link to confirm.", "title_found": "Attention Is All You Need", "authors_found": ["Ashish Vaswani", "Noam Shazeer"], "year_found": "2017", "identifiers_found": {{"doi": "10.5555/3295222.3295349", "pmid": "", "arxiv_id": "1706.03762", "isbn": "", "urls": ["https://arxiv.org/abs/1706.03762"]}}, "evidence": {{"doi_url": "https://doi.org/10.5555/3295222.3295349", "google_scholar_link": "https://scholar.google.com/scholar?q=Attention+Is+All+You+Need", "publisher_link": "", "additional_urls": []}}}}

CRITICAL: Your response must be ONLY the JSON object. No other text before or after it."""