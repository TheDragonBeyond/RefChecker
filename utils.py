# utils.py
# utilities application file for the Citation Validator.
# Contains helper functions for text processing, normalization, and data comparison.

import re
import string
from rapidfuzz import fuzz, distance
from unidecode import unidecode
from typing import Dict, Optional, Tuple, List

def clean_citation_data(citation_data):
    """Cleans specific fields in a citation dictionary."""
    cleaned_data = {}
    for field, value in citation_data.items():
        if value is None:
            value = ''

        # FIX: Ensure value is a string before stripping
        # This handles integers (like Citation Number) gracefully
        if not isinstance(value, str):
            value = str(value)

        cleaned_value = value.strip()
        field_lower = field.lower()

        if 'publication title' in field_lower or 'journal' in field_lower:
            cleaned_value = re.sub(r'^in:\s+', '', cleaned_value, flags=re.IGNORECASE)
        if 'pages' in field_lower:
            match = re.search(r'(\d+)\s*-\s*(\d+)', cleaned_value)
            if match:
                cleaned_value = f"{match.group(1)}-{match.group(2)}"
            else:
                match = re.search(r'\d+', cleaned_value)
                if match:
                    cleaned_value = match.group(0)
        if 'year' in field_lower or 'date' in field_lower:
            # Extract just the year if present
            year_match = re.search(r'\b(19|20)\d{2}\b', cleaned_value)
            if year_match:
                cleaned_value = year_match.group(0)

        cleaned_data[field] = cleaned_value

    return cleaned_data

def normalize_text(text):
    """Normalize text for comparison by removing punctuation and converting to lowercase."""
    if not text:
        return ""
    return text.lower().translate(str.maketrans('', '', string.punctuation))


def normalize_for_api(text):
    """
    Normalize text for API queries - lighter than full normalization.
    Removes trailing punctuation, extra whitespace, but keeps hyphens and meaningful punctuation.
    """
    if not text:
        return ""

    # Strip whitespace
    text = text.strip()

    # Remove trailing periods and commas
    text = text.rstrip('.').rstrip(',')

    # Normalize internal whitespace (replace multiple spaces with single space)
    text = re.sub(r'\s+', ' ', text)

    # Convert to lowercase for better matching
    text = text.lower()

    return text


# ===========================================================================
# v3 Title Scoring — Multi-metric blend with distinctive-token penalty
# ===========================================================================

# Common academic/English terms with low identity signal.  Titles that
# only share these words should NOT be treated as similar.
ACADEMIC_STOPWORDS = frozenset({
    # Standard English function words
    "a", "an", "the", "of", "for", "in", "on", "to", "and", "with", "by",
    "from", "is", "are", "as", "at", "or", "its", "their", "this", "that",
    "these", "those", "it", "we", "our", "can", "how", "what", "when",
    "where", "which", "who", "through", "between", "into", "over", "under",
    # Common academic / ML terms (high frequency across papers)
    "learning", "deep", "neural", "network", "networks", "model", "models",
    "approach", "method", "methods", "using", "based", "towards", "via",
    "novel", "new", "improved", "analysis", "study", "framework", "system",
    "data", "large", "language", "training", "evaluation", "performance",
    "task", "tasks", "representation", "representations", "information",
    # Preprint-specific
    "arxiv", "preprint",
})


def normalize_title(title: str) -> str:
    """
    Normalize a title for fuzzy comparison.

    Steps: convert to string → unidecode (accent removal) → lowercase →
    strip all punctuation → collapse whitespace → strip.

    This is the standard pre-processing step for ``compute_title_score()``
    and ``compute_distinctive_token_penalty()``.  It is stricter than
    ``normalize_for_api()`` (which preserves hyphens for query formatting).

    Parameters
    ----------
    title : str
        Raw title string.

    Returns
    -------
    str
        Normalized title, or empty string if input is empty/None.
    """
    if not title:
        return ""

    text = str(title)
    text = unidecode(text)
    text = text.lower()
    text = text.translate(str.maketrans('', '', string.punctuation))
    text = ' '.join(text.split())
    return text.strip()


