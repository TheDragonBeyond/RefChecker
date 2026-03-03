# output_handler.py
# Output handling module for formatting and saving results
import os
from pathlib import Path
from unidecode import unidecode
from config import Config
import csv
from io import StringIO


class OutputHandler:
    """Handles output formatting and file saving"""

    @staticmethod
    def sanitize_text(text):
        """
        Sanitize text for output by converting to ASCII

        Args:
            text: Text to sanitize

        Returns:
            ASCII-safe text
        """
        return unidecode(text)

    @staticmethod
    def validate_csv_output(csv_text):
        """
        Validate that the output is proper CSV format

        Args:
            csv_text: CSV text to validate

        Returns:
            Validated and cleaned CSV text
        """
        # Remove any markdown formatting if present
        csv_text = csv_text.replace('```csv', '').replace('```', '').strip()

        # Try to parse as CSV to validate format
        try:
            reader = csv.reader(StringIO(csv_text))
            rows = list(reader)

            if len(rows) < 2:  # Should have at least header and one data row
                print("Warning: CSV output seems to have fewer than expected rows")

            # Check if first row matches expected headers (approximately)
            if rows:
                header_count = len(Config.CSV_HEADERS)
                actual_count = len(rows[0])
                if actual_count != header_count:
                    print(f"Warning: Expected {header_count} columns but got {actual_count}")

            return csv_text

        except csv.Error as e:
            print(f"Warning: CSV validation error: {e}")
            return csv_text  # Return anyway, let user handle issues

    @staticmethod
    def save_to_file(content, output_filepath):
        """
        Save content to a specific output file

        Args:
            content: Content to save
            output_filepath: Full path where the file should be saved

        Returns:
            Output filename
        """
        # Sanitize content
        sanitized_content = OutputHandler.sanitize_text(content)

        # Validate CSV format
        validated_content = OutputHandler.validate_csv_output(sanitized_content)

        # Save to file
        try:
            with open(output_filepath, "w", encoding=Config.OUTPUT_ENCODING, errors='ignore') as f:
                f.write(validated_content)

            print(f"\nSuccess! Reference data has been saved to '{output_filepath}'")
            return output_filepath

        except Exception as e:
            raise IOError(f"Failed to save output file: {e}")

    @staticmethod
    def create_empty_csv_template():
        """
        Create an empty CSV template with headers

        Returns:
            CSV template string
        """
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(Config.CSV_HEADERS)
        return output.getvalue()

    @staticmethod
    def append_to_csv(existing_csv, new_entries):
        """
        Append new entries to existing CSV content

        Args:
            existing_csv: Existing CSV content
            new_entries: New entries to append (as CSV string)

        Returns:
            Combined CSV content
        """
        # Parse both CSV contents
        existing_reader = csv.reader(StringIO(existing_csv))
        new_reader = csv.reader(StringIO(new_entries))

        existing_rows = list(existing_reader)
        new_rows = list(new_reader)

        # Skip header from new_rows if it matches
        if new_rows and existing_rows and new_rows[0] == existing_rows[0]:
            new_rows = new_rows[1:]

        # Combine
        output = StringIO()
        writer = csv.writer(output)
        for row in existing_rows + new_rows:
            writer.writerow(row)

        return output.getvalue()

    @staticmethod
    def format_output_summary(output_filename, row_count=None):
        """
        Format a summary message about the output

        Args:
            output_filename: Name of the output file
            row_count: Optional count of extracted citations

        Returns:
            Formatted summary string
        """
        summary = f"\n{'=' * 50}\n"
        summary += f"Output saved to: {output_filename}\n"

        if row_count:
            summary += f"Total citations extracted: {row_count}\n"

        summary += f"{'=' * 50}\n"
        return summary