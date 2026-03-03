# validators_plugin/arxiv_validator.py
#
# FULLY MIGRATED — Follows Crossref/DBLP/PubMed/OpenLibrary architecture.
#
# Architecture:
#   - CitationAccessor for uniform field access (replaces manual key guessing + _get_field)
#   - direct_id_verified=True for successful ArXiv ID lookups (instant score of 100)
#   - calculate_title_similarity + check_author_overlap + years_match for title searches
#   - Returns MatchCandidate for successful matches (scored by ScoringPipeline)
#   - Returns ValidationResult directly for errors, skips, and not-found
#
# Rate limiting (arXiv Terms of Use):
#   "Make no more than one request every three seconds, and limit requests
#    to a single connection at a time." — https://info.arxiv.org/help/api/tou.html
#
#   How we comply:
#   - DELAY_SECONDS defaults to 3.0. A runtime warning fires if a user sets
#     it below 3.0 — the library will enforce it silently, but the user should
#     know they'd be violating the ToU.
#   - The arxiv library's Client tracks the last request time and enforces
#     delay_seconds before every client.results() call — including between
#     two separate calls (e.g. an ID lookup followed by a title search
#     fallback). Reusing the same client instance is what gives it the state
#     to do this correctly. No explicit time.sleep() is needed between calls.
#   - Client is instantiated once in __init__ and reused across all validate()
#     calls. Reusing the client shares the connection pool, satisfying the
#     "single connection at a time" requirement.
#   - PAGE_SIZE is derived from MAX_RESULTS (not a separate config key). Since
#     page_size >= max_results means exactly one HTTP request per search, no
#     unnecessary data is transferred and arXiv's servers are not over-loaded.

from typing import Dict, Any, List, Optional, Union
import arxiv


from validators_plugin.base import BaseValidator, ValidationResult
from validators_plugin.manager import register_validator
from scoring import MatchCandidate, ScoringPipeline

from utils import (
    CitationAccessor,
    calculate_title_similarity,
    check_author_overlap,
    years_match,
)

# ArXiv's documented minimum interval between requests.
# Referenced at: https://info.arxiv.org/help/api/tou.html
_ARXIV_MIN_DELAY = 3.0


