# scoring.py
#
# Centralized scoring pipeline for the citation validation system (v3).
#
# v3 CHANGES (from v2):
#   - Scoring model: additive points → weighted average of 0–1 components
#   - Title comparison: pipeline computes multi-metric blend internally
#     (validators provide raw titles, not pre-computed similarity)
#   - Negative evidence: author anti-overlap penalty for zero/low overlap
#   - Hard floor: scores below 60 → 0 / "Not Validated"
#   - Status tiers: 5 → 3  (Validated ≥80, Possible Match 60–79, Not Validated <60)
#   - DOI/ID cross-check: mandatory title verification (no legacy fallback)
#   - Search rank: removed from scoring (tracked in details only)
#
# ARCHITECTURE:
#   Validators return a MatchCandidate (raw match signals) or a
#   ValidationResult (errors, skips, LLM validators).
#   ScoringPipeline.score() converts MatchCandidate → ValidationResult
#   using consistent weights and thresholds.
#
# MIGRATION:
#   The ValidatorManager dispatches via _resolve_result():
#
#     raw = validator.validate(citation_data)
#     if isinstance(raw, MatchCandidate):
#         result = ScoringPipeline.score(raw)
#     else:
#         result = raw   # ValidationResult (errors, LLM validators)
#
# LLM EXCEPTION:
#   ChatGPT and Gemini validators continue returning ValidationResult
#   directly — they bypass the pipeline.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from validators_plugin.base import ValidationResult


# ═══════════════════════════════════════════════════════════════════════════
# MatchCandidate — Raw match signals, produced by validators
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MatchCandidate:
    """
    Raw match data from a validator, before scoring.

    A validator's job is to:
      1. Query its API
      2. Extract match signals (overlap counts, booleans, raw titles)
      3. Return a MatchCandidate with those signals populated

    It does NOT assign points, determine status, or compute title
    similarity.  ScoringPipeline handles all of that.

    For error / skip / not-found cases, validators should return a
    ValidationResult directly instead of a MatchCandidate.

    v3 changes from v2
    -------------------
    - ``title_similarity`` REMOVED — the pipeline computes this internally
      from ``citation_title`` and ``matched_title`` using a multi-metric
      blend with distinctive-token penalty.
    - ``citation_title`` ADDED — the raw citation title for pipeline-side
      comparison.
    - ``citation_author_count`` ADDED — total authors in the citation,
      used for the anti-overlap penalty.
    - ``resolved_title`` ADDED — the title returned when resolving a DOI
      or other identifier.  Required for DOI/ID cross-check (no legacy
      fallback).

    Fields
    ------
    source_name : str
        Which validator produced this candidate (e.g. "Crossref API").

    citation_title : str
        The title from the original citation.  The pipeline uses this
        together with ``matched_title`` to compute the composite title
        score internally.  Must always be populated.

    matched_title : str
        The title string returned by the API for this candidate.

    matched_authors : list of str
        Author names from the API response.  May be empty.

    matched_year : str
        Publication year from the API response.  May be empty.

    author_overlap_count : int
        Number of citation authors whose surnames matched API authors,
        as returned by ``check_author_overlap()``.

    author_overlap_matched : bool
        True if at least one author surname matched (the first return
        value of ``check_author_overlap()``).

    citation_author_count : int
        Total number of authors listed in the original citation.
        Used by the anti-overlap penalty — zero overlap with 3+ authors
        triggers a score reduction.  Populate from
        ``CitationAccessor.author_count``.

    year_matched : bool
        True if citation year and API year match within tolerance,
        as returned by ``years_match()``.

    doi_verified : bool
        True if the validator performed a direct DOI lookup and got a
        successful response.  When True, ``resolved_title`` must also
        be populated for the cross-check.

    direct_id_verified : bool
        True if the validator performed a direct identifier lookup
        (PMID, ArXiv ID, ISBN exact match, etc.) and got a successful
        response.  Same semantics as doi_verified.  When True,
        ``resolved_title`` must also be populated.

    resolved_title : str
        The title returned by DOI/ID resolution.  Required when
        ``doi_verified`` or ``direct_id_verified`` is True.  The
        pipeline cross-checks this against ``citation_title``.

    result_rank : int or None
        1-indexed position of this candidate in the API search results.
        None if not applicable (e.g., direct ID lookup).
        Tracked for details/debugging only — NOT used in scoring.

    evidence_links : list of str
        URLs that a human reviewer can use to verify the match
        (DOI link, database record URL, etc.).

    raw_metadata : dict
        Full API response for this candidate, preserved for the
        ValidationResult metadata field and for debugging.

    match_details : str
        Free-text description from the validator about what was found.
        Included in the pipeline's human-readable details output.
        Example: ``"Found match (rank #1): 'Deep Learning for Citations'"``
    """

    source_name: str

    # Citation's own title (pipeline computes similarity from this)
    citation_title: str = ''

    # What the API returned
    matched_title: str = ''
    matched_authors: List[str] = field(default_factory=list)
    matched_year: str = ''

    # Signal strengths (computed by the validator using shared utils)
    # NOTE: title_similarity is intentionally absent — the pipeline
    # computes it internally using compute_title_score().
    author_overlap_count: int = 0
    author_overlap_matched: bool = False
    citation_author_count: int = 0
    year_matched: bool = False

    # Hard verification flags
    doi_verified: bool = False
    direct_id_verified: bool = False

    # Cross-check title (required when doi_verified or direct_id_verified)
    resolved_title: str = ''

    # Search rank (tracked for details, NOT used in scoring)
    result_rank: Optional[int] = None

    # Evidence for humans
    evidence_links: List[str] = field(default_factory=list)
    raw_metadata: Dict = field(default_factory=dict)
    match_details: str = ''