def compute_distinctive_token_penalty(title_a: str, title_b: str) -> float:
    """
    Penalizes title pairs that share common vocabulary but diverge on
    identity-carrying (distinctive) tokens.

    Both inputs should already be normalized via ``normalize_title()``.

    Returns a multiplier in [0.5, 1.0]:

    ========= =======================================================
    Overlap   Penalty
    ========= =======================================================
    >= 0.7    1.0   — good overlap, no penalty
    >= 0.4    0.75  — moderate penalty (some distinctive terms missing)
    < 0.4     0.5   — heavy penalty (most distinctive terms diverge)
    ========= =======================================================

    "Overlap" is the *minimum* of the two directional ratios: what
    fraction of A's distinctive tokens appear in B, and vice versa.
    Using the minimum means poor overlap in *either* direction triggers
    the penalty.

    Parameters
    ----------
    title_a, title_b : str
        Pre-normalized title strings (output of ``normalize_title()``).

    Returns
    -------
    float
        Penalty multiplier to apply to the raw title blend score.
    """
    tokens_a = set(title_a.split()) - ACADEMIC_STOPWORDS
    tokens_b = set(title_b.split()) - ACADEMIC_STOPWORDS

    if not tokens_a and not tokens_b:
        return 1.0  # No distinctive tokens to compare

    # Bidirectional overlap: what fraction of A's distinctive tokens
    # appear in B, and vice versa?
    intersection = tokens_a & tokens_b

    if tokens_a:
        a_in_b = len(intersection) / len(tokens_a)
    else:
        a_in_b = 1.0

    if tokens_b:
        b_in_a = len(intersection) / len(tokens_b)
    else:
        b_in_a = 1.0

    # Use the minimum — poor overlap in either direction is suspicious
    overlap = min(a_in_b, b_in_a)

    if overlap >= 0.7:
        return 1.0
    elif overlap >= 0.4:
        return 0.75
    else:
        return 0.5


def compute_title_score(citation_title: str, matched_title: str) -> float:
    """
    Multi-metric title similarity with distinctive-token penalty.

    Replaces ``calculate_title_similarity()`` as the pipeline's title
    comparison function.  Uses a blend of three RapidFuzz metrics to
    capture different failure modes, then applies a distinctive-token
    penalty to discount shared-vocabulary inflation.

    Blend formula::

        raw = 0.4 * token_set_ratio + 0.4 * token_sort_ratio + 0.2 * WRatio

    Each metric captures a different failure mode:

    - ``token_set_ratio``: Shared vocabulary regardless of order or extra
      words.  Generous — inflated by domain vocabulary.
    - ``token_sort_ratio``: Structural similarity after alphabetical token
      sort.  Stricter — penalizes extra/missing words.
    - ``WRatio``: Adaptive metric that picks the best of ratio/partial_ratio
      based on string length difference.  Good general tiebreaker.

    The distinctive-token penalty (see ``compute_distinctive_token_penalty``)
    is applied as a multiplier on the raw blend, catching cases where the
    fuzzy metrics are fooled by shared academic vocabulary.

    Parameters
    ----------
    citation_title : str
        The title from the original citation.
    matched_title : str
        The title returned by the API for a candidate match.

    Returns
    -------
    float
        0.0 to 1.0
    """
    a = normalize_title(citation_title)
    b = normalize_title(matched_title)

    if not a or not b:
        return 0.0

    # Three complementary metrics (each returns 0–100)
    token_set  = fuzz.token_set_ratio(a, b) / 100.0
    token_sort = fuzz.token_sort_ratio(a, b) / 100.0
    wratio     = fuzz.WRatio(a, b) / 100.0

    # Blend: token_set and token_sort equally weighted, WRatio as tiebreaker
    raw = 0.4 * token_set + 0.4 * token_sort + 0.2 * wratio

    # Distinctive-token penalty
    penalty = compute_distinctive_token_penalty(a, b)

    return raw * penalty


# ===========================================================================
# v3 Author Scoring — Normalized 0–1 component + anti-overlap penalty
# ===========================================================================


