# grobid_tei_parser.py
#
# Parses GROBID TEI-XML output into flat citation dictionaries
# matching the Extraction Blueprint CSV schema (22 columns).
#
# This module is deliberately dependency-free beyond the Python
# standard library (xml.etree.ElementTree).  It is separated from
# the extractor plugin so it can be tested and iterated independently.
#
# TEI reference structure summary (inside <listBibl>):
#
#   <biblStruct xml:id="b0">
#     <analytic>                          ← article-level metadata
#       <title level="a" type="main">     ← Article Title
#       <author><persName>…</persName></author>
#       <idno type="DOI">…</idno>
#     </analytic>
#     <monogr>                            ← container/venue metadata
#       <title level="j">                 ← journal  → Publication Title
#       <title level="m">                 ← monograph/proceedings → Pub Title
#       <title level="s">                 ← series   → Series
#       <editor><persName>…</persName></editor>
#       <imprint>
#         <biblScope unit="volume">
#         <biblScope unit="issue">
#         <biblScope unit="page" from="…" to="…"/>
#         <date type="published" when="2020-03-15"/>
#         <publisher>…</publisher>
#         <pubPlace>…</pubPlace>
#       </imprint>
#       <meeting>…</meeting>              ← conference name (rare)
#     </monogr>
#     <note type="report_type">…</note>   ← thesis/report classification
#     <idno type="DOI">…</idno>           ← DOI (can appear at multiple levels)
#     <idno type="arXiv">…</idno>
#     <idno type="ISSN">…</idno>
#     <idno type="ISBN">…</idno>
#     <ptr target="https://…"/>           ← URL
#     <ref target="https://…"/>           ← URL (alternative)
#   </biblStruct>
#
# Title level mapping (from TEI/GROBID conventions):
#   level="a"  → article / chapter title          → Article Title
#   level="j"  → journal title                    → Publication Title
#   level="m"  → monograph / book / proceedings   → Publication Title
#                (or Article Title if no analytic)
#   level="s"  → series                           → Series

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple
from config import Config


# ═══════════════════════════════════════════════════════════════════════
# TEI namespace handling
# ═══════════════════════════════════════════════════════════════════════

TEI_NS = "http://www.tei-c.org/ns/1.0"
NS = {"tei": TEI_NS}


def _ns(tag: str) -> str:
    """Prefix a tag with the TEI namespace for ElementTree find/findall."""
    return f"{{{TEI_NS}}}{tag}"


def _text(element: Optional[ET.Element]) -> str:
    """
    Safely extract the full inner text of an element, including text
    in child elements (itertext), stripped and joined.
    Returns empty string if element is None.
    """
    if element is None:
        return ""
    parts = list(element.itertext())
    return " ".join(p.strip() for p in parts if p.strip())


def _direct_text(element: Optional[ET.Element]) -> str:
    """
    Extract only the direct text content of an element (not children).
    Useful for elements where child text is separately processed.
    """
    if element is None:
        return ""
    text = element.text or ""
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════
# Author extraction
# ═══════════════════════════════════════════════════════════════════════

def _parse_persname(persname_el: Optional[ET.Element]) -> str:
    """
    Parse a <persName> element into 'Lastname, Firstname' format.

    Handles:
      <persName>
        <forename type="first">John</forename>
        <forename type="middle">M.</forename>
        <surname>Doe</surname>
      </persName>

    Falls back to full text if no structured sub-elements found.
    """
    if persname_el is None:
        return ""

    surname = _text(persname_el.find(_ns("surname")))

    # Collect all forename parts (first, middle, etc.)
    forename_parts = []
    for fn in persname_el.findall(_ns("forename")):
        fn_text = _text(fn)
        if fn_text:
            forename_parts.append(fn_text)

    forename = " ".join(forename_parts)

    if surname and forename:
        return f"{surname}, {forename}"
    elif surname:
        return surname
    elif forename:
        return forename
    else:
        # Fallback: just get all the text
        return _text(persname_el)


