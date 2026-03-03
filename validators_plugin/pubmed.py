# validators_plugin/pubmed.py
#
# FULLY MIGRATED — Uses the same architecture as Crossref, DBLP, Open Library.
#
# Architecture:
#   - CitationAccessor for uniform field access
#   - years_match() for year comparison
#   - Returns MatchCandidate for successful matches (scored by ScoringPipeline)
#   - Returns ValidationResult directly for errors, skips, and not-found
#
# Rate limiting:
#   BioPython's Entrez module handles NCBI rate limiting automatically.
#   We add REQUEST_DELAY between our own sequential calls (esearch → esummary).

import time
import re
from typing import Dict, Any, List, Optional, Union

try:
    from Bio import Entrez
except ImportError:
    Entrez = None

from validators_plugin.base import BaseValidator, ValidationResult
from validators_plugin.manager import register_validator
from scoring import MatchCandidate, ScoringPipeline

from utils import (
    CitationAccessor,
    calculate_title_similarity,
    check_author_overlap,
    years_match,
)


@register_validator
class PubMedValidator(BaseValidator):
    DEPENDENCIES = ["biopython"]

    def get_default_settings(self) -> Dict[str, Any]:
        return {
            "EMAIL": "your_email@example.com",
            "TOOL_NAME": "CitationValidator",
            "API_KEY": "",
            "MAX_RESULTS": 5,
            "MAX_QUERY_WORDS": 8,
            "REQUEST_DELAY": 0.35,
            "YEAR_MATCH_TOLERANCE": 1,
        }

    @property
    def name(self) -> str:
        return "PubMed (BioPython)"

    # FIX (P4): PubMed has working defaults for EMAIL — always configured.
    # The EMAIL default is sufficient for NCBI access to work.
    def is_configured(self) -> bool:
        return True

    def __init__(self):
        super().__init__()
        if Entrez:
            Entrez.email = self.config.get("EMAIL", "citation_validator@example.com")
            Entrez.tool = self.config.get("TOOL_NAME", "CitationValidator")

            api_key = self.config.get("API_KEY")
            if api_key:
                Entrez.api_key = api_key
                self.config["REQUEST_DELAY"] = 0.11

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def validate(self, citation_data: Dict[str, str]) -> Union[MatchCandidate, ValidationResult]:
        if not Entrez:
            return ValidationResult(
                source_name=self.name,
                status="Validation Error",
                confidence_score=0,
                details="[X] BioPython not installed (required for PubMed access)"
            )

        acc = CitationAccessor(citation_data)

        time.sleep(self.config.get("REQUEST_DELAY", 0.35))

        # 1. Direct PMID Lookup
        pmid = acc.pmid
        if pmid:
            return self._fetch_by_id(pmid, acc)

        # 2. Metadata Search
        if not acc.title:
            return ValidationResult(
                source_name=self.name,
                status="Not Validated",
                confidence_score=0,
                details="[X] No title or PMID provided for PubMed search"
            )

        try:
            return self._search_metadata(acc)
        except Exception as e:
            return ValidationResult(
                source_name=self.name,
                status="Validation Error",
                confidence_score=0,
                details=f"[X] PubMed API Error: {str(e)}"
            )

    # ------------------------------------------------------------------
    # Direct PMID Lookup → MatchCandidate
    # ------------------------------------------------------------------

    # FIX (P2): Added acc parameter so we can populate citation_title/citation_author_count
    def _fetch_by_id(self, pmid: str, acc: CitationAccessor) -> Union[MatchCandidate, ValidationResult]:
        try:
            handle = Entrez.esummary(db="pubmed", id=pmid, retmode="xml")
            records = Entrez.read(handle)
            handle.close()

            if not records:
                return ValidationResult(
                    source_name=self.name,
                    status="Not Validated",
                    confidence_score=0,
                    details=f"[X] No record found for PMID: {pmid}"
                )

            item = records[0]
            title = item.get('Title', '')

            # FIX (P2): Added citation_title, citation_author_count, resolved_title
            return MatchCandidate(
                source_name=self.name,
                direct_id_verified=True,
                citation_title=acc.title,                    # FIX: was missing
                matched_title=title,
                resolved_title=title,                        # FIX: was missing — required for cross-check
                matched_authors=list(item.get('AuthorList', [])),
                citation_author_count=acc.author_count,      # FIX: was missing
                evidence_links=[f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"],
                raw_metadata=dict(item),
                match_details=f"Direct PMID Match: '{title}'\n  PMID: {pmid}",
            )
        except Exception as e:
            return ValidationResult(
                source_name=self.name,
                status="Validation Error",
                confidence_score=0,
                details=f"[X] PMID lookup failed: {str(e)}"
            )

    # ------------------------------------------------------------------
    # Metadata search → best MatchCandidate or ValidationResult
    # ------------------------------------------------------------------

    def _search_metadata(
        self, acc: CitationAccessor
    ) -> Union[MatchCandidate, ValidationResult]:
        searchable_title = self._clean_title_for_search(acc.title)
        first_author = acc.first_author_surname

        # --- Strategy A: Strict Title + Author ---
        search_term = f"{searchable_title}[Title]"
        if first_author:
            search_term += f" AND {first_author}[Author]"

        id_list = self._execute_esearch(search_term)

        # --- Strategy B: Title Only (field restricted) ---
        if not id_list and first_author:
            search_term = f"{searchable_title}[Title]"
            id_list = self._execute_esearch(search_term)

        # --- Strategy C: Loose Keyword Search ---
        if not id_list:
            id_list = self._execute_esearch(searchable_title)

        if not id_list:
            return ValidationResult(
                source_name=self.name,
                status="Not Validated",
                confidence_score=0,
                details="[X] No results found on PubMed"
            )

        return self._evaluate_candidates(id_list, acc)

    def _evaluate_candidates(
        self, id_list: List[str], acc: CitationAccessor
    ) -> Union[MatchCandidate, ValidationResult]:
        ids_to_fetch = id_list[:self.config["MAX_RESULTS"]]

        time.sleep(self.config.get("REQUEST_DELAY", 0.35))

        fetch_handle = Entrez.esummary(
            db="pubmed", id=",".join(ids_to_fetch), retmode="xml"
        )
        records = Entrez.read(fetch_handle)
        fetch_handle.close()

        best_candidate: Optional[MatchCandidate] = None
        best_sim = -1.0

        for rank, record in enumerate(records, 1):
            r_title = record.get('Title', '')
            r_authors = list(record.get('AuthorList', []))
            r_pubdate = record.get('PubDate', '')
            pmid_cand = record.get('Id', '')

            sim = calculate_title_similarity(acc.title, r_title)

            # FIX (P1): Was ScoringPipeline.THRESHOLDS['title_low']
            if sim < ScoringPipeline.TITLE_GATE:
                continue

            author_matched = False
            author_count = 0
            if acc.authors and r_authors:
                author_matched, author_count = check_author_overlap(
                    acc.authors, r_authors, author_format="surname_first"
                )

            api_year = self._extract_year_from_pubdate(r_pubdate)
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

                evidence = [f"https://pubmed.ncbi.nlm.nih.gov/{pmid_cand}/"] if pmid_cand else []

                # FIX (P3): Added citation_title, citation_author_count
                best_candidate = MatchCandidate(
                    source_name=self.name,
                    citation_title=acc.title,                # FIX: was missing
                    matched_title=r_title,
                    matched_authors=r_authors,
                    matched_year=api_year,
                    author_overlap_matched=author_matched,
                    author_overlap_count=author_count,
                    citation_author_count=acc.author_count,  # FIX: was missing
                    year_matched=year_matched,
                    result_rank=rank,
                    evidence_links=evidence,
                    raw_metadata=dict(record),
                    match_details=f"Found match (rank #{rank}): '{r_title}'",
                )

        if best_candidate:
            return best_candidate

        return ValidationResult(
            source_name=self.name,
            status="Not Validated",
            confidence_score=0,
            details="[X] No sufficiently similar match found on PubMed"
        )

    # ------------------------------------------------------------------
    # PubMed-specific helpers
    # ------------------------------------------------------------------

    def _execute_esearch(self, term: str) -> List[str]:
        try:
            handle = Entrez.esearch(
                db="pubmed",
                term=term,
                retmax=self.config["MAX_RESULTS"],
                sort="relevance"
            )
            results = Entrez.read(handle)
            handle.close()
            return results.get("IdList", [])
        except Exception:
            return []

    def _clean_title_for_search(self, title: str) -> str:
        if not title:
            return ""

        main_title = title.split(':')[0]
        main_title = re.sub(r'[-–—]', ' ', main_title)
        main_title = main_title.replace("'", " ")

        clean = "".join(c for c in main_title if c.isalnum() or c.isspace())
        clean = " ".join(clean.split())

        limit = self.config.get("MAX_QUERY_WORDS", 8)
        words = clean.split()
        if len(words) > limit:
            clean = " ".join(words[:limit])

        return clean

    @staticmethod
    def _extract_year_from_pubdate(pubdate: str) -> str:
        match = re.search(r'\b(19|20)\d{2}\b', str(pubdate))
        return match.group(0) if match else ""