@register_validator
class ArxivValidator(BaseValidator):
    DEPENDENCIES = ["arxiv"]

    def get_default_settings(self) -> Dict[str, Any]:
        return {
            "DELAY_SECONDS": 3.0,
            "NUM_RETRIES": 1,
            "MAX_RESULTS": 5,
        }

    @property
    def name(self) -> str:
        return "ArXiv Validator"

    # FIX (A4): ArXiv has no API key — always configured.
    def is_configured(self) -> bool:
        return True

    def __init__(self):
        super().__init__()

        delay = self.config.get("DELAY_SECONDS", _ARXIV_MIN_DELAY)
        if delay < _ARXIV_MIN_DELAY:
            print(
                f"[{self.name}] WARNING: DELAY_SECONDS={delay} is below the arXiv "
                f"ToU minimum of {_ARXIV_MIN_DELAY}s. Overriding to {_ARXIV_MIN_DELAY}s."
            )
            delay = _ARXIV_MIN_DELAY
            self.config["DELAY_SECONDS"] = delay

        if arxiv:
            self.client = arxiv.Client(
                page_size=self.config.get("MAX_RESULTS", 5),
                delay_seconds=delay,
                num_retries=self.config.get("NUM_RETRIES", 1),
            )
        else:
            self.client = None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def validate(self, citation_data: Dict[str, str]) -> Union[MatchCandidate, ValidationResult]:
        if not arxiv:
            return ValidationResult(
                source_name=self.name,
                status="Validation Error",
                confidence_score=0,
                details="[X] 'arxiv' library not installed. Run: pip install arxiv"
            )

        acc = CitationAccessor(citation_data)

        # Strategy A: Direct ArXiv ID lookup
        arxiv_id = acc.arxiv_id

        if arxiv_id:
            result = self._validate_by_id(arxiv_id, acc)
            if result is not None:
                return result

        # Strategy B: Title search
        if not acc.title:
            return ValidationResult(
                source_name=self.name,
                status="Not Validated",
                confidence_score=0,
                details="[X] No ArXiv ID or title provided"
            )

        return self._validate_by_title(acc)

    # ------------------------------------------------------------------
    # Strategy A: Direct ID lookup → MatchCandidate or None
    # ------------------------------------------------------------------

    def _validate_by_id(
        self, arxiv_id: str, acc: CitationAccessor
    ) -> Optional[Union[MatchCandidate, ValidationResult]]:
        try:
            search = arxiv.Search(id_list=[arxiv_id])
            try:
                paper = next(self.client.results(search))
            except StopIteration:
                return None
            except Exception as e:
                return ValidationResult(
                    source_name=self.name,
                    status="Validation Error",
                    confidence_score=0,
                    details=f"[X] ArXiv ID lookup failed: {e}"
                )

            found_title = paper.title
            api_year = str(paper.published.year) if paper.published else ""
            api_authors = [a.name for a in paper.authors]

            mismatch_note = ""
            if acc.title:
                # FIX (A1): Was ScoringPipeline.THRESHOLDS["title_low"]
                sim = calculate_title_similarity(acc.title, found_title)
                if sim < ScoringPipeline.TITLE_GATE:
                    mismatch_note = (
                        f"\n  ⚠ Title mismatch: expected '{acc.title}', "
                        f"found '{found_title}' (similarity {sim:.2f}). "
                        f"Verify the ArXiv ID is correct."
                    )

            evidence = [paper.entry_id]
            if paper.doi:
                evidence.append(f"https://doi.org/{paper.doi}")

            # FIX (A2): Added citation_title, citation_author_count, resolved_title
            return MatchCandidate(
                source_name=self.name,
                direct_id_verified=True,
                citation_title=acc.title,                    # FIX: was missing
                matched_title=found_title,
                resolved_title=found_title,                  # FIX: was missing — required for cross-check
                matched_authors=api_authors,
                matched_year=api_year,
                citation_author_count=acc.author_count,      # FIX: was missing
                evidence_links=evidence,
                raw_metadata=self._paper_to_metadata(paper),
                match_details=(
                    f"Direct ArXiv ID Match: '{found_title}'"
                    f"\n  ArXiv ID: {arxiv_id}"
                    + mismatch_note
                ),
            )

        except Exception as e:
            return ValidationResult(
                source_name=self.name,
                status="Validation Error",
                confidence_score=0,
                details=f"[X] ArXiv ID lookup failed unexpectedly: {e}"
            )

    # ------------------------------------------------------------------
    # Strategy B: Title search → best MatchCandidate or ValidationResult
    # ------------------------------------------------------------------

    def _validate_by_title(
        self, acc: CitationAccessor
    ) -> Union[MatchCandidate, ValidationResult]:
        clean_title = acc.title.replace('"', "").replace(":", " ").strip()
        query = f'ti:"{clean_title}"'

        try:
            search = arxiv.Search(
                query=query,
                max_results=self.config["MAX_RESULTS"],
                sort_by=arxiv.SortCriterion.Relevance,
            )
            results = list(self.client.results(search))
        except Exception as e:
            return ValidationResult(
                source_name=self.name,
                status="Validation Error",
                confidence_score=0,
                details=f"[X] ArXiv title search failed: {e}"
            )

        if not results:
            return ValidationResult(
                source_name=self.name,
                status="Not Validated",
                confidence_score=0,
                details="[X] No results found on ArXiv for this title"
            )

        best_candidate: Optional[MatchCandidate] = None
        best_sim = -1.0

        for rank, paper in enumerate(results, 1):
            found_title = paper.title

            sim = calculate_title_similarity(acc.title, found_title)

            # FIX (A1): Was ScoringPipeline.THRESHOLDS["title_low"]
            if sim < ScoringPipeline.TITLE_GATE:
                continue

            api_authors = [a.name for a in paper.authors]
            author_matched = False
            author_count = 0
            if acc.authors and api_authors:
                author_matched, author_count = check_author_overlap(
                    acc.authors, api_authors, author_format="names"
                )

            api_year = str(paper.published.year) if paper.published else ""
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

                evidence = [paper.entry_id]
                if paper.doi:
                    evidence.append(f"https://doi.org/{paper.doi}")

                # FIX (A3): Added citation_title, citation_author_count
                best_candidate = MatchCandidate(
                    source_name=self.name,
                    citation_title=acc.title,                # FIX: was missing
                    matched_title=found_title,
                    matched_authors=api_authors,
                    matched_year=api_year,
                    author_overlap_matched=author_matched,
                    author_overlap_count=author_count,
                    citation_author_count=acc.author_count,  # FIX: was missing
                    year_matched=year_matched,
                    result_rank=rank,
                    evidence_links=evidence,
                    raw_metadata=self._paper_to_metadata(paper),
                    match_details=f"Found match (rank #{rank}): '{found_title}'",
                )

        if best_candidate:
            return best_candidate

        top_title = results[0].title if results else "N/A"
        return ValidationResult(
            source_name=self.name,
            status="Not Validated",
            confidence_score=0,
            details=f"[X] No sufficiently similar match found on ArXiv. Top result: '{top_title}'"
        )

    # ------------------------------------------------------------------
    # ArXiv-specific helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _paper_to_metadata(paper) -> Dict:
        return {
            "arxiv_id": paper.get_short_id(),
            "title": paper.title,
            "authors": [a.name for a in paper.authors],
            "published": str(paper.published.date()) if paper.published else "",
            "primary_category": paper.primary_category,
            "categories": paper.categories,
            "doi": paper.doi or "",
            "entry_id": paper.entry_id,
            "summary": paper.summary[:300] + "..." if len(paper.summary) > 300 else paper.summary,
        }