def _extract_authors(parent_el: Optional[ET.Element]) -> List[str]:
    """
    Extract all <author> elements from a parent (analytic or monogr).
    Returns a list of formatted author strings.

    Handles both structured persName authors and organizational authors.
    """
    if parent_el is None:
        return []

    authors = []
    for author_el in parent_el.findall(_ns("author")):
        persname = author_el.find(_ns("persName"))
        if persname is not None:
            name = _parse_persname(persname)
            if name:
                authors.append(name)
        else:
            # Organizational author or unstructured name
            org = author_el.find(_ns("orgName"))
            if org is not None:
                name = _text(org)
            else:
                name = _text(author_el)
            if name:
                authors.append(name)

    return authors


def _extract_editors(monogr_el: Optional[ET.Element]) -> List[str]:
    """Extract editors from <monogr> using same logic as authors."""
    if monogr_el is None:
        return []

    editors = []
    for editor_el in monogr_el.findall(_ns("editor")):
        persname = editor_el.find(_ns("persName"))
        if persname is not None:
            name = _parse_persname(persname)
            if name:
                editors.append(name)
        else:
            name = _text(editor_el)
            if name:
                editors.append(name)

    return editors


# ═══════════════════════════════════════════════════════════════════════
# Identifier extraction
# ═══════════════════════════════════════════════════════════════════════

def _clean_doi(raw: str) -> str:
    """
    Strip URL prefixes from a DOI, returning only the bare identifier.
    e.g. 'https://doi.org/10.1234/abc' → '10.1234/abc'
    """
    raw = raw.strip()
    # Remove common prefixes
    for prefix in ["https://doi.org/", "http://doi.org/",
                    "https://dx.doi.org/", "http://dx.doi.org/",
                    "doi:", "DOI:", "DOI "]:
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    return raw.strip()


def _clean_isbn(raw: str) -> str:
    """Strip hyphens and spaces from ISBN, return digits only (+ possible X)."""
    return re.sub(r"[\s-]", "", raw.strip())


def _clean_issn(raw: str) -> str:
    """
    Normalize ISSN to XXXX-XXXX format if possible.
    If already in that format, return as-is.
    If digits only, insert hyphen.
    """
    raw = raw.strip()
    digits = re.sub(r"[\s-]", "", raw)
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:]}"
    return raw


def _extract_identifiers(bib_struct: ET.Element) -> Dict[str, str]:
    """
    Extract all identifiers (DOI, ISBN, ISSN, arXiv, PMID, URL) from
    a <biblStruct> and its children.

    Searches at all levels: biblStruct, analytic, monogr.
    """
    result = {"DOI": "", "ISBN": "", "ISSN": "", "URL": ""}

    # Collect all <idno> elements at every level
    idno_elements = list(bib_struct.findall(_ns("idno")))

    analytic = bib_struct.find(_ns("analytic"))
    if analytic is not None:
        idno_elements.extend(analytic.findall(_ns("idno")))

    monogr = bib_struct.find(_ns("monogr"))
    if monogr is not None:
        idno_elements.extend(monogr.findall(_ns("idno")))
        imprint = monogr.find(_ns("imprint"))
        if imprint is not None:
            idno_elements.extend(imprint.findall(_ns("idno")))

    for idno in idno_elements:
        id_type = (idno.get("type") or "").strip().upper()
        value = _text(idno).strip()
        if not value:
            continue

        if id_type == "DOI" and not result["DOI"]:
            result["DOI"] = _clean_doi(value)
        elif id_type == "ISBN" and not result["ISBN"]:
            result["ISBN"] = _clean_isbn(value)
        elif id_type in ("ISSN", "EISSN", "PISSN") and not result["ISSN"]:
            result["ISSN"] = _clean_issn(value)
        elif id_type == "ARXIV" and not result["URL"]:
            # Convert arXiv ID to URL
            arxiv_id = value.replace("arXiv:", "").strip()
            result["URL"] = f"https://arxiv.org/abs/{arxiv_id}"
        elif id_type == "PMID" and not result["URL"]:
            result["URL"] = f"https://pubmed.ncbi.nlm.nih.gov/{value}/"

    # Check for <ptr> and <ref> URLs
    if not result["URL"]:
        for tag in ("ptr", "ref"):
            for el in bib_struct.findall(_ns(tag)):
                target = el.get("target", "").strip()
                if target and target.startswith("http"):
                    # Check if it's actually a DOI URL
                    if "doi.org/" in target and not result["DOI"]:
                        result["DOI"] = _clean_doi(target)
                    if not result["URL"]:
                        result["URL"] = target
                    break
            if result["URL"]:
                break

        # Also check inside analytic and monogr
        for section in [analytic, monogr]:
            if section is None or result["URL"]:
                continue
            for tag in ("ptr", "ref"):
                for el in section.findall(_ns(tag)):
                    target = el.get("target", "").strip()
                    if target and target.startswith("http"):
                        if "doi.org/" in target and not result["DOI"]:
                            result["DOI"] = _clean_doi(target)
                        if not result["URL"]:
                            result["URL"] = target
                        break

    return result