def compute_author_score(overlap_count: int, citation_author_count: int) -> float:
    """
    Converts raw author overlap counts into a 0.0–1.0 score for the
    weighted-average pipeline.

    Scaling by match count:

    =============== =====================================================
    Overlap count   Treatment
    =============== =====================================================
    0               0.0
    1               ``overlap_ratio * 0.9`` — slight discount for single
    2               ``overlap_ratio * 1.05`` — mild boost, capped at 1.0
    3+              ``overlap_ratio * 1.1``  — strong boost, capped at 1.0
    =============== =====================================================

    Where ``overlap_ratio = overlap_count / max(citation_author_count, 1)``.

    Parameters
    ----------
    overlap_count : int
        Number of citation authors whose surnames matched API authors
        (from ``check_author_overlap``).
    citation_author_count : int
        Total number of authors listed in the original citation.

    Returns
    -------
    float
        0.0 to 1.0
    """
    if overlap_count <= 0:
        return 0.0

    total = max(citation_author_count, 1)
    overlap_ratio = min(overlap_count / total, 1.0)

    if overlap_count >= 3:
        return min(1.0, overlap_ratio * 1.1)
    elif overlap_count == 2:
        return min(1.0, overlap_ratio * 1.05)
    else:
        # Single author match — moderate confidence
        return overlap_ratio * 0.9


def compute_author_penalty(citation_author_count: int, overlap_count: int) -> float:
    """
    Post-hoc penalty multiplier for suspicious author overlap patterns.

    Applied to the final weighted-average score (not the author component)
    to penalize cases where a match has zero or near-zero author overlap
    despite the citation listing many authors.  This is a strong signal
    that the candidate is a different paper on the same topic.

    ========================= ==========================================
    Condition                 Penalty
    ========================= ==========================================
    < 3 citation authors      1.0  — too few to draw conclusions
    0 overlap, 3+ authors     0.7  — zero overlap is a red flag
    <= 20% overlap, 3+ authors 0.85 — very low overlap is suspicious
    Otherwise                 1.0  — no penalty
    ========================= ==========================================

    Parameters
    ----------
    citation_author_count : int
        Total number of authors listed in the original citation.
    overlap_count : int
        Number of citation authors whose surnames matched API authors.

    Returns
    -------
    float
        Penalty multiplier in [0.7, 1.0].
    """
    if citation_author_count < 3:
        return 1.0  # Too few authors to draw conclusions

    if overlap_count == 0:
        return 0.7  # Zero overlap with 3+ authors is a red flag

    overlap_ratio = overlap_count / citation_author_count
    if overlap_ratio <= 0.2:
        return 0.85  # Very low overlap

    return 1.0


def are_titles_similar(title1, title2, threshold=0.7, substring_boost=False):
    """
    Checks if two titles are similar enough to be considered a match.
    Uses RapidFuzz optimized Levenshtein (Token Sort Ratio).

    If *substring_boost* is True, containment of one title within the
    other is treated as a perfect match (1.0) before fuzzy comparison.
    See ``calculate_title_similarity`` for details.
    """
    similarity = calculate_title_similarity(title1, title2, substring_boost=substring_boost)
    return similarity >= threshold


def calculate_title_similarity(title1, title2, substring_boost=False):
    """
    Calculate the similarity score between two titles using RapidFuzz.

    Uses token_sort_ratio to handle word reordering (similar to the previous
    set-based Jaccard approach) while utilizing Levenshtein for fuzzy string matching.

    Parameters
    ----------
    title1, title2 : str
        Titles to compare.
    substring_boost : bool
        When True, if either normalized title is fully contained within the
        other, return 1.0 immediately.  This is useful for sources where
        result titles frequently include subtitles, e.g.:

            search:  "Introduction to Algorithms"
            result:  "Introduction to Algorithms: A Modern Approach"

        The contained title must be at least 3 words long to prevent
        short generic fragments from trivially matching everything.

        Validators that benefit: Google Books, Open Library, WorldCat.

    Returns
    -------
    float
        0.0 to 1.0

    .. deprecated::
        This function uses a single metric (token_sort_ratio) which is
        vulnerable to shared-vocabulary inflation in academic titles.
        New pipeline code should use ``compute_title_score()`` instead,
        which applies a multi-metric blend with a distinctive-token
        penalty.  This function is retained for non-pipeline callers
        (e.g. ``are_titles_similar``).
    """
    if not title1 or not title2:
        return 0.0

    # normalization is handled internally by RapidFuzz mostly,
    # but basic lowercase helps consistency
    t1 = str(title1).lower()
    t2 = str(title2).lower()

    # Substring containment check (opt-in)
    if substring_boost:
        # Strip punctuation so "Patterns: Elements" matches "Patterns Elements"
        t1_clean = t1.translate(str.maketrans('', '', string.punctuation)).strip()
        t2_clean = t2.translate(str.maketrans('', '', string.punctuation)).strip()
        # Collapse whitespace after punctuation removal
        t1_clean = ' '.join(t1_clean.split())
        t2_clean = ' '.join(t2_clean.split())

        shorter, longer = (t1_clean, t2_clean) if len(t1_clean) <= len(t2_clean) else (t2_clean, t1_clean)
        # Require at least 3 words — prevents "Clean Code" or "The Art"
        # from trivially matching anything containing those fragments
        if len(shorter.split()) >= 3 and shorter in longer:
            return 1.0

    # token_sort_ratio: Sorts words alphabetically then applies Levenshtein.
    # This handles "Garnet: A System" vs "A System: Garnet" very well.
    score = fuzz.token_sort_ratio(t1, t2)

    # Normalize 0-100 score to 0.0-1.0 to match existing config thresholds
    return score / 100.0



