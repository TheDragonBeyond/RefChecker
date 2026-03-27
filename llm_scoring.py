# llm_scoring.py
#
# LLM Scoring Pipeline for the citation validation system.
#
# Parallel to ScoringPipeline (which scores MatchCandidates from
# programmatic validators), this module scores LLMCandidates from
# LLM-based validators through a three-stage pipeline:
#
#   1. Proof Verification — resolve identifiers the LLM found using
#      the same APIs as Tier 1 validators (habanero, biopython, arxiv,
#      requests)
#   2. Cross-Check — compare LLM's found title/authors/year against
#      the citation using the same utils as the programmatic pipeline
#   3. Calibration — apply trust adjustments and produce final score
#
# Design principles:
#   - LLMs return structured JSON (schema-constrained) so identifiers
#     arrive in dedicated fields — no fragile regex extraction
#   - Proof verification reuses the same libraries as Tier 1 validators
#     but in lightweight resolve-only mode
#   - LLM's "Not Validated" is trusted (if it searched and can't find
#     it, that's useful signal)
#   - LLM's "Validated" is verified programmatically before trusting
#   - Unverifiable claims are capped — no more binary 0/100 scores
#
# Integration:
#   ValidatorManager._resolve_result() dispatches by type:
#     MatchCandidate → ScoringPipeline.score()
#     LLMCandidate   → LLMScoringPipeline.score()
#     ValidationResult → passthrough (errors, skips)

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from validators_plugin.base import ValidationResult


# ═══════════════════════════════════════════════════════════════════════
# LLMCandidate — Structured output from LLM validators
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class LLMCandidate:
    """
    Structured output from an LLM validator, before pipeline scoring.

    Unlike MatchCandidate (raw search signals from programmatic validators),
    LLMCandidate carries the LLM's holistic assessment plus identifiers
    and metadata it found, all in explicit fields.  Because the LLM uses
    schema-constrained JSON output (response_mime_type + response_schema),
    identifiers arrive in dedicated fields — no regex extraction needed.

    The LLMScoringPipeline verifies these identifiers programmatically,
    cross-checks findings against the citation, and produces a calibrated
    ValidationResult.

    Fields
    ------
    source_name : str
        Which LLM validator produced this (e.g. "Gemini Research Agent").

    citation_title, citation_authors, citation_year : str
        The original citation data, populated by the LLM validator from
        CitationAccessor before calling the LLM.

    citation_container_title : str
        The container title (journal/book/proceedings) from the citation.
        Used as a fallback during proof verification for book chapters
        where the ISBN/DOI resolves to the parent volume rather than
        the chapter itself.

    citation_author_count : int
        Total authors in the citation (for anti-overlap penalty).

    recommendation : str
        LLM's classification: "Validated", "Ambiguous", or "Not Validated".

    llm_confidence : int
        LLM's self-reported confidence 0–100 (unreliable — the pipeline
        ignores this for scoring but includes it in details).

    reasoning, verification_note : str
        LLM's textual explanation and manual verification instructions.

    title_found, authors_found, year_found : str / list
        The metadata the LLM found for the work.  Used for cross-checking
        when no identifiers are available.

    doi_found, pmid_found, arxiv_id_found, isbn_found : str
        Identifiers the LLM found, in explicit schema fields.
        These are programmatically verified by ProofVerifier.

    urls_found : list of str
        URLs the LLM found (not currently verified, reserved for future).

    evidence_links : list of str
        Links for human review (DOI URLs, Google Scholar, publisher pages).

    raw_response : dict
        The full parsed JSON from the LLM, for debugging.
    """

    source_name: str

    # ── Citation being validated ─────────────────────────────────────
    citation_title: str = ''
    citation_container_title: str = ''    # [FIX] Publication/journal/book title (for book chapters where ISBN resolves to parent volume)
    citation_authors: str = ''
    citation_year: str = ''
    citation_author_count: int = 0

    # ── LLM's assessment ─────────────────────────────────────────────
    recommendation: str = ''
    llm_confidence: int = 0
    reasoning: str = ''
    verification_note: str = ''

    # ── What the LLM found ───────────────────────────────────────────
    title_found: str = ''
    authors_found: List[str] = field(default_factory=list)
    year_found: str = ''

    # ── Identifiers found (explicit schema fields) ───────────────────
    doi_found: str = ''
    pmid_found: str = ''
    arxiv_id_found: str = ''
    isbn_found: str = ''
    urls_found: List[str] = field(default_factory=list)

    # ── Evidence links for human review ──────────────────────────────
    evidence_links: List[str] = field(default_factory=list)

    # ── Raw response for debugging ───────────────────────────────────
    raw_response: Dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