# ═══════════════════════════════════════════════════════════════════════════
# ScoringPipeline — Weighted-average scoring with negative evidence
# ═══════════════════════════════════════════════════════════════════════════

class ScoringPipeline:
    """
    Centralized, consistent scoring for all non-LLM validators (v3).

    Takes a MatchCandidate (raw signals) and produces a ValidationResult
    (status + confidence score + human-readable details).

    v3 scoring model
    ----------------
    All component scores are 0.0–1.0 floats.  Final score is a weighted
    average, mapped to 0–100 for display::

        raw = (W_TITLE × title + W_AUTHOR × author + W_YEAR × year)
              / (W_TITLE + W_AUTHOR + W_YEAR)

        final = raw × author_penalty
        display = round(final × 100)

    Component weights (sum = 5.0)::

        W_TITLE  = 3.0   (~60% of score)
        W_AUTHOR = 1.5   (~30% of score)
        W_YEAR   = 0.5   (~10% of score)

    Title is structurally dominant.  A perfect author + year match with
    zero title match scores: (3×0 + 1.5×1 + 0.5×1) / 5 = 0.40 → 40/100,
    well below the validation threshold.

    Search rank is NOT a scoring component.  It reflects query relevance,
    not identity confidence.  It is tracked in details for debugging.

    Status vocabulary (3 tiers)
    ---------------------------
    ============== ======= ================================================
    Status         Score   Meaning
    ============== ======= ================================================
    Validated      >= 80   High confidence the citation matches a real work
    Possible Match 60–79   Partial evidence; manual review recommended
    Not Validated  < 60    No credible match (displayed as score 0)
    ============== ======= ================================================

    Usage
    -----
    >>> from scoring import ScoringPipeline, MatchCandidate
    >>> candidate = MatchCandidate(
    ...     source_name="Crossref API",
    ...     citation_title="Attention is all you need",
    ...     matched_title="Attention Is All You Need",
    ...     author_overlap_matched=True,
    ...     author_overlap_count=6,
    ...     citation_author_count=8,
    ...     year_matched=True,
    ...     result_rank=1,
    ...     match_details="Found match (rank #1): 'Attention Is All You Need'",
    ...     evidence_links=["https://doi.org/10.5555/3295222.3295349"],
    ... )
    >>> result = ScoringPipeline.score(candidate)
    >>> result.status
    'Validated'
    >>> result.confidence_score
    95
    """

    # ── Component weights ────────────────────────────────────────────────

    W_TITLE  = 3.0    # ~60% of score
    W_AUTHOR = 1.5    # ~30% of score
    W_YEAR   = 0.5    # ~10% of score
    W_SUM    = W_TITLE + W_AUTHOR + W_YEAR  # 5.0

    # ── Thresholds ───────────────────────────────────────────────────────

    VALIDATED_THRESHOLD = 80    # display score >= 80 → "Validated"
    HARD_FLOOR          = 60    # display score < 60 → 0 / "Not Validated"
    TITLE_GATE          = 0.30  # title_score below this → skip full scoring
    DOI_CROSS_HIGH      = 0.70  # DOI cross-check: title confirms identifier
    DOI_CROSS_LOW       = 0.40  # DOI cross-check: partial match threshold

    # ── Status vocabulary (reduced from 5 to 3) ─────────────────────────

    STATUS_VALIDATED     = "Validated"
    STATUS_POSSIBLE      = "Possible Match"
    STATUS_NOT_VALIDATED = "Not Validated"

    # ── Public API ───────────────────────────────────────────────────────

    @classmethod
    def score(cls, candidate: MatchCandidate) -> ValidationResult:
        """
        Score a MatchCandidate and produce a ValidationResult.

        This is a pure function: same candidate in → same result out.
        No side effects, no state, no API calls.

        Parameters
        ----------
        candidate : MatchCandidate
            Raw match signals from a validator.

        Returns
        -------
        ValidationResult
            Status, confidence score, human-readable details, evidence
            links, and raw metadata — ready for the ValidatorManager.
        """
        # Late import to avoid circular dependency at module load time.
        # utils.py does not import scoring.py, so this is safe.
        from utils import compute_title_score, compute_author_score, compute_author_penalty

        # ── Phase 1: DOI/ID cross-check ──────────────────────────────
        if candidate.doi_verified or candidate.direct_id_verified:
            return cls._score_identifier(candidate, compute_title_score)

        # ── Phase 2: Compute component scores (all 0.0–1.0) ─────────
        title_score = compute_title_score(
            candidate.citation_title, candidate.matched_title
        )

        # Title gate: below this, the match is hopeless
        if title_score < cls.TITLE_GATE:
            return cls._not_validated(
                candidate,
                title_score=title_score,
                reason="Title similarity too low",
            )

        author_score = compute_author_score(
            candidate.author_overlap_count,
            candidate.citation_author_count,
        )
        year_score = 1.0 if candidate.year_matched else 0.0

        # ── Phase 3: Weighted average ────────────────────────────────
        raw_score = (
            cls.W_TITLE  * title_score +
            cls.W_AUTHOR * author_score +
            cls.W_YEAR   * year_score
        ) / cls.W_SUM

        # ── Phase 4: Negative evidence penalties ─────────────────────
        author_penalty = compute_author_penalty(
            candidate.citation_author_count,
            candidate.author_overlap_count,
        )
        final_score = raw_score * author_penalty

        # ── Phase 5: Map to display score and apply floor ────────────
        display_score = round(final_score * 100)

        components = {
            'title_score': title_score,
            'author_score': author_score,
            'year_score': year_score,
            'weighted_avg': raw_score,
            'author_penalty': author_penalty,
            'final': final_score,
            'display': display_score,
        }

        if display_score < cls.HARD_FLOOR:
            return cls._make_result(
                candidate, components,
                effective_score=0,
                status=cls.STATUS_NOT_VALIDATED,
            )
        elif display_score >= cls.VALIDATED_THRESHOLD:
            return cls._make_result(
                candidate, components,
                effective_score=display_score,
                status=cls.STATUS_VALIDATED,
            )
        else:
            return cls._make_result(
                candidate, components,
                effective_score=display_score,
                status=cls.STATUS_POSSIBLE,
            )

    # ── DOI / ID cross-check ─────────────────────────────────────────

    @classmethod
    def _score_identifier(cls, candidate: MatchCandidate, compute_title_score) -> ValidationResult:
        """
        Scores a candidate where a DOI or direct ID was verified.

        Cross-checks the resolved title against the citation title to
        catch mismatched or fabricated DOIs.

        Parameters
        ----------
        candidate : MatchCandidate
            Must have ``doi_verified=True`` or ``direct_id_verified=True``.
        compute_title_score : callable
            The ``compute_title_score`` function from utils (passed to
            avoid repeated imports).

        Returns
        -------
        ValidationResult
        """
        id_type = "DOI" if candidate.doi_verified else "Identifier"

        # No resolved_title → validator error (no legacy fallback)
        if not candidate.resolved_title:
            return ValidationResult(
                source_name=candidate.source_name,
                status=cls.STATUS_NOT_VALIDATED,
                confidence_score=0,
                details=(
                    f"{id_type} verified but no resolved title provided.\n"
                    f"  Validator must populate resolved_title for cross-check."
                ),
                evidence_links=list(candidate.evidence_links),
                metadata=dict(candidate.raw_metadata),
            )

        # Cross-check: does the resolved title match the citation?
        cross_sim = compute_title_score(
            candidate.citation_title, candidate.resolved_title
        )

        if cross_sim >= cls.DOI_CROSS_HIGH:
            # Title confirms the identifier — full confidence
            detail_parts = [
                f"{id_type} verified.  Title cross-check: {cross_sim:.2f}"
            ]
            if candidate.match_details:
                detail_parts.append(f"  {candidate.match_details}")
            return ValidationResult(
                source_name=candidate.source_name,
                status=cls.STATUS_VALIDATED,
                confidence_score=100,
                details='\n'.join(detail_parts),
                evidence_links=list(candidate.evidence_links),
                metadata=dict(candidate.raw_metadata),
            )

        elif cross_sim >= cls.DOI_CROSS_LOW:
            # Partial match — DOI resolves but title only partially matches
            return ValidationResult(
                source_name=candidate.source_name,
                status=cls.STATUS_NOT_VALIDATED,
                confidence_score=0,
                details=(
                    f"{id_type} verified but title only partially matches "
                    f"(cross-check: {cross_sim:.2f}).\n"
                    f"  Citation title: '{candidate.citation_title}'\n"
                    f"  Resolved title: '{candidate.resolved_title}'"
                ),
                evidence_links=list(candidate.evidence_links),
                metadata=dict(candidate.raw_metadata),
            )

        else:
            # Mismatch — DOI is wrong or citation is fabricated
            return ValidationResult(
                source_name=candidate.source_name,
                status=cls.STATUS_NOT_VALIDATED,
                confidence_score=0,
                details=(
                    f"{id_type} verified but title does NOT match resolved record "
                    f"(cross-check: {cross_sim:.2f}).\n"
                    f"  Citation title: '{candidate.citation_title}'\n"
                    f"  Resolved title: '{candidate.resolved_title}'"
                ),
                evidence_links=list(candidate.evidence_links),
                metadata=dict(candidate.raw_metadata),
            )

    # ── Result construction helpers ──────────────────────────────────

    @classmethod
    def _make_result(
        cls,
        candidate: MatchCandidate,
        components: dict,
        effective_score: int,
        status: str,
    ) -> ValidationResult:
        """Constructs a ValidationResult with a full component breakdown."""
        details = cls._format_details(candidate, components, status)
        return ValidationResult(
            source_name=candidate.source_name,
            status=status,
            confidence_score=effective_score,
            details=details,
            evidence_links=list(candidate.evidence_links),
            metadata=dict(candidate.raw_metadata),
        )

    @classmethod
    def _not_validated(
        cls,
        candidate: MatchCandidate,
        title_score: float = 0.0,
        reason: str = '',
    ) -> ValidationResult:
        """
        Shorthand for early-exit Not Validated results (e.g. title gate).
        """
        lines = []
        if candidate.match_details:
            lines.append(candidate.match_details)
        lines.append(f"  Title score: {title_score:.2f} — {reason}")
        if candidate.result_rank is not None:
            lines.append(f"  Search rank: #{candidate.result_rank}")

        return ValidationResult(
            source_name=candidate.source_name,
            status=cls.STATUS_NOT_VALIDATED,
            confidence_score=0,
            details='\n'.join(lines),
            evidence_links=list(candidate.evidence_links),
            metadata=dict(candidate.raw_metadata),
        )

    # ── Details formatting ───────────────────────────────────────────

    @classmethod
    def _format_details(
        cls,
        candidate: MatchCandidate,
        components: dict,
        status: str,
    ) -> str:
        """
        Builds the human-readable details string showing all scoring
        components.

        Format::

            Found match (rank #1): 'Attention Is All You Need'
              Title score:  0.98
              Author score: 0.83 (6/8 overlapping)
              Year score:   1.0
              Weighted avg: 0.95 → display 95
              Penalties:    none
              Status:       Validated
        """
        lines = []

        # Validator's own description first
        if candidate.match_details:
            lines.append(candidate.match_details)

        # Component breakdown
        ts = components['title_score']
        aus = components['author_score']
        ys = components['year_score']

        lines.append(f"  Title score:  {ts:.2f}")

        if candidate.citation_author_count > 0:
            lines.append(
                f"  Author score: {aus:.2f} "
                f"({candidate.author_overlap_count}/{candidate.citation_author_count} overlapping)"
            )
        else:
            lines.append(f"  Author score: {aus:.2f}")

        lines.append(f"  Year score:   {ys:.1f}")

        # Aggregate
        raw = components['weighted_avg']
        display = components['display']
        lines.append(f"  Weighted avg: {raw:.3f} -> display {display}")

        # Penalties
        penalty = components['author_penalty']
        if penalty < 1.0:
            lines.append(f"  Penalties:    author anti-overlap x{penalty:.2f}")
        else:
            lines.append(f"  Penalties:    none")

        if candidate.result_rank is not None:
            lines.append(f"  Search rank:  #{candidate.result_rank}")

        lines.append(f"  Status:       {status}")

        return '\n'.join(lines)

    # ── Convenience: check if a result meets a threshold ─────────────

    @classmethod
    def is_validated(cls, result: ValidationResult) -> bool:
        """True if the result's status is 'Validated'."""
        return result.status == cls.STATUS_VALIDATED

    @classmethod
    def is_at_least_possible(cls, result: ValidationResult) -> bool:
        """True if the result's status is 'Validated' or 'Possible Match'."""
        return result.status in (cls.STATUS_VALIDATED, cls.STATUS_POSSIBLE)