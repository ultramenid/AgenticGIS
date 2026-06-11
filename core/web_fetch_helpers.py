"""Pure helpers for web_fetch: binary detection and download naming.

Binary responses (ZIP, GeoTIFF, images, ...) must never be inlined into
the chat history — a single fetched archive can balloon every subsequent
LLM request by hundreds of KB of undecodable garbage. web_fetch uses
``is_binary_content`` to decide between returning text and streaming the
body to a temp file, and ``safe_filename_from_url`` to name that file.

Kept QGIS-free so it is unit-testable everywhere.
"""

import re
from urllib.parse import unquote, urlparse

_TEXT_TYPE_HINTS = (
    "json",
    "xml",
    "html",
    "javascript",
    "ecmascript",
    "csv",
    "yaml",
    "x-www-form-urlencoded",
    "svg",
)

_FILENAME_MAX_LEN = 120
_SAFE_CHAR_RE = re.compile(r"[^A-Za-z0-9._-]+")


def is_binary_content(content_type, head):
    """True when a response should be saved to disk instead of inlined.

    ``content_type`` is the raw Content-Type header value (may be empty);
    ``head`` is the first chunk of the body, used to sniff when the
    header is missing or unrecognized.
    """
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype:
        if ctype.startswith("text/"):
            return False
        if any(hint in ctype for hint in _TEXT_TYPE_HINTS):
            return False
        if ctype.startswith(("image/", "audio/", "video/", "font/")):
            return True
        if ctype == "application/octet-stream":
            return True
        if ctype.startswith("application/") and (
            "zip" in ctype
            or "gzip" in ctype
            or "tar" in ctype
            or "pdf" in ctype
            or "protobuf" in ctype
        ):
            return True
        # Unrecognized type (e.g. application/x-unknown): fall through to sniff.

    if not head:
        return False
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
        return False
    except UnicodeDecodeError:
        # A multi-byte character cut off at the end of the sniff window is
        # still text; undecodable bytes in the middle are not.
        try:
            head[:-3].decode("utf-8")
            return False
        except UnicodeDecodeError:
            return True


def safe_filename_from_url(url, default="download"):
    """Filesystem-safe filename derived from a URL path, capped in length."""
    try:
        path = unquote(urlparse(url).path or "")
    except Exception:  # noqa: BLE001
        path = ""
    name = path.rsplit("/", 1)[-1].strip()
    name = _SAFE_CHAR_RE.sub("_", name).strip("._")
    if not name:
        return default
    if len(name) > _FILENAME_MAX_LEN:
        # Keep the extension when truncating.
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            keep = _FILENAME_MAX_LEN - len(ext) - 1
            name = stem[:keep] + "." + ext
        else:
            name = name[:_FILENAME_MAX_LEN]
    return name or default


def stream_response_to_file(resp, fh, head, *, chunk_size, max_bytes, task=None, total_bytes=None):
    """Stream ``head`` plus the rest of ``resp`` into ``fh``.

    ``task`` is duck-typed (QgsTask-compatible): ``setProgress(pct)`` is
    called as bytes arrive when ``total_bytes`` is known, and
    ``isCanceled()`` is polled between chunks so a user Stop aborts the
    download promptly.

    Returns ``(size, cancelled)``. Raises ``ValueError`` when the body
    exceeds ``max_bytes``.
    """
    def _report(size):
        if task is None:
            return
        if total_bytes:
            pct = min(100, int(size * 100 / total_bytes))
            task.setProgress(pct)

    def _cancelled():
        try:
            return task is not None and task.isCanceled()
        except Exception:  # noqa: BLE001 — a dying task must not kill the download
            return False

    fh.write(head)
    size = len(head)
    _report(size)
    while True:
        if _cancelled():
            return size, True
        chunk = resp.read(chunk_size)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise ValueError(f"download exceeds the {max_bytes // (1024 * 1024)} MB limit")
        fh.write(chunk)
        _report(size)
    return size, False