# ═══════════════════════════════════════════════════════════════════════
# Date extraction
# ═══════════════════════════════════════════════════════════════════════

# Month name lookup
MONTH_NAMES = {
    "01": "January", "02": "February", "03": "March", "04": "April",
    "05": "May", "06": "June", "07": "July", "08": "August",
    "09": "September", "10": "October", "11": "November", "12": "December",
    "1": "January", "2": "February", "3": "March", "4": "April",
    "5": "May", "6": "June", "7": "July", "8": "August",
    "9": "September", "10": "October", "11": "November", "12": "December",
}

# Also handle text months in 'when' attribute (rare but possible)
MONTH_TEXT_TO_NAME = {
    "jan": "January", "feb": "February", "mar": "March", "apr": "April",
    "may": "May", "jun": "June", "jul": "July", "aug": "August",
    "sep": "September", "oct": "October", "nov": "November", "dec": "December",
}


def _extract_date(imprint_el: Optional[ET.Element]) -> Dict[str, str]:
    """
    Extract Year, Month, Day from <date> elements in an <imprint>.

    GROBID typically uses the 'when' attribute:
      <date type="published" when="2020-03-15"/>  → Year=2020, Month=March, Day=15
      <date type="published" when="2020-03"/>     → Year=2020, Month=March
      <date type="published" when="2020"/>        → Year=2020

    Falls back to text content if 'when' is absent.
    """
    result = {"Year": "", "Month": "", "Day": ""}

    if imprint_el is None:
        return result

    # Search for date elements; prefer 'published', fall back to any
    date_el = None
    for d in imprint_el.findall(_ns("date")):
        dtype = d.get("type", "")
        if dtype == "published":
            date_el = d
            break
    if date_el is None:
        dates = imprint_el.findall(_ns("date"))
        if dates:
            date_el = dates[0]

    if date_el is None:
        return result

    when = date_el.get("when", "").strip()

    if when:
        # Parse ISO-style date: YYYY, YYYY-MM, or YYYY-MM-DD
        parts = when.split("-")
        if len(parts) >= 1 and len(parts[0]) == 4 and parts[0].isdigit():
            result["Year"] = parts[0]
        if len(parts) >= 2:
            month_str = parts[1]
            result["Month"] = MONTH_NAMES.get(month_str, "")
        if len(parts) >= 3:
            day_str = parts[2]
            # Remove leading zeros for Day
            try:
                result["Day"] = str(int(day_str))
            except ValueError:
                pass
    else:
        # Fallback: extract year from text content
        text = _text(date_el)
        year_match = re.search(r"\b(1[89]\d{2}|20\d{2})\b", text)
        if year_match:
            result["Year"] = year_match.group(1)

    return result


