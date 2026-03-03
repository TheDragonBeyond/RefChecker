# ui/manual_entry.py
#
# Redesigned Manual Entry tab for the Unified Citation System.
#
# Key improvements over v1:
#   - Type is a dropdown (combobox) with the canonical CITATION_TYPES list
#   - Authors are entered individually (Last, First) and managed via a listbox
#   - Fields are tiered: primary fields always visible, secondary/tertiary
#     fields in collapsible sections (collapsed by default)
#   - Keyboard shortcuts: Enter to add author, Delete to remove staged citation
#   - Treeview shows only key columns for readability; full data still exported
#
# CSV output format is unchanged — same headers, same semicolon author format.

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import csv
from io import StringIO

from config import Config
from output_handler import OutputHandler
from .components import CollapsiblePane


# ── Canonical citation types ─────────────────────────────────────────────
# Shared with GeminiExtractor.CITATION_TYPES.  Defined here so the UI
# doesn't depend on the extraction plugin being importable.

CITATION_TYPES = [
    "Journal Article",
    "Conference Paper",
    "Book",
    "Book Chapter",
    "Thesis",
    "Report",
    "Website",
    "Magazine Article",
    "Newspaper Article",
    "Encyclopedia Entry",
    "Other",
]


class ManualEntryTab(tk.Frame):
    """
    Manual citation entry tab with structured author input, type dropdown,
    and tiered field visibility.
    """

    def __init__(self, parent, app_reference):
        """
        parent:        The Notebook widget.
        app_reference: Reference to the main CitationApp instance.
        """
        super().__init__(parent, padx=10, pady=10)
        self.app = app_reference
        self.headers = Config.CSV_HEADERS
        self.validate_on_save = tk.BooleanVar(value=False)

        # Internal state
        self._authors_list = []          # List of "Last, First" strings
        self._field_widgets = {}         # header_name → tk.Entry / ttk.Combobox
        self._type_var = tk.StringVar(value=CITATION_TYPES[0])

        try:
            self._cit_num_index = self.headers.index("Citation Number")
        except ValueError:
            self._cit_num_index = 0

        self._init_ui()

    # ══════════════════════════════════════════════════════════════════════
    # UI Construction
    # ══════════════════════════════════════════════════════════════════════

    def _init_ui(self):
        # ── Top action bar ───────────────────────────────────────────────
        btn_frame = tk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=(0, 8), side=tk.TOP)

        tk.Button(btn_frame, text="Remove Selected",
                  command=self._remove_selected).pack(side=tk.LEFT)

        tk.Button(btn_frame, text="Save CSV", bg="#c8e6c9",
                  font=("Arial", 10, "bold"),
                  command=self._perform_save).pack(side=tk.RIGHT)

        tk.Checkbutton(btn_frame, text="Validate After Saving",
                       variable=self.validate_on_save
                       ).pack(side=tk.RIGHT, padx=(0, 10))

        # ── Scrollable form area ─────────────────────────────────────────
        # Wraps the form in a canvas so it can scroll when the window is small
        form_outer = tk.Frame(self)
        form_outer.pack(fill=tk.BOTH, expand=True)

        form_canvas = tk.Canvas(form_outer, highlightthickness=0)
        form_scrollbar = ttk.Scrollbar(form_outer, orient="vertical",
                                       command=form_canvas.yview)
        self._form_scroll_frame = tk.Frame(form_canvas)

        self._form_scroll_frame.bind(
            "<Configure>",
            lambda e: form_canvas.configure(
                scrollregion=form_canvas.bbox("all"))
        )

        form_canvas.create_window((0, 0), window=self._form_scroll_frame,
                                  anchor="nw")
        form_canvas.configure(yscrollcommand=form_scrollbar.set)

        form_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        form_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mousewheel scrolling
        def _on_mousewheel(event):
            form_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        form_canvas.bind_all("<MouseWheel>", _on_mousewheel, add="+")

        parent_frame = self._form_scroll_frame

        # ── Next-ID indicator ────────────────────────────────────────────
        self._next_id_label = tk.Label(parent_frame,
                                       text="Next Citation ID: 1",
                                       fg="#1565c0",
                                       font=("Arial", 10, "bold"))
        self._next_id_label.pack(anchor="w", padx=5, pady=(5, 8))

        # ── SECTION: Primary Fields ──────────────────────────────────────
        primary = tk.LabelFrame(parent_frame, text="Primary Fields",
                                padx=10, pady=8, font=("Arial", 10, "bold"))
        primary.pack(fill=tk.X, padx=5, pady=(0, 5))

        self._build_type_row(primary)
        self._build_entry_row(primary, "Article Title", width=60,
                              placeholder="Title of the article or paper")
        self._build_entry_row(primary, "Publication Title", width=60,
                              placeholder="Journal, book, or proceedings name")

        year_doi_frame = tk.Frame(primary)
        year_doi_frame.pack(fill=tk.X, pady=2)
        self._build_entry_inline(year_doi_frame, "Year", width=8,
                                 placeholder="e.g. 2024", side=tk.LEFT)
        self._build_entry_inline(year_doi_frame, "DOI", width=40,
                                 placeholder="e.g. 10.1000/xyz123", side=tk.LEFT,
                                 padx=(20, 0))

        # ── SECTION: Authors ─────────────────────────────────────────────
        self._build_author_section(parent_frame)

        # ── SECTION: Publication Details (collapsed) ─────────────────────
        pub_pane = CollapsiblePane(parent_frame,
                                   title="Publication Details (Volume, Pages, Publisher…)",
                                   expanded=False)
        pub_pane.pack(fill=tk.X, padx=5, pady=2)
        pf = pub_pane.content_frame

        row1 = tk.Frame(pf)
        row1.pack(fill=tk.X, pady=2)
        self._build_entry_inline(row1, "Volume", width=8, side=tk.LEFT)
        self._build_entry_inline(row1, "Issue", width=8, side=tk.LEFT, padx=(15, 0))
        self._build_entry_inline(row1, "Pages", width=12, side=tk.LEFT, padx=(15, 0),
                                 placeholder="e.g. 10-25")

        self._build_entry_row(pf, "Publisher", width=40)
        self._build_entry_row(pf, "Publication Location", width=40,
                              placeholder="e.g. New York, NY")
        self._build_entry_row(pf, "Editor", width=40,
                              placeholder="For edited volumes")
        self._build_entry_row(pf, "Series", width=40,
                              placeholder="Book or conference series name")

        # ── SECTION: Additional Info & Identifiers (collapsed) ───────────
        extra_pane = CollapsiblePane(parent_frame,
                                     title="Additional Info & Identifiers",
                                     expanded=False)
        extra_pane.pack(fill=tk.X, padx=5, pady=2)
        ef = extra_pane.content_frame

        date_row = tk.Frame(ef)
        date_row.pack(fill=tk.X, pady=2)
        self._build_entry_inline(date_row, "Month", width=10, side=tk.LEFT)
        self._build_entry_inline(date_row, "Day", width=5, side=tk.LEFT, padx=(15, 0))
        self._build_entry_inline(date_row, "Edition", width=8, side=tk.LEFT, padx=(15, 0))

        self._build_entry_row(ef, "Institution", width=40,
                              placeholder="For theses or reports")

        id_row = tk.Frame(ef)
        id_row.pack(fill=tk.X, pady=2)
        self._build_entry_inline(id_row, "ISBN", width=18, side=tk.LEFT)
        self._build_entry_inline(id_row, "ISSN", width=12, side=tk.LEFT, padx=(15, 0))

        self._build_entry_row(ef, "URL", width=60)
        self._build_entry_row(ef, "Date Accessed", width=15,
                              placeholder="e.g. 2024-03-15")

        # ── Add-to-List button ───────────────────────────────────────────
        tk.Button(parent_frame, text="  Add to List ⇩  ",
                  bg="#e1f5fe", font=("Arial", 10, "bold"),
                  command=self._add_entry_to_list
                  ).pack(pady=(10, 5), anchor="center")

        # ── SECTION: Staged Citations (Treeview) ─────────────────────────
        self._build_staged_list(parent_frame)

        # ── Keyboard bindings ────────────────────────────────────────────
        self.bind_all("<Delete>", self._on_delete_key)

    # ── Type dropdown ────────────────────────────────────────────────────

    def _build_type_row(self, parent):
        row = tk.Frame(parent)
        row.pack(fill=tk.X, pady=3)
        tk.Label(row, text="Type:", width=18, anchor="w",
                 font=("Arial", 9)).pack(side=tk.LEFT)
        combo = ttk.Combobox(row, textvariable=self._type_var,
                             values=CITATION_TYPES, state="readonly", width=22)
        combo.pack(side=tk.LEFT, padx=(0, 10))
        # Store reference so we can clear it
        self._field_widgets["Type"] = combo

    # ── Generic entry helpers ────────────────────────────────────────────

    def _build_entry_row(self, parent, header, width=30, placeholder=""):
        """Full-width label + entry on its own row."""
        row = tk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        tk.Label(row, text=f"{header}:", width=18, anchor="w",
                 font=("Arial", 9)).pack(side=tk.LEFT)
        entry = tk.Entry(row, width=width)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        if placeholder:
            self._set_placeholder(entry, placeholder)
        self._field_widgets[header] = entry

    def _build_entry_inline(self, parent, header, width=12, side=tk.LEFT,
                            padx=(0, 0), placeholder=""):
        """Compact label + entry packed inline within an existing frame."""
        tk.Label(parent, text=f"{header}:", anchor="w",
                 font=("Arial", 9)).pack(side=side, padx=(padx[0] if isinstance(padx, tuple) else padx, 2))
        entry = tk.Entry(parent, width=width)
        entry.pack(side=side, padx=(0, padx[1] if isinstance(padx, tuple) else 0))
        if placeholder:
            self._set_placeholder(entry, placeholder)
        self._field_widgets[header] = entry

    @staticmethod
    def _set_placeholder(entry, text):
        """Simple placeholder text that clears on focus."""
        entry.insert(0, text)
        entry.config(fg="grey")

        def _on_focus_in(e):
            if entry.get() == text:
                entry.delete(0, tk.END)
                entry.config(fg="black")

        def _on_focus_out(e):
            if not entry.get():
                entry.insert(0, text)
                entry.config(fg="grey")

        entry.bind("<FocusIn>", _on_focus_in)
        entry.bind("<FocusOut>", _on_focus_out)

    # ══════════════════════════════════════════════════════════════════════
    # Author Section
    # ══════════════════════════════════════════════════════════════════════

    def _build_author_section(self, parent):
        author_frame = tk.LabelFrame(parent, text="Authors",
                                     padx=10, pady=8,
                                     font=("Arial", 10, "bold"))
        author_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        # Input row
        input_row = tk.Frame(author_frame)
        input_row.pack(fill=tk.X, pady=(0, 5))

        tk.Label(input_row, text="Last Name:", font=("Arial", 9)).pack(side=tk.LEFT)
        self._author_last = tk.Entry(input_row, width=18)
        self._author_last.pack(side=tk.LEFT, padx=(2, 10))

        tk.Label(input_row, text="First / Initials:", font=("Arial", 9)).pack(side=tk.LEFT)
        self._author_first = tk.Entry(input_row, width=14)
        self._author_first.pack(side=tk.LEFT, padx=(2, 10))

        tk.Button(input_row, text="Add Author +", bg="#e3f2fd",
                  command=self._add_author).pack(side=tk.LEFT, padx=5)

        # Bind Enter key in author fields
        self._author_last.bind("<Return>", lambda e: self._add_author())
        self._author_first.bind("<Return>", lambda e: self._add_author())

        # Author list + controls
        list_row = tk.Frame(author_frame)
        list_row.pack(fill=tk.X)

        self._author_listbox = tk.Listbox(list_row, height=4, width=45,
                                          font=("Consolas", 9))
        self._author_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)

        btn_col = tk.Frame(list_row)
        btn_col.pack(side=tk.LEFT, padx=(8, 0))

        tk.Button(btn_col, text="▲ Up", width=8,
                  command=self._move_author_up).pack(pady=1)
        tk.Button(btn_col, text="▼ Down", width=8,
                  command=self._move_author_down).pack(pady=1)
        tk.Button(btn_col, text="✕ Remove", width=8, fg="#c62828",
                  command=self._remove_author).pack(pady=1)

    def _add_author(self):
        last = self._author_last.get().strip()
        first = self._author_first.get().strip()

        if not last:
            # If only first name provided (mononymous), use it as last
            if first:
                last = first
                first = ""
            else:
                return  # Nothing entered

        if first:
            display = f"{last}, {first}"
        else:
            display = last

        self._authors_list.append(display)
        self._refresh_author_listbox()

        # Clear and refocus
        self._author_last.delete(0, tk.END)
        self._author_first.delete(0, tk.END)
        self._author_last.focus_set()

    def _remove_author(self):
        sel = self._author_listbox.curselection()
        if sel:
            idx = sel[0]
            self._authors_list.pop(idx)
            self._refresh_author_listbox()

    def _move_author_up(self):
        sel = self._author_listbox.curselection()
        if sel and sel[0] > 0:
            idx = sel[0]
            self._authors_list[idx - 1], self._authors_list[idx] = \
                self._authors_list[idx], self._authors_list[idx - 1]
            self._refresh_author_listbox()
            self._author_listbox.selection_set(idx - 1)

    def _move_author_down(self):
        sel = self._author_listbox.curselection()
        if sel and sel[0] < len(self._authors_list) - 1:
            idx = sel[0]
            self._authors_list[idx + 1], self._authors_list[idx] = \
                self._authors_list[idx], self._authors_list[idx + 1]
            self._refresh_author_listbox()
            self._author_listbox.selection_set(idx + 1)

    def _refresh_author_listbox(self):
        self._author_listbox.delete(0, tk.END)
        for i, author in enumerate(self._authors_list, 1):
            self._author_listbox.insert(tk.END, f"  {i}. {author}")

    def _get_authors_string(self) -> str:
        """Serialize the author list to the canonical semicolon format."""
        return "; ".join(self._authors_list)

    # ══════════════════════════════════════════════════════════════════════
    # Staged Citations List
    # ══════════════════════════════════════════════════════════════════════

    def _build_staged_list(self, parent):
        list_frame = tk.LabelFrame(parent, text="Staged Citations (Ready to Save)",
                                   padx=10, pady=8)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0), padx=5)

        # Show only key columns in the Treeview for readability
        self._display_columns = [
            "Citation Number", "Type", "Authors",
            "Article Title", "Year", "DOI"
        ]

        self._tree = ttk.Treeview(list_frame,
                                  columns=self._display_columns,
                                  show="headings", height=6)

        col_widths = {
            "Citation Number": 40,
            "Type": 110,
            "Authors": 180,
            "Article Title": 250,
            "Year": 50,
            "DOI": 130,
        }

        for col in self._display_columns:
            self._tree.heading(col, text=col)
            self._tree.column(col, width=col_widths.get(col, 100), minwidth=40)

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(list_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        # Store full row data separately (keyed by Treeview item id)
        # because the Treeview only shows display columns
        self._full_row_data = {}

    # ══════════════════════════════════════════════════════════════════════
    # Add / Remove / Save Logic
    # ══════════════════════════════════════════════════════════════════════

    def _get_entry_value(self, header: str) -> str:
        """
        Retrieves the current value from a field widget, respecting
        placeholder text (returns empty string if placeholder is showing).
        """
        widget = self._field_widgets.get(header)
        if widget is None:
            return ""

        if isinstance(widget, ttk.Combobox):
            return widget.get()

        val = widget.get().strip()

        # Check if this is placeholder text (grey foreground)
        try:
            if widget.cget("fg") == "grey":
                return ""
        except tk.TclError:
            pass

        return val

    def _add_entry_to_list(self):
        """Harvest all fields, build a full row, insert into the Treeview."""
        # Build a dict of header → value for ALL CSV headers
        row_map = {}

        # Auto-increment citation number
        next_id = self._get_next_citation_number()
        row_map["Citation Number"] = str(next_id)

        # Type from combobox
        row_map["Type"] = self._type_var.get()

        # Authors from the structured list
        row_map["Authors"] = self._get_authors_string()

        # All other fields from entry widgets
        for header in self.headers:
            if header in ("Citation Number", "Type", "Authors"):
                continue
            row_map[header] = self._get_entry_value(header)

        # Validate: at least one meaningful field besides Citation Number
        meaningful = [v for k, v in row_map.items()
                      if k != "Citation Number" and v]
        if not meaningful:
            messagebox.showwarning("Empty Input",
                                   "Please fill in at least one field "
                                   "(Title, Authors, or Year at minimum).")
            return

        # Build the full row list in CSV_HEADERS order
        full_values = [row_map.get(h, "") for h in self.headers]

        # Build the display row (only key columns)
        display_values = [row_map.get(h, "") for h in self._display_columns]

        # Insert into Treeview
        item_id = self._tree.insert("", "end", values=display_values)

        # Store full data for CSV export
        self._full_row_data[item_id] = full_values

        # Clear the form
        self._clear_form()
        self._update_next_id_label()

    def _clear_form(self):
        """Reset all input fields to their default/empty state."""
        # Reset type to first option
        self._type_var.set(CITATION_TYPES[0])

        # Clear author list
        self._authors_list.clear()
        self._refresh_author_listbox()
        self._author_last.delete(0, tk.END)
        self._author_first.delete(0, tk.END)

        # Clear all entry widgets — need to handle placeholders
        for header, widget in self._field_widgets.items():
            if isinstance(widget, ttk.Combobox):
                continue  # Handled above

            widget.delete(0, tk.END)
            # Re-trigger placeholder by simulating focus out
            widget.event_generate("<FocusOut>")

    def _remove_selected(self):
        selected = self._tree.selection()
        for item_id in selected:
            self._full_row_data.pop(item_id, None)
            self._tree.delete(item_id)
        self._update_next_id_label()

    def _on_delete_key(self, event):
        """Handle Delete key — only act when the tree has focus and selection."""
        if self._tree.focus() and self._tree.selection():
            self._remove_selected()

    def _get_next_citation_number(self) -> int:
        """Calculate next ID from the max existing ID in staged data."""
        max_id = 0
        for item_id in self._tree.get_children():
            row = self._full_row_data.get(item_id, [])
            if row:
                try:
                    cid = int(str(row[self._cit_num_index]))
                    if cid > max_id:
                        max_id = cid
                except (ValueError, IndexError):
                    pass
        return max_id + 1

    def _update_next_id_label(self):
        nxt = self._get_next_citation_number()
        self._next_id_label.config(text=f"Next Citation ID: {nxt}")

    def _perform_save(self):
        """Generate CSV from staged items. Optionally trigger validation."""
        all_items = self._tree.get_children()
        if not all_items:
            messagebox.showwarning("No Data",
                                   "Add citations to the list first.")
            return

        # Build CSV
        output_io = StringIO()
        writer = csv.writer(output_io)
        writer.writerow(self.headers)

        for item_id in all_items:
            row = self._full_row_data.get(item_id, [])
            writer.writerow(row)

        csv_content = output_io.getvalue()

        # Save dialog
        output_path = filedialog.asksaveasfilename(
            title="Save Manual References",
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            initialfile="Manual_References.csv"
        )

        if output_path:
            try:
                OutputHandler.save_to_file(csv_content, output_path)

                if self.validate_on_save.get():
                    self.app.validation_file_path.set(output_path)
                    messagebox.showinfo("Saved",
                                        "CSV Saved.\n\nSwitching to Validator Tab.")
                    self.app.notebook.select(0)
                else:
                    messagebox.showinfo("Saved", "CSV Saved Successfully.")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to save file: {e}")