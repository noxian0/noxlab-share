"""Desktop layout for the Send, Receive and Activity views."""

import os
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from . import __version__
from .qr_tools import create_qr_image
from .utils import format_bytes


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


BG, HEADER, SIDE = "#18191d", "#101114", "#1d1e23"
INPUT, BTN, LINE = "#141519", "#303137", "#41434b"
WHITE, SOFT, MUTED = "#f4f3f1", "#c6c6ce", "#a4a4b0"
RED, TEAL, GREEN = "#ff655c", "#66d3c1", "#82db99"
GLYPHS = {"file": 0xe8a5, "folder": 0xe8b7, "send": 0xe72a, "receive": 0xe896,
          "log": 0xe9f9, "copy": 0xe8c8, "open": 0xe8a7, "save": 0xe74e,
          "stop": 0xe71a, "clear": 0xe777, "link": 0xe71b, "pc": 0xe770}


def label(parent, text="", *, size=10, color=SOFT, bold=False, variable=None, **opts):
    return tk.Label(parent, text=text, textvariable=variable, bg=parent["bg"], fg=color,
                    font=("Segoe UI", size, "bold" if bold else "normal"), bd=0,
                    anchor="w", justify="left", **opts)


def line(parent):
    tk.Frame(parent, bg=LINE, height=1).pack(fill="x")


def band(parent, **opts):
    return tk.Frame(parent, bg=parent["bg"], **opts)