# ProofResult — Result of verifying a single identifier
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ProofResult:
    """
    Result of attempting to resolve a single identifier via its
    authoritative API.  Includes the resolved title and a pre-computed
    title cross-check score against the citation.
    """
    identifier_type: str        # "DOI", "PMID", "ArXiv", "ISBN"
    identifier_value: str       # The raw identifier string
    resolved: bool              # Did it resolve successfully?
    resolved_title: str         # Title from the resolved record
    title_cross_score: float = 0.0  # compute_title_score(citation_title, resolved_title)
    error: str = ''             # Error message if resolution failed


# ═══════════════════════════════════════════════════════════════════════
# ProofVerifier — Resolves identifiers using existing APIs
# ═══════════════════════════════════════════════════════════════════════

class ProofVerifier:
    """
    Lightweight identifier resolution using the same libraries as
    Tier 1 validators.  Each method takes an identifier string and
    the citation title, resolves the identifier against its
    authoritative API, and returns a ProofResult with the resolved
    title and a pre-computed cross-check score.

    All methods are static and handle missing dependencies gracefully
    (returning a failed ProofResult with an explanatory error message).

    These are resolve-only calls — no search, no scoring, just:
    "does this identifier exist, and what title does it map to?"
    """

    @staticmethod
    def verify_doi(doi: str, citation_title: str) -> ProofResult:
        """Resolve a DOI via Crossref and cross-check the title."""
        if not doi or not doi.strip():
            return ProofResult("DOI", "", False, "", error="Empty DOI")

        # Clean the DOI (strip URL prefixes)
        doi = doi.strip()
        for prefix in ['https://doi.org/', 'http://doi.org/',
                        'https://dx.doi.org/', 'http://dx.doi.org/',
                        'doi:', 'DOI:', 'DOI ']:
            if doi.startswith(prefix):
                doi = doi[len(prefix):]
                break
        doi = doi.strip()

        try:
            from habanero import Crossref
            cr = Crossref(mailto="citation_validator@example.com")
            result = cr.works(ids=doi)

            if result and 'message' in result:
                title = result['message'].get('title', [''])[0]
                if title:
                    from utils import compute_title_score
                    score = compute_title_score(citation_title, title)
                    return ProofResult("DOI", doi, True, title, score)

            return ProofResult(
                "DOI", doi, False, "",
                error="DOI resolved but no title in response"
            )

        except ImportError:
            return ProofResult("DOI", doi, False, "", error="habanero not installed")
        except Exception as e:
            return ProofResult("DOI", doi, False, "", error=f"Resolution failed: {e}")

    @staticmethod
    def verify_pmid(pmid: str, citation_title: str) -> ProofResult:
        """Resolve a PMID via PubMed (Entrez) and cross-check the title."""
        if not pmid or not pmid.strip():
            return ProofResult("PMID", "", False, "", error="Empty PMID")

        pmid = pmid.strip()

        try:
            from Bio import Entrez
            Entrez.email = "citation_validator@example.com"

            handle = Entrez.esummary(db="pubmed", id=pmid, retmode="xml")
            records = Entrez.read(handle)
            handle.close()

            if records:
                title = records[0].get('Title', '')
                if title:
                    from utils import compute_title_score
                    score = compute_title_score(citation_title, title)
                    return ProofResult("PMID", pmid, True, title, score)

            return ProofResult(
                "PMID", pmid, False, "",
                error="PMID resolved but no title in response"
            )

        except ImportError:
            return ProofResult("PMID", pmid, False, "", error="biopython not installed")
        except Exception as e:
            return ProofResult("PMID", pmid, False, "", error=f"Resolution failed: {e}")

    @staticmethod
    def verify_arxiv_id(arxiv_id: str, citation_title: str) -> ProofResult:
        """Resolve an ArXiv ID and cross-check the title."""
        if not arxiv_id or not arxiv_id.strip():
            return ProofResult("ArXiv", "", False, "", error="Empty ArXiv ID")

        arxiv_id = arxiv_id.strip()
        # Strip common prefixes — we want just the bare ID
        for prefix in ['arXiv:', 'arxiv:', 'https://arxiv.org/abs/',
                        'https://arxiv.org/pdf/', 'http://arxiv.org/abs/']:
            if arxiv_id.startswith(prefix):
                arxiv_id = arxiv_id[len(prefix):]
                break
        arxiv_id = arxiv_id.strip()

        try:
            import arxiv as arxiv_lib
            client = arxiv_lib.Client(
                page_size=1,
                delay_seconds=3.0,
                num_retries=1,
            )
            search = arxiv_lib.Search(id_list=[arxiv_id])
            paper = next(client.results(search))

            title = paper.title
            if title:
                from utils import compute_title_score
                score = compute_title_score(citation_title, title)
                return ProofResult("ArXiv", arxiv_id, True, title, score)

            return ProofResult(
                "ArXiv", arxiv_id, False, "",
                error="ArXiv ID resolved but no title"
            )

        except StopIteration:
            return ProofResult("ArXiv", arxiv_id, False, "", error="ArXiv ID not found")
        except ImportError:
            return ProofResult("ArXiv", arxiv_id, False, "", error="arxiv library not installed")
        except Exception as e:
            return ProofResult("ArXiv", arxiv_id, False, "", error=f"Resolution failed: {e}")

    @staticmethod
    def verify_isbn(isbn: str, citation_title: str) -> ProofResult:
        """
        Resolve an ISBN and cross-check the title.

        Backend selection (from global config PROOF_ISBN_USE_GOOGLE_BOOKS):
          - False (default): Open Library — free, no API key, no quota.
          - True: Google Books — requires a valid API key in the Google
            Books validator config.  Falls back to Open Library if no
            key is found.
        """
        if not isbn or not isbn.strip():
            return ProofResult("ISBN", "", False, "", error="Empty ISBN")

        # Clean ISBN — strip hyphens and spaces
        isbn = re.sub(r'[\s\-]', '', isbn.strip())

        # Check config for backend preference
        import config as cfg
        use_google = cfg._current_settings.get("PROOF_ISBN_USE_GOOGLE_BOOKS", False)

        if use_google:
            api_key = ProofVerifier._get_google_books_api_key()
            if api_key:
                print(f"[ProofVerifier] ISBN backend: Google Books (API key present)")
                return ProofVerifier._verify_isbn_google_books(isbn, citation_title, api_key)
            else:
                print(f"[ProofVerifier] Google Books selected but no API key found. Falling back to Open Library.")

        return ProofVerifier._verify_isbn_open_library(isbn, citation_title)

    # ── ISBN backend: Open Library ───────────────────────────────────

    @staticmethod
    def _verify_isbn_open_library(isbn: str, citation_title: str) -> ProofResult:
        """
        Resolve an ISBN via Open Library's direct ISBN endpoint.
        No API key or quota — returns JSON with the book's title directly.
        """
        try:
            import requests
            url = f"https://openlibrary.org/isbn/{isbn}.json"
            resp = requests.get(url, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                title = data.get("title", "")
                if title:
                    from utils import compute_title_score
                    score = compute_title_score(citation_title, title)
                    return ProofResult("ISBN", isbn, True, title, score)

                return ProofResult(
                    "ISBN", isbn, False, "",
                    error="ISBN resolved but no title in response"
                )

            elif resp.status_code == 404:
                return ProofResult(
                    "ISBN", isbn, False, "",
                    error="ISBN not found in Open Library"
                )

            return ProofResult(
                "ISBN", isbn, False, "",
                error=f"Open Library returned HTTP {resp.status_code}"
            )

        except ImportError:
            return ProofResult("ISBN", isbn, False, "", error="requests not installed")
        except Exception as e:
            return ProofResult("ISBN", isbn, False, "", error=f"Resolution failed: {e}")

    # ── ISBN backend: Google Books ───────────────────────────────────

    @staticmethod
    def _verify_isbn_google_books(isbn: str, citation_title: str, api_key: str) -> ProofResult:
        """
        Resolve an ISBN via Google Books API.  Requires a valid API key
        to avoid anonymous quota exhaustion (HTTP 429).
        """
        try:
            import requests
            params = {
                "q": f"isbn:{isbn}",
                "maxResults": 1,
                "key": api_key,
            }
            resp = requests.get(
                "https://www.googleapis.com/books/v1/volumes",
                params=params,
                timeout=10,
            )

            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    title = items[0].get("volumeInfo", {}).get("title", "")
                    if title:
                        from utils import compute_title_score
                        score = compute_title_score(citation_title, title)
                        return ProofResult("ISBN", isbn, True, title, score)

            return ProofResult(
                "ISBN", isbn, False, "",
                error=f"ISBN not found (HTTP {resp.status_code})"
            )

        except ImportError:
            return ProofResult("ISBN", isbn, False, "", error="requests not installed")
        except Exception as e:
            return ProofResult("ISBN", isbn, False, "", error=f"Resolution failed: {e}")

    # ── Google Books API key lookup ──────────────────────────────────

    @staticmethod
    def _get_google_books_api_key() -> str:
        """
        Try to read the Google Books API key from its validator config
        file.  Does NOT import or instantiate the validator — just reads
        the JSON config directly.

        Returns the key string, or '' if not found / not set.
        """
        import json
        import os
        import sys

        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))

        # Check source layout: validators_plugin/GoogleBooksValidator_Config.json
        config_path = os.path.join(base, 'validators_plugin', 'GoogleBooksValidator_Config.json')

        if not os.path.exists(config_path):
            # Frozen build layout: plugins/validators/GoogleBooksValidator_Config.json
            config_path = os.path.join(base, 'plugins', 'validators', 'GoogleBooksValidator_Config.json')

        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    key = data.get('API_KEY', '').strip()
                    if key:
                        return key
            except Exception:
                pass

        return ''


