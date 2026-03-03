# validators_plugin/google_books.py
#
# FULLY MIGRATED — Follows Crossref/DBLP/PubMed/OpenLibrary/ArXiv architecture.
#
# Architecture:
#   - CitationAccessor for uniform field access
#   - direct_id_verified=True for successful ISBN lookups (instant score of 100)
#   - Three-strategy cascade: ISBN → combined intitle+inauthor → title-only fallback
#   - calculate_title_similarity(substring_boost=True) for book title matching
#   - Returns MatchCandidate for successful matches (scored by ScoringPipeline)
#   - Returns ValidationResult directly for errors, skips, and not-found

import time
import re
import logging
from typing import Dict, Any, List, Optional, Union, Tuple

import requests

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

logger = logging.getLogger(__name__)

_BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"

_FIELDS = (
    "items(id,"
    "volumeInfo(title,authors,publishedDate,industryIdentifiers,"
    "canonicalVolumeLink,infoLink))"
)

_LEADING_ARTICLES = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_YEAR_RE = re.compile(r"(\d{4})")


@register_validator
class GoogleBooksValidator(BaseValidator):
    DEPENDENCIES = []

    def get_default_settings(self) -> Dict[str, Any]:
        return {
            "API_KEY": "",
            "REQUEST_DELAY": 1.0,
            "MAX_RESULTS": 10,
            "MAX_TITLE_WORDS": 8,
        }

    @property
    def name(self) -> str:
        return "Google Books API"

    # FIX (G4): Google Books works without an API key (anonymous quota).
    def is_configured(self) -> bool:
        return True

    def __init__(self):
        super().__init__()
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "CitationValidator/1.0 (Academic citation validation tool)",
            "Accept": "application/json",
        })
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def validate(self, citation_data: Dict[str, str]) -> Union[MatchCandidate, ValidationResult]:
        acc = CitationAccessor(citation_data)

        # ── Strategy 1: Direct ISBN lookup ─────────────────────────────
        isbn = self._extract_isbn(acc)
        if isbn:
            result = self._validate_by_isbn(isbn, acc)
            if result is not None:
                return result

        # ── Need a title to proceed ─────────────────────────────────────
        search_title = acc.best_title(prefer_container=True)
        if not search_title:
            return ValidationResult(
                source_name=self.name,
                status="Not Validated",
                confidence_score=0,
                details="[X] No title or ISBN available for Google Books lookup"
            )

        # ── Strategy 2: Combined intitle: + inauthor: search ───────────
        author_surname = acc.first_author_surname
        if author_surname:
            result = self._search_volumes(search_title, acc, author_surname=author_surname)
            if isinstance(result, MatchCandidate):
                return result
            if isinstance(result, ValidationResult) and result.status == "Validation Error":
                return result

        # ── Strategy 3: Title-only search ──────────────────────────────
        return self._search_volumes(search_title, acc, author_surname=None)

    # ------------------------------------------------------------------
    # Strategy 1: ISBN direct lookup
    # ------------------------------------------------------------------

    def _validate_by_isbn(
        self, isbn: str, acc: CitationAccessor
    ) -> Optional[Union[MatchCandidate, ValidationResult]]:
        data, err = self._api_get({"q": f"isbn:{isbn}", "maxResults": 1})
        if err:
            return ValidationResult(
                source_name=self.name,
                status="Validation Error",
                confidence_score=0,
                details=f"[X] Google Books ISBN lookup failed: {err}"
            )

        items = data.get("items") if data else None
        if not items:
            logger.debug("Google Books: ISBN %s returned no results", isbn)
            return None

        item = items[0]
        vol = item.get("volumeInfo", {})
        found_title = vol.get("title", "")
        api_authors = vol.get("authors") or []
        api_year = self._extract_year(vol.get("publishedDate", ""))
        link = vol.get("canonicalVolumeLink") or vol.get("infoLink") or ""
        volume_id = item.get("id", "")

        mismatch_note = ""
        cite_title = acc.best_title(prefer_container=True)
        if cite_title and found_title:
            sim = calculate_title_similarity(
                cite_title, found_title, substring_boost=True
            )
            # FIX (G1): Was ScoringPipeline.THRESHOLDS["title_low"]
            if sim < ScoringPipeline.TITLE_GATE:
                mismatch_note = (
                    f"\n  ⚠ Title mismatch: expected "
                    f"'{cite_title}', "
                    f"found '{found_title}' (similarity {sim:.2f}). "
                    f"Verify the ISBN is correct."
                )

        evidence = []
        if link:
            evidence.append(link)
        if volume_id:
            evidence.append(f"https://books.google.com/books?id={volume_id}")

        # FIX (G2): Added citation_title, citation_author_count, resolved_title
        return MatchCandidate(
            source_name=self.name,
            direct_id_verified=True,
            citation_title=cite_title,                   # FIX: was missing
            matched_title=found_title,
            resolved_title=found_title,                  # FIX: was missing — required for cross-check
            matched_authors=api_authors,
            matched_year=api_year,
            citation_author_count=acc.author_count,      # FIX: was missing
            evidence_links=evidence,
            raw_metadata=self._vol_to_metadata(item),
            match_details=(
                f"Direct ISBN Match: '{found_title}'"
                f"\n  ISBN: {isbn}"
                + mismatch_note
            ),
        )

    # ------------------------------------------------------------------
    # Strategies 2 & 3: Volume search
    # ------------------------------------------------------------------

    def _search_volumes(
        self,
        search_title: str,
        acc: CitationAccessor,
        author_surname: Optional[str],
    ) -> Union[MatchCandidate, ValidationResult]:
        query = self._build_query(search_title, author_surname)

        params: Dict[str, Any] = {
            "q": query,
            "maxResults": self.config.get("MAX_RESULTS", 10),
            "printType": "books",
            "fields": _FIELDS,
        }
        api_key = self.config.get("API_KEY", "").strip()
        if api_key:
            params["key"] = api_key

        data, err = self._api_get(params)
        if err:
            return ValidationResult(
                source_name=self.name,
                status="Validation Error",
                confidence_score=0,
                details=f"[X] Google Books search failed: {err}"
            )

        items = data.get("items") if data else None
        if not items:
            label = "combined" if author_surname else "title-only"
            logger.debug("Google Books: %s search '%s' returned no results", label, query)
            return ValidationResult(
                source_name=self.name,
                status="Not Validated",
                confidence_score=0,
                details=f"[X] No results found on Google Books for this {'title + author' if author_surname else 'title'}"
            )

        best_candidate: Optional[MatchCandidate] = None
        best_sim: float = -1.0
        cite_title = acc.best_title(prefer_container=True)

        for rank, item in enumerate(items, 1):
            vol = item.get("volumeInfo", {})
            found_title = vol.get("title", "")
            if not found_title:
                continue

            sim = calculate_title_similarity(cite_title, found_title, substring_boost=True)
            # FIX (G1): Was ScoringPipeline.THRESHOLDS["title_low"]
            if sim < ScoringPipeline.TITLE_GATE:
                continue

            api_authors = vol.get("authors") or []
            author_matched = False
            author_count = 0
            if acc.authors and api_authors:
                author_matched, author_count = check_author_overlap(
                    acc.authors, api_authors, author_format="names"
                )

            api_year = self._extract_year(vol.get("publishedDate", ""))
            year_matched = years_match(acc.year, api_year)

            is_better = (
                sim > best_sim
                or (
                    sim == best_sim
                    and author_count > getattr(best_candidate, "author_overlap_count", 0)
                )
            )
            if is_better:
                best_sim = sim
                link = vol.get("canonicalVolumeLink") or vol.get("infoLink") or ""
                volume_id = item.get("id", "")
                evidence = []
                if link:
                    evidence.append(link)
                if volume_id:
                    evidence.append(f"https://books.google.com/books?id={volume_id}")

                # FIX (G3): Added citation_title, citation_author_count
                best_candidate = MatchCandidate(
                    source_name=self.name,
                    citation_title=cite_title,               # FIX: was missing
                    matched_title=found_title,
                    matched_authors=api_authors,
                    matched_year=api_year,
                    author_overlap_matched=author_matched,
                    author_overlap_count=author_count,
                    citation_author_count=acc.author_count,  # FIX: was missing
                    year_matched=year_matched,
                    result_rank=rank,
                    evidence_links=evidence,
                    raw_metadata=self._vol_to_metadata(item),
                    match_details=f"Found match (rank #{rank}): '{found_title}'",
                )

        if best_candidate:
            return best_candidate

        top_title = items[0].get("volumeInfo", {}).get("title", "N/A") if items else "N/A"
        return ValidationResult(
            source_name=self.name,
            status="Not Validated",
            confidence_score=0,
            details=f"[X] No sufficiently similar match on Google Books. Top result: '{top_title}'"
        )

    # ------------------------------------------------------------------
    # HTTP layer
    # ------------------------------------------------------------------

    def _api_get(self, params: Dict) -> Tuple[Optional[Dict], Optional[str]]:
        self._throttle()
        api_key = self.config.get("API_KEY", "").strip()
        if api_key and "key" not in params:
            params = dict(params, key=api_key)

        try:
            resp = self._session.get(_BOOKS_API_URL, params=params, timeout=15)
            self._last_request_time = time.monotonic()

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                logger.warning(
                    "Google Books API: rate limited (429), backing off %ds", retry_after
                )
                time.sleep(retry_after)
                self._throttle()
                resp = self._session.get(_BOOKS_API_URL, params=params, timeout=15)
                self._last_request_time = time.monotonic()

            if resp.status_code == 403:
                return None, "403 Forbidden — API key may be missing, invalid, or quota exceeded"
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}"

            return resp.json(), None

        except requests.exceptions.Timeout:
            return None, "Request timed out"
        except requests.exceptions.ConnectionError as e:
            return None, f"Connection error: {e}"
        except Exception as e:
            return None, f"Unexpected error: {e}"

    def _throttle(self) -> None:
        delay = self.config.get("REQUEST_DELAY", 1.0)
        elapsed = time.monotonic() - self._last_request_time
        remaining = delay - elapsed
        if remaining > 0:
            time.sleep(remaining)

    # ------------------------------------------------------------------
    # Query construction
    # ------------------------------------------------------------------

    def _build_query(self, title: str, author_surname: Optional[str]) -> str:
        clean = normalize_for_api(title)
        clean = _LEADING_ARTICLES.sub("", clean).strip()

        max_words = self.config.get("MAX_TITLE_WORDS", 8)
        words = clean.split()
        if len(words) > max_words:
            clean = " ".join(words[:max_words])

        query = f'intitle:"{clean}"' if " " in clean else f"intitle:{clean}"

        if author_surname:
            clean_author = normalize_for_api(author_surname)
            query += f" inauthor:{clean_author}"

        return query

    # ------------------------------------------------------------------
    # ISBN extraction
    # ------------------------------------------------------------------

    def _extract_isbn(self, acc: CitationAccessor) -> Optional[str]:
        candidates = [
            acc.get("ISBN"),
            acc.get("ISBN-13"),
            acc.get("ISBN-10"),
            acc.get("isbn"),
            acc.get("isbn13"),
            acc.get("isbn10"),
            acc.get("ISBN_13"),
            acc.get("ISBN_10"),
        ]
        for raw in candidates:
            if raw:
                cleaned = re.sub(r"[\s\-]", "", str(raw))
                if re.fullmatch(r"\d{10}|\d{13}", cleaned):
                    return cleaned
        return None

    # ------------------------------------------------------------------
    # Google Books-specific helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_year(published_date: str) -> str:
        if not published_date:
            return ""
        m = _YEAR_RE.search(published_date)
        return m.group(1) if m else ""

    @staticmethod
    def _vol_to_metadata(item: Dict) -> Dict:
        vol = item.get("volumeInfo", {})
        identifiers = {
            idf["type"]: idf["identifier"]
            for idf in (vol.get("industryIdentifiers") or [])
            if "type" in idf and "identifier" in idf
        }
        return {
            "volume_id": item.get("id", ""),
            "title": vol.get("title", ""),
            "subtitle": vol.get("subtitle", ""),
            "authors": vol.get("authors") or [],
            "publishedDate": vol.get("publishedDate", ""),
            "publisher": vol.get("publisher", ""),
            "identifiers": identifiers,
            "canonicalVolumeLink": vol.get("canonicalVolumeLink", ""),
        }