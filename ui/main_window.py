import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, ttk
import threading
import sys
import queue
from pathlib import Path

# Business Logic
from extraction_runner import run_extraction, run_batch_extraction
from validation_runner import run_validation, run_batch_validation_logic
import config
from validators_plugin.manager import ValidatorManager
from plugin_installer import PluginInstaller

# UI Package Imports
from .components import CollapsiblePane, TextRedirector
from .settings_window import SettingsEditor
from .manual_entry import ManualEntryTab


class CitationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Unified Citation System")
        self.root.geometry("1000x950")

        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.progress_val = tk.DoubleVar(value=0.0)

        # Variables - Single Mode
        self.extraction_file_path = tk.StringVar()
        self.validation_file_path = tk.StringVar()
        self.auto_validate = tk.BooleanVar(value=False)
        # Validation Output Options
        self.gen_md = tk.BooleanVar(value=True)
        self.gen_csv = tk.BooleanVar(value=False)
        self.gen_bib = tk.BooleanVar(value=False)
        self.gen_ris = tk.BooleanVar(value=False)

        # Variables - Batch Mode
        self.batch_extract_in_dir = tk.StringVar()
        self.batch_extract_out_dir = tk.StringVar()
        self.batch_valid_files_label = tk.StringVar(value="No files selected")
        self.batch_valid_files = []
        self.batch_valid_out_dir = tk.StringVar()

        self.validator_vars = {}
        self._init_validator_vars()
        self._init_ui()
        self.check_configuration_status()
        self._start_log_listener()

    def check_configuration_status(self):
        """Checks if the active extractor is configured and shows/hides a warning banner."""
        from extraction_plugins.manager import ExtractionManager
        manager = ExtractionManager()
        active_extractor = manager.get_active_extractor()

        if not active_extractor.is_configured():
            self._show_config_banner(active_extractor.name)
        elif hasattr(self, 'config_banner'):
            self.config_banner.pack_forget()

    def _show_config_banner(self, plugin_name):
        # Create the banner if it doesn't exist
        if not hasattr(self, 'config_banner'):
            self.config_banner = tk.Frame(self.root, bg="#ffcdd2", cursor="hand2")
            self.banner_label = tk.Label(
                self.config_banner,
                text=f"⚠️ {plugin_name} is not configured. Click here to set API Keys.",
                bg="#ffcdd2", fg="#b71c1c", font=("Arial", 10, "bold"), pady=5
            )
            self.banner_label.pack()

            # Bind click event to open settings
            self.config_banner.bind("<Button-1>", lambda e: self._open_settings_to_extractors())
            self.banner_label.bind("<Button-1>", lambda e: self._open_settings_to_extractors())

        # Pack it at the very top (above the header)
        self.config_banner.pack(side=tk.TOP, fill=tk.X, before=self.main_splitter)

    def _open_settings_to_extractors(self):
        # UPDATED: Pass the callback explicitly
        editor = SettingsEditor(self.root, on_save_callback=self.check_configuration_status)
        # Programmatically switch to the "Extractors" tab
        # Assuming "Extractors" is the second tab (index 1) based on _init_ui in settings_window.py
        editor.notebook.select(1)

    def _init_validator_vars(self):
        temp_manager = ValidatorManager()
        all_validators = temp_manager.primary_validators + temp_manager.research_validators

        # Load saved list from config
        saved_validators = config.ENABLED_VALIDATORS

        for v in all_validators:
            # If config is None (first run), default to True.
            # Otherwise check if the name is in the saved list.
            if saved_validators is None:
                is_enabled = True
            else:
                is_enabled = (v.name in saved_validators)

            self.validator_vars[v.name] = tk.BooleanVar(value=is_enabled)

    def _init_ui(self):
        self._create_menu()

        # 1. Header (Fixed at Top)
        header_frame = tk.Frame(self.root, pady=10, bg="#2c3e50")
        header_frame.pack(fill=tk.X)
        tk.Label(header_frame, text="Unified Citation Extractor & Validator",
                 font=("Helvetica", 16, "bold"), bg="#2c3e50", fg="white").pack()

        # 2. Main Split Container (PanedWindow)
        self.main_splitter = tk.PanedWindow(self.root, orient=tk.VERTICAL, sashwidth=4, sashrelief=tk.RAISED,
                                            bg="#d0d0d0")
        self.main_splitter.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # --- Top Pane: Inputs (Notebook) ---
        self.top_pane = tk.Frame(self.main_splitter)
        self.main_splitter.add(self.top_pane, height=380)

        self.notebook = ttk.Notebook(self.top_pane)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_single = tk.Frame(self.notebook, padx=5, pady=5)
        self.tab_batch = tk.Frame(self.notebook, padx=5, pady=5)
        self.tab_manual = ManualEntryTab(self.notebook, self)

        self.notebook.add(self.tab_single, text="Single File Mode")
        self.notebook.add(self.tab_batch, text="Batch Processing Mode")
        self.notebook.add(self.tab_manual, text="Manual Entry")

        # Build Tab Content
        self._init_single_mode_ui(self.tab_single)
        self._init_batch_mode_ui(self.tab_batch)

        # NEW: Bind Tab Change Event to toggle Log visibility
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # --- Bottom Pane: Progress & Logs ---
        self.bottom_pane = tk.Frame(self.main_splitter)
        self.main_splitter.add(self.bottom_pane)

        # Progress Bar
        progress_frame = tk.Frame(self.bottom_pane, padx=15, pady=5)
        progress_frame.pack(fill=tk.X)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_val, maximum=100)
        self.progress_bar.pack(fill=tk.X)

        # Logs
        log_container = tk.LabelFrame(self.bottom_pane, text="System Output / Logs", padx=5, pady=5)
        log_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.log_area = scrolledtext.ScrolledText(log_container, font=("Consolas", 9))
        self.log_area.pack(fill=tk.BOTH, expand=True)

        # Redirect Streams
        sys.stdout = TextRedirector(self.log_area, self.log_queue)
        sys.stderr = TextRedirector(self.log_area, self.log_queue)

    def _init_single_mode_ui(self, parent):
        # 1. Extraction Pane
        self.pane_extract = CollapsiblePane(parent, title="1. Extraction (Document -> CSV)", expanded=True)
        self.pane_extract.pack(fill=tk.X, pady=5)
        ex_frame = self.pane_extract.content_frame

        tk.Label(ex_frame, text="Input Document or URL:").grid(row=0, column=0, sticky="w")
        tk.Entry(ex_frame, textvariable=self.extraction_file_path, width=60).grid(row=0, column=1, padx=5)
        # UPDATED CALL: Pass 'LAST_DIR_EXTRACTION_INPUT'
        tk.Button(ex_frame, text="Browse...",
                  command=lambda: self._browse_file(self.extraction_file_path, "doc", "LAST_DIR_EXTRACTION_INPUT")
                  ).grid(row=0, column=2, padx=5)

        ex_btn_frame = tk.Frame(ex_frame, pady=10)
        ex_btn_frame.grid(row=1, column=0, columnspan=3, sticky="ew")

        tk.Label(ex_btn_frame, text=f"Using: {config.ACTIVE_EXTRACTOR}", fg="gray", font=("Arial", 8)).pack(
            side=tk.LEFT)
        tk.Button(ex_btn_frame, text="RUN EXTRACTION", bg="#e1f5fe", font=("Arial", 9, "bold"),
                  command=self.run_extraction_thread, width=20).pack(side=tk.LEFT, padx=15)
        tk.Checkbutton(ex_btn_frame, text="Auto-Validate", variable=self.auto_validate).pack(side=tk.LEFT)

        # 2. Validation Pane
        self.pane_valid = CollapsiblePane(parent, title="2. Validation (CSV -> Report)", expanded=True)
        self.pane_valid.pack(fill=tk.X, pady=5)
        val_frame = self.pane_valid.content_frame

        tk.Label(val_frame, text="Input CSV:").grid(row=0, column=0, sticky="w")
        tk.Entry(val_frame, textvariable=self.validation_file_path, width=60).grid(row=0, column=1, padx=5)
        # UPDATED CALL: Pass 'LAST_DIR_VALIDATION_INPUT'
        tk.Button(val_frame, text="Browse...",
                  command=lambda: self._browse_file(self.validation_file_path, "csv", "LAST_DIR_VALIDATION_INPUT")
                  ).grid(row=0, column=2, padx=5)

        val_btn_frame = tk.Frame(val_frame, pady=10)
        val_btn_frame.grid(row=1, column=0, columnspan=3, sticky="ew")

        tk.Button(val_btn_frame, text="RUN VALIDATION", bg="#e8f5e9", font=("Arial", 9, "bold"),
                  command=self.run_validation_thread, width=20).pack(side=tk.LEFT, padx=15)
        self._add_validation_options(val_btn_frame)

    def _init_batch_mode_ui(self, parent):
        # 1. Batch Extraction
        pane_batch_ex = CollapsiblePane(parent, title="Batch Extraction (Directory Scan)", expanded=True)
        pane_batch_ex.pack(fill=tk.X, pady=5)
        bx_frame = pane_batch_ex.content_frame

        tk.Label(bx_frame, text="Input Directory:").grid(row=0, column=0, sticky="w")
        tk.Entry(bx_frame, textvariable=self.batch_extract_in_dir, width=50).grid(row=0, column=1, padx=5)
        # UPDATED CALL: Pass 'LAST_DIR_EXTRACTION_INPUT'
        tk.Button(bx_frame, text="Select Dir...",
                  command=lambda: self._browse_dir(self.batch_extract_in_dir, "LAST_DIR_EXTRACTION_INPUT")
                  ).grid(row=0, column=2)

        tk.Label(bx_frame, text="Output Directory:").grid(row=1, column=0, sticky="w")
        tk.Entry(bx_frame, textvariable=self.batch_extract_out_dir, width=50).grid(row=1, column=1, padx=5)
        # UPDATED CALL: Pass 'LAST_DIR_EXTRACTION_OUTPUT'
        tk.Button(bx_frame, text="Select Dir...",
                  command=lambda: self._browse_dir(self.batch_extract_out_dir, "LAST_DIR_EXTRACTION_OUTPUT")
                  ).grid(row=1, column=2)

        tk.Button(bx_frame, text="RUN BATCH EXTRACTION", bg="#e1f5fe", font=("Arial", 9, "bold"),
                  command=self.run_batch_extraction_thread).grid(row=2, column=1, pady=10, sticky="w")

        # 2. Batch Validation
        pane_batch_val = CollapsiblePane(parent, title="Batch Validation (Merged Queue)", expanded=True)
        pane_batch_val.pack(fill=tk.X, pady=5)
        bv_frame = pane_batch_val.content_frame

        tk.Label(bv_frame, text="Input Files:").grid(row=0, column=0, sticky="w")
        tk.Label(bv_frame, textvariable=self.batch_valid_files_label, fg="blue", width=50, anchor="w").grid(row=0,
                                                                                                            column=1)
        # _browse_multi_files handles the memory internally now
        tk.Button(bv_frame, text="Select CSVs...", command=self._browse_multi_files).grid(row=0, column=2)

        tk.Label(bv_frame, text="Output Folder:").grid(row=1, column=0, sticky="w")
        tk.Entry(bv_frame, textvariable=self.batch_valid_out_dir, width=50).grid(row=1, column=1, padx=5)
        # UPDATED CALL: Pass 'LAST_DIR_VALIDATION_OUTPUT'
        tk.Button(bv_frame, text="Select Dir...",
                  command=lambda: self._browse_dir(self.batch_valid_out_dir, "LAST_DIR_VALIDATION_OUTPUT")
                  ).grid(row=1, column=2)
        tk.Button(bv_frame, text="RUN BATCH VALIDATION", bg="#e8f5e9", font=("Arial", 9, "bold"),
                  command=self.run_batch_validation_thread).grid(row=2, column=1, pady=10, sticky="w")

    def _add_validation_options(self, parent_frame):
        tk.Button(parent_frame, text="STOP", bg="#ffcdd2", fg="red", font=("Arial", 9, "bold"),
                  command=self.stop_operations, width=8).pack(side=tk.LEFT, padx=5)
        tk.Label(parent_frame, text="Report:").pack(side=tk.LEFT, padx=5)
        tk.Checkbutton(parent_frame, text="MD", variable=self.gen_md).pack(side=tk.LEFT)
        tk.Checkbutton(parent_frame, text="CSV", variable=self.gen_csv).pack(side=tk.LEFT)
        tk.Checkbutton(parent_frame, text="BIB", variable=self.gen_bib).pack(side=tk.LEFT)
        tk.Checkbutton(parent_frame, text="RIS", variable=self.gen_ris).pack(side=tk.LEFT)
        tk.Button(parent_frame, text="Plugins...", command=self._open_quick_plugin_toggle).pack(side=tk.LEFT, padx=10)

    # --- Helpers ---
    def _create_menu(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Install Plugin...", command=self._install_plugin_flow)

        # UPDATED: Pass callback here as well
        file_menu.add_command(label="Configuration...",
                              command=lambda: SettingsEditor(self.root,
                                                             on_save_callback=self.check_configuration_status))

        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menubar)

    def _open_quick_plugin_toggle(self):
        win = tk.Toplevel(self.root)
        win.title("Active Plugins")
        win.geometry("350x500")  # Increased height for save button

        tk.Label(win, text="Select Active Validators:", font=("Helvetica", 11, "bold")).pack(pady=10)

        scroll_frame = tk.Frame(win)
        scroll_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        # Simple list of checkboxes
        for name, var in self.validator_vars.items():
            tk.Checkbutton(scroll_frame, text=name, variable=var).pack(anchor="w", pady=2)

        # SAVE BUTTON
        btn_frame = tk.Frame(win, pady=15)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        tk.Button(btn_frame, text="Save Configuration", bg="#4caf50", fg="white", font=("Arial", 10, "bold"),
                  command=lambda: self._save_validator_config(win)).pack(pady=5)

    def _save_validator_config(self, window):
        """Helper to save the current selection to settings.json"""
        # Create a list of names where the variable is True
        enabled_list = [name for name, var in self.validator_vars.items() if var.get()]

        # Save to Config
        config.save_settings({"ENABLED_VALIDATORS": enabled_list})
        config.Config.reload()

        messagebox.showinfo("Success",
                            "Validator preferences saved.\nThese settings will persist next time you open the app.")
        window.destroy()

    def _install_plugin_flow(self):
        filename = filedialog.askopenfilename(filetypes=[("Python Files", "*.py")])
        if filename:
            try:
                msg = PluginInstaller.install_plugin(filename)
                messagebox.showinfo("Success", msg)
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def _browse_file(self, target_var, ftype, config_key=None):
        """
        Browses for a file.
        :param config_key: The key in config.py to read/write the 'initialdir' from.
                           Stored as a relative path, resolved to absolute for the dialog.
        """
        # 1. Get the stored (relative) path and resolve it to absolute
        if config_key:
            stored = config._current_settings.get(config_key, "source")
            initial = config.resolve_path(stored)
        else:
            initial = config.get_base_path()

        ftypes = [("CSV", "*.csv")] if ftype == "csv" else [("Docs", "*.pdf *.txt *.rtf")]

        f = filedialog.askopenfilename(filetypes=ftypes, initialdir=initial)

        if f:
            target_var.set(f)
            # 2. Convert the parent directory back to relative and save
            if config_key:
                parent_dir = str(Path(f).parent)
                relative_dir = config.to_relative_path(parent_dir)
                config.save_settings({config_key: relative_dir})

    def _browse_dir(self, target_var, config_key=None):
        """
        Browses for a directory.
        :param config_key: The key in config.py to read/write the 'initialdir' from.
                           Stored as a relative path, resolved to absolute for the dialog.
        """
        if config_key:
            stored = config._current_settings.get(config_key, "output")
            initial = config.resolve_path(stored)
        else:
            initial = config.get_base_path()

        d = filedialog.askdirectory(initialdir=initial)

        if d:
            target_var.set(d)
            if config_key:
                relative_dir = config.to_relative_path(d)
                config.save_settings({config_key: relative_dir})

    def _browse_multi_files(self):
        # Uses Validation Input memory
        key = "LAST_DIR_VALIDATION_INPUT"
        stored = config._current_settings.get(key, "output")
        initial = config.resolve_path(stored)

        files = filedialog.askopenfilenames(filetypes=[("CSV Files", "*.csv")], initialdir=initial)

        if files:
            self.batch_valid_files = list(files)
            self.batch_valid_files_label.set(f"{len(files)} files selected")
            # Save directory of the first file as relative
            if len(files) > 0:
                parent_dir = str(Path(files[0]).parent)
                relative_dir = config.to_relative_path(parent_dir)
                config.save_settings({key: relative_dir})

    def _start_log_listener(self):
        try:
            while True:
                self.log_area.insert(tk.END, self.log_queue.get_nowait())
                self.log_area.see(tk.END)
        except queue.Empty:
            pass
        self.root.after(100, self._start_log_listener)

    def stop_operations(self):
        self.stop_event.set()

    def _on_tab_changed(self, event):
        selected_tab_id = self.notebook.select()
        if selected_tab_id == str(self.tab_manual):
            self.main_splitter.forget(self.bottom_pane)
        else:
            if str(self.bottom_pane) not in self.main_splitter.panes():
                self.main_splitter.add(self.bottom_pane)

    # --- Thread Runners ---
    def run_extraction_thread(self):
        self._start_thread(self._single_extraction)

    def _single_extraction(self):
        inp = self.extraction_file_path.get()
        if not inp: return

        # 1. Get memory for output directory (resolve relative to absolute)
        key = "LAST_DIR_EXTRACTION_OUTPUT"
        stored = config._current_settings.get(key, "output")
        initial = config.resolve_path(stored)

        # 2. Open Save Dialog with resolved absolute path
        out = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialdir=initial,
            initialfile=Path(inp).stem + "_references.csv"
        )
        if not out: return

        # 3. Save new memory as relative path
        relative_dir = config.to_relative_path(str(Path(out).parent))
        config.save_settings({key: relative_dir})

        # 4. Proceed with Extraction
        self.progress_val.set(10)
        res = run_extraction(inp, out)
        self.progress_val.set(100)

        # 5. Auto-Validate if checked
        if res and self.auto_validate.get():
            self.validation_file_path.set(res)
            self._execute_validation(res, str(Path(res).with_suffix('')) + "_Validation_Report")

    def run_validation_thread(self):
        inp = self.validation_file_path.get()
        if not inp: return
        out = filedialog.asksaveasfilename(defaultextension=".md", initialfile=Path(inp).stem + "_Validation_Report")
        if not out: return
        self._start_thread(lambda: self._execute_validation(inp, str(Path(out).with_suffix(''))))

    def _execute_validation(self, inp, out_base):
        enabled = [n for n, v in self.validator_vars.items() if v.get()]
        run_validation(inp, enabled, out_base,
                       self.gen_md.get(),
                       self.gen_csv.get(),
                       self.gen_bib.get(),
                       self.gen_ris.get(),
                       lambda p: self.root.after(0, lambda: self.progress_val.set(p)), self.stop_event)
        self.root.after(0, lambda: self.progress_val.set(100))

    def run_batch_extraction_thread(self):
        inp = self.batch_extract_in_dir.get()
        out = self.batch_extract_out_dir.get()
        if not inp or not out: return messagebox.showerror("Error", "Select input and output directories.")
        self._start_thread(lambda: run_batch_extraction(
            inp, out, lambda p: self.root.after(0, lambda: self.progress_val.set(p)), self.stop_event))

    def run_batch_validation_thread(self):
        files = self.batch_valid_files
        out = self.batch_valid_out_dir.get()
        if not files or not out: return messagebox.showerror("Error", "Select files and output directory.")

        enabled = [n for n, v in self.validator_vars.items() if v.get()]
        self._start_thread(lambda: run_batch_validation_logic(
            files, out, enabled,
            self.gen_md.get(),
            self.gen_csv.get(),
            self.gen_bib.get(),
            self.gen_ris.get(),
            lambda p: self.root.after(0, lambda: self.progress_val.set(p)), self.stop_event))

    def _start_thread(self, target):
        self.stop_event.clear()
        self.progress_val.set(0)
        threading.Thread(target=target, daemon=True).start()