# validation_runner.py
import csv
import os
import traceback
import tkinter as tk
from tkinter import filedialog
from pathlib import Path

import config as cfg
from utils import clean_citation_data
from validators_plugin.manager import ValidatorManager
from report_generator import ReportGenerator


def get_csv_file():
    """Opens a file dialog to select a CSV file."""
    root = tk.Tk()
    root.withdraw()
    return filedialog.askopenfilename(
        title="Select a CSV file to validate",
        filetypes=(("CSV files", "*.csv"), ("All files", "*.*"))
    )


def print_header(title):
    print("\n" + "=" * 60)
    print(f" {title.upper()}")
    print("=" * 60)


def format_citation_display(citation_data, citation_number):
    """Formats the input citation data for display."""
    lines = []
    lines.append("-" * 60)
    lines.append(f" Citation #{citation_number}")
    lines.append("-" * 60)

    display_order = [
        "Citation Number", "Type", "Authors", "Author", "Article Title",
        "Publication Title", "Journal", "Publisher",
        "Publication Location", "Volume", "Issue", "Year", "Date",
        "Pages", "DOI", "URL"
    ]

    if "Citation Number" not in citation_data:
        lines.append(f" Citation Number: {citation_number}")

    for key in display_order:
        # Case-insensitive lookup
        found_key = next((k for k in citation_data if k.lower() == key.lower()), None)
        if found_key and citation_data[found_key]:
            val = str(citation_data[found_key])
            if len(val) > 100: val = val[:97] + "..."
            lines.append(f" {key}: {val}")

    return "\n".join(lines)


def run_validation(file_path=None, enabled_validators=None, report_output_path=None,
                   generate_md=True, generate_csv=False,
                   generate_bib=False, generate_ris=False,
                   progress_callback=None, stop_event=None):
    """Standard Single-File Validation Entry Point"""
    if not file_path:
        file_path = get_csv_file()

    if not file_path or not os.path.exists(file_path):
        print("Invalid file selection.")
        return

    _execute_validation_logic(file_path, enabled_validators, report_output_path,
                              generate_md, generate_csv, generate_bib, generate_ris,
                              progress_callback, stop_event)


def run_batch_validation_logic(file_paths, output_dir, enabled_validators=None,
                               generate_md=True, generate_csv=False,
                               generate_bib=False, generate_ris=False,
                               progress_callback=None, stop_event=None):
    """
    Batch Mode: Iterates through multiple CSVs and validates them individually.
    Produces separate reports for each file, rather than merging them.
    """
    if not file_paths:
        print("No files provided for batch validation.")
        return

    total_files = len(file_paths)
    print_header(f"STARTING BATCH VALIDATION ({total_files} FILES)")

    for i, fp in enumerate(file_paths):
        if stop_event and stop_event.is_set():
            print("\n[!] Batch processing stopped by user.")
            break

        filename = Path(fp).name
        stem = Path(fp).stem
        print(f"\nProcessing file [{i + 1}/{total_files}]: {filename}")

        # Construct Report Path for this specific file
        # Example: Input "paper1.csv" -> Output "paper1_Validation_Report"
        report_base = os.path.join(output_dir, f"{stem}_Validation_Report")

        # Create a scoped progress callback to update the global progress bar
        # based on the contribution of this single file.
        def file_progress_adapter(inner_val):
            if progress_callback:
                # Calculate global percentage:
                # Base % for completed files + (Current File % * Weight of one file)
                base_progress = (i / total_files) * 100
                file_weight = 100 / total_files
                current_contribution = (inner_val / 100) * file_weight
                progress_callback(base_progress + current_contribution)

        try:
            _execute_validation_logic(
                file_path=fp,
                enabled_validators=enabled_validators,
                report_output_path=report_base,
                generate_md=generate_md,
                generate_csv=generate_csv,
                generate_bib=generate_bib,
                generate_ris=generate_ris,
                progress_callback=file_progress_adapter,
                stop_event=stop_event
            )
        except Exception as e:
            print(f"❌ Failed to process {filename}: {e}")
            traceback.print_exc()

    print_header("BATCH VALIDATION COMPLETE")


