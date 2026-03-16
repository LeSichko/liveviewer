import tkinter as tk
from pathlib import Path

from app import App


def main():
    root = tk.Tk()
    ico_path = Path(__file__).with_name("icon.ico")
    if ico_path.exists():
        root.iconbitmap(str(ico_path))
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