def check_author_overlap(citation_authors, api_authors, author_format="structured"):
    """
    Check if there's significant overlap between citation authors and API authors
    using Jaro-Winkler distance for name comparison.

    Parameters
    ----------
    citation_authors : str
        Semicolon- or comma-separated author string from the citation CSV.
    api_authors : list
        Author data from the API response.
    author_format : str
        How the API represents authors:
            "structured" — list of dicts with a 'family' key (Crossref).
            "names"      — list of plain name strings (DBLP, PubMed, Open Library).
    """
    if not citation_authors or not api_authors:
        return False, 0

    # Parse citation authors — split on semicolons (the canonical CSV separator)
    # and bare "and", but NOT commas (which are part of "Surname, Initials" format).
    citation_author_list = re.split(r';\s*(?:and\s+)?|\band\b', citation_authors)
    citation_lastnames = set()

    for author in citation_author_list:
        author = author.strip()
        if not author:
            continue
        # "Surname, Initials" format (canonical: "Smith, J.")
        if ',' in author:
            lastname = author.split(',')[0].strip().lower()
        else:
            # "First Last" format
            lastname = author.split()[-1].strip().lower()
        if lastname:
            citation_lastnames.add(lastname)

    # Extract API author last names
    api_lastnames = set()
    if author_format == "surname_first":
        # PubMed-style: "Poitras VJ", "Gray CE" — surname is the FIRST token
        for author in api_authors:
            if isinstance(author, str):
                parts = author.strip().split()
                if parts:
                    lastname = parts[0].strip().lower()
                    if lastname:
                        api_lastnames.add(lastname)
    elif author_format == "names":
        for author in api_authors:
            if isinstance(author, str):
                lastname = author.split()[-1].strip().lower()
                if lastname:
                    api_lastnames.add(lastname)
    else:
        for author in api_authors:
            lastname = author.get('family', '').strip().lower()
            if lastname:
                api_lastnames.add(lastname)

    # Use Jaro-Winkler to count matches
    matches = 0
    JW_THRESHOLD = 0.9

    matched_api_names = set()

    for cite_name in citation_lastnames:
        best_match_score = 0.0
        best_match_name = None

        for api_name in api_lastnames:
            score = distance.JaroWinkler.similarity(cite_name, api_name)

            if score > best_match_score:
                best_match_score = score
                best_match_name = api_name

        if best_match_score >= JW_THRESHOLD:
            if best_match_name not in matched_api_names:
                matches += 1
                matched_api_names.add(best_match_name)

    if len(citation_lastnames) == 1:
        return matches >= 1, matches
    else:
        return matches >= 1, matches


def check_year_match(citation_year, item, tolerance=1, is_dblp=False):
    """
    Check if publication years match within tolerance.

    .. deprecated::
        This function bundles API-specific year *extraction* with year
        *comparison*.  New code should use ``years_match()`` for comparison
        and a per-validator static helper for extraction.  This function
        remains for validators that have not yet migrated.
    """
    if not citation_year:
        return True, "N/A"

    try:
        cite_year = int(citation_year)
    except (ValueError, TypeError):
        return True, "N/A"

    api_year = None
    if is_dblp:
        api_year = item.get('year')
        if api_year:
            try:
                api_year = int(api_year)
            except (ValueError, TypeError):
                api_year = None
    else:
        # Crossref format
        if 'published' in item:
            date_parts = item['published'].get('date-parts', [[]])[0]
            if date_parts:
                api_year = date_parts[0]
        elif 'published-print' in item:
            date_parts = item['published-print'].get('date-parts', [[]])[0]
            if date_parts:
                api_year = date_parts[0]
        elif 'published-online' in item:
            date_parts = item['published-online'].get('date-parts', [[]])[0]
            if date_parts:
                api_year = date_parts[0]

    if api_year is None:
        return True, "N/A"

    year_diff = abs(cite_year - api_year)
    return year_diff <= tolerance, str(api_year)


