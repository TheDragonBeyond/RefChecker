# gemini_extractor_prompts.py
# Prompts and queries for the LLM
#
# NOTE: With Structured Output enabled, Gemini's response is constrained
# to the JSON schema defined in GeminiExtractor._build_response_schema().
# These prompts therefore focus on *extraction quality and field semantics*
# rather than output formatting.  The allowed "type" values are enforced
# by the schema's enum constraint — the descriptions here are guidance
# for the model to choose the correct one.


class Prompts:
    """Class containing all prompts used by the Citation Extractor"""

    @staticmethod
    def _field_instructions() -> str:
        """
        Shared instructions for how to populate individual fields.
        Referenced by both the PDF and text prompts.
        """
        return """
Field instructions:
- "citation_number": Sequential integer starting from 1.
- "type": Classify each citation using the best fit:
  "Journal Article" for peer-reviewed journals.
  "Conference Paper" for conference proceedings.
  "Book" for full books.
  "Book Chapter" for chapters in edited volumes.
  "Thesis" for dissertations and theses.
  "Report" for technical reports and working papers.
  "Website" for web pages that don't fit another category.
  "Magazine Article" for magazines and periodicals.
  "Newspaper Article" for newspaper articles.
  "Encyclopedia Entry" for encyclopedias and reference works.
  "Other" only as a last resort when none of the above apply.
- "authors": Separate multiple authors with semicolons (e.g. "Smith, J.; Doe, A.").
- "series": Include book series names or conference series abbreviations if present.
- "editor": Include editor names for edited collections or books.
- "month"/"day": Extract if available (common in magazine/newspaper citations).
- "institution": For theses or technical reports.
- "date_accessed": If the citation explicitly mentions when a URL was accessed.
- "isbn"/"issn": Extract only if explicitly listed in the citation.
- For any field not present in a citation, use an empty string.
"""

    @staticmethod
    def get_citation_extraction_prompt() -> str:
        """Returns the main prompt for citation extraction from PDFs."""
        return f"""
Based on the content of the attached file, perform the following tasks:
1. Locate the "References", "Bibliography", or "Works Cited" section of the document.
2. Extract every single citation listed in that section.
3. Return the citations as structured data.

{Prompts._field_instructions()}

Extract all citations completely and accurately.
"""

    @staticmethod
    def get_text_document_prompt() -> str:
        """Specialized prompt for plain text / HTML documents."""
        return f"""
This is a plain text or HTML document. Identify and extract any citations or references it contains.
Look for patterns like:
- Numbered references (e.g., [1], 1., (1))
- Author-year citations (e.g., Smith 2020)
- Full bibliographic entries in any standard format (IEEE, MLA, APA, Chicago, etc.)

{Prompts._field_instructions()}

Extract all citations completely and accurately.
"""

    @staticmethod
    def get_alternative_citation_prompt() -> str:
        """Alternative prompt for different document types or retry scenarios."""
        return f"""
Please extract all bibliographic references from this document as structured data.
Include all available information for each citation.

{Prompts._field_instructions()}

Extract all citations completely and accurately.
"""