# validators_plugin/dblp.py
#
# FULLY MIGRATED — Second reference implementation (after Crossref).
#
# Architecture:
#   - CitationAccessor for uniform field access (replaces _extract_metadata)
#   - years_match() for year comparison (replaces check_year_match)
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
class DBLPValidator(BaseValidator):

    def get_default_settings(self) -> Dict[str, Any]:
        return {
            "MAX_RESULTS_FETCH": 10,
            "MAX_RESULTS_TO_CHECK": 5,
            "TIMEOUT": 8,
            "REQUEST_DELAY": 1.0,
            "MAX_RETRIES": 2,
            "RETRY_BACKOFF": 5.0,
            "YEAR_MATCH_TOLERANCE": 1,
        }

    @property
    def name(self) -> str:
        return "DBLP API"

    # FIX (D3): DBLP requires no API key — always configured.
    def is_configured(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def validate(self, citation_data: Dict[str, str]) -> Union[MatchCandidate, ValidationResult]:
        acc = CitationAccessor(citation_data)

        if not acc.title:
            return ValidationResult(
                source_name=self.name,
                status="Not Validated",
                confidence_score=0,
                details="[X] No title provided for DBLP search"
            )

        try:
            results = self._search_api(acc.title, acc.first_author_surname)
        except Exception as e:
            return ValidationResult(
                source_name=self.name,
                status="Validation Error",
                confidence_score=0,
                details=f"[X] DBLP API Error: {str(e)}"
            )

        if not results:
            return ValidationResult(
                source_name=self.name,
                status="Not Validated",
                confidence_score=0,
                details="[X] No results found in DBLP"
            )

        return self._find_best_candidate(results, acc)

    # ------------------------------------------------------------------
    # API search with rate limiting
    # ------------------------------------------------------------------

    def _search_api(self, title: str, first_author: str = '') -> List[Dict]:
        norm_title = normalize_for_api(title)
        query_parts = [norm_title]
        if first_author:
            query_parts.append(normalize_for_api(first_author))

        url = "https://dblp.org/search/publ/api"
        params = {
            'q': ' '.join(query_parts),
            'format': 'json',
            'h': self.config["MAX_RESULTS_FETCH"]
        }

        time.sleep(self.config["REQUEST_DELAY"])

        response = self._request_with_retry(url, params)
        response.raise_for_status()
        data = response.json()

        return self._parse_search_results(data)

    def _request_with_retry(self, url: str, params: Dict) -> requests.Response:
        max_retries = self.config["MAX_RETRIES"]
        timeout = self.config["TIMEOUT"]

        for attempt in range(max_retries + 1):
            response = requests.get(url, params=params, timeout=timeout)

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

                print(f"[DBLP] Rate limited (429). Waiting {wait_seconds}s before retry {attempt + 1}/{max_retries}.")
                time.sleep(wait_seconds)

        return response

    # ------------------------------------------------------------------
    # Response parsing (DBLP-specific)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_search_results(data: Dict) -> List[Dict]:
        results = []
        if 'result' not in data or 'hits' not in data['result']:
            return results

        hits = data['result']['hits'].get('hit', [])
        for hit in hits:
            info = hit.get('info', {})

            authors_data = info.get('authors', {}).get('author', [])
            if isinstance(authors_data, dict):
                authors_data = [authors_data]
            authors = [
                a.get('text', '') if isinstance(a, dict) else str(a)
                for a in authors_data
            ]

            results.append({
                'authors': authors,
                'title': info.get('title', ''),
                'venue': info.get('venue', ''),
                'year': info.get('year', ''),
                'doi': info.get('doi', ''),
                'url': info.get('url', ''),
                'key': info.get('key', '')
            })

        return results

    # ------------------------------------------------------------------
    # Best candidate selection → MatchCandidate or ValidationResult
    # ------------------------------------------------------------------

    def _find_best_candidate(
        self, results: List[Dict], acc: CitationAccessor
    ) -> Union[MatchCandidate, ValidationResult]:
        best_candidate: Optional[MatchCandidate] = None
        best_sim = -1.0

        limit = self.config["MAX_RESULTS_TO_CHECK"]
        for rank, item in enumerate(results[:limit], 1):
            item_title = item.get('title', '').strip().rstrip('.')

            sim = calculate_title_similarity(acc.title, item_title)

            # FIX (D1): Was ScoringPipeline.THRESHOLDS['title_low']
            if sim < ScoringPipeline.TITLE_GATE:
                continue

            item_authors = item.get('authors', [])
            author_matched = False
            author_count = 0
            if acc.authors and item_authors:
                author_matched, author_count = check_author_overlap(
                    acc.authors, item_authors, author_format="names"
                )

            api_year = item.get('year', '')
            year_matched = years_match(
                acc.year, api_year,
                tolerance=self.config["YEAR_MATCH_TOLERANCE"]
            )

            is_better = (
                sim > best_sim
                or (
                    sim == best_sim
                    and author_count > getattr(best_candidate, 'author_overlap_count', 0)
                )
            )

            if is_better:
                best_sim = sim

                evidence = []
                if item.get('url'):
                    evidence.append(item['url'])
                if item.get('doi'):
                    evidence.append(f"https://doi.org/{item['doi']}")

                # FIX (D2): Added citation_title, citation_author_count
                best_candidate = MatchCandidate(
                    source_name=self.name,
                    citation_title=acc.title,                # FIX: was missing
                    matched_title=item_title,
                    matched_authors=item_authors,
                    matched_year=api_year,
                    author_overlap_matched=author_matched,
                    author_overlap_count=author_count,
                    citation_author_count=acc.author_count,  # FIX: was missing
                    year_matched=year_matched,
                    result_rank=rank,
                    evidence_links=evidence,
                    raw_metadata=item,
                    match_details=(
                        f"Found match (rank #{rank}): '{item_title}'"
                        f"\n  Venue: {item.get('venue', 'N/A')}"
                        f"\n  Year: {api_year or 'N/A'}"
                    ),
                )

        if best_candidate:
            return best_candidate

        top_title = results[0].get('title', 'N/A')
        return ValidationResult(
            source_name=self.name,
            status="Not Validated",
            confidence_score=0,
            details=f"[X] No sufficiently similar match found. Top result: '{top_title}'",
            metadata={'top_result_raw': results[0]}
        )