import tkinter as tk
from tkinter import messagebox
import traceback
import sys

try:
    # Your existing import that triggers the UI
    from gui_app import main
    if __name__ == "__main__":
        main()
except Exception as e:
    # This forces the error to be visible even if the console closes
    error_info = traceback.format_exc()
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Startup Error", f"The application failed to start:\n\n{error_info}")
    sys.exit(1)