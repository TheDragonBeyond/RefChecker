# extraction_runner.py
import os
from pathlib import Path
from extraction_plugins.manager import ExtractionManager
from file_handler import FileHandler
from output_handler import OutputHandler
import config


def run_extraction(provided_filepath=None, output_filepath=None):
    """
    Runs extraction on a single file OR URL.
    """
    print("\n" + "-" * 50)
    temp_file_created = None  # Keep track to delete later

    try:
        # 1. Setup Manager
        manager = ExtractionManager()

        # 2. Input Handling (File vs URL)
        if provided_filepath:
            raw_input = provided_filepath.strip()

            if FileHandler.is_url(raw_input):
                # --- URL PATH ---
                print(f"Detected URL input...")
                filepath = FileHandler.download_to_temp(raw_input)
                temp_file_created = filepath  # Mark for deletion
            else:
                # --- LOCAL FILE PATH ---
                filepath = raw_input
                FileHandler.validate_file(filepath)

            print(f"Processing source: {filepath}")
        else:
            # Fallback to UI dialog
            filepath = FileHandler.get_valid_file_path()

        if not output_filepath:
            raise ValueError("No output destination specified.")

        # 3. Execution (Same as before)
        csv_content = manager.run_extraction(filepath)

        # 4. Save
        output_file = OutputHandler.save_to_file(csv_content, output_filepath)
        print(OutputHandler.format_output_summary(output_file))
        return output_file

    except Exception as e:
        print(f"\n❌ Extraction Error: {e}")
        return None

    finally:
        # 5. Cleanup Temp File
        if temp_file_created and os.path.exists(temp_file_created):
            try:
                os.remove(temp_file_created)
                print(f"[System] Cleaned up temp file: {temp_file_created}")
            except Exception as e:
                print(f"[System] Warning: Could not delete temp file: {e}")


def run_batch_extraction(input_dir, output_dir, progress_callback=None, stop_event=None):
    """
    Runs extraction on all supported files in a directory.
    """
    print("\n" + "=" * 60)
    print(" BATCH EXTRACTION STARTED")
    print("=" * 60)

    try:
        files = FileHandler.scan_directory(input_dir)
        total_files = len(files)

        if total_files == 0:
            print("No supported files found in directory.")
            return

        print(f"Found {total_files} files to process in '{input_dir}'")

        manager = ExtractionManager()
        success_count = 0

        for i, filepath in enumerate(files):
            if stop_event and stop_event.is_set():
                print("\n[!] Batch processing stopped by user.")
                break

            filename = Path(filepath).name
            print(f"\n[{i + 1}/{total_files}] Processing: {filename}")

            try:
                # Construct output filename
                stem = Path(filepath).stem
                out_name = f"{stem}{config.Config.OUTPUT_SUFFIX}"
                out_path = os.path.join(output_dir, out_name)

                # Run Extraction
                csv_content = manager.run_extraction(filepath)
                OutputHandler.save_to_file(csv_content, out_path)
                success_count += 1

            except Exception as e:
                print(f"❌ Failed to process {filename}: {e}")

            # Update Progress
            if progress_callback:
                progress_callback(((i + 1) / total_files) * 100)

        print("\n" + "=" * 60)
        print(f" BATCH COMPLETE: {success_count}/{total_files} files processed successfully.")
        print("=" * 60)

    except Exception as e:
        print(f"Critical Batch Error: {e}")