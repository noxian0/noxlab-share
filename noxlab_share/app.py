from __future__ import annotations

import ctypes
import queue
import os
import platform
import sys
import traceback
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

from PIL import ImageTk

from .qr_tools import copy_image_to_clipboard, create_qr_image, save_qr_image
from .server import ReceiveServer, ShareItem, ShareServer
from .utils import build_folder_zip, folder_size, format_bytes, remove_temp_file


BG = "#0c0e12"
PANEL = "#171a20"
PANEL_2 = "#11141a"
BORDER = "#303640"
TEXT = "#f2f2f2"
MUTED = "#a6aab2"
RED = "#e53935"
RED_DARK = "#9f2020"
SUCCESS = "#58c98d"
WARN = "#f0b94c"
RECEIVE = "#1f6feb"
RECEIVE_DARK = "#174ea6"
SCROLL_TRACK = "#11141a"
SCROLL_THUMB = "#c62828"
SCROLL_THUMB_ACTIVE = "#ff443f"


UPLOAD_LIMITS: dict[str, int | None] = {
    "512 MB": 512 * 1024 * 1024,
    "2 GB": 2 * 1024 * 1024 * 1024,
    "5 GB": 5 * 1024 * 1024 * 1024,
    "10 GB": 10 * 1024 * 1024 * 1024,
    "No fixed limit": None,
}


ASCII_LOGO = r"""
 _   _   ___  __  __ _        _     ____      ____   _   _    _     ____   _____
| \ | | / _ \ \ \/ /| |      / \   | __ )    / ___| | | | |  / \   |  _ \ | ____|
|  \| || | | | \  / | |     / _ \  |  _ \    \___ \ | |_| | / _ \  | |_) ||  _|
| |\  || |_| | /  \ | |___ / ___ \ | |_) |    ___) ||  _  |/ ___ \ |  _ < | |___
|_| \_| \___/ /_/\_\|_____/_/   \_\|____/    |____/ |_| |_/_/   \_\|_| \_\|_____|
""".strip("\n")


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base_path / relative_path


class NoxLabShareApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("NoxLab Share")
        self._configure_window_size()
        self.configure(bg=BG)
        self._set_window_icon()
        self._apply_dark_title_bar()

        self.selected_path: Path | None = None
        self.selected_is_folder = False
        self.selected_size = 0
        self.temp_zip_path: Path | None = None
        self.server: ShareServer | None = None
        self.receive_server: ReceiveServer | None = None
        self.qr_image = None
        self.qr_photo = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.timer_after_id: str | None = None
        self.deadline: datetime | None = None
        self.auto_stop_pending = False

        self.selected_name_var = tk.StringVar(value="No file or folder selected")
        self.selected_type_var = tk.StringVar(value="-")
        self.selected_size_var = tk.StringVar(value="-")
        self.status_var = tk.StringVar(value="Idle")
        self.lan_url_var = tk.StringVar(value="")
        self.password_enabled_var = tk.BooleanVar(value=False)
        self.password_status_var = tk.StringVar(value="Off")
        self.timer_var = tk.StringVar(value="Manual")
        self.timer_status_var = tk.StringVar(value="Manual")
        self.receive_folder = Path.home() / "Downloads" / "NoxLab Share Received"
        self.receive_folder_var = tk.StringVar(value=str(self.receive_folder))
        self.upload_limit_var = tk.StringVar(value="2 GB")

        self._build_ui()
        self._set_running_state(False)
        self._poll_logs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._maximize_on_windows)

    def _configure_window_size(self) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        min_width = min(1120, max(920, screen_width - 80))
        min_height = min(780, max(680, screen_height - 120))
        width = min(1380, max(1240, screen_width - 160))
        height = min(940, max(860, screen_height - 160))

        width = min(width, max(min_width, screen_width - 40))
        height = min(height, max(min_height, screen_height - 90))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 3)

        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min_width, min_height)

    def _maximize_on_windows(self) -> None:
        if platform.system() != "Windows":
            return
        try:
            self.state("zoomed")
            self._apply_dark_title_bar()
        except tk.TclError:
            pass

    def _set_window_icon(self) -> None:
        icon_path = resource_path("assets/noxlab_share.ico")
        if icon_path.exists():
            try:
                self.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass

    def _apply_dark_title_bar(self) -> None:
        if platform.system() != "Windows":
            return

        try:
            self.update_idletasks()
            hwnd = self.winfo_id()
            dwmapi = ctypes.windll.dwmapi

            dark_mode = ctypes.c_int(1)
            for attribute in (20, 19):
                result = dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    attribute,
                    ctypes.byref(dark_mode),
                    ctypes.sizeof(dark_mode),
                )
                if result == 0:
                    break

            border = ctypes.c_int(self._colorref(BORDER))
            caption = ctypes.c_int(self._colorref(PANEL))
            text = ctypes.c_int(self._colorref(TEXT))
            for attribute, value in ((34, border), (35, caption), (36, text)):
                dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    attribute,
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
        except (AttributeError, OSError, tk.TclError):
            pass

    @staticmethod
    def _colorref(hex_color: str) -> int:
        red = int(hex_color[1:3], 16)
        green = int(hex_color[3:5], 16)
        blue = int(hex_color[5:7], 16)
        return red | (green << 8) | (blue << 16)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = tk.Frame(self, bg=BG, padx=18, pady=14)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        logo = tk.Label(
            header,
            text=ASCII_LOGO,
            bg=BG,
            fg=RED,
            justify="left",
            anchor="w",
            font=("Consolas", 10, "bold"),
        )
        logo.grid(row=0, column=0, sticky="w")

        contact = tk.Label(
            header,
            text="Discord: noxian_ | Github: noxian0",
            bg=BG,
            fg=RED,
            anchor="w",
            font=("Consolas", 10, "bold"),
        )
        contact.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        subtitle = tk.Label(
            header,
            text="LAN-only file sharing. No cloud upload. Keep this app open while devices transfer files.",
            bg=BG,
            fg=MUTED,
            anchor="w",
            font=("Segoe UI", 10),
        )
        subtitle.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        body = tk.Frame(self, bg=BG, padx=18, pady=10)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1, minsize=470)
        body.grid_columnconfigure(1, weight=1, minsize=360)
        body.grid_rowconfigure(0, weight=1)

        left_outer, left = self._scroll_panel(body)
        left_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_columnconfigure(0, weight=1)

        right = self._panel(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(4, weight=1)

        self._build_left(left)
        self._build_right(right)

    def _build_left(self, parent: tk.Frame) -> None:
        title = self._section_title(parent, "Share item")
        title.grid(row=0, column=0, sticky="ew")

        picker = tk.Frame(parent, bg=PANEL)
        picker.grid(row=1, column=0, sticky="ew", pady=(8, 12))
        picker.grid_columnconfigure(0, weight=1)
        picker.grid_columnconfigure(1, weight=1)

        self.select_file_button = self._button(picker, "Select File", self._select_file)
        self.select_file_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.select_folder_button = self._button(picker, "Select Folder", self._select_folder)
        self.select_folder_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        details = tk.Frame(parent, bg=PANEL_2, highlightbackground=BORDER, highlightthickness=1)
        details.grid(row=2, column=0, sticky="ew")
        details.grid_columnconfigure(1, weight=1)
        self._value_row(details, 0, "Selected", self.selected_name_var)
        self._value_row(details, 1, "Type", self.selected_type_var)
        self._value_row(details, 2, "Size", self.selected_size_var)

        security_title = self._section_title(parent, "Security")
        security_title.grid(row=3, column=0, sticky="ew", pady=(16, 0))

        security = tk.Frame(parent, bg=PANEL)
        security.grid(row=4, column=0, sticky="ew", pady=(8, 12))
        security.grid_columnconfigure(1, weight=1)

        self.password_check = tk.Checkbutton(
            security,
            text="Require password",
            variable=self.password_enabled_var,
            command=self._update_password_state,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL,
            activeforeground=TEXT,
            selectcolor=PANEL_2,
            font=("Segoe UI", 10),
        )
        self.password_check.grid(row=0, column=0, sticky="w", padx=(0, 10))

        self.password_entry = tk.Entry(
            security,
            show="*",
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightbackground=BORDER,
            highlightcolor=RED,
            highlightthickness=1,
            font=("Segoe UI", 10),
        )
        self.password_entry.grid(row=0, column=1, sticky="ew")

        timer_line = tk.Frame(security, bg=PANEL)
        timer_line.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        timer_line.grid_columnconfigure(1, weight=1)

        timer_label = tk.Label(timer_line, text="Auto-stop", bg=PANEL, fg=MUTED, font=("Segoe UI", 10))
        timer_label.grid(row=0, column=0, sticky="w", padx=(0, 10))

        self.timer_menu = tk.OptionMenu(timer_line, self.timer_var, "Manual", "10 minutes", "30 minutes", "60 minutes")
        self.timer_menu.configure(
            bg=PANEL_2,
            fg=TEXT,
            activebackground=RED_DARK,
            activeforeground=TEXT,
            highlightthickness=1,
            highlightbackground=BORDER,
            relief="flat",
            font=("Segoe UI", 10),
        )
        self.timer_menu["menu"].configure(bg=PANEL_2, fg=TEXT, activebackground=RED_DARK, activeforeground=TEXT)
        self.timer_menu.grid(row=0, column=1, sticky="ew")

        status_title = self._section_title(parent, "Status")
        status_title.grid(row=5, column=0, sticky="ew", pady=(4, 0))

        status_panel = tk.Frame(parent, bg=PANEL_2, highlightbackground=BORDER, highlightthickness=1)
        status_panel.grid(row=6, column=0, sticky="ew", pady=(8, 12))
        status_panel.grid_columnconfigure(1, weight=1)
        self._value_row(status_panel, 0, "Service", self.status_var, accent=True)
        self._value_row(status_panel, 1, "Password", self.password_status_var)
        self._value_row(status_panel, 2, "Timer", self.timer_status_var)

        url_title = self._section_title(parent, "LAN URL")
        url_title.grid(row=7, column=0, sticky="ew", pady=(4, 0))

        url_frame = tk.Frame(parent, bg=PANEL)
        url_frame.grid(row=8, column=0, sticky="ew", pady=(8, 10))
        url_frame.grid_columnconfigure(0, weight=1)

        self.url_entry = tk.Entry(
            url_frame,
            textvariable=self.lan_url_var,
            readonlybackground=PANEL_2,
            fg=TEXT,
            relief="flat",
            highlightbackground=BORDER,
            highlightcolor=RED,
            highlightthickness=1,
            font=("Consolas", 10),
        )
        self.url_entry.configure(state="readonly")
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.copy_link_button = self._button(url_frame, "Copy Link", self._copy_link)
        self.copy_link_button.grid(row=0, column=1, sticky="ew")

        actions = tk.Frame(parent, bg=PANEL)
        actions.grid(row=9, column=0, sticky="ew", pady=(4, 0))
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)
        actions.grid_columnconfigure(2, weight=1)

        self.start_button = self._button(actions, "Start Sharing", self._start_sharing, primary=True)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.stop_button = self._button(actions, "Stop", self._stop_active_service)
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=6)
        self.clear_button = self._button(actions, "Clear / Reset", self._clear_reset)
        self.clear_button.grid(row=0, column=2, sticky="ew", padx=(6, 0))

        secondary = tk.Frame(parent, bg=PANEL)
        secondary.grid(row=10, column=0, sticky="ew", pady=(10, 0))
        secondary.grid_columnconfigure(0, weight=1)
        secondary.grid_columnconfigure(1, weight=1)

        self.open_page_button = self._button(secondary, "Open Page", self._open_download_page)
        self.open_page_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.copy_qr_button = self._button(secondary, "Copy QR", self._copy_qr)
        self.copy_qr_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        receive_title = self._section_title(parent, "Receive from phone")
        receive_title.grid(row=11, column=0, sticky="ew", pady=(16, 0))

        receive = tk.Frame(parent, bg=PANEL)
        receive.grid(row=12, column=0, sticky="ew", pady=(8, 0))
        receive.grid_columnconfigure(1, weight=1)

        receive_label = tk.Label(receive, text="Save to", bg=PANEL, fg=MUTED, font=("Segoe UI", 10))
        receive_label.grid(row=0, column=0, sticky="w", padx=(0, 10))

        self.receive_folder_entry = tk.Entry(
            receive,
            textvariable=self.receive_folder_var,
            readonlybackground=PANEL_2,
            fg=TEXT,
            relief="flat",
            highlightbackground=BORDER,
            highlightcolor=RED,
            highlightthickness=1,
            font=("Consolas", 9),
        )
        self.receive_folder_entry.configure(state="readonly")
        self.receive_folder_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        self.choose_receive_folder_button = self._button(receive, "Choose", self._choose_receive_folder)
        self.choose_receive_folder_button.grid(row=0, column=2, sticky="ew")

        limit_label = tk.Label(receive, text="Upload limit", bg=PANEL, fg=MUTED, font=("Segoe UI", 10))
        limit_label.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(10, 0))

        self.upload_limit_menu = tk.OptionMenu(receive, self.upload_limit_var, *UPLOAD_LIMITS.keys())
        self.upload_limit_menu.configure(
            bg=PANEL_2,
            fg=TEXT,
            activebackground=RED_DARK,
            activeforeground=TEXT,
            highlightthickness=1,
            highlightbackground=BORDER,
            relief="flat",
            font=("Segoe UI", 10),
        )
        self.upload_limit_menu["menu"].configure(bg=PANEL_2, fg=TEXT, activebackground=RED_DARK, activeforeground=TEXT)
        self.upload_limit_menu.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(10, 0))

        receive_actions = tk.Frame(receive, bg=PANEL)
        receive_actions.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        receive_actions.grid_columnconfigure(0, weight=1)
        receive_actions.grid_columnconfigure(1, weight=1)

        self.start_receive_button = self._button(
            receive_actions,
            "Start Receiving From Phone",
            self._start_receiving,
            variant="receive",
        )
        self.start_receive_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.open_receive_folder_button = self._button(receive_actions, "Open Receive Folder", self._open_receive_folder)
        self.open_receive_folder_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self._update_password_state()

    def _build_right(self, parent: tk.Frame) -> None:
        qr_title = self._section_title(parent, "QR code")
        qr_title.grid(row=0, column=0, sticky="ew")

        qr_box = tk.Frame(parent, bg=PANEL_2, highlightbackground=BORDER, highlightthickness=1, width=320, height=320)
        qr_box.grid(row=1, column=0, sticky="n", pady=(8, 12))
        qr_box.grid_propagate(False)
        qr_box.grid_columnconfigure(0, weight=1)
        qr_box.grid_rowconfigure(0, weight=1)

        self.qr_label = tk.Label(
            qr_box,
            text="QR appears after sharing or receiving starts",
            bg=PANEL_2,
            fg=MUTED,
            justify="center",
            wraplength=260,
            font=("Segoe UI", 11),
        )
        self.qr_label.grid(row=0, column=0, sticky="nsew")

        qr_actions = tk.Frame(parent, bg=PANEL)
        qr_actions.grid(row=2, column=0, sticky="new", pady=(0, 12))
        qr_actions.grid_columnconfigure(0, weight=1)

        self.save_qr_button = self._button(qr_actions, "Save QR PNG", self._save_qr)
        self.save_qr_button.grid(row=0, column=0, sticky="ew")

        log_title = self._section_title(parent, "Activity log")
        log_title.grid(row=3, column=0, sticky="ew")

        log_frame = tk.Frame(parent, bg=PANEL)
        log_frame.grid(row=4, column=0, sticky="nsew", pady=(8, 12))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightbackground=BORDER,
            highlightcolor=BORDER,
            highlightthickness=1,
            height=12,
            wrap="word",
            font=("Consolas", 9),
        )
        log_scrollbar, log_scroll_set = self._styled_scrollbar(log_frame, self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll_set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.log_text.configure(state="disabled")

        trouble = tk.Label(
            parent,
            text=(
                "Troubleshooting: devices must be on the same Wi-Fi or LAN. "
                "If the link will not open, allow Python or NoxLab Share through Windows Firewall "
                "on Private networks, disable VPN isolation, and check that guest Wi-Fi client isolation is off."
            ),
            bg=PANEL,
            fg=MUTED,
            justify="left",
            anchor="w",
            wraplength=430,
            font=("Segoe UI", 9),
        )
        trouble.grid(row=5, column=0, sticky="ew")

    def _panel(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(parent, bg=PANEL, padx=16, pady=16, highlightbackground=BORDER, highlightthickness=1)

    def _scroll_panel(self, parent: tk.Widget) -> tuple[tk.Frame, tk.Frame]:
        outer = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=PANEL, highlightthickness=0, bd=0)
        scrollbar, scroll_set = self._styled_scrollbar(outer, canvas.yview, width=22)
        canvas.configure(yscrollcommand=scroll_set)

        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))

        inner = tk.Frame(canvas, bg=PANEL, padx=16, pady=16)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def update_scroll_region(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_inner_width(event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        def wheel(event) -> None:
            if event.delta:
                canvas.yview_scroll(int(-event.delta / 120), "units")

        inner.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", fit_inner_width)
        canvas.bind("<Enter>", lambda _event: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _event: canvas.unbind_all("<MouseWheel>"))

        return outer, inner

    def _styled_scrollbar(self, parent: tk.Widget, scroll_target, width: int = 18) -> tuple[tk.Canvas, object]:
        bar = tk.Canvas(
            parent,
            bg=SCROLL_TRACK,
            width=width,
            highlightthickness=1,
            highlightbackground=BORDER,
            bd=0,
            cursor="sb_v_double_arrow",
        )
        thumb = bar.create_rectangle(
            4,
            4,
            width - 4,
            52,
            fill=SCROLL_THUMB,
            outline=SCROLL_THUMB_ACTIVE,
        )
        state = {"first": 0.0, "last": 1.0, "drag_y": 0, "drag_first": 0.0}

        def clamp(value: float) -> float:
            return max(0.0, min(1.0, value))

        def set_thumb(first: str, last: str) -> None:
            first_float = float(first)
            last_float = float(last)
            state["first"] = first_float
            state["last"] = last_float
            height = max(bar.winfo_height(), 1)
            content_span = last_float - first_float

            if content_span >= 0.999:
                bar.itemconfigure(thumb, state="hidden")
                return

            bar.itemconfigure(thumb, state="normal")
            pad = 4
            available = max(height - (pad * 2), 1)
            thumb_height = max(54, int(available * content_span))
            top = pad + int(available * first_float)
            bottom = min(height - pad, top + thumb_height)
            if bottom - top < thumb_height:
                top = max(pad, bottom - thumb_height)
            bar.coords(thumb, 4, top, width - 4, bottom)

        def move_to_y(y: int) -> None:
            height = max(bar.winfo_height(), 1)
            scroll_target("moveto", clamp(y / height))

        def on_press(event) -> None:
            state["drag_y"] = event.y
            state["drag_first"] = state["first"]
            if thumb not in bar.find_withtag("current"):
                move_to_y(event.y)

        def on_drag(event) -> None:
            height = max(bar.winfo_height(), 1)
            delta = (event.y - state["drag_y"]) / height
            scroll_target("moveto", clamp(state["drag_first"] + delta))

        def on_wheel(event) -> str:
            if event.delta:
                scroll_target("scroll", int(-event.delta / 120), "units")
            return "break"

        bar.bind("<Configure>", lambda _event: set_thumb(str(state["first"]), str(state["last"])))
        bar.bind("<Button-1>", on_press)
        bar.bind("<B1-Motion>", on_drag)
        bar.bind("<MouseWheel>", on_wheel)
        bar.bind("<Enter>", lambda _event: bar.itemconfigure(thumb, fill=SCROLL_THUMB_ACTIVE))
        bar.bind("<Leave>", lambda _event: bar.itemconfigure(thumb, fill=SCROLL_THUMB))
        return bar, set_thumb

    def _section_title(self, parent: tk.Widget, text: str) -> tk.Label:
        return tk.Label(parent, text=text.upper(), bg=PANEL, fg=RED, anchor="w", font=("Segoe UI", 9, "bold"))

    def _button(self, parent: tk.Widget, text: str, command, primary: bool = False, variant: str = "default") -> tk.Button:
        bg = PANEL_2
        active_bg = RED_DARK
        font_weight = "normal"
        if primary:
            bg = RED
            font_weight = "bold"
        if variant == "receive":
            bg = RECEIVE
            active_bg = RECEIVE_DARK
            font_weight = "bold"

        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=TEXT,
            activebackground=active_bg,
            activeforeground=TEXT,
            disabledforeground="#646974",
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=10,
            font=("Segoe UI", 10, font_weight),
            cursor="hand2",
        )

    def _value_row(self, parent: tk.Widget, row: int, label: str, variable: tk.StringVar, accent: bool = False) -> None:
        key = tk.Label(parent, text=label, bg=PANEL_2, fg=MUTED, anchor="w", font=("Segoe UI", 9))
        key.grid(row=row, column=0, sticky="nw", padx=12, pady=(10 if row == 0 else 6, 6))
        value = tk.Label(
            parent,
            textvariable=variable,
            bg=PANEL_2,
            fg=SUCCESS if accent else TEXT,
            anchor="w",
            justify="left",
            wraplength=340,
            font=("Segoe UI", 10, "bold" if accent else "normal"),
        )
        value.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=(10 if row == 0 else 6, 6))

    def _select_file(self) -> None:
        path = filedialog.askopenfilename(title="Select a file to share")
        if not path:
            return
        selected = Path(path)
        try:
            size = selected.stat().st_size
        except OSError as exc:
            self._show_error("Could not read selected file", exc)
            return
        self._set_selected(selected, is_folder=False, size=size)
        self._log(f"File selected: {selected.name}")

    def _select_folder(self) -> None:
        path = filedialog.askdirectory(title="Select a folder to share")
        if not path:
            return
        selected = Path(path)
        try:
            size = folder_size(selected)
        except OSError as exc:
            self._show_error("Could not read selected folder", exc)
            return
        self._set_selected(selected, is_folder=True, size=size)
        self._log(f"Folder selected: {selected.name}")

    def _set_selected(self, path: Path, is_folder: bool, size: int) -> None:
        if self.server or self.receive_server:
            self._stop_active_service()
        self.selected_path = path
        self.selected_is_folder = is_folder
        self.selected_size = size
        self.selected_name_var.set(str(path))
        self.selected_type_var.set("Folder (ZIP will be created on start)" if is_folder else "File")
        self.selected_size_var.set(format_bytes(size))
        self.status_var.set("Ready")

    def _update_password_state(self) -> None:
        enabled = self.password_enabled_var.get()
        self.password_entry.configure(state="normal" if enabled else "disabled")
        self.password_status_var.set("On (required)" if enabled else "Off")

    def _start_sharing(self) -> None:
        if not self.selected_path:
            messagebox.showwarning("Select something first", "Choose one file or folder before starting sharing.")
            return
        if self.server or self.receive_server:
            messagebox.showinfo("Already active", "Stop the current service before starting another one.")
            return

        password = ""
        if self.password_enabled_var.get():
            password = self.password_entry.get()
            if not password:
                messagebox.showwarning("Password required", "Enter a password or turn password protection off.")
                return

        try:
            self.status_var.set("Preparing")
            self.update_idletasks()
            item = self._prepare_share_item()
            self.server = ShareServer(item=item, password=password, log_callback=self._enqueue_log)
            url = self.server.start()
            self.lan_url_var.set(url)
            self._render_qr(url)
            self._set_running_state(True)
            self._start_timer_if_needed()
            self.status_var.set("Sharing")
            self._log("Share is live on the local network")
        except Exception as exc:
            self._cleanup_temp_zip()
            self.server = None
            self.status_var.set("Error")
            self._log(f"Error: {exc}")
            self._show_error("Could not start sharing", exc)

    def _choose_receive_folder(self) -> None:
        path = filedialog.askdirectory(title="Choose where received files should be saved", initialdir=str(self.receive_folder))
        if not path:
            return
        self.receive_folder = Path(path)
        self.receive_folder_var.set(str(self.receive_folder))
        self._log(f"Receive folder selected: {self.receive_folder}")

    def _start_receiving(self) -> None:
        if self.server or self.receive_server:
            messagebox.showinfo("Already active", "Stop the current service before starting another one.")
            return

        password = ""
        if self.password_enabled_var.get():
            password = self.password_entry.get()
            if not password:
                messagebox.showwarning("Password required", "Enter a password or turn password protection off.")
                return

        try:
            self.receive_folder.mkdir(parents=True, exist_ok=True)
            self.status_var.set("Preparing")
            self.update_idletasks()
            self.receive_server = ReceiveServer(
                save_dir=self.receive_folder,
                password=password,
                log_callback=self._enqueue_log,
                max_upload_bytes=self._selected_upload_limit_bytes(),
            )
            url = self.receive_server.start()
            self.lan_url_var.set(url)
            self._render_qr(url)
            self._set_running_state(True)
            self._start_timer_if_needed()
            self.status_var.set("Receiving")
            self._log(f"Ready to receive files into {self.receive_folder}")
            self._log(f"Upload limit: {self.upload_limit_var.get()}")
        except Exception as exc:
            self.receive_server = None
            self.status_var.set("Error")
            self._log(f"Error: {exc}")
            self._show_error("Could not start receiving", exc)

    def _selected_upload_limit_bytes(self) -> int | None:
        return UPLOAD_LIMITS.get(self.upload_limit_var.get(), UPLOAD_LIMITS["2 GB"])

    def _prepare_share_item(self) -> ShareItem:
        assert self.selected_path is not None
        selected = self.selected_path

        if self.selected_is_folder:
            self._log("Creating temporary ZIP for folder")
            self.temp_zip_path = build_folder_zip(selected)
            served_path = self.temp_zip_path
            served_name = f"{selected.name or 'shared-folder'}.zip"
            served_size = served_path.stat().st_size
        else:
            served_path = selected
            served_name = selected.name
            served_size = selected.stat().st_size

        return ShareItem(
            source_path=selected,
            served_path=served_path,
            display_name=selected.name or str(selected),
            served_name=served_name,
            original_size=self.selected_size,
            served_size=served_size,
            is_folder=self.selected_is_folder,
        )

    def _stop_sharing(self) -> None:
        self._cancel_timer()
        if self.server:
            try:
                self.server.stop()
            except Exception as exc:
                self._log(f"Error while stopping server: {exc}")
            self.server = None
        self._cleanup_temp_zip()
        self.deadline = None
        self.auto_stop_pending = False
        self.lan_url_var.set("")
        self.qr_image = None
        self.qr_photo = None
        self.qr_label.configure(image="", text="QR appears after sharing or receiving starts")
        self.status_var.set("Stopped" if self.selected_path else "Idle")
        self.timer_status_var.set(self.timer_var.get())
        self._set_running_state(False)

    def _stop_receiving(self) -> None:
        self._cancel_timer()
        if self.receive_server:
            try:
                self.receive_server.stop()
            except Exception as exc:
                self._log(f"Error while stopping receive server: {exc}")
            self.receive_server = None
        self.deadline = None
        self.auto_stop_pending = False
        self.lan_url_var.set("")
        self.qr_image = None
        self.qr_photo = None
        self.qr_label.configure(image="", text="QR appears after sharing or receiving starts")
        self.status_var.set("Stopped")
        self.timer_status_var.set(self.timer_var.get())
        self._set_running_state(False)

    def _stop_active_service(self) -> None:
        if self.server:
            self._stop_sharing()
            return
        if self.receive_server:
            self._stop_receiving()
            return
        self._cancel_timer()

    def _clear_reset(self) -> None:
        self._stop_active_service()
        self.selected_path = None
        self.selected_is_folder = False
        self.selected_size = 0
        self.selected_name_var.set("No file or folder selected")
        self.selected_type_var.set("-")
        self.selected_size_var.set("-")
        self.status_var.set("Idle")
        self.password_enabled_var.set(False)
        self.password_entry.configure(state="normal")
        self.password_entry.delete(0, tk.END)
        self._update_password_state()
        self.timer_var.set("Manual")
        self.timer_status_var.set("Manual")
        self._log("Reset complete")

    def _set_running_state(self, running: bool) -> None:
        normal = "normal"
        disabled = "disabled"
        self.start_button.configure(state=disabled if running else normal)
        self.stop_button.configure(state=normal if running else disabled)
        self.copy_link_button.configure(state=normal if running else disabled)
        self.open_page_button.configure(state=normal if running else disabled)
        self.save_qr_button.configure(state=normal if running and self.qr_image else disabled)
        self.copy_qr_button.configure(state=normal if running and self.qr_image else disabled)
        self.select_file_button.configure(state=disabled if running else normal)
        self.select_folder_button.configure(state=disabled if running else normal)
        self.start_receive_button.configure(state=disabled if running else normal)
        self.choose_receive_folder_button.configure(state=disabled if running else normal)
        self.upload_limit_menu.configure(state=disabled if running else normal)
        self.open_receive_folder_button.configure(state=normal)
        self.password_check.configure(state=disabled if running else normal)
        self.password_entry.configure(state=disabled if running or not self.password_enabled_var.get() else normal)
        self.timer_menu.configure(state=disabled if running else normal)

    def _render_qr(self, url: str) -> None:
        self.qr_image = create_qr_image(url, size=280)
        self.qr_photo = ImageTk.PhotoImage(self.qr_image)
        self.qr_label.configure(image=self.qr_photo, text="")

    def _copy_link(self) -> None:
        url = self.lan_url_var.get()
        if not url:
            return
        self.clipboard_clear()
        self.clipboard_append(url)
        self._log("Link copied to clipboard")

    def _open_download_page(self) -> None:
        url = self.lan_url_var.get()
        if url:
            webbrowser.open(url)
            self._log("Opened page locally")

    def _open_receive_folder(self) -> None:
        try:
            self.receive_folder.mkdir(parents=True, exist_ok=True)
            os.startfile(self.receive_folder)
        except Exception as exc:
            self._show_error("Could not open receive folder", exc)

    def _save_qr(self) -> None:
        if not self.qr_image:
            return
        path = filedialog.asksaveasfilename(
            title="Save QR code",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png")],
            initialfile="noxlab-share-qr.png",
        )
        if not path:
            return
        try:
            save_qr_image(self.qr_image, Path(path))
            self._log(f"QR saved: {Path(path).name}")
        except Exception as exc:
            self._show_error("Could not save QR code", exc)

    def _copy_qr(self) -> None:
        if not self.qr_image:
            return
        try:
            if copy_image_to_clipboard(self.qr_image):
                self._log("QR image copied to clipboard")
            else:
                self._log("QR image clipboard copy is not available")
                messagebox.showinfo("Copy QR", "QR image copy is not available on this system. Use Save QR PNG instead.")
        except Exception as exc:
            self._show_error("Could not copy QR image", exc)

    def _start_timer_if_needed(self) -> None:
        value = self.timer_var.get()
        minutes = {"10 minutes": 10, "30 minutes": 30, "60 minutes": 60}.get(value)
        if not minutes:
            self.timer_status_var.set("Manual")
            self.deadline = None
            return
        self.deadline = datetime.now() + timedelta(minutes=minutes)
        self._tick_timer()
        self._log(f"Auto-stop timer set: {value}")

    def _tick_timer(self) -> None:
        if not self.deadline or not (self.server or self.receive_server):
            return
        remaining = self.deadline - datetime.now()
        seconds = max(0, int(remaining.total_seconds()))
        minutes, secs = divmod(seconds, 60)
        self.timer_status_var.set(f"Stops in {minutes:02d}:{secs:02d}")
        if seconds <= 0 and not self.auto_stop_pending:
            self.auto_stop_pending = True
            self._log("Auto-stop timer expired")
            self._stop_active_service()
            return
        self.timer_after_id = self.after(1000, self._tick_timer)

    def _cancel_timer(self) -> None:
        if self.timer_after_id:
            try:
                self.after_cancel(self.timer_after_id)
            except tk.TclError:
                pass
            self.timer_after_id = None

    def _cleanup_temp_zip(self) -> None:
        if self.temp_zip_path:
            remove_temp_file(self.temp_zip_path)
            self.temp_zip_path = None

    def _enqueue_log(self, message: str) -> None:
        self.log_queue.put(message)

    def _poll_logs(self) -> None:
        try:
            while True:
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(250, self._poll_logs)

    def _log(self, message: str) -> None:
        self._append_log(message)

    def _append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"[{stamp}] {message}\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 180:
            self.log_text.delete("1.0", "40.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _show_error(self, title: str, exc: BaseException) -> None:
        details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        messagebox.showerror(title, details)

    def _on_close(self) -> None:
        self._stop_active_service()
        self.destroy()


def main() -> None:
    try:
        app = NoxLabShareApp()
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "NoxLab Share failed to start",
            f"{exc}\n\nInstall dependencies with: pip install -r requirements.txt",
        )
        root.destroy()
        raise
    app.mainloop()