def _extract_date_from_monogr(monogr_el: Optional[ET.Element]) -> Dict[str, str]:
    """
    Try to find date info from <monogr>, checking both <imprint>
    child and direct <date> children.
    """
    if monogr_el is None:
        return {"Year": "", "Month": "", "Day": ""}

    # Primary: look in <imprint>
    imprint = monogr_el.find(_ns("imprint"))
    date_info = _extract_date(imprint)

    # If no year found in imprint, check monogr-level date
    if not date_info["Year"]:
        date_info = _extract_date(monogr_el)

    return date_info


# ═══════════════════════════════════════════════════════════════════════
# Title extraction and Type classification
# ═══════════════════════════════════════════════════════════════════════

def _extract_titles(bib_struct: ET.Element) -> Dict[str, str]:
    """
    Extract Article Title, Publication Title, and Series from
    a <biblStruct> using TEI title level conventions.

    Returns dict with keys: ArticleTitle, PublicationTitle, Series
    """
    result = {"ArticleTitle": "", "PublicationTitle": "", "Series": ""}

    analytic = bib_struct.find(_ns("analytic"))
    monogr = bib_struct.find(_ns("monogr"))

    # --- Analytic-level titles (article/chapter) ---
    if analytic is not None:
        for title_el in analytic.findall(_ns("title")):
            level = title_el.get("level", "")
            ttype = title_el.get("type", "")
            text = _text(title_el).strip()
            if not text:
                continue

            if level == "a" or ttype == "main":
                if not result["ArticleTitle"]:
                    result["ArticleTitle"] = text

    # --- Monogr-level titles (journal, book, proceedings, series) ---
    if monogr is not None:
        for title_el in monogr.findall(_ns("title")):
            level = title_el.get("level", "")
            text = _text(title_el).strip()
            if not text:
                continue

            if level == "j":
                # Journal → Publication Title
                if not result["PublicationTitle"]:
                    result["PublicationTitle"] = text
            elif level == "m":
                # Monograph / book / proceedings → Publication Title
                if not result["PublicationTitle"]:
                    result["PublicationTitle"] = text
            elif level == "s":
                # Series
                if not result["Series"]:
                    result["Series"] = text
            elif level == "a" and not result["ArticleTitle"]:
                # Sometimes GROBID puts article-level titles in monogr
                result["ArticleTitle"] = text
            elif not level:
                # Unleveled title — context-dependent placement
                if not result["PublicationTitle"]:
                    result["PublicationTitle"] = text

    # --- Edge case: standalone book (no analytic, only monogr) ---
    # If there's no article title but there IS a monogr title at level "m",
    # the monogr IS the work itself.  Move it to Article Title.
    if not result["ArticleTitle"] and result["PublicationTitle"]:
        # Check if there was no analytic section at all
        if analytic is None:
            result["ArticleTitle"] = result["PublicationTitle"]
            result["PublicationTitle"] = ""

    # --- Clean trailing periods from titles ---
    for key in result:
        if result[key].endswith("."):
            result[key] = result[key][:-1].strip()

    return result


