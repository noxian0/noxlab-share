"""Receive one bounded chunk at a time and publish only complete files."""

import hmac
import os
import secrets
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


CHUNK_SIZE = 2 * 1024 * 1024


class UploadError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


@dataclass
class IncomingFile:
    temp: Path
    name: str
    size: int
    offset: int = 0
    saved: bool = False
    touched: float = field(default_factory=time.monotonic)

    def receipt(self):
        return {"offset": self.offset, "saved": self.saved, "name": self.name}


class IncomingFiles:
    def __init__(self, server):
        self.server = server
        self.files = {}
        self.lock = threading.Lock()
        self.closed = False

    def begin(self, data, device):
        password = data.get("password", "")
        if not isinstance(password, str) or not hmac.compare_digest(password.encode(), self.server.password.encode()):
            raise UploadError(401, "Check the receive password on the PC.")
        name, size = data.get("name"), data.get("size")
        if not isinstance(name, str) or not name or type(size) is not int or not 0 <= size <= 2**53 - 1:
            raise UploadError(400, "Choose a file with a known size.")
        if self.server.max_upload_bytes is not None and size > self.server.max_upload_bytes:
            raise UploadError(413, "This file exceeds the upload limit set on the PC.")
        with self.lock:
            if self.closed:
                raise UploadError(410, "Receiving has stopped.")
            if sum(not item.saved for item in self.files.values()) >= 32:
                raise UploadError(429, "The PC already has too many uploads in progress.")
            if shutil.disk_usage(self.server.save_dir).free < size:
                raise UploadError(507, "The receive drive has too little free space.")
            token = secrets.token_urlsafe(24)
            with tempfile.NamedTemporaryFile(dir=self.server.save_dir, prefix=".noxlab-", suffix=".part", delete=False) as f:
                item = IncomingFile(Path(f.name), self.server._safe_filename(name), size)
            self.files[token] = item
        self.server.log_callback(f"Device {device} started uploading {item.name}")
        return {"token": token, "chunkSize": CHUNK_SIZE}

    def append(self, token, offset, block, device):
        with self.lock:
            item = self.files.get(token)
            if item is None:
                raise UploadError(410, "This transfer expired. Select the file again.")
            if offset < 0 or offset > item.offset or len(block) != min(CHUNK_SIZE, item.size - offset):
                raise UploadError(409, "The chunk does not match this transfer.")
            item.touched = time.monotonic()
            if item.saved or offset < item.offset:
                if offset + len(block) > item.offset:
                    raise UploadError(409, "The chunk overlaps an incomplete transfer.")
                return item.receipt()
            with item.temp.open("r+b") as output:
                output.seek(offset)
                output.write(block)
            end = offset + len(block)
            if end == item.size:
                target = self.server._reserve_upload_path(item.name)
                try:
                    os.replace(item.temp, target)
                except OSError:
                    target.unlink(missing_ok=True)
                    raise
                item.saved = True
                item.name = target.name
                self.server.log_callback(f"Device {device} uploaded {target.name} ({item.size:,} bytes)")
            item.offset = end
            return item.receipt()

    def discard(self, token):
        with self.lock:
            self._remove(token)

    def _remove(self, token):
        item = self.files.get(token)
        if item:
            if not item.saved:
                item.temp.unlink(missing_ok=True)
            del self.files[token]

    def expire(self, stopping=False):
        with self.lock:
            self.closed |= stopping
            cutoff = time.monotonic() - 900
            for token, item in list(self.files.items()):
                if stopping or item.touched < cutoff:
                    try:
                        self._remove(token)
                    except OSError as exc:
                        self.server.log_callback(f"Could not clean up an incomplete upload: {exc}")
