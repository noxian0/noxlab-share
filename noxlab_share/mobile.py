"""Shared, self-contained LAN transfer pages."""

import base64
import html
from functools import lru_cache
from pathlib import Path

from . import __version__
from .utils import format_bytes


@lru_cache(maxsize=1)
def _assets():
    package = Path(__file__).resolve().parent
    css = (package / "mobile.css").read_text(encoding="utf-8")
    mark = base64.b64encode((package.parent / "assets/noxlab_share.png").read_bytes()).decode("ascii")
    return css, f"data:image/png;base64,{mark}"


def page(title, content, *, receiving=False):
    css, icon = _assets()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#101114"><title>{title} | NoxLab Share</title>
<link rel="icon" href="{icon}"><style>{css}</style></head>
<body class="{'receive' if receiving else 'send'}">
<header><div class="bar"><img src="{icon}" width="38" height="38" alt="">
<strong>NoxLab Share</strong><span class="version">v{__version__}</span></div></header>
<main><div class="route"><span class="signal" aria-hidden="true"></span> Local network</div>
<h1>{title}</h1>{content}</main>
<footer>Keep the PC app open during the transfer.</footer></body></html>"""


def _error(message):
    return f'<p class="error" role="alert">{html.escape(message)}</p>' if message else ""


PASSWORD = """<label for="password">Session password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>"""


def download_page(item, file_url, protected, error=None):
    filename = html.escape(item.served_name)
    size = format_bytes(item.served_size)
    details = f'<dt>Original folder</dt><dd>{html.escape(item.display_name)} &middot; {format_bytes(item.original_size)}</dd>' if item.is_folder else ""
    button = '<span aria-hidden="true">&darr;</span> Download file'
    action = (f'<form action="/download" method="post">{PASSWORD}<button>{button}</button></form>'
              if protected else f'<a class="primary" href="{html.escape(file_url, quote=True)}" download="{filename}">{button}</a>')
    return page("Download from PC", f"""
<section class="item"><div class="direction" aria-hidden="true">&darr;</div>
<div><small>{html.escape(item.display_type)}</small><h2>{filename}</h2></div></section>
<dl><dt>Download size</dt><dd>{size}</dd>{details}
<dt>Access</dt><dd>{'Password required' if protected else 'Anyone with the LAN link'}</dd></dl>
{_error(error)}{action}""")


def upload_page(limit, protected, error=None):
    size = "No fixed limit" if limit is None else format_bytes(limit)
    return page("Send files to this PC", f"""
<form id="upload-form" data-limit="{limit if limit is not None else ''}" method="post"
 action="/upload" enctype="multipart/form-data">
<section class="selection"><label for="files">Files to send</label>
<input id="files" name="files" type="file" multiple required>
<ul id="selection-list" aria-label="Selected files" hidden></ul></section>
<dl><dt>Upload limit</dt><dd>{size}</dd><dt>Access</dt>
<dd>{'Password required' if protected else 'Anyone with the LAN link'}</dd></dl>
{PASSWORD if protected else ''}{_error(error)}
<progress id="upload-progress" aria-label="File received by PC" value="0" max="100" hidden></progress>
<p id="upload-state" role="status" aria-live="polite"></p>
<button id="upload-button" type="submit">Upload to PC</button>
</form><script src="/upload.js"></script>""", receiving=True)


def success_page(uploaded):
    items = "".join(f"<li>{html.escape(path.name)}<small>{format_bytes(size)}</small></li>" for path, size in uploaded)
    return page("Upload complete", f'<h2>Saved on PC</h2><ul class="receipt">{items}</ul>'
                '<a class="primary" href="/upload">Send more files</a>', receiving=True)


def not_found(receiving=False):
    route = "/upload" if receiving else "/download"
    return page("Page not found", f'<a class="primary" href="{route}">Open transfer page</a>', receiving=receiving)