class DesktopLayout:
    def _configure_window_size(self):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(990, sw - 24), min(700, sh - 68)
        self.geometry(f"{w}x{h}+{max(0, (sw-w)//2)}+{max(0, (sh-h)//3)}")
        self.minsize(min(730, w), min(510, h))

    def icon(self, name, color, size=16):
        key = (name, color, size)
        if key not in self.images:
            font_path = Path(os.environ["WINDIR"]) / "Fonts" / "segmdl2.ttf"
            font = ImageFont.truetype(str(font_path), size * 2)
            image = Image.new("RGBA", (size * 2 + 6, size * 2 + 6))
            draw = ImageDraw.Draw(image)
            char = chr(GLYPHS[name])
            left, top, right, bottom = draw.textbbox((0, 0), char, font=font)
            draw.text(((image.width-right-left)//2, (image.height-bottom-top)//2),
                      char, font=font, fill=color)
            self.images[key] = ImageTk.PhotoImage(image.resize((size+2, size+2), Image.Resampling.LANCZOS))
        return self.images[key]

    def action(self, parent, text, callback, glyph, *, bg=BTN, fg=WHITE):
        return tk.Button(parent, text=text, image=self.icon(glyph, fg), compound="left",
                         command=callback, bg=bg, fg=fg, activebackground=LINE,
                         activeforeground=WHITE, disabledforeground="#777780",
                         relief="flat", overrelief="flat", bd=0, highlightthickness=1,
                         highlightbackground=bg, highlightcolor=WHITE, padx=11, pady=7,
                         font=("Segoe UI", 10), cursor="hand2")

    def _build_ui(self):
        self.configure(bg=BG)
        self.images = {}
        self.mode = "Send"
        self.latest = tk.StringVar(value="Ready")
        self.item_title = tk.StringVar(value="No item selected")
        self.item_detail = tk.StringVar(value="File or folder")
        self.current_status = tk.StringVar(value="Idle")
        self.link_display = tk.StringVar(value="No active link")
        self.lan_url_var.trace_add("write", lambda *_: self.link_display.set(self.lan_url_var.get() or "No active link"))
        self.transfer_note = tk.StringVar(value="Waiting for an upload")
        self.views = {}
        self.tabs = {}

        header = tk.Frame(self, bg=HEADER, padx=22, pady=9)
        header.pack(fill="x")
        brand = band(header)
        brand.pack(side="left")
        tk.Label(brand, text=ASCII_LOGO, fg=RED, bg=HEADER, justify="left",
                 font=("Consolas", 7, "bold"), bd=0).pack(anchor="w")
        label(brand, "Discord: noxian_  |  Github: noxian0", size=8, color=MUTED).pack(anchor="w")
        stamp = band(header)
        stamp.pack(side="right")
        label(stamp, __version__, size=10, color=WHITE, bold=True).pack(anchor="e")
        label(stamp, "Local network only", size=9, color=MUTED).pack(anchor="e")
        nav = tk.Frame(self, bg=HEADER, padx=18)
        nav.pack(fill="x")
        for mode, glyph in (("Send", "send"), ("Receive", "receive"), ("Activity", "log")):
            col = band(nav)
            col.pack(side="left", padx=(0, 5))
            tab = self.action(col, " " + mode, lambda m=mode: self.switch(m), glyph, bg=HEADER)
            tab.pack()
            marker = tk.Frame(col, height=3, bg=HEADER)
            marker.pack(fill="x")
            self.tabs[mode] = tab, marker
        self.badge = label(nav, variable=self.current_status, color=MUTED, bold=True)
        self.badge.pack(side="right", padx=6)
        line(self)

        footer = tk.Frame(self, bg=HEADER, padx=20, pady=6)
        footer.pack(side="bottom", fill="x")
        tk.Button(footer, text="Network help", command=self.help, bg=HEADER, fg=SOFT,
                  activebackground=BTN, activeforeground=WHITE, bd=0).pack(side="right")
        label(footer, variable=self.latest, size=9, color=MUTED).pack(side="left", fill="x", expand=True)

        self.body = band(self)
        self.body.pack(fill="both", expand=True)
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        self.work = band(self.body)
        self.work.grid(row=0, column=0, sticky="nsew")
        self.work.columnconfigure(0, weight=1)
        self.work.rowconfigure(0, weight=1)
        self.settings_area = self.scrollable(self.work)
        left = self.settings_area
        self.space = band(left)
        self.space.pack(fill="x")
        self.build_send()
        self.build_receive()
        self.build_settings(left)
        actions = tk.Frame(self.work, bg=BG, padx=20, pady=12)
        actions.grid(row=1, column=0, sticky="ew")
        self.start_button = self.action(actions, " Start sharing", self._start_sharing, "send", bg=RED, fg=HEADER)
        self.start_receive_button = self.action(actions, " Start receiving", self._start_receiving,
                                                "receive", bg=TEAL, fg=HEADER)
        self.stop_button = self.action(actions, " Stop session", self._stop_active_service, "stop", bg=RED, fg=HEADER)
        self.clear_button = self.action(actions, " Reset", self.reset, "clear")
        self.clear_button.pack(side="right")
        self.build_connection()
        self.build_activity()
        self.switch("Send")
        self._update_password_state()
        self.poll_transfer()

    def scrollable(self, parent):
        shell = band(parent)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        canvas = tk.Canvas(shell, bg=parent["bg"], bd=0, highlightthickness=0, width=1, height=1)
        canvas.grid(row=0, column=0, sticky="nsew")
        bar, update_thumb = self._styled_scrollbar(shell, canvas.yview, width=12)
        bar.grid(row=0, column=1, sticky="ns")
        inner = tk.Frame(canvas, bg=parent["bg"], padx=20, pady=12)
        item = canvas.create_window(0, 0, window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        def sync(lo, hi):
            update_thumb(lo, hi)
            if float(hi) - float(lo) >= 0.999:
                bar.grid_remove()
            else:
                bar.grid()
        canvas.configure(yscrollcommand=sync)
        def wheel(e):
            if str(e.widget).startswith(str(shell)) and canvas.yview() != (0.0, 1.0):
                canvas.yview_scroll(-int(e.delta / 120), "units")
        self.bind("<MouseWheel>", wheel, add=True)
        return inner

    def build_send(self):
        panel = self.views["Send"] = band(self.space)
        label(panel, "Send from this PC", size=20, bold=True, color=WHITE).pack(fill="x", pady=(0, 10))
        box = tk.Frame(panel, bg=SIDE, highlightthickness=1, highlightbackground=LINE, padx=14, pady=12)
        box.pack(fill="x")
        pic = label(box, image=self.icon("file", RED, 30))
        pic.pack(side="left", padx=(0, 12))
        title = label(box, variable=self.item_title, color=WHITE, bold=True)
        title.pack(fill="x")
        title.bind("<Configure>", lambda e: title.configure(wraplength=max(e.width-2, 100)))
        label(box, variable=self.item_detail, size=9, color=MUTED).pack(fill="x")
        row = band(panel)
        row.pack(fill="x", pady=(9, 16))
        self.select_file_button = self.action(row, " Choose file", self._select_file, "file", bg=RED, fg=HEADER)
        self.select_file_button.pack(side="left", padx=(0, 8))
        self.select_folder_button = self.action(row, " Choose folder", self._select_folder, "folder")
        self.select_folder_button.pack(side="left")

    def build_receive(self):
        panel = self.views["Receive"] = band(self.space)
        label(panel, "Receive on this PC", size=20, bold=True, color=WHITE).pack(fill="x", pady=(0, 8))
        label(panel, "Save incoming files to", size=9, color=MUTED).pack(anchor="w")
        path = label(panel, variable=self.receive_folder_var, color=WHITE)
        path.pack(fill="x", pady=(2, 6))
        path.bind("<Configure>", lambda e: path.configure(wraplength=max(100, e.width-2)))
        row = band(panel)
        row.pack(fill="x")
        self.choose_receive_folder_button = self.action(row, " Change folder", self._choose_receive_folder, "folder")
        self.choose_receive_folder_button.pack(side="left", padx=(0, 8))
        self.open_receive_folder_button = self.action(row, " Open", self._open_receive_folder, "open")
        self.open_receive_folder_button.pack(side="left")
        limits = band(panel)
        limits.pack(fill="x", pady=10)
        label(limits, "Upload limit").pack(side="left")
        self.upload_limit_menu = self.menu(limits, self.upload_limit_var, UPLOAD_LIMITS)
        self.upload_limit_menu.pack(side="right")

    def menu(self, parent, var, values):
        menu = tk.OptionMenu(parent, var, *values)
        menu.configure(bg=BTN, fg=WHITE, activebackground=LINE, activeforeground=WHITE,
                       bd=0, highlightthickness=0, relief="flat", width=13, font=("Segoe UI", 10))
        menu["menu"].configure(bg=INPUT, fg=WHITE, activebackground=LINE, activeforeground=WHITE)
        return menu

    def build_settings(self, parent):
        line(parent)
        label(parent, "Session settings", size=12, bold=True, color=WHITE).pack(fill="x", pady=(11, 6))
        row = band(parent)
        row.pack(fill="x", pady=3)
        self.password_check = tk.Checkbutton(row, text="Password", variable=self.password_enabled_var,
            command=self._update_password_state, bg=BG, fg=SOFT, selectcolor=INPUT, highlightthickness=0,
            activebackground=BG, activeforeground=WHITE, font=("Segoe UI", 10))
        self.password_check.pack(side="left", padx=(0, 12))
        self.password_entry = tk.Entry(row, bg=INPUT, fg=WHITE, insertbackground=WHITE, show="*",
            disabledbackground=BG, relief="flat", highlightthickness=1, highlightbackground=LINE,
            highlightcolor=TEAL, width=10, font=("Segoe UI", 10))
        self.password_entry.pack(side="right", fill="x", expand=True, ipady=4)
        row = band(parent)
        row.pack(fill="x", pady=3)
        label(row, "Auto-stop").pack(side="left")
        self.timer_menu = self.menu(row, self.timer_var, ("Manual", "10 minutes", "30 minutes", "60 minutes"))
        self.timer_menu.pack(side="right")
        self.progress_row = band(parent)
        label(self.progress_row, variable=self.transfer_note, color=TEAL, wraplength=300).pack(fill="x")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Receive.Horizontal.TProgressbar", background=TEAL, troughcolor=INPUT,
                        bordercolor=INPUT, lightcolor=TEAL, darkcolor=TEAL, thickness=6)
        self.progress = ttk.Progressbar(self.progress_row, maximum=100, style="Receive.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=5)

    def build_connection(self):
        self.side = tk.Frame(self.body, bg=SIDE, width=300)
        self.side.grid(row=0, column=1, sticky="nsew")
        self.side.grid_propagate(False)
        self.side.columnconfigure(0, weight=1)
        self.side.rowconfigure(0, weight=1)
        pane = self.scrollable(self.side)
        label(pane, "Connection", size=14, bold=True, color=WHITE).pack(anchor="w", pady=(0, 8))
        box = tk.Frame(pane, bg=INPUT, width=194, height=194, highlightbackground=LINE, highlightthickness=1)
        box.pack(pady=3)
        box.pack_propagate(False)
        self.offline_image = self.icon("pc", MUTED, 36)
        self.qr_label = tk.Label(box, text="QR offline", image=self.offline_image, compound="top",
                                 bg=INPUT, fg=MUTED, font=("Segoe UI", 10), bd=0)
        self.qr_label.pack(fill="both", expand=True)
        qrtools = band(pane)
        qrtools.pack(pady=(4, 12))
        self.copy_qr_button = self.action(qrtools, " Copy QR", self._copy_qr, "copy")
        self.copy_qr_button.pack(side="left", padx=(0, 5))
        self.save_qr_button = self.action(qrtools, " Save QR", self._save_qr, "save")
        self.save_qr_button.pack(side="left")
        line(pane)
        label(pane, "LAN link", bold=True, color=WHITE).pack(fill="x", pady=(10, 5))
        self.url_entry = tk.Entry(pane, textvariable=self.link_display, state="readonly", width=8,
                                  readonlybackground=INPUT, fg=WHITE, relief="flat", font=("Consolas", 9))
        self.url_entry.pack(fill="x", ipady=6)
        tools = band(pane)
        tools.pack(fill="x", pady=6)
        self.copy_link_button = self.action(tools, " Copy link", self._copy_link, "copy")
        self.copy_link_button.pack(side="left", padx=(0, 5))
        self.open_page_button = self.action(tools, " Open", self._open_download_page, "open")
        self.open_page_button.pack(side="left")
        line(pane)
        for name, value in (("Password", self.password_status_var), ("Auto-stop", self.timer_status_var),
                            ("Last send", self.last_download_var)):
            row = band(pane)
            row.pack(fill="x", pady=2)
            label(row, name, size=9, color=MUTED, width=9).pack(side="left", anchor="n")
            label(row, variable=value, size=9, color=SOFT, wraplength=165).pack(side="left")

    def build_activity(self):
        self.activity = tk.Frame(self.body, bg=BG, padx=20, pady=14)
        heading = band(self.activity)
        heading.pack(fill="x")
        label(heading, "Activity", size=20, bold=True, color=WHITE).pack(side="left")
        self.activity_stop = self.action(heading, " Stop session", self._stop_active_service, "stop")
        self.activity_stop.pack(side="right")
        frame = band(self.activity)
        frame.pack(fill="both", expand=True, pady=(12, 0))
        self.log_text = tk.Text(frame, bg=INPUT, fg=SOFT, padx=12, pady=8, relief="flat",
                                state="disabled", wrap="word", font=("Consolas", 10), height=2)
        bar, sync = self._styled_scrollbar(frame, self.log_text.yview, width=16)
        bar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=sync)
        self.log_text.pack(fill="both", expand=True)

    def switch(self, mode):
        self.mode = mode
        if mode == "Activity":
            self.work.grid_remove()
            self.side.grid_remove()
            self.activity.grid(row=0, column=0, columnspan=2, sticky="nsew")
        else:
            self.activity.grid_remove()
            self.work.grid()
            self.side.grid()
            for name, view in self.views.items():
                view.pack_forget()
                if name == mode:
                    view.pack(fill="x")
            self.refresh_actions()
        for name, (tab, marker) in self.tabs.items():
            accent = TEAL if name == "Receive" else RED
            marker.configure(bg=accent if name == mode else HEADER)
            tab.configure(fg=WHITE if name == mode else MUTED)

    def refresh_actions(self):
        busy = bool(self.server or self.receive_server or self.preparing_folder)
        for button in (self.start_button, self.start_receive_button, self.stop_button):
            button.pack_forget()
        action = self.stop_button if busy else (self.start_button if self.mode == "Send" else self.start_receive_button)
        action.pack(side="left")
        self.clear_button.configure(state="disabled" if busy else "normal")
        self.activity_stop.configure(state="normal" if busy else "disabled")
        self.start_button.configure(state="normal" if self.selected_path and not busy else "disabled",
                                    bg=RED if self.selected_path else BTN)
        self.tabs["Send"][0].configure(state="disabled" if self.receive_server else "normal")
        self.tabs["Receive"][0].configure(state="disabled" if self.server or self.preparing_folder else "normal")
        if self.receive_server:
            self.progress_row.pack(fill="x", pady=(8, 0))
        else:
            self.progress_row.pack_forget()

    def _render_qr(self, url):
        self.qr_image = create_qr_image(url, size=384)
        self.qr_photo = ImageTk.PhotoImage(create_qr_image(url, size=180))
        self.qr_label.configure(image=self.qr_photo, text="", bg="white")

    def reset(self):
        self._clear_reset()
        self.item_title.set("No item selected")
        self.item_detail.set("File or folder")
        self.refresh_actions()

    def poll_transfer(self):
        status = self.status_var.get()
        self.current_status.set(status.split(" (")[0])
        self.badge.configure(fg=TEAL if self.receive_server else GREEN if self.server else SOFT)
        if self.receive_server:
            incoming = self.receive_server.incoming
            if incoming.lock.acquire(blocking=False):
                try:
                    item = max(incoming.files.values(), key=lambda f: (not f.saved, f.touched), default=None)
                    if item:
                        pct = int(100 * item.offset / item.size) if item.size else (100 if item.saved else 0)
                        self.progress["value"] = pct
                        detail = format_bytes(item.offset) + " / " + format_bytes(item.size)
                        self.transfer_note.set(("Saved" if item.saved else f"{pct}% received") +
                                               "  |  " + item.name + "  |  " + detail)
                    else:
                        self.transfer_note.set("Waiting for an upload")
                        self.progress["value"] = 0
                finally:
                    incoming.lock.release()
        self.after(350, self.poll_transfer)

    def help(self):
        messagebox.showinfo("LAN connection", "Connect both devices to the same Wi-Fi or LAN and use the link "
            "from the active session. On a trusted network, allow NoxLab Share through Windows Firewall "
            "on the Private profile. Guest Wi-Fi can isolate devices.", parent=self)
