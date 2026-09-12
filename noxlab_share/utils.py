from __future__ import annotations

import os
import shutil
import socket
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Callable


def format_bytes(size: int) -> str:
    """Return a compact human-readable byte count."""
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(size, 0))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def folder_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            file_path = Path(root) / name
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
    return total


def get_lan_ip() -> str:
    """Find a likely LAN IP without contacting an internet service."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        sock.close()

    try:
        candidates = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        candidates = []

    for candidate in candidates:
        ip = candidate[4][0]
        if ip and not ip.startswith("127."):
            return ip

    return "127.0.0.1"


class ZipPreparationCancelled(RuntimeError):
    """Raised when a folder ZIP is cancelled before it is ready to share."""


ZipProgressCallback = Callable[[int, int, Path], None]


def build_folder_zip(
    folder_path: Path,
    *,
    total_bytes: int | None = None,
    progress_callback: ZipProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Create a temporary transfer ZIP without blocking the app's interface."""
    folder_path = folder_path.resolve()
    root_name = folder_path.name or "shared-folder"
    temp_dir = Path(tempfile.gettempdir())

    if total_bytes is not None:
        required_space = total_bytes + 16 * 1024 * 1024
        available_space = shutil.disk_usage(temp_dir).free
        if available_space < required_space:
            raise OSError(
                "Not enough free space for the temporary folder ZIP. "
                f"Need about {format_bytes(required_space)} free on {temp_dir.drive or temp_dir}."
            )

    temp = tempfile.NamedTemporaryFile(
        prefix="noxlab_share_",
        suffix=".zip",
        delete=False,
    )
    temp_path = Path(temp.name)
    temp.close()

    completed_bytes = 0

    def report_progress(current_item: Path) -> None:
        if progress_callback:
            progress_callback(completed_bytes, total_bytes or 0, current_item)

    try:
        report_progress(folder_path)
        # Sharing favors quick preparation over shrinking the archive. This is
        # especially important for large folders containing video, games, or archives.
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
            for item in folder_path.rglob("*"):
                if cancel_event and cancel_event.is_set():
                    raise ZipPreparationCancelled("Folder preparation was stopped.")

                relative = item.relative_to(folder_path)
                archive_name = (Path(root_name) / relative).as_posix()

                if item.is_dir():
                    try:
                        if not any(item.iterdir()):
                            archive.writestr(f"{archive_name.rstrip('/')}/", "")
                    except OSError:
                        continue
                elif item.is_file():
                    try:
                        item_size = item.stat().st_size
                        archive.write(item, archive_name)
                    except OSError:
                        continue
                    completed_bytes += item_size
                    report_progress(relative)
        return temp_path
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise


def remove_temp_file(path: Path | None) -> None:
    if not path:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