def _execute_validation_logic(file_path, enabled_validators, report_output_path,
                              generate_md, generate_csv, generate_bib, generate_ris,
                              progress_callback, stop_event):
    """Internal shared logic for validation processing."""
    try:
        manager = ValidatorManager()
        if enabled_validators:
            manager.set_enabled_validators(enabled_validators)
            print(f"Active Validators: {', '.join(enabled_validators)}")

        validation_results = []

        with open(file_path, 'r', encoding='utf-8', errors='replace') as csvfile:
            reader = csv.reader(csvfile)
            try:
                header = [name.strip() for name in next(reader)]
            except StopIteration:
                print("Error: CSV file is empty.")
                return

            print_header("CITATION VALIDATION REPORT")
            print(f" Source Data: {os.path.basename(file_path)}")

            rows = list(reader)
            total_items = len(rows)
            stats = {'validated': 0, 'possible_match': 0, 'not_validated': 0, 'error': 0}

            for i, row_list in enumerate(rows):
                if stop_event and stop_event.is_set():
                    print("\n[!] Process cancelled by user.")
                    break

                if not any(row_list): continue

                if len(row_list) < len(header):
                    row_list += [''] * (len(header) - len(row_list))

                row = dict(zip(header, row_list))
                cleaned_citation = clean_citation_data(row)

                # Validate
                final_result = manager.validate_citation(cleaned_citation)

                validation_results.append({
                    'citation_data': cleaned_citation,
                    'result': final_result
                })

                # Stats
                s = final_result.status
                if s == "Validated":
                    stats['validated'] += 1
                elif s == "Possible Match":
                    stats['possible_match'] += 1
                elif "Error" in s:
                    stats['error'] += 1
                else:
                    stats['not_validated'] += 1

                # Display
                print(format_citation_display(cleaned_citation, i + 1))
                print(f"\n Status: {final_result.status} (via {final_result.source_name})")
                print(f" Confidence: {final_result.confidence_score}/100")
                if final_result.details:
                    print(f" Details:\n  {final_result.details.replace(chr(10), chr(10) + '  ')}")
                if final_result.evidence_links:
                    print(f" Evidence: {final_result.evidence_links[0]}")
                print("\n" + "-" * 30 + "\n")

                if progress_callback and total_items > 0:
                    progress_callback(((i + 1) / total_items) * 100)

            # --- REPORT GENERATION LOGIC (UPDATED) ---
            if report_output_path and validation_results:
                print("\nGenerating Reports...")

                # Filter Results
                success_results = [r for r in validation_results if r['result'].status == "Validated"]
                review_results = [r for r in validation_results if r['result'].status != "Validated"]

                # Determine which reports to generate based on config
                mode = cfg.REPORT_SPLIT_MODE
                reports_to_process = []

                # 1. Complete Report
                if mode in ["merged", "both"]:
                    reports_to_process.append({
                        "suffix": "",
                        "data": validation_results,
                        "title_mod": ""
                    })

                # 2. Split Reports
                if mode in ["split", "both"]:
                    if success_results:
                        reports_to_process.append({
                            "suffix": "_Validated",
                            "data": success_results,
                            "title_mod": " (Success)"
                        })
                    if review_results:
                        reports_to_process.append({
                            "suffix": "_NeedsReview",
                            "data": review_results,
                            "title_mod": " (Attention Needed)"
                        })

                # Generate Loop
                base_name = os.path.basename(file_path)

                for job in reports_to_process:
                    if not job["data"]: continue  # Skip empty sets

                    current_output_path = report_output_path + job["suffix"]
                    current_title = base_name + job["title_mod"]

                    generator = ReportGenerator(current_title, job["data"])

                    if generate_md:
                        generator.save_report(current_output_path + ".md", 'md')
                    if generate_csv:
                        generator.save_report(current_output_path + ".csv", 'csv')
                    if generate_bib:
                        generator.save_report(current_output_path + ".bib", 'bib')
                    if generate_ris:
                        generator.save_report(current_output_path + ".ris", 'ris')

    except Exception as e:
        print(f"Critical Error in Runner: {e}")
        traceback.print_exc()