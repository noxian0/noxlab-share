from __future__ import annotations

import hmac
import html
import mimetypes
import os
import secrets
import shutil
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

from .utils import format_bytes, get_lan_ip


LogCallback = Callable[[str], None]


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


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class ShareServer:
    def __init__(
        self,
        item: ShareItem,
        password: str | None = None,
        start_port: int = 8765,
        log_callback: LogCallback | None = None,
    ) -> None:
        self.item = item
        self.password = password or ""
        self.start_port = start_port
        self.log_callback = log_callback or (lambda message: None)
        self.httpd: ReusableThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int | None = None
        self.lan_ip = get_lan_ip()
        self.url = ""
        self.auth_cookie_name = "noxlab_share_auth"
        self.auth_cookie_value = secrets.token_urlsafe(32)

    @property
    def password_required(self) -> bool:
        return bool(self.password)

    def start(self) -> str:
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

        if self.password_required and not hmac.compare_digest(supplied, self.password):
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
        encoded_name = quote(self.item.served_name)

        try:
            handler.send_response(200)
            handler.send_header("Content-Type", mime_type)
            handler.send_header("Content-Length", str(path.stat().st_size))
            handler.send_header(
                "Content-Disposition",
                f'attachment; filename="{safe_name}"; filename*=UTF-8\'\'{encoded_name}',
            )
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.end_headers()

            if not headers_only:
                with path.open("rb") as source:
                    shutil.copyfileobj(source, handler.wfile)

                self.log_callback(f"Device {handler.client_address[0]} downloaded {self.item.served_name}")
        except (BrokenPipeError, ConnectionResetError):
            self.log_callback(f"Download interrupted from {handler.client_address[0]}")
        except OSError as exc:
            self.log_callback(f"Download error: {exc}")

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
        item_name = html.escape(self.item.display_name)
        served_name = html.escape(self.item.served_name)
        item_type = html.escape(self.item.display_type)
        item_size = html.escape(format_bytes(self.item.served_size))
        source_size = html.escape(format_bytes(self.item.original_size))
        error_html = ""
        if error:
            error_html = f'<p class="error">{html.escape(error)}</p>'

        if self.password_required:
            action_html = """
                <form method="post" action="/download" class="download-form">
                    <label for="password">Password</label>
                    <input id="password" name="password" type="password" autocomplete="current-password" required>
                    <button type="submit">Download</button>
                </form>
            """
            password_note = "Password required"
        else:
            file_href = html.escape(self._file_url(), quote=True)
            action_html = f'<a class="button" href="{file_href}" download="{served_name}">Download</a>'
            password_note = "No password required"

        folder_note = ""
        if self.item.is_folder:
            folder_note = f"<p class=\"muted\">Folder is served as <strong>{served_name}</strong>. Original size: {source_size}.</p>"

        return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>NoxLab Share Download</title>
    <style>
        :root {{
            color-scheme: dark;
            --bg: #0c0e12;
            --panel: #171a20;
            --text: #f2f2f2;
            --muted: #a6aab2;
            --red: #e53935;
            --border: #303640;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            background: var(--bg);
            color: var(--text);
            font-family: Segoe UI, Arial, sans-serif;
            padding: 24px;
        }}
        main {{
            width: min(100%, 560px);
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--panel);
            padding: 28px;
            box-shadow: 0 18px 50px rgba(0, 0, 0, 0.35);
        }}
        .brand {{
            color: var(--red);
            font-family: Consolas, monospace;
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 0;
            margin-bottom: 18px;
        }}
        h1 {{
            font-size: 24px;
            line-height: 1.2;
            margin: 0 0 12px;
            overflow-wrap: anywhere;
        }}
        dl {{
            display: grid;
            grid-template-columns: 110px 1fr;
            gap: 8px 12px;
            margin: 20px 0;
        }}
        dt {{ color: var(--muted); }}
        dd {{ margin: 0; overflow-wrap: anywhere; }}
        .muted {{
            color: var(--muted);
            font-size: 14px;
            line-height: 1.5;
        }}
        .warning {{
            border-left: 3px solid var(--red);
            padding: 10px 12px;
            background: rgba(229, 57, 53, 0.1);
            margin: 18px 0;
        }}
        .error {{
            color: #ffd2d2;
            background: rgba(229, 57, 53, 0.18);
            border: 1px solid rgba(229, 57, 53, 0.4);
            padding: 10px 12px;
            border-radius: 6px;
        }}
        .button,
        button {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 44px;
            padding: 0 18px;
            border: 0;
            border-radius: 6px;
            background: var(--red);
            color: white;
            font-weight: 700;
            font-size: 16px;
            text-decoration: none;
            cursor: pointer;
        }}
        input {{
            width: 100%;
            min-height: 42px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background: #0f1217;
            color: var(--text);
            padding: 8px 10px;
            font-size: 16px;
        }}
        label {{
            display: block;
            margin-bottom: 8px;
            color: var(--muted);
        }}
        .download-form {{
            display: grid;
            gap: 12px;
            margin-top: 18px;
        }}
    </style>