def _classify_type(bib_struct: ET.Element, titles: Dict[str, str]) -> str:
    """
    Classify the citation type based on structural clues in the TEI.

    Heuristic priority:
      1. <note type="report_type"> → Thesis / Report / Technical Report
      2. Has analytic + monogr with journal title → Journal Article
      3. Has analytic + monogr with proceedings → Conference Paper
      4. Has analytic + monogr (book) → Book Chapter
      5. Has monogr only with no analytic → Book
      6. URL-only citations → Website
      7. arXiv/preprint indicators → Preprint
      8. Fallback → Journal Article (most common in academic PDFs)
    """
    analytic = bib_struct.find(_ns("analytic"))
    monogr = bib_struct.find(_ns("monogr"))

    # Check for thesis/report note
    for note in bib_struct.findall(_ns("note")):
        ntype = (note.get("type") or "").lower()
        ntext = _text(note).lower()

        if ntype == "report_type" or "thesis" in ntext or "dissertation" in ntext:
            if "thesis" in ntext or "dissertation" in ntext:
                return "Thesis"
            if "technical report" in ntext or "tech report" in ntext or "tech. report" in ntext:
                return "Technical Report"
            if "report" in ntext:
                return "Report"

    pub_title = titles.get("PublicationTitle", "").lower()

    # Check for preprint/arXiv
    for idno in bib_struct.findall(_ns("idno")):
        if (idno.get("type") or "").upper() == "ARXIV":
            return "Preprint"
    if "arxiv" in pub_title:
        return "Preprint"

    # Has analytic section = article/chapter in a container
    if analytic is not None and monogr is not None:
        # Check monogr title level for classification
        for title_el in monogr.findall(_ns("title")):
            level = title_el.get("level", "")
            if level == "j":
                return "Journal Article"
            elif level == "m":
                text = _text(title_el).lower()
                if any(kw in text for kw in [
                    "proceedings", "proc.", "conference", "workshop",
                    "symposium", "congress", "meeting",
                    # Major ML/AI venues
                    "advances in neural information processing",
                    "neural information processing systems",
                    "neurips", "nips", "icml", "iclr", "aaai", "ijcai",
                    # NLP
                    "acl", "emnlp", "naacl", "coling", "eacl",
                    # Vision
                    "cvpr", "iccv", "eccv", "wacv",
                    # Systems/DB/SE
                    "sigmod", "vldb", "sigcomm", "sosp", "osdi",
                    "isca", "micro", "hpca", "asplos", "pldi",
                    "icse", "fse", "ase",
                    # Security/Theory
                    "ieee s&p", "usenix", "ccs", "ndss",
                    "stoc", "focs", "soda",
                    # General patterns
                    "int. conf.", "intl. conf.", "int'l conf.",
                    "annual conf.", "joint conf.",
                    "lecture notes in computer science", "lncs",
                    "lecture notes in artificial intelligence", "lnai",
                ]):
                    return "Conference Paper"
                else:
                    return "Book Chapter"

        # No clear level on monogr title — check meeting element
        if monogr.find(_ns("meeting")) is not None:
            return "Conference Paper"

        # Default with analytic: Journal Article
        return "Journal Article"

    # No analytic section — standalone work
    if monogr is not None and analytic is None:
        # Check for URL-only (website)
        has_title = bool(titles.get("ArticleTitle"))
        has_url = False
        for idno in bib_struct.findall(_ns("idno")):
            if (idno.get("type") or "").upper() in ("URL",):
                has_url = True
        for tag in ("ptr", "ref"):
            if bib_struct.find(_ns(tag)) is not None:
                has_url = True

        if has_url and not has_title:
            return "Website"

        return "Book"

    # Absolute fallback
    return "Journal Article"


# ═══════════════════════════════════════════════════════════════════════
# Imprint metadata (volume, issue, pages, publisher, location)
# ═══════════════════════════════════════════════════════════════════════

