# validators_plugin/crossref.py
#
# FULLY MIGRATED to v3 scoring pipeline.
#
# Architecture:
#   - CitationAccessor for uniform field access
#   - years_match() for year comparison
#   - Returns MatchCandidate for successful matches (scored by ScoringPipeline)
#   - Returns ValidationResult directly for errors, skips, and not-found

import re
from typing import Dict, List, Any, Optional, Union

from habanero import Crossref
from rapidfuzz import fuzz
from requests.exceptions import HTTPError

from validators_plugin.base import BaseValidator, ValidationResult
from validators_plugin.manager import register_validator
from scoring import MatchCandidate

from utils import (
    CitationAccessor,
    normalize_for_api,
    check_author_overlap,
    years_match,
)

# Local pre-filter threshold for bibliographic search.  Candidates below
# this token_sort_ratio are skipped before being sent to the pipeline.
# This is a performance optimisation — the pipeline's own title gate
# (0.30) is the authoritative threshold; this just avoids building
# MatchCandidates for hopeless results.
_QUICK_FILTER_THRESHOLD = 30  # fuzz returns 0–100


@register_validator
class CrossrefValidator(BaseValidator):

    def get_default_settings(self) -> Dict[str, Any]:
        return {
            "MAX_RESULTS": 10,
            "YEAR_MATCH_TOLERANCE": 1,
        }

    def __init__(self, mailto: str = "citation_validator@example.com"):
        super().__init__()
        self.cr = Crossref(mailto=mailto)

    @property
    def name(self) -> str:
        return "Crossref API"

    # FIX (C1): Crossref requires no API key — always configured.
    def is_configured(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def validate(self, citation_data: Dict[str, str]) -> Union[MatchCandidate, ValidationResult]:
        acc = CitationAccessor(citation_data)

        doi = self._resolve_doi(acc)

        if doi:
            try:
                result = self.cr.works(ids=doi)
                if result and 'message' in result:
                    return self._build_doi_candidate(result['message'], doi, acc)
            except HTTPError:
                pass
            except Exception as e:
                print(f"DOI Lookup failed: {e}")

        if not acc.title:
            return ValidationResult(
                source_name=self.name,
                status="Not Validated",
                confidence_score=0,
                details="[X] No title or DOI provided for Crossref search"
            )

        try:
            return self._perform_bibliographic_search(acc)
        except Exception as e:
            return ValidationResult(
                source_name=self.name,
                status="Validation Error",
                confidence_score=0,
                details=f"[X] Crossref API Error: {str(e)}"
            )

    # ------------------------------------------------------------------
    # DOI resolution
    # ------------------------------------------------------------------

    def _resolve_doi(self, acc: CitationAccessor) -> Optional[str]:
        if acc.doi:
            return acc.doi

        for field_name in ('DOI', 'doi', 'URL', 'url'):
            raw_val = acc.raw.get(field_name, '').strip()
            if raw_val:
                extracted = self._extract_doi_from_text(raw_val)
                if extracted:
                    return extracted

        return None

    # ------------------------------------------------------------------
    # DOI match → MatchCandidate
    # ------------------------------------------------------------------

    def _build_doi_candidate(
        self, item: Dict, doi_input: str, acc: CitationAccessor
    ) -> MatchCandidate:
        title = item.get('title', [''])[0]

        evidence = []
        if item.get('URL'):
            evidence.append(item['URL'])
        evidence.append(f"https://doi.org/{doi_input}")

        return MatchCandidate(
            source_name=self.name,
            doi_verified=True,
            citation_title=acc.title,
            matched_title=title,
            resolved_title=title,
            citation_author_count=acc.author_count,
            evidence_links=evidence,
            raw_metadata=item,
            match_details=f"DOI Exact Match: '{title}'\n  DOI: {doi_input}",
        )

    # ------------------------------------------------------------------
    # Bibliographic search → best MatchCandidate or ValidationResult
    # ------------------------------------------------------------------

    def _perform_bibliographic_search(
        self, acc: CitationAccessor
    ) -> Union[MatchCandidate, ValidationResult]:
        norm_title = normalize_for_api(acc.title)
        query_parts = [norm_title]

        if acc.authors:
            first_author = acc.first_author_surname
            if first_author:
                query_parts.append(normalize_for_api(first_author))

        query = ' '.join(query_parts)

        results = self.cr.works(
            query_bibliographic=query,
            limit=self.config["MAX_RESULTS"]
        )

        if not results or 'message' not in results or not results['message']['items']:
            return ValidationResult(
                self.name, "Not Validated", 0,
                "[X] No matching items found"
            )

        items = results['message']['items']

        best_candidate: Optional[MatchCandidate] = None
        best_quick_sim = -1.0

        for rank, item in enumerate(items, 1):
            item_title = item.get('title', [''])[0]

            quick_sim = fuzz.token_sort_ratio(acc.title, item_title)
            if quick_sim < _QUICK_FILTER_THRESHOLD:
                continue

            item_authors = item.get('author', [])
            author_matched = False
            author_count = 0
            if acc.authors and item_authors:
                author_matched, author_count = check_author_overlap(
                    acc.authors, item_authors
                )

            api_year = self._extract_crossref_year(item)
            year_matched = years_match(
                acc.year, api_year,
                tolerance=self.config["YEAR_MATCH_TOLERANCE"]
            )

            is_better = (
                quick_sim > best_quick_sim
                or (
                    quick_sim == best_quick_sim
                    and author_count > getattr(best_candidate, 'author_overlap_count', 0)
                )
            )

            if is_better:
                best_quick_sim = quick_sim

                evidence = []
                if item.get('URL'):
                    evidence.append(item['URL'])
                if item.get('DOI'):
                    evidence.append(f"https://doi.org/{item.get('DOI')}")

                matched_author_names = self._format_crossref_authors(item_authors)

                best_candidate = MatchCandidate(
                    source_name=self.name,
                    citation_title=acc.title,
                    matched_title=item_title,
                    matched_authors=matched_author_names,
                    matched_year=api_year or '',
                    author_overlap_matched=author_matched,
                    author_overlap_count=author_count,
                    citation_author_count=acc.author_count,
                    year_matched=year_matched,
                    result_rank=rank,
                    evidence_links=evidence,
                    raw_metadata=item,
                    match_details=f"Found match (rank #{rank}): '{item_title}'",
                )

        if best_candidate:
            return best_candidate

        top_title = items[0].get('title', ['N/A'])[0]
        return ValidationResult(
            source_name=self.name,
            status="Not Validated",
            confidence_score=0,
            details=f"[X] No sufficiently similar match found. Top result: {top_title}"
        )

    # ------------------------------------------------------------------
    # Crossref-specific helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_crossref_year(item: Dict) -> Optional[str]:
        for date_field in ('published', 'published-print', 'published-online'):
            if date_field in item:
                date_parts = item[date_field].get('date-parts', [[]])[0]
                if date_parts:
                    return str(date_parts[0])
        return None

    @staticmethod
    def _format_crossref_authors(api_authors: List[Dict]) -> List[str]:
        names = []
        for author in api_authors:
            given = author.get('given', '')
            family = author.get('family', '')
            full = f"{given} {family}".strip()
            if full:
                names.append(full)
        return names

    @staticmethod
    def _extract_doi_from_text(text: str) -> Optional[str]:
        if not text:
            return None
        patterns = [
            r'10\.\d{4,}/[^\s]+',
            r'doi\.org/(10\.\d{4,}/[^\s]+)',
            r'dx\.doi\.org/(10\.\d{4,}/[^\s]+)',
        ]
        for p in patterns:
            match = re.search(p, text, re.IGNORECASE)
            if match:
                return match.group(1) if match.groups() else match.group(0)
        return None

    @staticmethod
    def _is_url(text: str) -> bool:
        if not text:
            return False
        t = text.strip().lower()
        patterns = [r'^https?://', r'^www\.', r'^ftp://']
        return any(re.match(p, t) for p in patterns)