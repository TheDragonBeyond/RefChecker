# RefChecker

A modular, plugin-based desktop application that extracts citation data from documents, validates references against academic databases and LLMs, and generates comprehensive reports.

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
  - [Option A: Pre-Built Download](#option-a-pre-built-download-windows)
  - [Option B: Automated Setup (Windows)](#option-b-automated-setup-windows)
  - [Option C: Manual Setup](#option-c-manual-setup)
- [First Launch & Configuration](#first-launch--configuration)
  - [Setting API Keys](#setting-api-keys)
  - [Choosing Models](#choosing-models)
- [Using the App](#using-the-app)
  - [Single File Mode](#single-file-mode)
  - [Batch Processing Mode](#batch-processing-mode)
  - [Manual Entry](#manual-entry)
- [Validation Output Formats](#validation-output-formats)
- [Managing Validators](#managing-validators)
- [Built-in Extractors](#built-in-extractors)
  - [Google Gemini API](#google-gemini-api)
  - [GROBID Service](#grobid-service)
- [Built-in Validators](#built-in-validators)
  - [Tier 1 (Primary) Validators](#tier-1-primary-validators)
  - [Tier 2 (Deep Research) Validators](#tier-2-deep-research-validators)
  - [How Scoring Works](#how-scoring-works)
- [Plugin System](#plugin-system)
  - [Installing a Plugin](#installing-a-plugin)
  - [Writing Your Own Plugin](#writing-your-own-plugin)
- [Configuration Reference](#configuration-reference)
- [Troubleshooting](#troubleshooting)

---

## Features

- **Three operating modes** — process a single file, batch-scan an entire directory, or manually build a reference list.
- **URL support** — paste a web URL directly into the input field and the app will download and process it.
- **Plugin architecture** — extractors and validators are independent plugins. Add new ones without changing core code.
- **Centralized scoring pipeline** — all non-LLM validators are scored with consistent weights and thresholds, making results comparable across sources.
- **Multiple output formats** — export validation reports as Markdown, CSV, BibTeX (`.bib`), or RIS (`.ris`).
- **Report splitting** — generate a single merged report, separate "Validated" / "Needs Review" reports, or both.
- **Configurable everything** — API keys, model names, thresholds, output encoding, and more are editable from within the app.

---

## Installation

### Option A: Pre-Built Download (Windows)

A pre-built frozen release is available as **`RefChecker.zip`**. This bundles the application into a standalone executable — no Python installation required.

1. [**Download RefChecker.zip**](https://github.com/TheDragonBeyond/RefChecker/releases/download/v1.0.0/RefChecker.zip) from the latest release.
2. Extract the zip file.
3. Run the executable inside the extracted folder.
4. On first launch, you will be prompted to configure your API keys (see [First Launch & Configuration](#first-launch--configuration)).

> **Windows SmartScreen:** Because the executable is not code-signed, Windows may show a "Windows protected your PC" warning. Click **More info** → **Run anyway** to proceed. This is expected for independent open-source releases that haven't purchased a code-signing certificate.

> **Note:** You still need to provide your own API keys for the Gemini extractor and Gemini Research Agent validator. The frozen build simply removes the need to install Python and dependencies yourself. All other built-in validators (Crossref, DBLP, ArXiv, PubMed, Open Library, Google Books) work out of the box with no keys.

### Option B: Automated Setup (Windows)

The included `run.bat` script handles virtual environment creation, dependency installation, and launcher generation.

1. Clone this repository.
2. Ensure Python 3.10+ is installed and available on your PATH.
3. Double-click **`run.bat`** (or run it from a terminal).
4. When the script finishes, launch the app via the generated **`CitationUnified.bat`** or the desktop shortcut (if Pillow was available for icon generation).

### Option C: Manual Setup

```bash
git clone <repo-url>
cd RefChecker
pip install -r requirements.txt
python gui_app.py
```

---

## First Launch & Configuration

When the app starts for the first time, a red warning banner appears at the top of the window:

> ⚠️ Google Gemini API is not configured. Click here to set API Keys.

Click the banner (or go to **File → Configuration…**) to open the settings editor.

### Setting API Keys

The settings editor has three tabs: **Global**, **Extractors**, and **Validators**.

1. Go to the **Extractors** tab.
2. Select **Google Gemini API** from the dropdown.
3. Paste your Google GenAI API key into the **API_KEY** field.
4. Go to the **Validators** tab.
5. Select **Gemini Research Agent** from the dropdown.
6. Paste your API key there as well. Extractors and validators maintain separate key fields — both need to be set.
7. Click **Save All Settings**.

The warning banner will disappear once the active extractor has a valid key.

> **Good news:** The four of the Tier 1 validators (Crossref, DBLP, ArXiv, , Open Library) require **no API keys** or setup and work immediately. The only plugins that need keys are the three Google-based ones — the extractor the Gemini Research Agent validator, The Google Books validator, and PubMed requires an email.

### Choosing Models

The settings editor also lets you set the model name for each plugin. Two model fields are available on the Gemini extractor:

| Field | Used for | Recommended Model |
|---|---|---|
| `MODEL_NAME` | PDFs and binary files | `gemini-2.5-flash` |
| `MODEL_NAME_TEXT` | HTML, TXT, and other text files | `gemini-2.5-pro` |

The app automatically selects the appropriate model based on the input file type.

**Why these recommendations?** We ran extensive testing across both models. For PDF extraction, Flash and Pro performed nearly identically — Pro was marginally more verbose with metadata details, but not enough to justify the higher token cost. For HTML and web content, however, Pro was clearly superior. +3.0 gemini models did not produce improved results.

We recommend keeping `TEMPERATURE` at `0.0` for reproducible results.

---

## Using the App

The main window is organized into a tabbed interface at the top (your workspace) and a log panel at the bottom (system output and progress).

### Single File Mode

This is the default tab. It has two collapsible sections: **Extraction** and **Validation**.

**Step 1 — Extraction (Document → CSV)**

1. Enter a file path or URL in the input field, or click **Browse…** to select a local document (PDF, TXT, RTF, HTML).
2. Click **RUN EXTRACTION**.
3. A save dialog appears — choose where to save the output CSV.
4. The extractor scans the document and writes a structured CSV of all detected citations.

> **Tip:** Check the **Auto-Validate** box before running extraction. When enabled, the app will automatically feed the resulting CSV into the validation step as soon as extraction completes.

**Step 2 — Validation (CSV → Report)**

1. The **Input CSV** field is either pre-filled (if you used Auto-Validate) or you can browse to any previously generated CSV.
2. Select your desired output formats using the checkboxes (MD, CSV, BIB, RIS).
3. Click **RUN VALIDATION**.
4. A save dialog appears — choose a base filename for the report(s).
5. The system checks every citation against all enabled validators and produces the final report(s).

The **STOP** button next to the validation controls cancels a running job.

### Batch Processing Mode

Switch to the **Batch Processing Mode** tab for bulk operations.

**Batch Extraction**

1. Select an **Input Directory** containing your documents.
2. Select an **Output Directory** where the per-file CSVs will be saved.
3. Click **RUN BATCH EXTRACTION**. Each supported file in the input folder is processed individually, and a corresponding `_references.csv` is written to the output folder.

**Batch Validation**

1. Click **Select CSVs…** to pick multiple CSV files (or all CSVs from a folder).
2. Select an **Output Folder** for the reports.
3. Click **RUN BATCH VALIDATION**. Each CSV gets its own validation report.

The progress bar at the bottom tracks overall batch progress.

### Manual Entry

Switch to the **Manual Entry** tab to hand-build or correct a reference list.

The form is divided into collapsible sections:

- **Primary Fields** — Type (dropdown), Article Title, Publication Title, Year, DOI. These are always visible.
- **Authors** — Add authors one at a time (Last Name, First/Initials), manage ordering with Up/Down buttons. Press Enter in the author fields to quickly add.
- **Publication Details** — Volume, Issue, Pages, Publisher, Location, Editor, Series. Collapsed by default.
- **Additional Info & Identifiers** — Month, Day, Edition, Institution, ISBN, ISSN, URL, Date Accessed. Collapsed by default.

**Workflow:**

1. Fill in the relevant fields.
2. Click **Add to List ⇩** to stage the citation. It appears in the treeview at the bottom.
3. Repeat for additional citations. The citation number auto-increments.
4. Select a staged citation and click **Remove Selected** (or press Delete) to discard it.
5. When finished, click **Save CSV**.
6. Optionally check **Validate After Saving** — the app will save the CSV and immediately switch to the Single File tab with the validation input pre-filled.

> **Note:** For validation purposes, the most important fields are **Authors** and **Article Title**. Most validators primarily search on those two fields. **Year** also matter, though to a lesser extent. Other metadata helps refine matches but is less critical.

---

## Validation Output Formats

When running validation, you can select one or more output formats:

| Format | Extension | Description |
|---|---|---|
| Markdown Report | `.md` | Human-readable validation report with status, confidence scores, and evidence links for each citation. |
| CSV | `.csv` | Machine-readable spreadsheet of results. |
| BibTeX | `.bib` | Standard bibliography format for LaTeX workflows. |
| RIS | `.ris` | Reference manager interchange format (Zotero, Mendeley, EndNote). |

The **Report Structure** setting (in **File → Configuration… → Global → Output Formatting**) controls how reports are organized:

- **merged** — one report containing all citations.
- **split** — two reports: one for validated citations, one for those needing review.
- **both** — generates all three files.

---

## Managing Validators

Click the **Plugins…** button (next to the validation controls) to open a quick toggle window. This lists every loaded validator with a checkbox. Uncheck any you don't want to query — useful if you lack a specific API key, want to save tokens, or want faster runs.

Click **Save Configuration** to persist your selection. These preferences are stored in `settings.json` and restored on next launch.

The validation engine runs enabled validators in sequence. When a configurable number of validators confirm a citation (the **Satisfied Threshold**, default 1), it stops early. If no validator reaches the **LLM Confidence Threshold** and LLM/Deep Research is enabled, the system escalates to research-tier validators (such as the Gemini Research Agent) for a deeper check.

---

## Built-in Extractors

The app ships with two extraction engines. Set the active one in **File → Configuration… → Global → Extraction Engine**.

### Google Gemini API

The default and recommended extractor. Uses Google's Gemini LLM to read a document and return structured citation data as JSON (via Gemini's structured output mode with a schema constraint), which is then converted to CSV.

**Requirements:** A Google GenAI API key (`API_KEY` in the Extractors tab).

**Supported input types:** PDF (uploaded to Gemini's file API), TXT, RTF, HTML, CSV, MD, XML, JSON (read locally and sent as text). URLs are also supported — the app downloads the page first, then processes the resulting file.

**Model selection** is automatic based on file type. The extractor maintains two model fields: `MODEL_NAME` (used for PDFs and binary files) and `MODEL_NAME_TEXT` (used for HTML, TXT, and other text-based files). See [Choosing Models](#choosing-models) for recommendations.

**Key settings** (editable in **File → Configuration… → Extractors → Google Gemini API**):

| Setting | Default | Description |
|---|---|---|
| `API_KEY` | `YOUR_KEY_HERE` | Your Google GenAI API key. |
| `MODEL_NAME` | `gemini-2.5-flash` | Model for PDFs and binary files. |
| `MODEL_NAME_TEXT` | `gemini-2.5-pro` | Model for HTML and text files. |
| `TEMPERATURE` | `0.0` | Generation temperature. Keep at 0 for deterministic output. |
| `MAX_OUTPUT_TOKENS` | `64000` | Maximum tokens in the response. Increase for documents with very large reference sections. |

### GROBID Service

A programmatic (non-LLM) extractor that uses the [GROBID](https://github.com/kermitt2/grobid) machine learning service for PDF parsing. GROBID is a well-established open-source tool for extracting structured bibliographic data from academic PDFs. It runs as a local service, typically via Docker.

**Requirements:** A running GROBID instance. GROBID is not preinstalled by RefChecker. The plugin supports two modes:

- **Managed mode** (`AUTO_START_GROBID = true`): The plugin automatically starts and stops a GROBID Docker container. Requires Docker to be installed and available on your PATH. The container is stopped automatically when the application exits (via `atexit`).
- **External mode** (`AUTO_START_GROBID = false`, the default): You run GROBID yourself and point the plugin at it via `GROBID_URL`.

To start GROBID manually with Docker:

```bash
docker run --rm -d -p 8070:8070 grobid/grobid:0.8.2
```

**Supported input types:** PDF (full document or references-only processing), TXT, MD, CSV, RTF (the plugin extracts reference-like lines from text files and sends them to GROBID's `processCitationList` endpoint).

**Key settings** (editable in **File → Configuration… → Extractors → GROBID Service**):

| Setting | Default | Description |
|---|---|---|
| `AUTO_START_GROBID` | `false` | If true, automatically manage a Docker container. |
| `GROBID_DOCKER_IMAGE` | `grobid/grobid:0.8.2` | Docker image to pull and run in managed mode. |
| `GROBID_CONTAINER_NAME` | `citation_app_grobid` | Name assigned to the managed Docker container. |
| `GROBID_STARTUP_TIMEOUT` | `120` | Seconds to wait for GROBID to become ready after starting the container. |
| `GROBID_URL` | `http://localhost:8070` | URL of the running GROBID service. |
| `GROBID_MODE` | `full` | `full` processes the entire document (`processFulltextDocument`); `light` extracts only the references section (`processReferences`). |
| `CONSOLIDATE_CITATIONS` | `0` | Set to `1` or `2` to have GROBID cross-reference extracted citations against Crossref for enrichment. Increases accuracy but is significantly slower. |
| `INCLUDE_RAW_CITATIONS` | `1` | Include raw citation strings in the GROBID output. |
| `FLAVOR` | (empty) | Optional GROBID processing flavor. Leave empty for default behavior. |
| `TIMEOUT` | `120` | HTTP request timeout in seconds per GROBID API call. |
| `MAX_RETRIES` | `5` | Number of retry attempts on server-busy (503) responses. |
| `RETRY_DELAY` | `3` | Base delay in seconds between retries (multiplied by attempt number). |

**When to use GROBID vs Gemini:** GROBID is free, fully local, and fast. It works well on cleanly formatted academic PDFs with standard reference sections. Gemini handles messy layouts, non-standard formats, and web pages much better, but requires an API key and costs tokens. For high-volume batch processing of well-formatted papers, GROBID can be a cost-effective alternative.

---

## Built-in Validators

Validators check each extracted citation against external databases to verify it exists and matches. The system includes six Tier 1 (primary) validators that run by default, and one Tier 2 (deep research) validator that activates only when Tier 1 results are inconclusive.

### Tier 1 (Primary) Validators

These run on every citation (unless disabled via the Plugins toggle). They all use free, public APIs and **most require no API keys or other setup**. The Google Books Validator and PubMed Validator have some setup before you should use them.

#### Crossref API

The broadest academic database validator. Searches Crossref's index of over 150 million scholarly works.

**Best for:** Journal articles, conference papers, book chapters — anything with a DOI.

**Strategies:** If the citation has a DOI (extracted from the DOI field or parsed from URLs), Crossref resolves it directly for an instant high-confidence match. Otherwise, it performs a bibliographic search using the title and first author surname. A quick pre-filter (token sort ratio ≥ 30) eliminates hopeless results before they reach the scoring pipeline.

| Setting | Default | Description |
|---|---|---|
| `MAX_RESULTS` | `10` | Number of search results to evaluate. |
| `YEAR_MATCH_TOLERANCE` | `1` | Accepted year difference (e.g., `1` means ±1 year). |

#### DBLP API

Searches the DBLP computer science bibliography database.

**Best for:** Computer science publications — conference papers, journal articles, and proceedings in CS/IT venues.

**Strategies:** Title + first author surname search against the DBLP index. Includes built-in rate limiting and retry logic with exponential backoff on 429 responses.

| Setting | Default | Description |
|---|---|---|
| `MAX_RESULTS_FETCH` | `10` | Number of results to retrieve from the API. |
| `MAX_RESULTS_TO_CHECK` | `5` | Number of top results to evaluate for matching. |
| `REQUEST_DELAY` | `1.0` | Seconds to wait between API calls. |
| `TIMEOUT` | `8` | HTTP request timeout in seconds. |
| `MAX_RETRIES` | `2` | Retries on rate-limit (429) responses. |
| `RETRY_BACKOFF` | `5.0` | Base backoff delay in seconds between retries. |
| `YEAR_MATCH_TOLERANCE` | `1` | Accepted year difference. |

#### ArXiv Validator

Searches the ArXiv preprint repository.

**Best for:** Preprints, working papers, and any citation that includes an ArXiv ID.

**Strategies:** If the citation contains an ArXiv ID (extracted from the URL or ID fields), the validator looks it up directly via ArXiv's ID-based search. Otherwise, it performs a title search (`ti:"..."`) using ArXiv's query API. A single `arxiv.Client` instance is reused across all calls to share the connection pool and rate-limit state, satisfying ArXiv's single-connection requirement.

**Rate limiting:** ArXiv's terms of use require a minimum of 3 seconds between requests. The plugin enforces this automatically — if you set `DELAY_SECONDS` below 3.0 in config, it will be overridden with a warning. The underlying `arxiv` library tracks the last request time internally and enforces the delay before every call, so no explicit sleep is needed between strategies.

| Setting | Default | Description |
|---|---|---|
| `DELAY_SECONDS` | `3.0` | Minimum delay between requests (ArXiv ToU minimum: 3s). |
| `NUM_RETRIES` | `1` | Retries on transient failures. |
| `MAX_RESULTS` | `5` | Number of search results to evaluate (also used as the page size). |

#### PubMed (BioPython)

Searches NCBI's PubMed database of biomedical literature via the Entrez API (using the BioPython library).

**Best for:** Medical, biomedical, and life sciences publications.

**Strategies:** Four-stage cascade. If the citation has a PMID, it resolves directly via `esummary`. Otherwise, three progressively looser search strategies are tried: title + author (field-restricted), title only (field-restricted), then a loose keyword search. Titles are truncated to `MAX_QUERY_WORDS` words and cleaned of colons, dashes, and apostrophes before querying.

**Rate limiting:** Without an API key, NCBI allows ~3 requests per second (the plugin uses a 0.35s delay). If you have an NCBI API key, paste it in the `API_KEY` field — the plugin automatically reduces the delay to ~0.11s for the higher ~10 requests/second rate limit.

**Email:** They request that users set a valid email before making API requests.

| Setting | Default | Description |
|---|---|---|
| `EMAIL` | `your_email@example.com` | Required by NCBI for contact in case of excessive usage. Set to your real email. |
| `API_KEY` | (empty) | Optional NCBI API key for higher rate limits. When set, the request delay is automatically reduced. |
| `MAX_RESULTS` | `5` | Number of search results to evaluate. |
| `MAX_QUERY_WORDS` | `8` | Truncates long titles for cleaner PubMed queries. |
| `REQUEST_DELAY` | `0.35` | Seconds between sequential Entrez calls. Auto-reduced to 0.11 when `API_KEY` is set. |
| `YEAR_MATCH_TOLERANCE` | `1` | Accepted year difference. |

#### Open Library API

Searches Open Library's catalog of books and editions.

**Best for:** Books, monographs, and edited volumes.

**Strategies:** Searches by title and first author surname. The plugin is book-type-aware: for book chapters it prefers the container/publication title, while for standalone books it uses the article title. Title matching uses substring boosting to handle books with subtitles (e.g., a citation for "Clean Code" will still match "Clean Code: A Handbook of Agile Software Craftsmanship"). Year comparison checks against all known publish years for a work, not just the first.

| Setting | Default | Description |
|---|---|---|
| `MAX_RESULTS` | `5` | Number of search results to evaluate. |
| `REQUEST_DELAY` | `1.0` | Seconds between API calls. |
| `TIMEOUT` | `15` | HTTP request timeout in seconds. |
| `MAX_RETRIES` | `2` | Retries on rate-limit (429) responses. |
| `RETRY_BACKOFF` | `5.0` | Base backoff delay in seconds between retries. |
| `YEAR_MATCH_TOLERANCE` | `1` | Accepted year difference. Compares against all known publish years for a work, not just the first. |

#### Google Books API

Searches Google's Books index.

**Best for:** Books, including those not well-covered by Open Library. Also useful for older or non-English publications.

**Strategies:** Three-stage cascade — ISBN direct lookup (if available), combined `intitle:` + `inauthor:` search, then title-only fallback. Like Open Library, uses substring-boosted title matching for books with subtitles. Titles are truncated to `MAX_TITLE_WORDS` words and leading articles ("the", "a", "an") are stripped before querying.

**API key:** Required. Google Books works with anonymous requests at a lower quota, but it is very limited and will not work with this app. If you use it without a key you will get an (HTTP 429). Add a free Google Books API key in the Validators settings tab for a usable quota. A 403 error typically means the key is invalid or quota is exhausted.

| Setting | Default | Description |
|---|---|---|
| `API_KEY` | (empty) | Optional. Provides higher request quota. |
| `REQUEST_DELAY` | `1.0` | Seconds between API calls. |
| `MAX_RESULTS` | `10` | Number of search results to evaluate. |
| `MAX_TITLE_WORDS` | `8` | Truncates long titles for cleaner queries. |

### Tier 2 (Deep Research) Validators

Tier 2 validators only run when Tier 1 fails to produce a confident result. They are LLM-based and consume API tokens.

#### Gemini Research Agent

Uses Google Gemini to perform a holistic assessment of a citation. Given the citation metadata, the model constructs a Google Scholar search link, checks any provided DOI, and returns a structured JSON verdict.

**Requirements:** A Google GenAI API key (`API_KEY` in the Validators tab). This is a separate config entry from the Gemini extractor — both need to be set independently.

**When it runs:** Only when all Tier 1 validators return a confidence score below the `LLM_CONFIDENCE_THRESHOLD` (default: 40) and `LLM_ENABLED` is true in global settings.

**Status vocabulary:** The Gemini Research Agent uses three categories: "Validated" (formal academic publication found), "Ambiguous" (non-standard source like a blog, repository, or product page exists and matches), and "Not Validated" (cannot find the work or link is dead). Each response includes a reasoning explanation, a verification note, and evidence links (DOI URL, Google Scholar link, publisher link, and any additional URLs).

| Setting | Default          | Description |
|---|------------------|---|
| `API_KEY` | `YOUR_KEY_HERE`  | Your Google GenAI API key. |
| `MODEL_NAME` | `gemini-2.5-Pro` | Which Gemini model to use. |
| `TEMPERATURE` | `0.0`            | Generation temperature. |
| `TIMEOUT` | `30`             | Request timeout in seconds. |
> **Tip:** Flash models often will hallucinate if given a DOI, so use a pro model here. 

### How Scoring Works

All Tier 1 validators return raw match signals (title similarity, author overlap, year match) as a `MatchCandidate`. The centralized **Scoring Pipeline** converts these into a final confidence score using consistent weights:

- **Title similarity** accounts for ~60% of the score.
- **Author overlap** accounts for ~30%.
- **Year match** accounts for ~10%.

An additional penalty is applied when a citation lists 3+ authors but zero overlap with the matched record.

A minimum title similarity gate (0.30) must be met before a result is even considered. Results below this threshold are discarded by the validators before reaching the pipeline.

The final score maps to three statuses:

| Status | Score | Meaning |
|---|---|---|
| **Validated** | ≥ 80 | High confidence the citation matches a real work. |
| **Possible Match** | 60–79 | Partial evidence found; manual review recommended. |
| **Not Validated** | < 60 | No credible match found (displayed as score 0). |

**Direct identifier matches** (DOI, PMID, ArXiv ID, ISBN) bypass the weighted scoring. The validator resolves the identifier directly, then cross-checks the resolved title against the citation title. A strong title match yields a score of 100; a weak match flags a potential mismatch (e.g., a fabricated or incorrectly copied DOI) with a warning in the match details.

---

## Plugin System

The app discovers plugins automatically. Extractors inherit from `BaseExtractor`, validators from `BaseValidator`. The plugin managers use `__subclasses__()` to find all subclasses at startup — no registration boilerplate is required. The `@register_extractor` and `@register_validator` decorators exist for backward compatibility but are functionally no-ops; simply inheriting from the base class is sufficient.

### Installing a Plugin

1. Go to **File → Install Plugin…**
2. Select a `.py` file containing a new extractor or validator class.
3. The installer analyzes the file via AST, copies it to the correct plugin directory, and attempts to install any declared pip dependencies automatically. In frozen builds, dependencies are installed to `plugins/lib/` via `pip install --target`.
4. **Restart the application** to activate the new plugin.

### Writing Your Own Plugin

**Extractor plugins** must:

- Inherit from `BaseExtractor`
- Implement `name` (property), `extract(filepath) → str` (returns CSV text), and `get_default_settings() → dict`
- Return `""` (empty string) on any failure — never raise exceptions from `extract()`
- Optionally declare a `DEPENDENCIES` class attribute listing pip packages
- Optionally override `is_programmatic` (default `False` for LLM-based, set `True` for regex/parsing extractors)

**Validator plugins** must:

- Inherit from `BaseValidator`
- Implement `name` (property), `validate(citation_data) → ValidationResult | MatchCandidate`, and `get_default_settings() → dict`
- Return a `MatchCandidate` for the centralized scoring pipeline to handle, or a `ValidationResult` directly for error/skip/LLM cases
- For Tier 2 (deep research) validators, apply both `@register_validator` and `@deep_research_validator` decorators — the Plugin Installer's AST check requires both to be present
- Optionally declare a `DEPENDENCIES` class attribute listing pip packages

Place the file anywhere and install it via the menu, or drop it directly into `plugins/extraction/` or `plugins/validators/` (for frozen builds) or `extraction_plugins/` / `validators_plugin/` (for source installs).

---

## Configuration Reference

All settings are stored in `settings.json` in the application root and can be edited via **File → Configuration…** or by hand.

| Setting | Default | Description |
|---|---|---|
| `ACTIVE_EXTRACTOR` | `Google Gemini API` | Which extraction engine to use. |
| `SATISFIED_THRESHOLD` | `1` | How many validators must confirm before stopping early. |
| `LLM_ENABLED` | `true` | Whether to escalate to research-tier validators for low-confidence results. |
| `LLM_CONFIDENCE_THRESHOLD` | `40` | Score below which the system escalates to LLM validators. |
| `ADAPTIVE_ORDERING` | `true` | Reorders validators so historically successful ones run first. |
| `OUTPUT_ENCODING` | `cp1252` | Character encoding for saved CSV files. |
| `OUTPUT_SUFFIX` | `_references.csv` | Suffix appended to extraction output filenames. |
| `REPORT_SPLIT_MODE` | `merged` | Report structure: `merged`, `split`, or `both`. |
| `DEBUG_MODE` | `false` | Enables verbose logging of plugin loading and config resolution. |

Plugin-specific settings (API keys, model names, temperature, max tokens, timeouts) are stored in separate JSON files per plugin (e.g., `GeminiExtractor_Config.json`, `CrossrefValidator_Config.json`) and are editable from the Extractors/Validators tabs in the settings editor.

---

## Troubleshooting

**"API Key Missing" or red configuration banner**
Open **File → Configuration…**, go to the appropriate tab (Extractors or Validators), select the plugin, and paste your key. Extractors and validators have separate config entries — setting the key in one does not set it in the other.

**Empty extraction output**
Check the log panel at the bottom of the window. Common causes: rate limiting on the API, an expired or invalid key, or an unsupported file format. If using the Gemini extractor, verify you haven't hit your provider's token or request limits.

**GROBID connection refused**
The GROBID extractor needs a running GROBID server. If `AUTO_START_GROBID` is false (the default), you must start the Docker container yourself before running extraction. Check that the `GROBID_URL` in settings matches where your instance is running. If using managed mode, ensure Docker is installed and running. Check Docker logs with `docker logs citation_app_grobid` for startup issues.

**GROBID returns 204 (No Content)**
This means GROBID could not find any extractable references in the document. The PDF may not contain a standard references section, or the formatting may be too unusual for GROBID to parse. Try the Gemini extractor instead.

**Validator returns "Not Validated" for a citation you know exists**
The scoring pipeline requires a minimum title similarity of 0.30 to even consider a match, and a display score of at least 60 to register as a "Possible Match." If the extracted title has typos or is truncated, the match may fall below these thresholds. Try correcting the title in the CSV and re-running validation. Also check which validators are enabled — a CS paper is unlikely to be found in PubMed, and a medical paper won't be in DBLP.

**ArXiv rate limiting**
The ArXiv validator enforces a minimum 3-second delay between requests per their terms of use. This is intentional and cannot be reduced. For batches with many ArXiv-heavy citations, expect the ArXiv validator to be the slowest step.

**PubMed returning few results**
PubMed queries are truncated to `MAX_QUERY_WORDS` (default 8) words for cleaner searches. If your citations have very long titles, the truncation may remove distinguishing words. You can increase this setting, though very long queries tend to return fewer results on PubMed.

**Google Books returning 403 Forbidden**
This usually means the API key is invalid or the daily quota has been exhausted. Google Books works without a key at a lower quota — try clearing the `API_KEY` field to fall back to anonymous access, or wait for the quota to reset.

**Newly installed plugin doesn't appear**
Restart the application after installing. If it still doesn't appear, check the log panel for import errors — the plugin may have unmet dependencies. You can also enable **Debug Mode** in settings and click **Apply & Test Debug Output** to see exactly which plugins loaded and which failed.

**Dependencies fail to install for a plugin**
The installer attempts `pip install` automatically, but this can fail in frozen builds or restricted environments. In frozen builds, dependencies are installed to `plugins/lib/` using `pip install --target`. The error message will include a manual install command you can run in your terminal.

**Batch processing is slow**
Each file in a batch goes through the full extraction or validation pipeline sequentially. For large batches, consider disabling slower validators (like the Gemini Research Agent or ArXiv) via the **Plugins…** toggle, or reducing `MAX_RESULTS` in individual validator settings to limit API calls per citation.