def _extract_imprint_meta(monogr_el: Optional[ET.Element]) -> Dict[str, str]:
    """
    Extract volume, issue, pages, publisher, and publication location
    from <monogr><imprint>.
    """
    result = {
        "Volume": "", "Issue": "", "Pages": "",
        "Publisher": "", "PublicationLocation": "", "Edition": ""
    }

    if monogr_el is None:
        return result

    imprint = monogr_el.find(_ns("imprint"))
    if imprint is None:
        return result

    for scope in imprint.findall(_ns("biblScope")):
        unit = (scope.get("unit") or "").lower()
        if unit == "volume":
            result["Volume"] = _text(scope)
        elif unit == "issue":
            result["Issue"] = _text(scope)
        elif unit == "page":
            page_from = scope.get("from", "")
            page_to = scope.get("to", "")
            if page_from and page_to:
                result["Pages"] = f"{page_from}-{page_to}"
            elif page_from:
                result["Pages"] = page_from
            else:
                result["Pages"] = _text(scope)

    publisher = imprint.find(_ns("publisher"))
    if publisher is not None:
        result["Publisher"] = _text(publisher)

    pubplace = imprint.find(_ns("pubPlace"))
    if pubplace is not None:
        result["PublicationLocation"] = _text(pubplace)

    # Also check monogr-level for publisher (sometimes outside imprint)
    if not result["Publisher"]:
        publisher = monogr_el.find(_ns("publisher"))
        if publisher is not None:
            result["Publisher"] = _text(publisher)

    # Edition (can appear in imprint or directly in monogr)
    edition = imprint.find(_ns("edition"))
    if edition is not None:
        result["Edition"] = _text(edition)

    if not result["Edition"] and monogr_el is not None:
        edition = monogr_el.find(_ns("edition"))
        if edition is not None:
            result["Edition"] = _text(edition)

    return result


# ═══════════════════════════════════════════════════════════════════════
# Institution extraction (for theses, technical reports)
# ═══════════════════════════════════════════════════════════════════════

def _extract_institution(bib_struct: ET.Element) -> str:
    """
    Extract institution from <orgName> elements, primarily for
    theses and technical reports.
    """
    # Check in analytic, monogr, and directly on biblStruct
    for section_tag in ("analytic", "monogr"):
        section = bib_struct.find(_ns(section_tag))
        if section is not None:
            for author_el in section.findall(_ns("author")):
                org = author_el.find(_ns("orgName"))
                if org is not None:
                    return _text(org)
                # Check affiliation
                aff = author_el.find(_ns("affiliation"))
                if aff is not None:
                    org = aff.find(_ns("orgName"))
                    if org is not None:
                        return _text(org)

    # Check note elements for institution info
    for note in bib_struct.findall(_ns("note")):
        org = note.find(_ns("orgName"))
        if org is not None:
            return _text(org)

    return ""


# ═══════════════════════════════════════════════════════════════════════
# Main parser: biblStruct → flat dict
# ═══════════════════════════════════════════════════════════════════════

def parse_biblstruct(bib_struct: ET.Element, citation_number: int) -> Dict[str, str]:
    """
    Parse a single <biblStruct> element into a flat dictionary matching
    the 22-column CSV schema defined in Extraction_Blueprint.md.

    Parameters
    ----------
    bib_struct : ET.Element
        A single <biblStruct> element from the TEI XML.
    citation_number : int
        The 1-indexed position in the reference list.

    Returns
    -------
    dict
        Keys match the CSV headers exactly.
    """
    analytic = bib_struct.find(_ns("analytic"))
    monogr = bib_struct.find(_ns("monogr"))

    # --- Authors (prefer analytic, fall back to monogr) ---
    authors = _extract_authors(analytic)
    if not authors:
        authors = _extract_authors(monogr)

    # --- Editors ---
    editors = _extract_editors(monogr)

    # --- Titles ---
    titles = _extract_titles(bib_struct)

    # --- Type classification ---
    cite_type = _classify_type(bib_struct, titles)

    # --- Identifiers ---
    identifiers = _extract_identifiers(bib_struct)

    # --- Date ---
    date_info = _extract_date_from_monogr(monogr)

    # --- Imprint metadata ---
    imprint_meta = _extract_imprint_meta(monogr)

    # --- Institution ---
    institution = ""
    if cite_type in ("Thesis", "Report", "Technical Report"):
        institution = _extract_institution(bib_struct)

    # --- Date Accessed (rarely present, check notes) ---
    date_accessed = ""
    for note in bib_struct.findall(_ns("note")):
        ntext = _text(note).lower()
        if "accessed" in ntext or "retrieved" in ntext:
            # Try to extract a date from the note text
            date_match = re.search(
                r"(\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})",
                _text(note)
            )
            if date_match:
                date_accessed = date_match.group(1)

    # --- Assemble the flat dictionary ---
    return {
        "Citation Number": str(citation_number),
        "Type": cite_type,
        "Authors": "; ".join(authors),
        "Article Title": titles["ArticleTitle"],
        "Publication Title": titles["PublicationTitle"],
        "Series": titles["Series"],
        "Editor": "; ".join(editors),
        "Volume": imprint_meta["Volume"],
        "Issue": imprint_meta["Issue"],
        "Publisher": imprint_meta["Publisher"],
        "Publication Location": imprint_meta["PublicationLocation"],
        "Year": date_info["Year"],
        "Month": date_info["Month"],
        "Day": date_info["Day"],
        "Pages": imprint_meta["Pages"],
        "Edition": imprint_meta["Edition"],
        "Institution": institution,
        "DOI": identifiers["DOI"],
        "ISBN": identifiers["ISBN"],
        "ISSN": identifiers["ISSN"],
        "URL": identifiers["URL"],
        "Date Accessed": date_accessed,
    }


