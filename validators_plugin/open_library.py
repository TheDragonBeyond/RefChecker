# validators_plugin/open_library.py
#
# FULLY MIGRATED — Uses the same architecture as Crossref and DBLP.
#
# Architecture:
#   - CitationAccessor for uniform field access
#   - best_title(prefer_container=...) for book-aware title selection
#   - years_match() for first_publish_year comparison
#   - substring_boost=True for title similarity (books have subtitles)
#   - Returns MatchCandidate for successful matches (scored by ScoringPipeline)
#   - Returns ValidationResult directly for errors, skips, and not-found

import time
import requests
from typing import Dict, List, Any, Optional, Union

from validators_plugin.base import BaseValidator, ValidationResult
from validators_plugin.manager import register_validator
from scoring import MatchCandidate, ScoringPipeline

from utils import (
    CitationAccessor,
    normalize_for_api,
    calculate_title_similarity,
    check_author_overlap,
    years_match,
)


@register_validator
class OpenLibraryValidator(BaseValidator):

    def get_default_settings(self) -> Dict[str, Any]:
        return {
            "MAX_RESULTS": 5,
            "TIMEOUT": 15,
            "USER_AGENT": "CitationValidator/1.0 (citation_validator@example.com)",
            "REQUEST_DELAY": 1.0,
            "MAX_RETRIES": 2,
            "RETRY_BACKOFF": 5.0,
            "YEAR_MATCH_TOLERANCE": 1,
        }

    @property
    def name(self) -> str:
        return "Open Library API"

    # FIX (O3): Open Library requires no API key — always configured.
    def is_configured(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def validate(self, citation_data: Dict[str, str]) -> Union[MatchCandidate, ValidationResult]:
        acc = CitationAccessor(citation_data)

        search_title = acc.best_title(
            prefer_container='book' in acc.citation_type
        )

        if not search_title:
            return ValidationResult(
                source_name=self.name,
                status="Not Validated",
                confidence_score=0,
                details="[X] No searchable title provided"
            )

        try:
            results = self._search_api(search_title, acc.first_author_surname)
        except Exception as e:
            return ValidationResult(
                source_name=self.name,
                status="Validation Error",
                confidence_score=0,
                details=f"[X] Open Library API Error: {str(e)}"
            )

        if not results:
            return ValidationResult(
                source_name=self.name,
                status="Not Validated",
                confidence_score=0,
                details="[X] No results found in Open Library"
            )

        return self._find_best_candidate(results, search_title, acc)

    # ------------------------------------------------------------------
    # API search with rate limiting and User-Agent
    # ------------------------------------------------------------------

    def _search_api(self, title: str, first_author: str = '') -> List[Dict]:
        params = {
            'title': normalize_for_api(title),
            'fields': 'title,author_name,first_publish_year,publish_year,key,publisher,id_isbn,id_doi',
            'limit': self.config["MAX_RESULTS"]
        }
        if first_author:
            params['author'] = normalize_for_api(first_author)

        url = "https://openlibrary.org/search.json"

        headers = {
            'User-Agent': self.config["USER_AGENT"]
        }

        time.sleep(self.config["REQUEST_DELAY"])

        response = self._request_with_retry(url, params, headers)
        response.raise_for_status()
        data = response.json()

        return data.get('docs', [])

    def _request_with_retry(
        self, url: str, params: Dict, headers: Dict
    ) -> requests.Response:
        max_retries = self.config["MAX_RETRIES"]
        timeout = self.config["TIMEOUT"]

        for attempt in range(max_retries + 1):
            response = requests.get(
                url, params=params, headers=headers, timeout=timeout
            )

            if response.status_code != 429:
                return response

            if attempt < max_retries:
                retry_after = response.headers.get('Retry-After')
                if retry_after:
                    try:
                        wait_seconds = float(retry_after)
                    except (ValueError, TypeError):
                        wait_seconds = self.config["RETRY_BACKOFF"]
                else:
                    wait_seconds = self.config["RETRY_BACKOFF"]

                print(
                    f"[Open Library] Rate limited (429). "
                    f"Waiting {wait_seconds}s before retry "
                    f"{attempt + 1}/{max_retries}."
                )
                time.sleep(wait_seconds)

        return response

    # ------------------------------------------------------------------
    # Best candidate selection → MatchCandidate or ValidationResult
    # ------------------------------------------------------------------

    def _find_best_candidate(
        self,
        results: List[Dict],
        search_title: str,
        acc: CitationAccessor,
    ) -> Union[MatchCandidate, ValidationResult]:
        best_candidate: Optional[MatchCandidate] = None
        best_sim = -1.0

        for rank, item in enumerate(results, 1):
            item_title = item.get('title', '')

            sim = calculate_title_similarity(
                search_title, item_title, substring_boost=True
            )

            # FIX (O1): Was ScoringPipeline.THRESHOLDS['title_low']
            if sim < ScoringPipeline.TITLE_GATE:
                continue

            item_authors = item.get('author_name', [])
            author_matched = False
            author_count = 0
            if acc.authors and item_authors:
                author_matched, author_count = check_author_overlap(
                    acc.authors, item_authors, author_format="names"
                )

            year_matched = self._extract_ol_year_matched(acc.year, item)

            is_better = (
                sim > best_sim
                or (
                    sim == best_sim
                    and author_count > getattr(best_candidate, 'author_overlap_count', 0)
                )
            )

            if is_better:
                best_sim = sim

                first_year = item.get('first_publish_year')
                matched_year = str(first_year) if first_year else ''

                ol_key = item.get('key', '')
                evidence = [f"https://openlibrary.org{ol_key}"] if ol_key else []

                # FIX (O2): Added citation_title, citation_author_count
                best_candidate = MatchCandidate(
                    source_name=self.name,
                    citation_title=search_title,                # FIX: was missing
                    matched_title=item_title,
                    matched_authors=item_authors,
                    matched_year=matched_year,
                    author_overlap_matched=author_matched,
                    author_overlap_count=author_count,
                    citation_author_count=acc.author_count,  # FIX: was missing
                    year_matched=year_matched,
                    result_rank=rank,
                    evidence_links=evidence,
                    raw_metadata=item,
                    match_details=(
                        f"Found match: '{item_title}'"
                        f" (First published: {matched_year or 'N/A'})"
                    ),
                )

        if best_candidate:
            return best_candidate

        return ValidationResult(
            source_name=self.name,
            status="Not Validated",
            confidence_score=0,
            details="[X] No sufficient match found in Open Library"
        )

    # ------------------------------------------------------------------
    # Open Library-specific helpers
    # ------------------------------------------------------------------

    def _extract_ol_year_matched(self, citation_year: str, item: Dict) -> bool:
        if not citation_year:
            return True

        first_year = item.get('first_publish_year')
        if first_year is not None:
            if years_match(
                citation_year, first_year,
                tolerance=self.config["YEAR_MATCH_TOLERANCE"]
            ):
                return True

        all_years = item.get('publish_year', [])
        if all_years:
            try:
                cite_int = int(citation_year)
                if cite_int in all_years:
                    return True
            except (ValueError, TypeError):
                pass

        if first_year is None and not all_years:
            return True

        return False