# ===========================================================================
# years_match — Pure year comparison, decoupled from API response formats
# ===========================================================================


def years_match(year_a, year_b, tolerance: int = 1) -> bool:
    """
    Compares two year values and returns True if they fall within *tolerance*.

    Accepts str, int, float, or None.  Gracefully returns True when either
    side is missing or unparseable — absence of evidence should never
    penalise a match.

    This replaces the *comparison* half of check_year_match(), which
    previously bundled API-specific extraction with the comparison itself.
    The old function still works unchanged; validators migrate at their
    own pace.

    Parameters
    ----------
    year_a, year_b : str | int | float | None
        Year values in any reasonable format.
    tolerance : int
        Maximum allowed difference (inclusive). Default 1.

    Examples
    --------
    >>> years_match("2019", "2020")          # within tolerance=1
    True
    >>> years_match(2019, 2021)              # outside tolerance=1
    False
    >>> years_match("2019", None)            # missing → non-penalising
    True
    >>> years_match("circa 2019", "2019")    # extracts embedded year
    True
    """
    ya = _parse_year(year_a)
    yb = _parse_year(year_b)

    # If either side can't be determined, treat as non-disqualifying
    if ya is None or yb is None:
        return True

    return abs(ya - yb) <= tolerance


def _parse_year(value) -> 'Optional[int]':
    """
    Extracts a 4-digit year (1800–2099) from a value that may be an int,
    a clean string like ``"2019"``, or a messy string like
    ``"Published 2019-03-15"`` or ``"2019 Mar"``.

    Returns None if no valid year can be extracted.
    """
    if value is None:
        return None

    if isinstance(value, int):
        return value if 1800 <= value <= 2099 else None

    if isinstance(value, float):
        iv = int(value)
        return iv if 1800 <= iv <= 2099 else None

    text = str(value).strip()
    if not text:
        return None

    # Fast path: value is already just a year string
    if text.isdigit() and len(text) == 4:
        y = int(text)
        return y if 1800 <= y <= 2099 else None

    # Slow path: extract first 4-digit year from longer text
    match = re.search(r'\b(1[89]\d{2}|20\d{2})\b', text)
    if match:
        return int(match.group(1))

    return None


# ===========================================================================
# CitationAccessor — Uniform field access across all validators
# ===========================================================================

