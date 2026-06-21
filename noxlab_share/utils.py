from __future__ import annotations

import os
import socket
import tempfile
import zipfile
from pathlib import Path


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


def build_folder_zip(folder_path: Path) -> Path:
    """Create a temporary ZIP containing the selected folder."""
    folder_path = folder_path.resolve()
    root_name = folder_path.name or "shared-folder"

    temp = tempfile.NamedTemporaryFile(
        prefix="noxlab_share_",
        suffix=".zip",
        delete=False,
    )
    temp_path = Path(temp.name)
    temp.close()

    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for item in folder_path.rglob("*"):
                relative = item.relative_to(folder_path)
                archive_name = (Path(root_name) / relative).as_posix()

                if item.is_dir():
                    try:
                        if not any(item.iterdir()):
                            archive.writestr(f"{archive_name.rstrip('/')}/", "")
                    except OSError:
                        continue
                elif item.is_file():
                    archive.write(item, archive_name)
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
