# report_generator.py
import os
import csv
import io
import re
from datetime import datetime
from typing import List, Dict
from validators_plugin.base import ValidationResult
import config as cfg


class ReportGenerator:
    """
    Generates human-readable validation reports (Markdown),
    machine-readable datasets (CSV), and bibliographic formats (BibTeX, RIS).
    """

    def __init__(self, manuscript_name: str, results: List[Dict]):
        self.manuscript_name = manuscript_name
        self.results = results
        self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def generate_markdown_report(self) -> str:
        """Constructs the report content."""
        lines = []

        # --- SECTION 1: MANUSCRIPT METADATA ---
        lines.append(f"# Citation Verification Report")
        lines.append(f"**Manuscript:** `{self.manuscript_name}`  ")
        lines.append(f"**Date Generated:** {self.timestamp}  ")
        lines.append(f"**Validator Engine:** Unified Citation Validator (v3.0)")
        lines.append("***")

        # --- SECTION 2: STATISTICS DASHBOARD ---
        stats = self._calculate_statistics()
        lines.append("## 1. Validation Statistics")
        lines.append("| Metric | Count | Percentage |")
        lines.append("| :--- | :---: | :---: |")
        lines.append(f"| **Total Citations** | {stats['total']} | 100% |")
        lines.append(f"| ✅ Validated | {stats['validated']} | {stats['pct_val']}% |")
        lines.append(f"| ⚠️ Possible Match | {stats['possible_match']} | {stats['pct_pos']}% |")
        lines.append(f"| ❌ Not Validated | {stats['not_validated']} | {stats['pct_not']}% |")
        lines.append(f"| ❗ Errors | {stats['error']} | - |")
        lines.append("\n**Source Breakdown:**")

        for source, count in stats['sources'].items():
            lines.append(f"| {source} | {count} |")

        lines.append("***")

        # --- SECTION 3: CITATION AUDIT LOG ---
        lines.append("## 2. Citation Audit Log")

        for item in self.results:
            cite = item['citation_data']
            res: ValidationResult = item['result']
            cid = cite.get('Citation Number', 'N/A')

            icon = "❌"
            if res.status == "Validated":
                icon = "✅"
            elif res.status == "Possible Match":
                icon = "⚠️"

            lines.append(f"### {icon} Citation #{cid}: {res.status}")

            author = cite.get('Authors', 'Unknown')
            title = cite.get('Article Title') or cite.get('Publication Title', 'Unknown Title')
            year = cite.get('Year', 'N/A')

            lines.append(f"> **Input:** {author} ({year}). *{title}*.")
            lines.append(f"\n**Verification Details:**")
            lines.append(f"- **Source:** {res.source_name}")
            lines.append(f"- **Confidence:** {res.confidence_score}/100")

            clean_details = res.details.replace('\n', '\n  ')
            lines.append(f"- **Analysis:**\n  {clean_details}")

            if res.evidence_links:
                lines.append(f"\n**Evidence & Human Verification:**")
                for link in res.evidence_links:
                    lines.append(f"- [External Link]({link})")

            lines.append("\n---")

        return "\n".join(lines)

    def generate_csv_report(self) -> str:
        """Constructs a CSV string from the validation results."""
        output = io.StringIO()

        headers = [
            "Citation ID", "Input Title", "Input Author", "Input Year",
            "Validation Status", "Confidence Score", "Validator Source",
            "Match Title", "Match DOI", "Evidence Links", "Analysis Details"
        ]

        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(headers)

        for item in self.results:
            cite_data = item['citation_data']
            res: ValidationResult = item['result']

            # Flatten Data
            cid = cite_data.get('Citation Number', 'N/A')
            input_title = cite_data.get('Article Title') or cite_data.get('Publication Title', '')
            input_author = cite_data.get('Authors', '')
            input_year = cite_data.get('Year', '')

            # Extract Match Metadata
            match_doi = res.metadata.get('DOI') or res.metadata.get('doi', '')
            match_title = res.metadata.get('title') or res.metadata.get('Article Title', '')
            if not match_title and isinstance(res.metadata, dict) and 'volumeInfo' in res.metadata:
                match_title = res.metadata['volumeInfo'].get('title', '')

            evidence_str = "; ".join(res.evidence_links) if res.evidence_links else ""
            clean_details = res.details.replace('\n', ' | ')

            row = [
                cid, input_title, input_author, input_year,
                res.status, res.confidence_score, res.source_name,
                match_title, match_doi, evidence_str, clean_details
            ]
            writer.writerow(row)

        return output.getvalue()

    def generate_bibtex_report(self) -> str:
        """
        Generates a .bib file content from the extracted citations.
        Uses input data primarily, enriched by validation if needed.
        """
        lines = []
        for item in self.results:
            data = item['citation_data']
            cid = data.get('Citation Number', 'unknown').replace(' ', '_')

            # Determine Entry Type
            raw_type = data.get('Type', '').lower()
            bib_type = "misc"
            if "journal" in raw_type:
                bib_type = "article"
            elif "book chapter" in raw_type:
                bib_type = "incollection"
            elif "book" in raw_type:
                bib_type = "book"
            elif "conference" in raw_type or "proceeding" in raw_type:
                bib_type = "inproceedings"
            elif "thesis" in raw_type:
                bib_type = "phdthesis"
            elif "report" in raw_type:
                bib_type = "techreport"

            # Create Citation Key (AuthorYear or ID)
            author = data.get('Author', '')
            first_author = author.split(';')[0].split(',')[0].strip().replace(' ', '') if author else "Unknown"
            year = data.get('Year', 'n.d.')
            cite_key = f"{first_author}{year}_{cid}"

            lines.append(f"@{bib_type}{{{cite_key},")

            # Field Mapping
            fields = []

            # Author formatting: "Smith, J.; Doe, A." -> "Smith, J. and Doe, A."
            author_list = [a.strip() for a in author.split(';') if a.strip()]
            clean_authors = " and ".join(author_list)
            if clean_authors: fields.append(f"  author = {{{clean_authors}}}")

            # Title
            title = data.get('Article Title') or data.get('Publication Title')
            if title: fields.append(f"  title = {{{title}}}")

            # Container Title (Journal/Book)
            container = data.get('Publication Title')
            if bib_type == "article":
                if container: fields.append(f"  journal = {{{container}}}")
            elif bib_type in ["inproceedings", "incollection"]:
                if container: fields.append(f"  booktitle = {{{container}}}")

            # Standard Fields
            if data.get('Year'): fields.append(f"  year = {{{data.get('Year')}}}")
            if data.get('Volume'): fields.append(f"  volume = {{{data.get('Volume')}}}")
            if data.get('Issue'): fields.append(f"  number = {{{data.get('Issue')}}}")
            if data.get('Pages'): fields.append(f"  pages = {{{data.get('Pages')}}}")
            if data.get('Publisher'): fields.append(f"  publisher = {{{data.get('Publisher')}}}")
            if data.get('DOI'): fields.append(f"  doi = {{{data.get('DOI')}}}")
            if data.get('URL'): fields.append(f"  url = {{{data.get('URL')}}}")
            if data.get('Institution'): fields.append(f"  school = {{{data.get('Institution')}}}")

            lines.append(",\n".join(fields))
            lines.append("}\n")

        return "\n".join(lines)

    def generate_ris_report(self) -> str:
        """Generates a .ris file content."""
        lines = []
        for item in self.results:
            data = item['citation_data']

            # Type Mapping
            raw_type = data.get('Type', '').lower()
            ty = "GEN"  # Generic
            if "journal" in raw_type:
                ty = "JOUR"
            elif "book chapter" in raw_type:
                ty = "CHAP"
            elif "book" in raw_type:
                ty = "BOOK"
            elif "conference" in raw_type:
                ty = "CONF"
            elif "thesis" in raw_type:
                ty = "THES"
            elif "report" in raw_type:
                ty = "RPRT"
            elif "web" in raw_type:
                ty = "ELEC"

            lines.append(f"TY  - {ty}")

            # Fields
            title = data.get('Article Title') or data.get('Publication Title', '')
            if title: lines.append(f"TI  - {title}")

            authors = data.get('Authors') or data.get('Author', '')
            if authors:
                for au in authors.split(';'):
                    if au.strip(): lines.append(f"AU  - {au.strip()}")

            # Container
            container = data.get('Publication Title')
            if container and ty in ["JOUR", "CONF"]:
                lines.append(f"JO  - {container}")
            elif container and ty == "CHAP":
                lines.append(f"T2  - {container}")

            if data.get('Year'): lines.append(f"PY  - {data.get('Year')}")
            if data.get('Volume'): lines.append(f"VL  - {data.get('Volume')}")
            if data.get('Issue'): lines.append(f"IS  - {data.get('Issue')}")

            # Pages
            pages = data.get('Pages', '')
            if '-' in pages:
                sp, ep = pages.split('-', 1)
                lines.append(f"SP  - {sp.strip()}")
                lines.append(f"EP  - {ep.strip()}")
            elif pages:
                lines.append(f"SP  - {pages}")

            if data.get('Publisher'): lines.append(f"PB  - {data.get('Publisher')}")
            if data.get('DOI'): lines.append(f"DO  - {data.get('DOI')}")
            if data.get('URL'): lines.append(f"UR  - {data.get('URL')}")
            if data.get('Institution'): lines.append(f"A3  - {data.get('Institution')}")  # Institution/Sponsor

            lines.append("ER  - \n")

        return "\n".join(lines)

    def _calculate_statistics(self):
        stats = {
            'total': len(self.results),
            'validated': 0,
            'possible_match': 0,
            'not_validated': 0,
            'error': 0,
            'sources': {}
        }
        for item in self.results:
            r = item['result']
            if r.status == "Validated":
                stats['validated'] += 1
            elif r.status == "Possible Match":
                stats['possible_match'] += 1
            elif "Error" in r.status:
                stats['error'] += 1
            else:
                stats['not_validated'] += 1

            if r.confidence_score > 0:
                stats['sources'][r.source_name] = stats['sources'].get(r.source_name, 0) + 1

        t = stats['total'] if stats['total'] > 0 else 1
        stats['pct_val'] = round((stats['validated'] / t) * 100, 1)
        stats['pct_pos'] = round((stats['possible_match'] / t) * 100, 1)
        stats['pct_not'] = round((stats['not_validated'] / t) * 100, 1)
        return stats

    def save_report(self, output_path: str, report_type: str = 'md'):
        """Saves the report to the specified path."""
        content = ""
        # Handle formats
        if report_type == 'csv':
            content = self.generate_csv_report()
            if not output_path.lower().endswith('.csv'): output_path += '.csv'
        elif report_type == 'bib':
            content = self.generate_bibtex_report()
            if not output_path.lower().endswith('.bib'): output_path += '.bib'
        elif report_type == 'ris':
            content = self.generate_ris_report()
            if not output_path.lower().endswith('.ris'): output_path += '.ris'
        else:
            # Default MD
            content = self.generate_markdown_report()
            if not output_path.lower().endswith('.md'): output_path += '.md'

        try:
            with open(output_path, 'w', encoding='utf-8', newline='') as f:
                f.write(content)
            print(f"Report ({report_type.upper()}) saved successfully to: {output_path}")
            return output_path
        except Exception as e:
            print(f"Failed to save {report_type} report: {e}")
            return None