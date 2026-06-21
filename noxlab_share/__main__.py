def main() -> None:
    try:
        from .app import main as app_main
    except ImportError as exc:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "NoxLab Share dependencies missing",
            f"{exc}\n\nInstall dependencies with: pip install -r requirements.txt",
        )
        root.destroy()
        raise

    app_main()


if __name__ == "__main__":
    main()
