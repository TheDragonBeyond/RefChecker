# Unified Citation Extractor & Validator

A modular, plugin-based system designed to automatically extract citation data from documents, validate the references against academic databases or LLMs, and generate comprehensive reports.

## 🚀 Features

* **Multi-Mode Operation**: Single-file processing, Batch directory scanning, and Manual entry.
* **Plugin Architecture**: Easily extend functionality with new Extractors (e.g., Regex, GPT, Gemini) and Validators (e.g., Crossref, Google Scholar, LLM).
* **Deep Validation**: Verifies existence, checks DOIs, and validates metadata accuracy.
* **Flexible Output**: Exports to CSV, Markdown Reports, BibTeX (.bib), and RIS (.ris).

---

## ⚙️ Setup & Configuration

### 1. Installation

Ensure you have Python installed. Clone this repository and install the initial dependencies:

```bash
pip install -r requirements.txt

```

*(Note: The system creates a `requirements.txt` based on the plugins you use. Core dependencies include `tkinter` (usually built-in), `google-genai`, etc.)*

### 2. Launching the App

Run the main application entry point:

```bash
python gui_app.py

```

### 3. Critical Configuration (API Keys & Models)

Before running your first extraction, you **must** configure your plugins.

1. Go to **File > Configuration...** in the menu.
2. **Global Settings**:
* **Active Extractor**: Select the engine you want to use for reading documents (recommended: *Google Gemini API*).


3. **Extractor Settings** (Tab: *Extractors*):
* Select **Google Gemini API**.
* **API_KEY**: Paste your valid Google GenAI API Key.
* **MODEL_NAME**: Change this to **`gemini-2.5-flash`** (See recommendation below).


4. **Validator Settings** (Tab: *Validators*):
* Select **Gemini Research Agent**.
* **API_KEY**: Paste your API Key here as well.
* **MODEL_NAME**: Change this to **`gemini-2.5-flash`**.


5. Click **Save All Settings**.

> **🏆 Recommended Model: Gemini 2.5 Flash**
> Based on extensive testing, we strongly recommend using the **`gemini-2.5-flash`** model for both the *Extraction* and *Validation* steps. It offers the best balance of speed, cost-efficiency, and high accuracy for citation parsing and verification.
> The except is for messy HTML references. When using the extractor on a webpage, gemini-2.5-Pro works signficantly better.


---

## 📖 User Guide

### Mode 1: Single File Mode

Ideal for processing one paper at a time.

1. **Extraction**:
* Click **Browse** to select your input document (PDF, DOCX, TXT).
* Click **RUN EXTRACTION**. The system will scan the text and generate a `_references.csv` file.
* *(Optional)* Check "Auto-Validate" to immediately proceed to step 2.


2. **Validation**:
* Select the CSV generated in step 1.
* Choose your output formats (Report (MD), CSV, BIB, RIS).
* Click **RUN VALIDATION**. The system will check every citation against enabled validators (Crossref, Gemini, etc.) and produce a final report.



### Mode 2: Batch Processing Mode

Ideal for bulk processing entire folders of PDFs.

1. **Batch Extraction**:
* Select an **Input Directory** containing your documents.
* Select an **Output Directory** for the results.
* Click **RUN BATCH EXTRACTION**.


2. **Batch Validation**:
* Select multiple CSV files or a folder of CSVs.
* Click **RUN BATCH VALIDATION** to process them all into a unified report.



### Mode 3: Manual Entry

Use this to manually build or correct a reference list.

1. Fill in the fields (Authors, Title, Year, DOI, etc.).
2. Click **Add to List**.
3. Once finished, click **Save CSV**.
4. *(Optional)* Check **Validate After Saving** to immediately switch to the Validation tab and verify your manual entries.

*(Note: The most important fields are an Author and a Title. Most Validators primarily use those two fields. Others can help, but are less key.)*

---

## 🔌 Plugin Management

The system is designed to be extensible. You can add new logic without changing the core code.

### Installing a New Plugin

If you have a Python file containing a new Extractor or Validator class:

1. Go to **File > Install Plugin...**
2. Select the `.py` file.
3. The system will analyze the file, install any required Python libraries (pip dependencies) automatically, and copy it to the correct plugin folder.
4. **Restart the application** to see the new plugin in the Configuration menu.

### Enabling/Disabling Validators

You can quickly toggle specific validators on or off without opening the full settings menu:

* Click the **Plugins...** button near the "Run Validation" controls.
* Uncheck any services you do not wish to query (e.g., if you don't have a specific API key or want to save tokens).

---

## 🛠 Troubleshooting

* **"API Key Missing" Error**: Ensure you have pasted your key in **File > Configuration** for the specific plugin you are using. Note that Extractors and Validators often have *separate* config entries.
* **Empty Output**: Check the "System Output / Logs" panel at the bottom of the window. If using an LLM, ensure you haven't hit a rate limit.
* **Dependencies**: If a newly installed plugin fails to load, try running the installer again or manually checking the console for `pip install` errors.