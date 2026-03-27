import tkinter as tk
from tkinter import ttk, messagebox
import config
from validators_plugin.manager import ValidatorManager
from extraction_plugins.manager import ExtractionManager


class SettingsEditor(tk.Toplevel):
    """
    Window for editing Global Configuration and Plugin Configurations.
    """

    def __init__(self, parent, on_save_callback=None):
        super().__init__(parent)
        self.title("System Configuration")
        self.resizable(True, True)
        self.geometry("700x600")
        self.grab_set()  # Modal window

        self.on_save_callback = on_save_callback

        self.plugin_vars = {}
        self.extractors = []
        self.validators = []
        self._load_plugins()

        # Initialize variables
        self._init_plugin_vars(self.extractors)
        self._init_plugin_vars(self.validators)

        # Footer buttons — packed FIRST so they are always visible at the bottom
        # regardless of window height or content size.
        btn_frame = tk.Frame(self, pady=10, bg="#f0f0f0")
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Button(btn_frame, text="Save All Settings", bg="#4caf50", fg="white",
                  font=("Arial", 10, "bold"),
                  command=self.save_all_settings, width=20).pack(side=tk.RIGHT, padx=20)
        tk.Button(btn_frame, text="Cancel", command=self.destroy,
                  width=10).pack(side=tk.RIGHT, padx=5)

        # Separator above footer
        tk.Frame(self, height=1, bg="#cccccc").pack(fill=tk.X, side=tk.BOTTOM)

        # Notebook fills whatever remains above the footer
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self._init_global_settings()
        self._init_category_tab("Extractors", self.extractors)
        self._init_category_tab("Validators", self.validators)

    def _load_plugins(self):
        ext_manager = ExtractionManager()
        if ext_manager.available_extractors:
            self.extractors = list(ext_manager.available_extractors.values())
            self.extractors.sort(key=lambda x: x.name)

        val_manager = ValidatorManager()
        self.validators = val_manager.primary_validators + val_manager.research_validators
        self.validators.sort(key=lambda x: x.name)

    def _init_plugin_vars(self, plugins):
        for plugin in plugins:
            self.plugin_vars[plugin.name] = {}
            for key, value in plugin.config.items():
                if key.startswith("_"):
                    continue
                if isinstance(value, bool):
                    var = tk.BooleanVar(value=value)
                elif isinstance(value, int):
                    var = tk.IntVar(value=value)
                elif isinstance(value, float):
                    var = tk.DoubleVar(value=value)
                else:
                    var = tk.StringVar(value=str(value))
                self.plugin_vars[plugin.name][key] = var

    # ------------------------------------------------------------------
    # Global tab — scrollable so no content is ever clipped
    # ------------------------------------------------------------------

    def _init_global_settings(self):
        outer = tk.Frame(self.notebook)
        self.notebook.add(outer, text="Global")

        # Canvas + scrollbar for the tab body
        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Inner frame that holds all the actual widgets
        tab = tk.Frame(canvas, padx=10, pady=10)
        window_id = canvas.create_window((0, 0), window=tab, anchor="nw")

        # Keep the inner frame width in sync with the canvas width
        def _on_canvas_resize(event):
            canvas.itemconfig(window_id, width=event.width)

        canvas.bind("<Configure>", _on_canvas_resize)

        # Update scroll region whenever content changes
        tab.bind("<Configure>",
                 lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        # Mousewheel scrolling — handles macOS, Windows, and Linux
        def _on_mousewheel(event):
            if event.num == 4:          # Linux scroll up
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:        # Linux scroll down
                canvas.yview_scroll(1, "units")
            else:                       # macOS / Windows
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<MouseWheel>", _on_mousewheel)
        canvas.bind("<Button-4>", _on_mousewheel)
        canvas.bind("<Button-5>", _on_mousewheel)

        # ── Widget population ────────────────────────────────────────

        self.debug_mode = tk.BooleanVar(value=config.DEBUG_MODE)
        self.adaptive_ordering = tk.BooleanVar(value=config.ADAPTIVE_ORDERING)
        self.llm_enabled = tk.BooleanVar(value=config.LLM_ENABLED)
        self.satisfied_threshold = tk.IntVar(value=config.SATISFIED_THRESHOLD)
        self.llm_conf_threshold = tk.IntVar(value=config.LLM_CONFIDENCE_THRESHOLD)
        self.output_encoding = tk.StringVar(value=config.OUTPUT_ENCODING)
        self.output_suffix = tk.StringVar(value=config.Config.OUTPUT_SUFFIX)
        self.active_extractor = tk.StringVar(value=config.ACTIVE_EXTRACTOR)
        self.report_split_mode = tk.StringVar(value=config.REPORT_SPLIT_MODE)

        tk.Label(tab, text="Core System Settings",
                 font=("Helvetica", 12, "bold")).pack(pady=(0, 15))

        # Extractor selection
        lf_extractor = tk.LabelFrame(tab, text="Extraction Engine (Artifact 1)",
                                     padx=10, pady=10)
        lf_extractor.pack(fill=tk.X, pady=5)
        tk.Label(lf_extractor, text="Active Extractor Plugin:",
                 width=25, anchor="w").pack(side=tk.LEFT)
        extractor_options = ([e.name for e in self.extractors]
                             if self.extractors else ["No Extractors Found"])
        ttk.Combobox(lf_extractor, textvariable=self.active_extractor,
                     values=extractor_options, state="readonly",
                     width=35).pack(side=tk.LEFT, padx=5)

        # Feature toggles
        lf_toggles = tk.LabelFrame(tab, text="Feature Toggles", padx=10, pady=10)
        lf_toggles.pack(fill=tk.X, pady=5)

        f_debug = tk.Frame(lf_toggles)
        f_debug.pack(anchor="w", fill=tk.X)
        tk.Checkbutton(f_debug, text="Debug Mode (Verbose Logging)",
                       variable=self.debug_mode).pack(side=tk.LEFT)
        tk.Button(f_debug, text="Apply & Test Debug Output",
                  command=self.perform_debug_reload,
                  bg="#e0e0e0", font=("Arial", 8)).pack(side=tk.LEFT, padx=20)

        tk.Checkbutton(lf_toggles, text="Adaptive Ordering",
                       variable=self.adaptive_ordering).pack(anchor="w")
        tk.Checkbutton(lf_toggles, text="Enable LLM / Deep Research",
                       variable=self.llm_enabled).pack(anchor="w")

        # Thresholds
        lf_thresh = tk.LabelFrame(tab, text="Validation Logic (Artifact 2)",
                                  padx=10, pady=10)
        lf_thresh.pack(fill=tk.X, pady=5)
        self._add_spinbox(lf_thresh, "Satisfied Threshold (Count):",
                          self.satisfied_threshold, 1, 10)
        self._add_spinbox(lf_thresh, "LLM Confidence Threshold (%):",
                          self.llm_conf_threshold, 0, 100)

        # Output formatting
        lf_out = tk.LabelFrame(tab, text="Output Formatting", padx=10, pady=10)
        lf_out.pack(fill=tk.X, pady=5)
        self._add_entry(lf_out, "Output Encoding:", self.output_encoding)
        self._add_entry(lf_out, "CSV Suffix:", self.output_suffix)

        f_split = tk.Frame(lf_out)
        f_split.pack(fill=tk.X, pady=2)
        tk.Label(f_split, text="Report Structure:",
                 width=25, anchor="w").pack(side=tk.LEFT)
        ttk.Combobox(f_split, textvariable=self.report_split_mode,
                     values=["merged", "split", "both"],
                     state="readonly", width=15).pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Extractors / Validators tabs (unchanged from original)
    # ------------------------------------------------------------------

    def perform_debug_reload(self):
        config.DEBUG_MODE = self.debug_mode.get()
        print("\n" + "=" * 50)
        print(" MANUAL PLUGIN RELOAD & DEBUG CHECK")
        print("=" * 50)
        print(f"Debug Mode Active: {config.DEBUG_MODE}\n")
        try:
            print("--- Reloading Extractors ---")
            ExtractionManager()
            print("\n--- Reloading Validators ---")
            ValidatorManager()
            print("\n[Success] Reload complete.")
        except Exception as e:
            print(f"\n[Error] Reload failed: {e}")
        finally:
            print("=" * 50 + "\n")

    def _init_category_tab(self, category_name, plugins):
        tab = tk.Frame(self.notebook, padx=10, pady=10)
        self.notebook.add(tab, text=category_name)

        if not plugins:
            tk.Label(tab, text=f"No {category_name} loaded.", fg="red").pack(pady=20)
            return

        nav_frame = tk.Frame(tab, bg="#e1e1e1", padx=10, pady=10,
                             relief=tk.RAISED, borderwidth=1)
        nav_frame.pack(fill=tk.X, pady=(0, 15))
        tk.Label(nav_frame, text=f"Select {category_name[:-1]}:",
                 bg="#e1e1e1", font=("Arial", 10, "bold")).pack(side=tk.LEFT)

        plugin_names = [p.name for p in plugins]
        combo = ttk.Combobox(nav_frame, values=plugin_names,
                             state="readonly", width=40)
        combo.pack(side=tk.LEFT, padx=10)
        combo.current(0)

        container_frame = tk.Frame(tab, relief=tk.GROOVE, borderwidth=1)
        container_frame.pack(fill=tk.BOTH, expand=True)

        combo.bind("<<ComboboxSelected>>",
                   lambda e: self._render_plugin_settings(
                       container_frame, combo.get(), plugins))
        self._render_plugin_settings(container_frame, plugin_names[0], plugins)

    def _render_plugin_settings(self, container, selected_name, plugins):
        for widget in container.winfo_children():
            widget.destroy()

        plugin = next((p for p in plugins if p.name == selected_name), None)
        if not plugin:
            return

        canvas = tk.Canvas(container)
        scrollbar = tk.Scrollbar(container, orient="vertical",
                                 command=canvas.yview)
        scroll_frame = tk.Frame(canvas)
        scroll_frame.bind("<Configure>",
                          lambda e: canvas.configure(
                              scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        header_frame = tk.Frame(scroll_frame, pady=10)
        header_frame.pack(fill=tk.X)
        tk.Label(header_frame, text=f"Configuration: {plugin.name}",
                 font=("Helvetica", 11, "bold")).pack(anchor="w")
        tk.Frame(header_frame, height=2, bd=1,
                 relief=tk.SUNKEN).pack(fill=tk.X, pady=5)

        vars_map = self.plugin_vars[plugin.name]
        for key in sorted(vars_map.keys()):
            var = vars_map[key]
            row_frame = tk.Frame(scroll_frame, pady=4)
            row_frame.pack(fill=tk.X, anchor="w")
            clean_key = key.replace("_", " ").title()
            tk.Label(row_frame, text=f"{clean_key}:",
                     width=35, anchor="w").pack(side=tk.LEFT)

            if isinstance(var, tk.BooleanVar):
                tk.Checkbutton(row_frame, variable=var).pack(side=tk.LEFT)
            elif isinstance(var, tk.IntVar):
                tk.Spinbox(row_frame, from_=0, to=1000000,
                           textvariable=var, width=10).pack(side=tk.LEFT)
            elif isinstance(var, tk.DoubleVar):
                tk.Entry(row_frame, textvariable=var,
                         width=15).pack(side=tk.LEFT)
            else:
                width = 50 if len(str(var.get())) > 20 or "KEY" in key else 25
                tk.Entry(row_frame, textvariable=var,
                         width=width).pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _add_spinbox(self, parent, label_text, var, min_val, max_val):
        f = tk.Frame(parent)
        f.pack(fill=tk.X, pady=2)
        tk.Label(f, text=label_text, width=25, anchor="w").pack(side=tk.LEFT)
        tk.Spinbox(f, from_=min_val, to=max_val,
                   textvariable=var, width=10).pack(side=tk.LEFT)

    def _add_entry(self, parent, label_text, var):
        f = tk.Frame(parent)
        f.pack(fill=tk.X, pady=2)
        tk.Label(f, text=label_text, width=25, anchor="w").pack(side=tk.LEFT)
        tk.Entry(f, textvariable=var, width=20).pack(side=tk.LEFT)

    def save_all_settings(self):
        try:
            global_settings = {
                "DEBUG_MODE": self.debug_mode.get(),
                "ADAPTIVE_ORDERING": self.adaptive_ordering.get(),
                "LLM_ENABLED": self.llm_enabled.get(),
                "SATISFIED_THRESHOLD": self.satisfied_threshold.get(),
                "LLM_CONFIDENCE_THRESHOLD": self.llm_conf_threshold.get(),
                "OUTPUT_ENCODING": self.output_encoding.get(),
                "OUTPUT_SUFFIX": self.output_suffix.get(),
                "ACTIVE_EXTRACTOR": self.active_extractor.get(),
                "REPORT_SPLIT_MODE": self.report_split_mode.get()
            }
            config.save_settings(global_settings)
            config.Config.reload()

            all_plugins = self.extractors + self.validators
            for plugin in all_plugins:
                if plugin.name in self.plugin_vars:
                    for key, var in self.plugin_vars[plugin.name].items():
                        plugin.config[key] = var.get()
                    plugin.save_settings()

            if self.on_save_callback:
                self.on_save_callback()

            messagebox.showinfo("Success", "All settings saved successfully.")
            self.destroy()

        except Exception as e:
            messagebox.showerror("Save Error",
                                 f"An error occurred while saving: {e}")