# ═══════════════════════════════════════════════════════════════════════
# LLMScoringPipeline — Three-stage scoring for LLM validators
# ═══════════════════════════════════════════════════════════════════════

class LLMScoringPipeline:
    """
    Scoring pipeline for LLM validators, parallel to ScoringPipeline.

    Converts an LLMCandidate into a calibrated ValidationResult through
    three stages:

      Stage 0 — LLM says "Not Validated" → trust it (score 0)
      Stage 1 — Proof Verification: resolve every identifier the LLM
                reported using authoritative APIs
      Stage 2 — Score based on verification results:
                (a) Verified ID + good title → Validated (100)
                (b) Verified ID + bad title → Not Validated (wrong work)
                (c) Claimed IDs but none resolve → Not Validated (hallucination)
      Stage 3 — No identifiers: cross-check LLM's found metadata against
                the citation using weighted scoring, capped since unverifiable

    Trust model
    -----------
    ========================== ===============================================
    Scenario                   Outcome
    ========================== ===============================================
    LLM: Not Validated         Trust it → score 0
    Verified ID, title ≥ 0.70  Full trust → Validated, score 100
    Verified ID, title < 0.70  Wrong work or mismatch → Not Validated, score 0
    IDs claimed, none resolve  Possible hallucination → Not Validated, score 0
    No IDs, good cross-check   Capped credit → Possible Match (max 75)
    Ambiguous recommendation   Lower cap → max 70
    No IDs, poor cross-check   Insufficient evidence → Not Validated, score 0
    ========================== ===============================================

    Usage
    -----
    >>> result = LLMScoringPipeline.score(candidate)
    """

    # ── Thresholds ───────────────────────────────────────────────────

    VERIFIED_HIGH   = 0.70  # Title cross-check: ID confirms citation
    VERIFIED_PARTIAL = 0.40  # Title cross-check: partial (suspicious)

    UNVERIFIED_CAP  = 75    # Max display score with no verified identifiers
    AMBIGUOUS_CAP   = 70    # Max display score when LLM says "Ambiguous"

    TITLE_GATE      = 0.30  # Minimum title score (same as ScoringPipeline)

    # Reuse ScoringPipeline's component weights for unverified cross-check
    W_TITLE  = 3.0   # ~60%
    W_AUTHOR = 1.5   # ~30%
    W_YEAR   = 0.5   # ~10%
    W_SUM    = W_TITLE + W_AUTHOR + W_YEAR  # 5.0

    # Status vocabulary (matches ScoringPipeline)
    STATUS_VALIDATED     = "Validated"
    STATUS_POSSIBLE      = "Possible Match"
    STATUS_NOT_VALIDATED = "Not Validated"

    # ── Public API ───────────────────────────────────────────────────

    @classmethod
    def score(cls, candidate: LLMCandidate) -> ValidationResult:
        """
        Score an LLMCandidate through the three-stage pipeline.

        This is a pure function: same candidate in → same result out.
        ProofVerifier calls are the only side effects (network I/O for
        identifier resolution).

        Parameters
        ----------
        candidate : LLMCandidate
            Structured output from an LLM validator.

        Returns
        -------
        ValidationResult
            Calibrated result ready for the ValidatorManager.
        """
        # ── Stage 0: LLM says "Not Validated" → trust it ────────────
        if candidate.recommendation == "Not Validated":
            return cls._trust_not_validated(candidate)

        # ── Stage 1: Proof Verification ──────────────────────────────
        proofs = cls._verify_proofs(candidate)

        # ── Stage 2: Score based on verification results ─────────────
        has_identifiers = any(p.identifier_value for p in proofs)
        verified_proofs = [p for p in proofs if p.resolved]

        if verified_proofs:
            return cls._score_with_verification(candidate, verified_proofs)

        if has_identifiers and not verified_proofs:
            # LLM claimed identifiers but none resolved → suspicious
            return cls._score_failed_verification(candidate, proofs)

        # ── Stage 3: No identifiers → cross-check and cap ───────────
        return cls._score_unverified(candidate)

    # ── Stage 0: Trust "Not Validated" ───────────────────────────────

    @classmethod
    def _trust_not_validated(cls, candidate: LLMCandidate) -> ValidationResult:
        """LLM searched and couldn't find the work — trust that signal."""
        details = [
            f"LLM assessment: Not Validated (LLM confidence: {candidate.llm_confidence})",
            f"  Reasoning: {candidate.reasoning}",
        ]
        if candidate.verification_note:
            details.append(f"  Note: {candidate.verification_note}")

        return ValidationResult(
            source_name=candidate.source_name,
            status=cls.STATUS_NOT_VALIDATED,
            confidence_score=0,
            details='\n'.join(details),
            evidence_links=list(candidate.evidence_links),
            metadata=dict(candidate.raw_response),
        )

    # ── Stage 1: Proof Verification ──────────────────────────────────

    @classmethod
    def _verify_proofs(cls, candidate: LLMCandidate) -> List[ProofResult]:
        """
        Attempt to resolve every identifier the LLM reported.
        Each identifier is checked against its authoritative API.
        Returns a list of ProofResults (one per identifier attempted).
        """
        results = []
        cite_title = candidate.citation_title

        if candidate.doi_found:
            print(f"[LLM Pipeline] Verifying DOI: {candidate.doi_found}")
            results.append(ProofVerifier.verify_doi(candidate.doi_found, cite_title))

        if candidate.pmid_found:
            print(f"[LLM Pipeline] Verifying PMID: {candidate.pmid_found}")
            results.append(ProofVerifier.verify_pmid(candidate.pmid_found, cite_title))

        if candidate.arxiv_id_found:
            print(f"[LLM Pipeline] Verifying ArXiv ID: {candidate.arxiv_id_found}")
            results.append(ProofVerifier.verify_arxiv_id(candidate.arxiv_id_found, cite_title))

        if candidate.isbn_found:
            print(f"[LLM Pipeline] Verifying ISBN: {candidate.isbn_found}")
            results.append(ProofVerifier.verify_isbn(candidate.isbn_found, cite_title))

        # Log results
        for r in results:
            if r.resolved:
                print(
                    f"  [OK] {r.identifier_type} resolved: "
                    f"'{r.resolved_title}' (cross-check: {r.title_cross_score:.2f})"
                )
            else:
                print(f"  [FAIL] {r.identifier_type}: {r.error}")

        return results

    # ── Stage 2a: Verified identifiers ───────────────────────────────

    @classmethod
    def _score_with_verification(
        cls, candidate: LLMCandidate, verified: List[ProofResult]
    ) -> ValidationResult:
        """
        At least one identifier resolved successfully.  Score based on
        the best title cross-check among verified proofs.
        """
        from utils import compute_title_score

        best = max(verified, key=lambda p: p.title_cross_score)

        # ── Substring containment check ──────────────────────────────
        # Databases frequently store truncated titles (e.g. "An Essay
        # on Man" instead of "An Essay on Man: An Introduction to a
        # Philosophy of Human Culture").  If the identifier resolved
        # and one title is clearly contained within the other, treat
        # it as confirmed — the identifier already proved identity.
        if best.title_cross_score < cls.VERIFIED_HIGH:
            if cls._titles_are_substring_match(
                candidate.citation_title, best.resolved_title
            ):
                details = cls._format_verified_details(
                    candidate, best, "confirmed"
                )
                return ValidationResult(
                    source_name=candidate.source_name,
                    status=cls.STATUS_VALIDATED,
                    confidence_score=100,
                    details=details,
                    evidence_links=list(candidate.evidence_links),
                    metadata=dict(candidate.raw_response),
                )

        # ── [FIX] Container title fallback (book chapters) ──────────
        # For book chapters, the ISBN/DOI belongs to the parent volume.
        # The resolved title may match the Publication Title (container)
        # rather than the Article Title (chapter).  Check the container
        # title when the article title check doesn't pass.
        if best.title_cross_score < cls.VERIFIED_HIGH:
            if candidate.citation_container_title:
                container_cross = compute_title_score(
                    candidate.citation_container_title, best.resolved_title
                )
                if container_cross >= cls.VERIFIED_HIGH or \
                   cls._titles_are_substring_match(
                       candidate.citation_container_title, best.resolved_title
                   ):
                    details = cls._format_verified_details(
                        candidate, best, "confirmed"
                    )
                    return ValidationResult(
                        source_name=candidate.source_name,
                        status=cls.STATUS_VALIDATED,
                        confidence_score=100,
                        details=details,
                        evidence_links=list(candidate.evidence_links),
                        metadata=dict(candidate.raw_response),
                    )

        if best.title_cross_score >= cls.VERIFIED_HIGH:
            # Title confirms the identifier — full confidence
            details = cls._format_verified_details(candidate, best, "confirmed")
            return ValidationResult(
                source_name=candidate.source_name,
                status=cls.STATUS_VALIDATED,
                confidence_score=100,
                details=details,
                evidence_links=list(candidate.evidence_links),
                metadata=dict(candidate.raw_response),
            )

        elif best.title_cross_score >= cls.VERIFIED_PARTIAL:
            # Identifier resolves but title only partially matches —
            # the LLM may have found a related but different work
            details = cls._format_verified_details(
                candidate, best, "partial mismatch"
            )
            return ValidationResult(
                source_name=candidate.source_name,
                status=cls.STATUS_NOT_VALIDATED,
                confidence_score=0,
                details=details,
                evidence_links=list(candidate.evidence_links),
                metadata=dict(candidate.raw_response),
            )

        else:
            # Identifier resolves to a completely different work
            details = cls._format_verified_details(
                candidate, best, "title mismatch"
            )
            return ValidationResult(
                source_name=candidate.source_name,
                status=cls.STATUS_NOT_VALIDATED,
                confidence_score=0,
                details=details,
                evidence_links=list(candidate.evidence_links),
                metadata=dict(candidate.raw_response),
            )

    # ── Substring containment helper ─────────────────────────────────

    @staticmethod
    def _titles_are_substring_match(title_a: str, title_b: str) -> bool:
        """
        Returns True if one normalized title is fully contained within
        the other and the shorter title is at least 2 words long.

        This catches cases where a database stores a truncated title
        (e.g. missing the subtitle after a colon), which would fail
        the fuzzy title cross-check despite being the same work.

        Same logic as calculate_title_similarity's substring_boost,
        applied here in the proof verification context.

        [FIX] Minimum lowered from 3 to 2 words.  This is safe because
        this method is only called during identifier-verified proof
        checking, where the ISBN/DOI already provides strong identity
        signal.  A 2-word title like "Re-thinking Europe" should match
        when the identifier has already resolved successfully.
        """
        import string
        from unidecode import unidecode

        if not title_a or not title_b:
            return False

        def _normalize(t: str) -> str:
            t = unidecode(str(t))
            t = t.lower()
            t = t.translate(str.maketrans('', '', string.punctuation))
            return ' '.join(t.split())

        a = _normalize(title_a)
        b = _normalize(title_b)

        if not a or not b:
            return False

        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)

        # [FIX] Was 3 — lowered to 2 for identifier-verified context
        if len(shorter.split()) < 2:
            return False

        return shorter in longer

    # ── Stage 2b: Failed verification ────────────────────────────────

    @classmethod
    def _score_failed_verification(
        cls, candidate: LLMCandidate, proofs: List[ProofResult]
    ) -> ValidationResult:
        """
        LLM claimed identifiers but none could be resolved.
        This is a strong signal of hallucination — LLMs frequently
        fabricate plausible-looking DOIs and PMIDs.
        """
        failed_ids = [
            f"{p.identifier_type}: {p.identifier_value} ({p.error})"
            for p in proofs if p.identifier_value
        ]

        details = [
            f"Identifier verification: FAILED",
            f"  Identifiers claimed but NONE could be verified:",
            *[f"    x {fid}" for fid in failed_ids],
            f"  Possible LLM hallucination — claimed identifiers do not resolve.",
            f"  LLM reasoning: {candidate.reasoning}",
            f"  Status: Not Validated",
        ]

        return ValidationResult(
            source_name=candidate.source_name,
            status=cls.STATUS_NOT_VALIDATED,
            confidence_score=0,
            details='\n'.join(details),
            evidence_links=list(candidate.evidence_links),
            metadata=dict(candidate.raw_response),
        )

    # ── Stage 3: No identifiers — cross-check and cap ────────────────

    @classmethod
    def _score_unverified(cls, candidate: LLMCandidate) -> ValidationResult:
        """
        No identifiers found by the LLM.  Cross-check the LLM's found
        title, authors, and year against the citation using the same
        weighted scoring as ScoringPipeline, but cap the result since
        we can't programmatically verify the LLM's claims.
        """
        from utils import (
            compute_title_score, compute_author_score,
            compute_author_penalty, check_author_overlap, years_match,
        )

        # Determine cap based on LLM's recommendation
        if candidate.recommendation == "Ambiguous":
            cap = cls.AMBIGUOUS_CAP
        else:
            cap = cls.UNVERIFIED_CAP

        # ── Title cross-check ────────────────────────────────────────
        title_score = 0.0
        if candidate.title_found:
            title_score = compute_title_score(
                candidate.citation_title, candidate.title_found
            )

        if title_score < cls.TITLE_GATE:
            # LLM claims validated but couldn't even produce a matching title
            details = [
                f"LLM assessment: {candidate.recommendation} "
                f"(LLM confidence: {candidate.llm_confidence})",
                f"  No verifiable identifiers found.",
                f"  Title cross-check: {title_score:.2f} (below gate of {cls.TITLE_GATE})",
            ]
            if candidate.title_found:
                details.append(f"  LLM found title: '{candidate.title_found}'")
                details.append(f"  Citation title:  '{candidate.citation_title}'")
            else:
                details.append(f"  LLM did not report a found title.")
            details.append(f"  LLM reasoning: {candidate.reasoning}")

            return ValidationResult(
                source_name=candidate.source_name,
                status=cls.STATUS_NOT_VALIDATED,
                confidence_score=0,
                details='\n'.join(details),
                evidence_links=list(candidate.evidence_links),
                metadata=dict(candidate.raw_response),
            )

        # ── Author cross-check ───────────────────────────────────────
        author_overlap_count = 0
        if candidate.citation_authors and candidate.authors_found:
            _, author_overlap_count = check_author_overlap(
                candidate.citation_authors,
                candidate.authors_found,
                author_format="names"
            )

        author_score = compute_author_score(
            author_overlap_count,
            candidate.citation_author_count,
        )

        # ── Year cross-check ────────────────────────────────────────
        year_score = 1.0 if years_match(
            candidate.citation_year, candidate.year_found
        ) else 0.0

        # ── Weighted average (same weights as ScoringPipeline) ───────
        raw = (
            cls.W_TITLE  * title_score +
            cls.W_AUTHOR * author_score +
            cls.W_YEAR   * year_score
        ) / cls.W_SUM

        # ── Author penalty ───────────────────────────────────────────
        author_penalty = compute_author_penalty(
            candidate.citation_author_count,
            author_overlap_count,
        )
        final = raw * author_penalty

        # ── Map to display score, apply cap, then floor ──────────────
        display = min(round(final * 100), cap)

        if display < 60:
            effective_score = 0
            status = cls.STATUS_NOT_VALIDATED
        elif display >= 80:
            status = cls.STATUS_VALIDATED
            effective_score = display
        else:
            status = cls.STATUS_POSSIBLE
            effective_score = display

        # ── Format details ───────────────────────────────────────────
        details = [
            f"LLM assessment: {candidate.recommendation} "
            f"(LLM confidence: {candidate.llm_confidence}, no verifiable identifiers)",
            f"  Cross-check scores (vs LLM's found metadata):",
            f"    Title:  {title_score:.2f}",
        ]

        if candidate.citation_author_count > 0:
            details.append(
                f"    Author: {author_score:.2f} "
                f"({author_overlap_count}/{candidate.citation_author_count} overlapping)"
            )
        else:
            details.append(f"    Author: {author_score:.2f}")

        details.append(f"    Year:   {year_score:.1f}")
        details.append(
            f"    Weighted avg: {raw:.3f} -> display {round(final * 100)} "
            f"(capped at {cap})"
        )

        if author_penalty < 1.0:
            details.append(f"    Author penalty: x{author_penalty:.2f}")

        details.append(f"  LLM reasoning: {candidate.reasoning}")
        details.append(f"  Status: {status}")

        return ValidationResult(
            source_name=candidate.source_name,
            status=status,
            confidence_score=effective_score,
            details='\n'.join(details),
            evidence_links=list(candidate.evidence_links),
            metadata=dict(candidate.raw_response),
        )

    # ── Details formatting helpers ───────────────────────────────────

    @classmethod
    def _format_verified_details(
        cls,
        candidate: LLMCandidate,
        proof: ProofResult,
        outcome: str,
    ) -> str:
        """
        Build human-readable details for a verification-based result.

        outcome is one of: "confirmed", "partial mismatch", "title mismatch"

        For confirmed: lead with the proof, minimal LLM context.
        For mismatches: lead with the proof failure, flag possible
        hallucination, include LLM reasoning for human review.
        """
        lines = []

        if outcome == "confirmed":
            # ── Confirmed: proof speaks for itself ───────────────────
            lines.append(
                f"{proof.identifier_type} verification: {outcome}"
            )
            lines.append(f"  Identifier: {proof.identifier_value}")
            lines.append(f"  Resolved title: '{proof.resolved_title}'")
            lines.append(f"  Title cross-check: {proof.title_cross_score:.2f}")
            lines.append(f"  Status: Validated (programmatically verified)")

        else:
            # ── Mismatch: lead with proof failure, flag hallucination ─
            lines.append(
                f"{proof.identifier_type} verification: {outcome}"
            )
            lines.append(f"  Identifier: {proof.identifier_value}")
            lines.append(f"  Resolved title: '{proof.resolved_title}'")
            lines.append(f"  Title cross-check: {proof.title_cross_score:.2f}")
            lines.append(f"  Citation title:  '{candidate.citation_title}'")
            lines.append(
                f"  Possible LLM hallucination — "
                f"identifier resolves to a different work."
            )
            lines.append(f"  LLM reasoning: {candidate.reasoning}")
            lines.append(
                f"  Status: Not Validated "
                f"({proof.identifier_type} resolves to a different work)"
            )

        return '\n'.join(lines)

    # ── Convenience methods (mirror ScoringPipeline) ─────────────────

    @classmethod
    def is_validated(cls, result: ValidationResult) -> bool:
        """True if the result's status is 'Validated'."""
        return result.status == cls.STATUS_VALIDATED

    @classmethod
    def is_at_least_possible(cls, result: ValidationResult) -> bool:
        """True if the result's status is 'Validated' or 'Possible Match'."""
        return result.status in (cls.STATUS_VALIDATED, cls.STATUS_POSSIBLE)