class CitationAccessor:
    """
    Wraps a citation_data dict (one row from the extraction CSV) and provides
    normalised, case-insensitive access to every field that validators need.

    Eliminates the per-validator boilerplate of trying multiple key spellings::

        # Before (repeated in every validator, differently each time):
        title = data.get('Article Title') or data.get('Title') or data.get('title')
        author = data.get('Author') or data.get('Authors') or data.get('authors')

        # After:
        acc = CitationAccessor(data)
        title  = acc.title
        author = acc.authors

    The canonical field names come from ``config.CSV_HEADERS``:

        Citation Number, Type, Authors, Article Title, Publication Title,
        Series, Editor, Volume, Issue, Publisher, Publication Location,
        Year, Month, Day, Pages, Edition, Institution, DOI, ISBN, ISSN,
        URL, Date Accessed

    But real-world data also arrives with alternative keys (lowercase,
    underscored, singular vs plural).  CitationAccessor handles all of these
    through a normalisation + alias table.

    Key design decisions
    --------------------
    * **title vs container_title**: ``title`` returns the *work* title
      (Article Title), ``container_title`` returns the *venue*
      (Publication Title / Journal).  ``best_title(prefer_container=...)``
      lets validators choose which one to search.

    * **First-writer-wins**: When the raw dict has both ``"Article Title"``
      and ``"Title"`` mapped to the same bucket, the first one encountered
      wins.  Since CSV_HEADERS puts ``"Article Title"`` before bare
      ``"Title"``, this does the right thing for standard extraction output.

    * **Non-destructive**: The original dict is preserved at ``.raw`` and
      can be passed to validators that haven't migrated yet.
    """

    # ── Class-level alias table ──────────────────────────────────────────
    #
    # Maps *normalised* key names → canonical bucket name.
    # Normalised means: lowercase, whitespace/hyphens → underscores.

    _FIELD_ALIASES = {
        # --- Title of the work (article / paper / chapter) ---
        'article_title':    'article_title',
        'articletitle':     'article_title',
        'title':            'article_title',

        # --- Container title (journal / book / proceedings) ---
        'publication_title': 'publication_title',
        'publicationtitle':  'publication_title',
        'journal':           'publication_title',
        'journal_name':      'publication_title',
        'booktitle':         'publication_title',
        'book_title':        'publication_title',
        'venue':             'publication_title',

        # --- Authors ---
        'authors':  'authors',
        'author':   'authors',

        # --- Year / date ---
        'year':     'year',
        'date':     'year',
        'pub_year': 'year',

        # --- DOI ---
        'doi': 'doi',

        # --- Type ---
        'type':           'type',
        'citation_type':  'type',
        'document_type':  'type',

        # --- Other identifiers ---
        'isbn':      'isbn',
        'issn':      'issn',
        'url':       'url',
        'pmid':      'pmid',
        'arxiv_id':  'arxiv_id',
        'arxivid':   'arxiv_id',

        # --- Numeric / bibliographic ---
        'citation_number':      'citation_number',
        'citationnumber':       'citation_number',
        'volume':               'volume',
        'issue':                'issue',
        'pages':                'pages',
        'edition':              'edition',
        'publisher':            'publisher',
        'publication_location': 'publication_location',
        'publicationlocation':  'publication_location',
        'series':               'series',
        'editor':               'editor',
        'institution':          'institution',
        'month':                'month',
        'day':                  'day',
        'date_accessed':        'date_accessed',
        'dateaccessed':         'date_accessed',
    }

    def __init__(self, data: dict):
        self._raw = data

        # Canonical bucket store: bucket_name → first non-empty value
        self._buckets = {}

        # Normalised key → value (for generic .get() fallback)
        self._norm_map = {}

        for original_key, raw_value in data.items():
            value = str(raw_value).strip() if raw_value is not None else ''
            norm_key = self._normalise_key(original_key)
            self._norm_map[norm_key] = value

            bucket = self._FIELD_ALIASES.get(norm_key)
            if bucket and value:
                # First writer wins (setdefault keeps earliest)
                self._buckets.setdefault(bucket, value)

    # ── Core properties ──────────────────────────────────────────────────

    @property
    def title(self) -> str:
        """
        The title of the *work itself* (article, paper, chapter).
        Resolution: Article Title → bare Title → (empty).
        """
        return self._buckets.get('article_title', '')

    @property
    def container_title(self) -> str:
        """
        The title of the *container* (journal, book, proceedings).
        Resolution: Publication Title → Journal → Venue → (empty).
        """
        return self._buckets.get('publication_title', '')

    def best_title(self, prefer_container: bool = False) -> str:
        """
        Returns the most useful title for searching.

        * ``prefer_container=False`` (default): article title, falling back
          to container.  Use for: Crossref, DBLP, PubMed, ArXiv, JSTOR.
        * ``prefer_container=True``: container title, falling back to
          article.  Use for: Google Books, Open Library (books), WorldCat.
        """
        if prefer_container:
            return self.container_title or self.title
        return self.title or self.container_title

    @property
    def authors(self) -> str:
        """Raw author string exactly as extracted (semicolon-separated)."""
        return self._buckets.get('authors', '')

    @property
    def first_author_surname(self) -> str:
        """
        Best-effort surname extraction of the first listed author.

        Handles common formats::

            "Smith, J.; Doe, A."    → "Smith"
            "J. Smith; A. Doe"      → "Smith"
            "Smith et al."          → "Smith"
            "Aristotle"             → "Aristotle"
        """
        raw = self.authors
        if not raw:
            return ''

        # Split on semicolons (canonical separator), take first
        first_author = re.split(r'[;]', raw)[0].strip()

        # Strip "et al."
        first_author = re.sub(
            r'\bet\s+al\.?\s*$', '', first_author, flags=re.IGNORECASE
        ).strip()

        if not first_author:
            return ''

        # "Last, First" → surname before comma
        if ',' in first_author:
            return first_author.split(',')[0].strip()

        # "First Last" → surname is final token
        parts = first_author.split()
        return parts[-1].strip() if parts else first_author

    @property
    def author_count(self) -> int:
        """
        Number of authors parsed from the authors string.

        Uses semicolons as the canonical separator (matching the extraction
        CSV format).  Filters out empty entries from trailing separators.

        Validators use this to populate ``MatchCandidate.citation_author_count``
        for the v3 anti-overlap penalty.
        """
        if not self.authors:
            return 0
        return len([a for a in re.split(r'[;]', self.authors) if a.strip()])

    @property
    def year(self) -> str:
        """
        Cleaned 4-digit year string, or ``''`` if unavailable.

        Applies ``_parse_year()`` so validators can compare directly
        without worrying about ``"2019-03-15"`` or ``"Published 2019"``.
        """
        raw = self._buckets.get('year', '')
        parsed = _parse_year(raw)
        return str(parsed) if parsed is not None else ''

    @property
    def year_raw(self) -> str:
        """The unprocessed year/date string, for validators that need it."""
        return self._buckets.get('year', '')

    @property
    def doi(self) -> str:
        """
        Cleaned DOI (e.g. ``'10.1000/xyz123'``).

        Strips URL prefixes and ``doi:`` notation.  Also checks the URL
        field as a fallback (some datasets put DOIs there).
        """
        raw = self._buckets.get('doi', '')
        if not raw:
            url = self._buckets.get('url', '')
            if url and ('doi.org/' in url or 'dx.doi.org/' in url):
                raw = url

        return self._clean_doi(raw)

    @property
    def url(self) -> str:
        """The URL field value."""
        return self._buckets.get('url', '')

    @property
    def citation_type(self) -> str:
        """Lowercased type string (e.g. ``'journal article'``, ``'book'``)."""
        return self._buckets.get('type', '').lower()

    @property
    def citation_number(self) -> str:
        return self._buckets.get('citation_number', '')

    @property
    def isbn(self) -> str:
        return self._buckets.get('isbn', '')

    @property
    def issn(self) -> str:
        return self._buckets.get('issn', '')

    @property
    def volume(self) -> str:
        return self._buckets.get('volume', '')

    @property
    def issue(self) -> str:
        return self._buckets.get('issue', '')

    @property
    def pages(self) -> str:
        return self._buckets.get('pages', '')

    @property
    def publisher(self) -> str:
        return self._buckets.get('publisher', '')

    @property
    def institution(self) -> str:
        return self._buckets.get('institution', '')

    @property
    def editor(self) -> str:
        return self._buckets.get('editor', '')

    @property
    def series(self) -> str:
        return self._buckets.get('series', '')

    # ── Special identifier properties ────────────────────────────────────

    @property
    def pmid(self) -> str:
        """PubMed ID if present."""
        return self._buckets.get('pmid', '')

    @property
    def arxiv_id(self) -> str:
        """
        ArXiv identifier.  Checks the explicit field first, then attempts
        extraction from DOI, URL, or ``arXiv:YYMM.NNNNN`` notation.

        Validates that the extracted ID matches arXiv's identifier format
        before returning it, to avoid passing arbitrary strings to the API.

        Supported formats::

            arXiv:2410.11782                        (colon notation in URL fields)
            arXiv.2202.12345                        (dot notation in DOI fields)
            https://arxiv.org/abs/2410.11782        (full URL)
            https://arxiv.org/pdf/2410.11782        (PDF URL)
            10.48550/arXiv.2202.12345               (DOI with embedded ID)
            arXiv:hep-th/9901001                    (old-style identifier)
        """
        explicit = self._buckets.get('arxiv_id', '')
        if explicit and self._is_valid_arxiv_id(explicit):
            return explicit

        # Check DOI and URL fields for embedded arXiv identifiers
        for field in ('doi', 'url'):
            raw = self._buckets.get(field, '')
            if not raw or 'arxiv' not in raw.lower():
                continue

            extracted = self._extract_arxiv_id(raw)
            if extracted:
                return extracted

        return ''

    @staticmethod
    def _extract_arxiv_id(text: str) -> str:
        """
        Extracts a validated arXiv ID from various notation formats.
        Returns the bare ID (e.g. '2410.11782') or empty string.
        """
        if not text:
            return ''

        # Full URL: arxiv.org/abs/2410.11782 or /pdf/ variant (new-style)
        match = re.search(
            r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)',
            text, re.IGNORECASE,
        )
        if match:
            return match.group(1)

        # Full URL old-style: arxiv.org/abs/hep-th/9901001
        match = re.search(
            r'arxiv\.org/(?:abs|pdf)/([-a-z]+/\d{7}(?:v\d+)?)',
            text, re.IGNORECASE,
        )
        if match:
            return match.group(1)

        # Short notation: arXiv:2410.11782 or DOI-embedded arXiv.2202.12345
        match = re.search(
            r'arxiv[.:]\s*(\d{4}\.\d{4,5}(?:v\d+)?)',
            text, re.IGNORECASE,
        )
        if match:
            return match.group(1)

        # Short notation old-style: arXiv:hep-th/9901001
        match = re.search(
            r'arxiv[.:]\s*([-a-z]+/\d{7}(?:v\d+)?)',
            text, re.IGNORECASE,
        )
        if match:
            return match.group(1)

        return ''

    @staticmethod
    def _is_valid_arxiv_id(text: str) -> bool:
        """
        Returns True if text matches arXiv's real identifier patterns.
        New-style: 2410.11782, 2410.11782v2
        Old-style: hep-th/9901001
        """
        if not text:
            return False
        text = text.strip()
        if re.fullmatch(r'\d{4}\.\d{4,5}(?:v\d+)?', text):
            return True
        if re.fullmatch(r'[-a-z]+/\d{7}(?:v\d+)?', text):
            return True
        return False

    # ── Generic access ───────────────────────────────────────────────────

    def get(self, field_name: str, default: str = '') -> str:
        """
        Generic field access by name (case-insensitive, space/underscore
        agnostic).  Falls back to *default* if the field is missing or empty.
        """
        norm = self._normalise_key(field_name)

        # Try the canonical bucket first
        bucket = self._FIELD_ALIASES.get(norm)
        if bucket:
            val = self._buckets.get(bucket, '')
            if val:
                return val

        # Fall back to direct normalised lookup
        val = self._norm_map.get(norm, '')
        return val if val else default

    @property
    def raw(self) -> dict:
        """The original unmodified citation_data dict."""
        return self._raw

    def has(self, field_name: str) -> bool:
        """Returns True if the field has a non-empty value."""
        return bool(self.get(field_name))

    def to_search_dict(self) -> dict:
        """
        Returns a clean dict of the most-used fields, keyed by canonical
        CSV header names.  Useful during gradual migration when a validator
        still expects a plain dict internally.

        Only populated fields are included.
        """
        result = {}
        if self.title:            result['Article Title'] = self.title
        if self.container_title:  result['Publication Title'] = self.container_title
        if self.authors:          result['Authors'] = self.authors
        if self.year:             result['Year'] = self.year
        if self.doi:              result['DOI'] = self.doi
        if self.isbn:             result['ISBN'] = self.isbn
        if self.url:              result['URL'] = self.url
        if self.citation_type:    result['Type'] = self.citation_type
        if self.citation_number:  result['Citation Number'] = self.citation_number
        return result

    # ── Private helpers ──────────────────────────────────────────────────

    @staticmethod
    def _normalise_key(key: str) -> str:
        """
        ``"Article Title"`` → ``"article_title"``
        ``"article-title"`` → ``"article_title"``
        ``"ArticleTitle"``  → ``"articletitle"``
        """
        k = str(key).strip().lower()
        k = re.sub(r'[\s\-]+', '_', k)
        k = re.sub(r'_+', '_', k)
        return k.strip('_')

    @staticmethod
    def _clean_doi(raw: str) -> str:
        """
        ``"https://doi.org/10.1000/xyz"`` → ``"10.1000/xyz"``
        ``"doi:10.1000/xyz"``             → ``"10.1000/xyz"``
        """
        if not raw:
            return ''

        text = raw.strip()

        for prefix in ['https://doi.org/', 'http://doi.org/',
                        'https://dx.doi.org/', 'http://dx.doi.org/',
                        'doi:', 'DOI:']:
            if text.startswith(prefix):
                text = text[len(prefix):]
                break

        match = re.search(r'(10\.\d{4,}/\S+)', text)
        if match:
            return match.group(1).rstrip('.,;:')

        return ''

    def __repr__(self) -> str:
        parts = []
        if self.title:   parts.append(f'title={self.title!r}')
        if self.authors: parts.append(f'authors={self.authors!r}')
        if self.year:    parts.append(f'year={self.year!r}')
        if self.doi:     parts.append(f'doi={self.doi!r}')
        return f'CitationAccessor({", ".join(parts)})'