</head>
<body>
    <main>
        <div class="brand">NOXLAB SHARE</div>
        <h1>{item_name}</h1>
        <dl>
            <dt>Type</dt><dd>{item_type}</dd>
            <dt>Download</dt><dd>{served_name}</dd>
            <dt>Size</dt><dd>{item_size}</dd>
            <dt>Security</dt><dd>{password_note}</dd>
        </dl>
        {folder_note}
        <p class="warning muted">This link only works while the sender keeps NoxLab Share running and your device is on the same Wi-Fi or LAN.</p>
        {error_html}
        {action_html}
    </main>
</body>
</html>"""

    @staticmethod
    def _not_found_page() -> str:
        return """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Not Found</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#0c0e12;color:#f2f2f2;padding:24px;">
<h1>Not found</h1><p>Open /download to access the active NoxLab Share link.</p>
</body>
</html>"""

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
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.log_callback(f"Receive server started on {self.url}")
        return self.url

    def stop(self) -> None:
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
        if parsed.path != "/upload":
            self._send_html(handler, self._not_found_page(), status=404)
            return
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
            self._send_html(handler, self._upload_page(error="Upload is larger than the allowed limit."), status=413)
            return

        try:
            uploaded = self._save_multipart(handler, boundary.encode("utf-8"), content_length)
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
                target = self._unique_upload_path(raw_filename)
                boundary_status, written = self._write_file_part(read_line, boundary_line, final_boundary, target)
                if written == 0:
                    target.unlink(missing_ok=True)
                else:
                    uploaded.append((target, written))
            else:
                boundary_status, value = self._read_text_part(read_line, boundary_line, final_boundary)
                if field_name == "password" and self.password_required:
                    authorized = hmac.compare_digest(value, self.password)

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

    def _unique_upload_path(self, filename: str) -> Path:
        safe_name = self._safe_filename(filename)
        candidate = self.save_dir / safe_name
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        for index in range(1, 10000):
            candidate = self.save_dir / f"{stem} ({index}){suffix}"
            if not candidate.exists():
                return candidate
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
        password_html = ""
        security_note = "No password required"
        if self.password_required:
            security_note = "Password required"
            password_html = """
                <label for="password">Password</label>
                <input id="password" name="password" type="password" autocomplete="current-password" required>
            """

        error_html = ""
        if error:
            error_html = f'<p class="error">{html.escape(error)}</p>'

        max_size = "No fixed limit" if self.max_upload_bytes is None else html.escape(format_bytes(self.max_upload_bytes))
        return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>NoxLab Share Upload</title>
    <style>
        :root {{
            color-scheme: dark;
            --bg: #0c0e12;
            --panel: #171a20;
            --text: #f2f2f2;
            --muted: #a6aab2;
            --red: #e53935;
            --border: #303640;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            background: var(--bg);
            color: var(--text);
            font-family: Segoe UI, Arial, sans-serif;
            padding: 24px;
        }}
        main {{
            width: min(100%, 560px);
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--panel);
            padding: 28px;
            box-shadow: 0 18px 50px rgba(0, 0, 0, 0.35);
        }}
        .brand {{
            color: var(--red);
            font-family: Consolas, monospace;
            font-size: 14px;
            font-weight: 700;
            margin-bottom: 18px;
        }}
        h1 {{
            font-size: 24px;
            line-height: 1.2;
            margin: 0 0 12px;
        }}
        .muted {{
            color: var(--muted);
            font-size: 14px;
            line-height: 1.5;
        }}
        .warning {{
            border-left: 3px solid var(--red);
            padding: 10px 12px;
            background: rgba(229, 57, 53, 0.1);
            margin: 18px 0;
        }}
        .error {{
            color: #ffd2d2;
            background: rgba(229, 57, 53, 0.18);
            border: 1px solid rgba(229, 57, 53, 0.4);
            padding: 10px 12px;
            border-radius: 6px;
        }}
        form {{
            display: grid;
            gap: 12px;
            margin-top: 18px;
        }}
        input {{
            width: 100%;
            min-height: 44px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background: #0f1217;
            color: var(--text);
            padding: 9px 10px;
            font-size: 16px;
        }}
        input[type="file"] {{
            padding: 10px;
        }}
        label {{
            color: var(--muted);
        }}
        button {{
            min-height: 46px;
            border: 0;
            border-radius: 6px;
            background: var(--red);
            color: white;
            font-weight: 700;
            font-size: 16px;
            cursor: pointer;
        }}
    </style>
</head>
<body>
    <main>
        <div class="brand">NOXLAB SHARE</div>
        <h1>Send files to this PC</h1>
        <p class="muted">Choose one or more files from your phone. They will be saved on the sender's PC.</p>
        <p class="warning muted">LAN only. Use this only on networks you trust. Limit: {max_size}. Security: {security_note}.</p>
        {error_html}
        <form method="post" action="/upload" enctype="multipart/form-data">
            {password_html}
            <label for="files">Files</label>
            <input id="files" name="files" type="file" multiple required>
            <button type="submit">Upload to PC</button>
        </form>
    </main>
</body>
</html>"""

    def _success_page(self, uploaded: list[tuple[Path, int]]) -> str:
        items = "\n".join(
            f"<li>{html.escape(path.name)} <span>{html.escape(format_bytes(size))}</span></li>"
            for path, size in uploaded
        )
        return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>NoxLab Share Upload Complete</title>
    <style>
        :root {{ color-scheme: dark; --bg:#0c0e12; --panel:#171a20; --text:#f2f2f2; --muted:#a6aab2; --red:#e53935; --border:#303640; }}
        * {{ box-sizing: border-box; }}
        body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg); color:var(--text); font-family:Segoe UI,Arial,sans-serif; padding:24px; }}
        main {{ width:min(100%,560px); border:1px solid var(--border); border-radius:8px; background:var(--panel); padding:28px; }}
        .brand {{ color:var(--red); font-family:Consolas,monospace; font-weight:700; margin-bottom:18px; }}
        h1 {{ font-size:24px; margin:0 0 12px; }}
        .muted, span {{ color:var(--muted); }}
        li {{ margin:8px 0; overflow-wrap:anywhere; }}
        a {{ color:white; background:var(--red); display:inline-flex; min-height:42px; align-items:center; padding:0 16px; border-radius:6px; text-decoration:none; font-weight:700; margin-top:18px; }}
    </style>
</head>
<body>
    <main>
        <div class="brand">NOXLAB SHARE</div>
        <h1>Upload complete</h1>
        <p class="muted">Saved on the PC:</p>
        <ul>{items}</ul>
        <a href="/upload">Upload more</a>
    </main>
</body>
</html>"""

    @staticmethod
    def _not_found_page() -> str:
        return """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Not Found</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#0c0e12;color:#f2f2f2;padding:24px;">
<h1>Not found</h1><p>Open /upload to send files to this PC.</p>
</body>
</html>"""

    @staticmethod
    def _send_html(handler: BaseHTTPRequestHandler, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(payload)

    @staticmethod
    def _redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
        handler.send_response(302)
        handler.send_header("Location", location)
        handler.end_headers()
