import tkinter as tk
from ui.main_window import CitationApp

def main():
    root = tk.Tk()
    app = CitationApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()