# ═══════════════════════════════════════════════════════════════════════
# Document-level parser
# ═══════════════════════════════════════════════════════════════════════

def parse_tei_references(xml_text: str) -> List[Dict[str, str]]:
    """
    Parse all <biblStruct> elements from a GROBID TEI-XML response
    into a list of flat citation dictionaries.

    Handles both:
      - Full document XML (from processFulltextDocument) — references
        are in <text><back><div type="references"><listBibl>
      - References-only XML (from processReferences) — references
        may be at the top level or in <listBibl>

    Parameters
    ----------
    xml_text : str
        The raw TEI-XML string from GROBID.

    Returns
    -------
    list of dict
        Each dict maps the 22 CSV column names to string values.
    """
    # Parse XML (handle potential encoding issues)
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        # Try stripping XML declaration if present and re-parsing
        cleaned = re.sub(r'<\?xml[^?]*\?>', '', xml_text).strip()
        root = ET.fromstring(cleaned.encode("utf-8"))

    # Find all <biblStruct> elements anywhere in the document
    bib_structs = root.findall(f".//{_ns('biblStruct')}")

    if not bib_structs:
        return []

    # Filter: skip the document's own header biblStruct (if present)
    # Header biblStruct is typically inside <sourceDesc>, not <listBibl>
    reference_bibs = []
    for bs in bib_structs:
        # Use xml:id attribute as a heuristic: reference bibs have "b0", "b1", etc.
        xml_id = bs.get(f"{{{' http://www.w3.org/XML/1998/namespace'.strip()}}}id", "")
        # Also check plain 'id' attribute
        if not xml_id:
            xml_id = bs.get("xml:id", "")
        if not xml_id:
            xml_id = bs.get("id", "")

        reference_bibs.append(bs)

    # If we found bibs inside <listBibl>, prefer those
    list_bibl_bibs = root.findall(f".//{_ns('listBibl')}/{_ns('biblStruct')}")
    if list_bibl_bibs:
        reference_bibs = list_bibl_bibs

    citations = []
    for idx, bs in enumerate(reference_bibs, start=1):
        citation = parse_biblstruct(bs, idx)
        citations.append(citation)

    return citations


def citations_to_csv(citations: List[Dict[str, str]]) -> str:
    """
    Convert a list of citation dictionaries into a CSV string matching
    the Extraction Blueprint specification.

    Every field is double-quoted.  The header row matches CSV_HEADERS exactly.
    """
    headers = Config.CSV_HEADERS

    def quote(val: str) -> str:
        """Double-quote a field, escaping internal quotes."""
        escaped = val.replace('"', '""')
        return f'"{escaped}"'

    lines = []

    # Header row
    lines.append(",".join(quote(h) for h in headers))

    # Data rows
    for citation in citations:
        row = []
        for h in headers:
            row.append(quote(citation.get(h, "")))
        lines.append(",".join(row))

    return "\n".join(lines)