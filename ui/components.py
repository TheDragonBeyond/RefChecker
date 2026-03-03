import tkinter as tk

class TextRedirector:
    """Redirects stdout/stderr to a Tkinter text widget"""
    def __init__(self, widget, queue):
        self.widget = widget
        self.queue = queue

    def write(self, string):
        self.queue.put(string)

    def flush(self):
        pass

class CollapsiblePane(tk.Frame):
    """A collapsible frame widget for the UI sections"""
    def __init__(self, parent, title="", expanded=True, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.expanded = expanded
        self.title = title
        self._header_bg = "#e1e1e1"
        self._content_bg = kwargs.get("bg", "SystemButtonFace")

        self.header_frame = tk.Frame(self, bg=self._header_bg, relief=tk.RAISED, borderwidth=1)
        self.header_frame.pack(fill=tk.X, side=tk.TOP)

        self.toggle_btn = tk.Label(self.header_frame, text="▼" if expanded else "▶",
                                   bg=self._header_bg, font=("Consolas", 10, "bold"), width=3)
        self.toggle_btn.pack(side=tk.LEFT, padx=5, pady=5)

        self.label = tk.Label(self.header_frame, text=title, bg=self._header_bg,
                              font=("Helvetica", 10, "bold"))
        self.label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        self.header_frame.bind("<Button-1>", self.toggle)
        self.toggle_btn.bind("<Button-1>", self.toggle)
        self.label.bind("<Button-1>", self.toggle)

        self.content_frame = tk.Frame(self, padx=10, pady=10)
        if expanded:
            self.content_frame.pack(fill=tk.X, expand=True)

    def toggle(self, event=None):
        self.expanded = not self.expanded
        if self.expanded:
            self.toggle_btn.config(text="▼")
            self.content_frame.pack(fill=tk.X, expand=True)
        else:
            self.toggle_btn.config(text="▶")
            self.content_frame.pack_forget()