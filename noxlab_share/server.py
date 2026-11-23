from __future__ import annotations

import hmac
import json
import errno
import mimetypes
import os
import secrets
import socket
import threading
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import mobile
from .incoming import CHUNK_SIZE, IncomingFiles, UploadError
from .utils import format_bytes, get_lan_ip


LogCallback = Callable[[str], None]
DownloadCompleteCallback = Callable[["DownloadCompleteEvent"], None]


@dataclass(frozen=True)
class ShareItem:
    source_path: Path
    served_path: Path
    display_name: str
    served_name: str
    original_size: int
    served_size: int
    is_folder: bool

    @property
    def display_type(self) -> str:
        return "Folder ZIP" if self.is_folder else "File"


@dataclass(frozen=True)
class DownloadCompleteEvent:
    """The sender finished writing a response ending at the file's final byte."""

    device_ip: str
    item_name: str
    completed_at: datetime
    resumed: bool
    transferred_bytes: int
    total_bytes: int


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class ShareServer:
    def __init__(
        self,
        item: ShareItem,
        password: str | None = None,
        start_port: int = 8765,
        log_callback: LogCallback | None = None,
        download_complete_callback: DownloadCompleteCallback | None = None,
    ) -> None:
        self.item = item
        self.password = password or ""
        self.start_port = start_port
        self.log_callback = log_callback or (lambda message: None)
        self.download_complete_callback = download_complete_callback or (lambda _event: None)
        self.httpd: ReusableThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int | None = None
        self.lan_ip = get_lan_ip()
        self.url = ""
        self.auth_cookie_name = "noxlab_share_auth"
        self.auth_cookie_value = secrets.token_urlsafe(32)
        self._active_connections: set[socket.socket] = set()
        self._connection_lock = threading.Lock()
        self._stopping = threading.Event()

    @property
    def password_required(self) -> bool:
        return bool(self.password)

    def start(self) -> str:
        self._stopping.clear()
        handler_class = self._make_handler()
        last_error: OSError | None = None

        for port in range(self.start_port, self.start_port + 100):
            try:
                self.httpd = ReusableThreadingHTTPServer(("0.0.0.0", port), handler_class)
                self.port = port
                break
            except OSError as exc:
                last_error = exc

        if not self.httpd or not self.port:
            raise RuntimeError(f"Could not start server: {last_error}")

        self.url = f"http://{self.lan_ip}:{self.port}/download"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.log_callback(f"Server started on {self.url}")
        return self.url

    def stop(self) -> None:
        self._stopping.set()
        with self._connection_lock:
            active_connections = tuple(self._active_connections)

        for connection in active_connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass

        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.thread = None
        self.log_callback("Server stopped")

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class RequestHandler(BaseHTTPRequestHandler):
            server_version = "NoxLabShare/0.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                owner._handle_get(self)

            def do_POST(self) -> None:
                owner._handle_post(self)

            def do_HEAD(self) -> None:
                owner._handle_head(self)

        return RequestHandler

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/":
            self._redirect(handler, "/download")
            return

        if parsed.path.startswith("/file/"):
            if not self._is_file_path(parsed.path):
                self._send_html(handler, self._not_found_page(), status=404)
                return
            if self.password_required and not self._has_auth_cookie(handler):
                self._send_html(handler, self._download_page(error="Enter the password to download this file."), status=401)
                return
            self._send_file(handler)
            return

        if parsed.path != "/download":
            self._send_html(handler, self._not_found_page(), status=404)
            return

        if params.get("download", ["0"])[0] == "1":
            if self.password_required:
                self._send_html(handler, self._download_page(error=None), status=401)
                return
            self._redirect(handler, self._file_url(), status=303)
            return

        self._send_html(handler, self._download_page(error=None))

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path != "/download":
            self._send_html(handler, self._not_found_page(), status=404)
            return

        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        if length > 8192:
            self._send_html(handler, self._download_page(error="Request is too large."), status=413)
            return

        raw = handler.rfile.read(length).decode("utf-8", errors="replace")
        params = parse_qs(raw)
        supplied = params.get("password", [""])[0]

        if self.password_required and not hmac.compare_digest(supplied.encode(), self.password.encode()):
            self.log_callback(f"Rejected password attempt from {handler.client_address[0]}")
            self._send_html(handler, self._download_page(error="Incorrect password."), status=401)
            return

        self._redirect(handler, self._file_url(), status=303, set_auth_cookie=self.password_required)

    def _handle_head(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path == "/download":
            handler.send_response(200)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.end_headers()
            return

        if parsed.path.startswith("/file/"):
            if not self._is_file_path(parsed.path):
                handler.send_response(404)
                handler.end_headers()
                return
            if self.password_required and not self._has_auth_cookie(handler):
                handler.send_response(401)
                handler.end_headers()
                return
            self._send_file(handler, headers_only=True)
            return

        if parsed.path != "/download":
            handler.send_response(404)
            handler.end_headers()
            return

    def _send_file(self, handler: BaseHTTPRequestHandler, headers_only: bool = False) -> None:
        path = self.item.served_path
        if not path.exists():
            self._send_html(handler, self._download_page(error="Shared file is no longer available."), status=410)
            return

        mime_type, _encoding = mimetypes.guess_type(self.item.served_name)
        if not mime_type:
            mime_type = "application/zip" if self.item.is_folder else "application/octet-stream"

        safe_name = self.item.served_name.replace("\\", "_").replace('"', "'")
        safe_name = safe_name.encode("ascii", "replace").decode("ascii").replace("?", "_")
        encoded_name = quote(self.item.served_name, safe="")

        try:
            file_size = path.stat().st_size
            byte_range = self._parse_byte_range(handler.headers.get("Range"), file_size)
        except ValueError:
            self._send_range_not_satisfiable(handler, path)
            return
        except OSError as exc:
            self.log_callback(f"Download error: {exc}")
            self._send_html(handler, self._download_page(error="Shared file is no longer available."), status=410)
            return

        start, end = byte_range if byte_range else (0, file_size - 1)
        content_length = end - start + 1 if file_size else 0
        is_partial = byte_range is not None

        try:
            handler.send_response(206 if is_partial else 200)
            handler.send_header("Content-Type", mime_type)
            handler.send_header("Content-Length", str(content_length))
            handler.send_header("Accept-Ranges", "bytes")
            if is_partial:
                handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            handler.send_header(
                "Content-Disposition",
                f'attachment; filename="{safe_name}"; filename*=UTF-8\'\'{encoded_name}',
            )
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.end_headers()

            if not headers_only:
                self._register_connection(handler.connection)
                try:
                    with path.open("rb") as source:
                        source.seek(start)
                        self._copy_file_range(source, handler.wfile, content_length)
                finally:
                    self._unregister_connection(handler.connection)

                self._report_download_completion(
                    device_ip=handler.client_address[0],
                    start=start,
                    end=end,
                    file_size=file_size,
                )
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            message = "Download stopped" if self._stopping.is_set() else "Download interrupted"
            self.log_callback(f"{message} from {handler.client_address[0]}")
        except OSError as exc:
            if self._stopping.is_set():
                self.log_callback(f"Download stopped from {handler.client_address[0]}")
            else:
                self.log_callback(f"Download error: {exc}")

    def _report_download_completion(self, device_ip: str, start: int, end: int, file_size: int) -> None:
        if end != file_size - 1:
            self.log_callback(f"Device {device_ip} received part of {self.item.served_name}")
            return

        resumed = start > 0
        transferred_bytes = end - start + 1 if file_size else 0
        event = DownloadCompleteEvent(
            device_ip=device_ip,
            item_name=self.item.served_name,
            completed_at=datetime.now(),
            resumed=resumed,
            transferred_bytes=transferred_bytes,
            total_bytes=file_size,
        )
        self.download_complete_callback(event)

        if resumed:
            self.log_callback(
                f"Sent final {format_bytes(transferred_bytes)} of {self.item.served_name} to {device_ip}"
            )
        else:
            self.log_callback(f"Sent {self.item.served_name} to {device_ip}")

    @staticmethod
    def _parse_byte_range(range_header: str | None, file_size: int) -> tuple[int, int] | None:
        if not range_header:
            return None
        if file_size <= 0:
            raise ValueError("Cannot serve a range from an empty file.")

        unit, separator, values = range_header.partition("=")
        if separator != "=" or unit.strip().lower() != "bytes" or "," in values:
            raise ValueError("Unsupported byte range.")

        start_text, dash, end_text = values.strip().partition("-")
        if dash != "-":
            raise ValueError("Invalid byte range.")

        if not start_text:
            try:
                suffix_length = int(end_text)
            except ValueError as exc:
                raise ValueError("Invalid byte range.") from exc
            if suffix_length <= 0:
                raise ValueError("Invalid byte range.")
            return max(file_size - suffix_length, 0), file_size - 1

        try:
            start = int(start_text)
            end = int(end_text) if end_text else file_size - 1
        except ValueError as exc:
            raise ValueError("Invalid byte range.") from exc

        if start < 0 or start >= file_size or end < start:
            raise ValueError("Invalid byte range.")
        return start, min(end, file_size - 1)

    @staticmethod
    def _send_range_not_satisfiable(handler: BaseHTTPRequestHandler, path: Path) -> None:
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0
        handler.send_response(416)
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Content-Range", f"bytes */{file_size}")
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    def _copy_file_range(self, source, destination, remaining: int) -> None:
        chunk_size = 1024 * 1024
        while remaining > 0:
            if self._stopping.is_set():
                raise ConnectionAbortedError("Sharing was stopped.")
            chunk = source.read(min(chunk_size, remaining))
            if not chunk:
                raise OSError("Shared file changed while it was being downloaded.")
            destination.write(chunk)
            remaining -= len(chunk)

    def _register_connection(self, connection: socket.socket) -> None:
        with self._connection_lock:
            if self._stopping.is_set():
                raise ConnectionAbortedError("Sharing was stopped.")
            self._active_connections.add(connection)

    def _unregister_connection(self, connection: socket.socket) -> None:
        with self._connection_lock:
            self._active_connections.discard(connection)

    def _file_url(self) -> str:
        return f"/file/{quote(self.item.served_name, safe='')}"

    def _is_file_path(self, path: str) -> bool:
        return unquote(path.removeprefix("/file/")) == self.item.served_name

    def _has_auth_cookie(self, handler: BaseHTTPRequestHandler) -> bool:
        raw_cookie = handler.headers.get("Cookie", "")
        for part in raw_cookie.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name == self.auth_cookie_name and hmac.compare_digest(value, self.auth_cookie_value):
                return True
        return False

    def _download_page(self, error: str | None) -> str:
        return mobile.download_page(self.item, self._file_url(), self.password_required, error)

    @staticmethod
    def _not_found_page() -> str:
        return mobile.not_found()

    @staticmethod
    def _send_html(handler: BaseHTTPRequestHandler, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(payload)

    def _redirect(
        self,
        handler: BaseHTTPRequestHandler,
        location: str,
        status: int = 302,
        set_auth_cookie: bool = False,
    ) -> None:
        handler.send_response(status)
        handler.send_header("Location", location)
        if set_auth_cookie:
            handler.send_header(
                "Set-Cookie",
                f"{self.auth_cookie_name}={self.auth_cookie_value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=3600",
            )
        handler.end_headers()


class ReceiveServer:
    def __init__(
        self,
        save_dir: Path,
        password: str | None = None,
        start_port: int = 8865,
        log_callback: LogCallback | None = None,
        max_upload_bytes: int | None = 2 * 1024 * 1024 * 1024,
    ) -> None:
        self.save_dir = save_dir
        self.password = password or ""
        self.start_port = start_port
        self.log_callback = log_callback or (lambda message: None)
        self.max_upload_bytes = max_upload_bytes
        self.incoming = IncomingFiles(self)
        self.httpd: ReusableThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int | None = None
        self.lan_ip = get_lan_ip()
        self.url = ""

    @property
    def password_required(self) -> bool:
        return bool(self.password)

    def start(self) -> str:
        self.save_dir.mkdir(parents=True, exist_ok=True)
        handler_class = self._make_handler()
        last_error: OSError | None = None

        for port in range(self.start_port, self.start_port + 100):
            try:
                self.httpd = ReusableThreadingHTTPServer(("0.0.0.0", port), handler_class)
                self.port = port
                break
            except OSError as exc:
                last_error = exc

        if not self.httpd or not self.port:
            raise RuntimeError(f"Could not start receive server: {last_error}")

        self.url = f"http://{self.lan_ip}:{self.port}/upload"
        self.httpd.service_actions = self.incoming.expire
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.log_callback(f"Receive server started on {self.url}")
        return self.url

    def stop(self) -> None:
        self.incoming.expire(stopping=True)
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.thread = None
        self.log_callback("Receive server stopped")

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class RequestHandler(BaseHTTPRequestHandler):
            server_version = "NoxLabShareReceive/0.1"
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                owner._handle_get(self)

            def do_POST(self) -> None:
                owner._handle_post(self)

            def do_HEAD(self) -> None:
                owner._handle_head(self)

        return RequestHandler

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path == "/":
            self._redirect(handler, "/upload")
            return
        if parsed.path == "/upload.js":
            script = Path(__file__).with_name("upload.js").read_bytes()
            self._respond(handler, script, "text/javascript; charset=utf-8", 200)
            return
        if parsed.path != "/upload":
            self._send_html(handler, self._not_found_page(), status=404)
            return
        self.log_callback(f"Device {handler.client_address[0]} opened the receive page")
        self._send_html(handler, self._upload_page())

    def _handle_head(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path != "/upload":
            handler.send_response(404)
            handler.end_headers()
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.end_headers()

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path in ("/upload/start", "/upload/chunk", "/upload/cancel"):
            self._receive_chunk_request(handler, parsed.path)
            return
        if parsed.path != "/upload":
            self._send_html(handler, self._not_found_page(), status=404)
            return

        content_type = handler.headers.get("Content-Type", "")
        boundary = self._multipart_boundary(content_type)
        if not boundary:
            self._send_html(handler, self._upload_page(error="Upload form data was not understood."), status=400)
            return

        try:
            content_length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        if content_length <= 0:
            self._send_html(handler, self._upload_page(error="No upload data was received."), status=411)
            return
        if self.max_upload_bytes is not None and content_length > self.max_upload_bytes:
            self.log_callback(f"Rejected oversized upload from {handler.client_address[0]}")
            self._send_html(handler, self._upload_page(error="Upload is larger than the allowed limit."), status=413)
            return

        self.log_callback(f"Device {handler.client_address[0]} started an upload ({format_bytes(content_length)})")
        try:
            uploaded = self._save_multipart(handler, boundary.encode("utf-8"), content_length)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.log_callback(f"Upload interrupted by {handler.client_address[0]}")
            return
        except PermissionError as exc:
            handler.close_connection = True
            self._send_html(handler, self._upload_page(error=str(exc)), status=401)
            return
        except ValueError as exc:
            self._send_html(handler, self._upload_page(error=str(exc)), status=400)
            return
        except OSError as exc:
            self.log_callback(f"Upload error: {exc}")
            self._send_html(handler, self._upload_page(error="Could not save uploaded file."), status=500)
            return

        if not uploaded:
            self._send_html(handler, self._upload_page(error="Choose at least one file to upload."), status=400)
            return

        for path, size in uploaded:
            self.log_callback(f"Device {handler.client_address[0]} uploaded {path.name} ({format_bytes(size)})")

        self._send_html(handler, self._success_page(uploaded))

    def _receive_chunk_request(self, handler, path):
        status = 200
        try:
            expected_type = "application/json" if path == "/upload/start" else "application/octet-stream"
            if handler.headers.get_content_type() != expected_type:
                raise UploadError(415, "Unsupported upload format.")
            length = int(handler.headers.get("Content-Length", "-1"))
            limit = {"/upload/start": 8192, "/upload/chunk": CHUNK_SIZE, "/upload/cancel": 0}[path]
            if not 0 <= length <= limit:
                raise UploadError(400, "Invalid upload request length.")
            handler.connection.settimeout(90)
            body = handler.rfile.read(length)
            if len(body) != length:
                raise UploadError(400, "The PC received an incomplete chunk. Retry the upload.")
            device = handler.client_address[0]
            token = handler.headers.get("X-Upload-Token", "")
            if path == "/upload/start":
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError()
                result = self.incoming.begin(data, device)
            elif path == "/upload/chunk":
                result = self.incoming.append(token, int(handler.headers.get("X-Offset", "-1")), body, device)
            else:
                self.incoming.discard(token)
                result = {}
        except UploadError as exc:
            status, result = exc.status, {"error": str(exc)}
        except ValueError:
            status, result = 400, {"error": "Invalid transfer details."}
        except (TimeoutError, ConnectionError):
            self.log_callback(f"Upload connection interrupted from {handler.client_address[0]}")
            handler.close_connection = True
            return
        except OSError as exc:
            self.log_callback(f"Upload write error: {exc}")
            status, result = (507, {"error": "The receive drive is full."}) if exc.errno == errno.ENOSPC else (
                500, {"error": "The PC could not write the file. Check the receive folder and activity log."})
        try:
            self._respond(handler, json.dumps(result).encode(), "application/json", status)
        except ConnectionError:
            handler.close_connection = True

    def _save_multipart(
        self,
        handler: BaseHTTPRequestHandler,
        boundary: bytes,
        content_length: int,
    ) -> list[tuple[Path, int]]:
        remaining = content_length
        boundary_line = b"--" + boundary
        final_boundary = boundary_line + b"--"
        max_line = 1024 * 1024 + len(boundary_line) + 16
        authorized = not self.password_required
        uploaded: list[tuple[Path, int]] = []

        def read_line() -> bytes:
            nonlocal remaining
            if remaining <= 0:
                return b""
            line = handler.rfile.readline(min(max_line, remaining))
            remaining -= len(line)
            return line

        first = read_line()
        if not first.startswith(boundary_line):
            raise ValueError("Upload data did not include a valid boundary.")
        if first.startswith(final_boundary):
            return uploaded

        done = False
        while not done and remaining > 0:
            headers = self._read_part_headers(read_line)
            if headers is None:
                break

            disposition = headers.get("content-disposition", "")
            params = self._parse_header_params(disposition)
            field_name = params.get("name", "")
            raw_filename = params.get("filename", "")

            if raw_filename:
                if not authorized:
                    raise PermissionError("Enter the correct password before uploading files.")
                target = self._reserve_upload_path(raw_filename)
                try:
                    boundary_status, written = self._write_file_part(read_line, boundary_line, final_boundary, target)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                if written == 0:
                    target.unlink(missing_ok=True)
                else:
                    uploaded.append((target, written))
            else:
                boundary_status, value = self._read_text_part(read_line, boundary_line, final_boundary)
                if field_name == "password" and self.password_required:
                    authorized = hmac.compare_digest(value.encode(), self.password.encode())

            done = boundary_status == "final"

        if self.password_required and not authorized:
            raise PermissionError("Incorrect password.")

        return uploaded

    @staticmethod
    def _read_part_headers(read_line) -> dict[str, str] | None:
        headers: dict[str, str] = {}
        while True:
            line = read_line()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                return headers
            text = line.decode("utf-8", errors="replace").strip()
            name, sep, value = text.partition(":")
            if sep:
                headers[name.lower()] = value.strip()

    def _write_file_part(self, read_line, boundary_line: bytes, final_boundary: bytes, target: Path) -> tuple[str, int]:
        previous: bytes | None = None
        written = 0

        with target.open("wb") as output:
            while True:
                line = read_line()
                if not line:
                    raise ValueError("Upload ended before the file was complete.")
                if line.startswith(boundary_line):
                    if previous is not None:
                        data = self._strip_part_ending(previous)
                        output.write(data)
                        written += len(data)
                    return ("final" if line.startswith(final_boundary) else "next", written)
                if previous is not None:
                    output.write(previous)
                    written += len(previous)
                previous = line

    def _read_text_part(self, read_line, boundary_line: bytes, final_boundary: bytes) -> tuple[str, str]:
        chunks: list[bytes] = []
        total = 0

        while True:
            line = read_line()
            if not line:
                raise ValueError("Upload ended before the form field was complete.")
            if line.startswith(boundary_line):
                if chunks:
                    chunks[-1] = self._strip_part_ending(chunks[-1])
                value = b"".join(chunks).decode("utf-8", errors="replace")
                return ("final" if line.startswith(final_boundary) else "next", value)
            total += len(line)
            if total > 8192:
                raise ValueError("Form field is too large.")
            chunks.append(line)

    @staticmethod
    def _strip_part_ending(data: bytes) -> bytes:
        if data.endswith(b"\r\n"):
            return data[:-2]
        if data.endswith(b"\n"):
            return data[:-1]
        return data

    @staticmethod
    def _multipart_boundary(content_type: str) -> str:
        for part in content_type.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name.lower() == "boundary":
                return value.strip().strip('"')
        return ""

    @staticmethod
    def _parse_header_params(header: str) -> dict[str, str]:
        params: dict[str, str] = {}
        parts = header.split(";")
        if parts:
            params["_type"] = parts[0].strip().lower()
        for part in parts[1:]:
            name, sep, value = part.strip().partition("=")
            if not sep:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            params[name.lower()] = value
        return params

    def _reserve_upload_path(self, filename: str) -> Path:
        safe_name = self._safe_filename(filename)
        candidate = self.save_dir / safe_name
        stem = candidate.stem
        suffix = candidate.suffix
        for index in range(10000):
            if index:
                candidate = self.save_dir / f"{stem} ({index}){suffix}"
            try:
                candidate.touch(exist_ok=False)
                return candidate
            except FileExistsError:
                continue
        raise OSError("Could not create a unique filename.")

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = os.path.basename(filename.replace("\\", "/")).strip().strip(".")
        invalid = '<>:"/\\|?*'
        cleaned = "".join("_" if char in invalid or ord(char) < 32 else char for char in name)
        cleaned = cleaned.strip() or "uploaded-file"
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if cleaned.split(".")[0].upper() in reserved:
            cleaned = f"_{cleaned}"
        return cleaned[:180]

    def _upload_page(self, error: str | None = None) -> str:
        return mobile.upload_page(self.max_upload_bytes, self.password_required, error)

    @staticmethod
    def _success_page(uploaded: list[tuple[Path, int]]) -> str:
        return mobile.success_page(uploaded)

    @staticmethod
    def _not_found_page() -> str:
        return mobile.not_found(receiving=True)

    @staticmethod
    def _send_html(handler: BaseHTTPRequestHandler, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        ReceiveServer._respond(handler, payload, "text/html; charset=utf-8", status)

    @staticmethod
    def _respond(handler, payload, content_type, status):
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(payload)
        handler.close_connection = True

    @staticmethod
    def _redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
        handler.send_response(302)
        handler.send_header("Location", location)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
