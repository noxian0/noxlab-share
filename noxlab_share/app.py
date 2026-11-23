from __future__ import annotations

import ctypes
import queue
import os
import platform
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

from . import __version__
from .desktop_ui import BG, DesktopLayout, UPLOAD_LIMITS

from .qr_tools import copy_image_to_clipboard, save_qr_image
from .server import DownloadCompleteEvent, ReceiveServer, ShareItem, ShareServer
from .utils import ZipPreparationCancelled, build_folder_zip, folder_size, format_bytes, remove_temp_file


BORDER = "#41434b"
PANEL = "#101114"
TEXT = "#f4f3f1"
SCROLL_TRACK = "#11141a"
SCROLL_THUMB = "#c62828"
SCROLL_THUMB_ACTIVE = "#ff443f"
WINDOWS_APP_ID = "noxian0.NoxLabShare"




def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base_path / relative_path


def set_windows_app_id() -> None:
    if platform.system() != "Windows":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except (AttributeError, OSError):
        pass


class NoxLabShareApp(DesktopLayout, tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"NoxLab Share {__version__}")
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
        self.preparing_folder = False
        self.prepare_cancel_event: threading.Event | None = None
        self.prepare_events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.qr_image = None
        self.qr_photo = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.download_events: queue.Queue[DownloadCompleteEvent] = queue.Queue()
        self.timer_after_id: str | None = None
        self.deadline: datetime | None = None
        self.auto_stop_pending = False

        self.selected_name_var = tk.StringVar(value="No file or folder selected")
        self.selected_type_var = tk.StringVar(value="-")
        self.selected_size_var = tk.StringVar(value="-")
        self.status_var = tk.StringVar(value="Idle")
        self.last_download_var = tk.StringVar(value="-")
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
        self._poll_download_events()
        self._poll_prepare_events()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._apply_dark_title_bar)


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
        self.last_download_var.set("-")
        self.status_var.set("Ready")
        self.item_title.set(path.name or str(path))
        self.item_detail.set(("Folder ZIP" if is_folder else "File") + "  |  " + format_bytes(size))
        self.refresh_actions()

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

        if self.selected_is_folder:
            self._begin_folder_preparation(self.selected_path, password)
            return

        try:
            self.status_var.set("Preparing")
            self.update_idletasks()
            item = self._prepare_share_item()
            self.server = ShareServer(
                item=item,
                password=password,
                log_callback=self._enqueue_log,
                download_complete_callback=self._enqueue_download_complete,
            )
            url = self.server.start()
            self.lan_url_var.set(url)
            self._render_qr(url)
            self._set_running_state(True)
            self._start_timer_if_needed()
            self.last_download_var.set("Waiting for a device")
            self.status_var.set("Sharing")
            self._log("Share is live on the local network")
        except Exception as exc:
            self._cleanup_temp_zip()
            self.server = None
            self.status_var.set("Error")
            self._log(f"Error: {exc}")
            self._show_error("Could not start sharing", exc)

    def _begin_folder_preparation(self, selected: Path, password: str) -> None:
        self.preparing_folder = True
        self.prepare_cancel_event = threading.Event()
        self.status_var.set("Preparing folder ZIP")
        self._set_running_state(False)
        self._log(f"Preparing {format_bytes(self.selected_size)} folder for sharing")
        self._log("Creating a fast transfer ZIP. The app will stay responsive while it prepares.")

        worker = threading.Thread(
            target=self._prepare_folder_worker,
            args=(selected, self.selected_size, password, self.prepare_cancel_event),
            name="NoxLabFolderPreparation",
            daemon=True,
        )
        worker.start()

    def _prepare_folder_worker(
        self,
        selected: Path,
        original_size: int,
        password: str,
        cancel_event: threading.Event,
    ) -> None:
        last_update = 0.0

        def on_progress(completed: int, total: int, current_item: Path) -> None:
            nonlocal last_update
            now = time.monotonic()
            if completed and now - last_update < 0.4 and completed < total:
                return
            last_update = now
            self.prepare_events.put(("progress", (completed, total, current_item.name)))

        zip_path: Path | None = None
        server: ShareServer | None = None
        try:
            zip_path = build_folder_zip(
                selected,
                total_bytes=original_size,
                progress_callback=on_progress,
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                raise ZipPreparationCancelled("Folder preparation was stopped.")

            item = ShareItem(
                source_path=selected,
                served_path=zip_path,
                display_name=selected.name or str(selected),
                served_name=f"{selected.name or 'shared-folder'}.zip",
                original_size=original_size,
                served_size=zip_path.stat().st_size,
                is_folder=True,
            )
            server = ShareServer(
                item=item,
                password=password,
                log_callback=self._enqueue_log,
                download_complete_callback=self._enqueue_download_complete,
            )
            url = server.start()
            self.prepare_events.put(("ready", (server, zip_path, url)))
        except ZipPreparationCancelled:
            if server:
                server.stop()
            remove_temp_file(zip_path)
            self.prepare_events.put(("cancelled", None))
        except Exception as exc:
            if server:
                server.stop()
            remove_temp_file(zip_path)
            self.prepare_events.put(("error", exc))

    def _poll_prepare_events(self) -> None:
        try:
            while True:
                event, value = self.prepare_events.get_nowait()
                if event == "progress":
                    completed, total, current_name = value
                    if total:
                        percent = min(100, int(completed * 100 / total))
                        self.status_var.set(
                            f"Preparing ZIP: {percent}% ({format_bytes(completed)} of {format_bytes(total)})"
                        )
                    else:
                        self.status_var.set(f"Preparing ZIP: {format_bytes(completed)}")
                elif event == "ready":
                    server, zip_path, url = value
                    if not self.preparing_folder:
                        server.stop()
                        remove_temp_file(zip_path)
                        continue
                    self.preparing_folder = False
                    self.prepare_cancel_event = None
                    self.server = server
                    self.temp_zip_path = zip_path
                    self.lan_url_var.set(url)
                    self._render_qr(url)
                    self._set_running_state(True)
                    self._start_timer_if_needed()
                    self.last_download_var.set("Waiting for a device")
                    self.status_var.set("Sharing")
                    self._log("Folder ZIP is ready. Share is live on the local network")
                elif event == "cancelled":
                    self.preparing_folder = False
                    self.prepare_cancel_event = None
                    self.status_var.set("Stopped")
                    self._set_running_state(False)
                    self._log("Folder preparation stopped")
                elif event == "error":
                    self.preparing_folder = False
                    self.prepare_cancel_event = None
                    self.status_var.set("Error")
                    self._set_running_state(False)
                    self._log(f"Error while preparing folder: {value}")
                    self._show_error("Could not prepare folder", value)
        except queue.Empty:
            pass
        self.after(150, self._poll_prepare_events)

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
            self.last_download_var.set("-")
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
        if self.preparing_folder:
            if self.prepare_cancel_event:
                self.prepare_cancel_event.set()
            self.status_var.set("Stopping folder preparation")
            self._log("Stopping folder ZIP preparation")
            return
        if self.server:
            self._stop_sharing()
            return
        if self.receive_server:
            self._stop_receiving()
            return
        self._cancel_timer()

    def _clear_reset(self) -> None:
        self._stop_active_service()
        if self.preparing_folder:
            return
        self.selected_path = None
        self.selected_is_folder = False
        self.selected_size = 0
        self.selected_name_var.set("No file or folder selected")
        self.selected_type_var.set("-")
        self.selected_size_var.set("-")
        self.last_download_var.set("-")
        self.status_var.set("Idle")
        self.password_enabled_var.set(False)
        self.password_entry.configure(state="normal")
        self.password_entry.delete(0, tk.END)
        self._update_password_state()
        self.timer_var.set("Manual")
        self.timer_status_var.set("Manual")
        self._log("Reset complete")

    def _set_running_state(self, running: bool) -> None:
        busy = running or self.preparing_folder
        normal = "normal"
        disabled = "disabled"
        self.start_button.configure(state=disabled if busy else normal)
        self.stop_button.configure(state=normal if busy else disabled)
        self.copy_link_button.configure(state=normal if running else disabled)
        self.open_page_button.configure(state=normal if running else disabled)
        self.save_qr_button.configure(state=normal if running and self.qr_image else disabled)
        self.copy_qr_button.configure(state=normal if running and self.qr_image else disabled)
        self.select_file_button.configure(state=disabled if busy else normal)
        self.select_folder_button.configure(state=disabled if busy else normal)
        self.start_receive_button.configure(state=disabled if busy else normal)
        self.choose_receive_folder_button.configure(state=disabled if busy else normal)
        self.upload_limit_menu.configure(state=disabled if busy else normal)
        self.open_receive_folder_button.configure(state=normal)
        self.password_check.configure(state=disabled if busy else normal)
        self.password_entry.configure(state=disabled if busy or not self.password_enabled_var.get() else normal)
        self.timer_menu.configure(state=disabled if busy else normal)
        self.refresh_actions()
        if not running:
            self.qr_label.configure(image=self.offline_image, text="QR offline", bg=self.qr_label.master["bg"], compound="top")
            self.transfer_note.set("Waiting for an upload")
            self.progress["value"] = 0


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

    def _enqueue_download_complete(self, event: DownloadCompleteEvent) -> None:
        self.download_events.put(event)

    def _poll_logs(self) -> None:
        try:
            while True:
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(250, self._poll_logs)

    def _poll_download_events(self) -> None:
        try:
            while True:
                event = self.download_events.get_nowait()
                finished_at = event.completed_at.strftime("%H:%M:%S")
                state = "final range sent" if event.resumed else "file sent"
                self.last_download_var.set(f"{finished_at} | {event.device_ip} | {state}")
        except queue.Empty:
            pass
        self.after(150, self._poll_download_events)

    def _log(self, message: str) -> None:
        self._append_log(message)

    def _append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.latest.set(stamp + "   " + message)
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
    set_windows_app_id()
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
