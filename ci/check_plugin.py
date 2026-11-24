"""CI validation helpers for AgenticGIS archive packaging, scanners, and i18n."""

from __future__ import annotations

import argparse
import ast
import configparser
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

EXPECTED_ROOT = "AgenticGIS"
PLUGIN_MAX_UPLOAD_SIZE = 25_000_000
PLUGIN_MAX_EXPANDED_SIZE = 100_000_000
QM_MAGIC = b"\x3c\xb8\x64\x18\xca\xef\x9c\x95\xcd\x21\x1c\xbf\x60\xa1\xbd\xdd"
HIDDEN_ALLOWLIST = frozenset({
    ".bandit",
    ".flake8",
    ".gitignore",
    ".gitattributes",
})
SUSPICIOUS_SUFFIXES = (
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bat",
    ".sh",
    ".ps1",
    ".cmd",
    ".com",
    ".msi",
    ".vbs",
)
NESTED_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tar.xz",
    ".7z",
    ".rar",
    ".whl",
    ".egg",
)
PRIVATE_KEY_NAMES = frozenset({
    "id_rsa",
    "id_dsa",
    "id_ed25519",
    "id_ecdsa",
})
PRIVATE_KEY_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx")
FORBIDDEN_PATH_COMPONENTS = frozenset({
    ".git",
    ".github",
    "__pycache__",
    "tests",
    "ci",
    "docs",
    ".pytest_cache",
    "htmlcov",
})
FORBIDDEN_FILENAMES = frozenset({".coverage", ".secrets.baseline"})
FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".pyd", ".db")
REQUIRED_METADATA_KEYS = (
    "name",
    "description",
    "version",
    "qgisMinimumVersion",
    "author",
    "email",
    "about",
    "tracker",
    "repository",
)
FORBIDDEN_PLACEHOLDER_URLS = frozenset({
    "http://bugs",
    "http://repo",
    "http://homepage",
})
ALLOWED_TOP_LEVEL_IMPORTS = frozenset({
    "qgis",
    "osgeo",
    "processing",
    "AgenticGIS",
})
OPTIONAL_GUARDED_IMPORTS = frozenset({
    "PIL",
    "ee",
    "ee_plugin",
})
CALLER_GUARDED_OPTIONAL_IMPORTS = frozenset({
    ("core/toolkit.py", "_ensure_gee_initialized", "ee"),
    ("core/toolkit.py", "_burn_labels_into_gif", "PIL"),
})
POST_PY39_STDLIB_MODULES = frozenset({
    "_android_support",
    "_apple_support",
    "_colorize",
    "_ios_support",
    "_opcode_metadata",
    "_pyrepl",
    "_typing",
    "_wmi",
    "_zstd",
    "annotationlib",
    "compression",
    "tomllib",
})
FORBIDDEN_QT_IMPORTS = frozenset({"PyQt5", "PyQt6", "sip"})
FORBIDDEN_QT_METHODS = frozenset({"exec_", "print_"})
RELEASE_TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)(-(?:alpha|beta|rc)\.?\d+)?$")
FLAKE8_LINE_RE = re.compile(
    r"^(?P<path>.+?):(?P<row>\d+):(?P<col>\d+):(?P<code>[A-Z]\d+):(?P<text>.*)$"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_qgis_version(raw: str) -> tuple[int, ...] | None:
    value = raw.strip()
    if not value or not re.fullmatch(r"\d+(?:\.\d+)*", value):
        return None
    return tuple(int(part) for part in value.split("."))


def _validate_member_path(raw_name: str, is_dir: bool) -> tuple[str | None, str | None]:
    """Return (normalized_relative_path, blocking_error_or_None)."""
    if not raw_name:
        return None, "Empty archive member name"
    if "\x00" in raw_name or any(ord(ch) < 32 for ch in raw_name):
        return None, f"Control or NUL character in archive member path: {raw_name!r}"
    if "\\" in raw_name:
        return None, f"Backslash in archive member path: {raw_name!r}"
    if raw_name.startswith("/") or re.match(r"^[A-Za-z]:", raw_name):
        return None, f"Absolute or drive-qualified path in archive: {raw_name!r}"

    trimmed = raw_name[:-1] if raw_name.endswith("/") else raw_name
    parts = trimmed.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None, f"Unsafe path segment in archive member: {raw_name!r}"
    win_reserved = frozenset({
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    })
    for part in parts:
        if any(ch in '<>:"|?*' for ch in part):
            return None, f"Windows-invalid character in archive member: {raw_name!r}"
        if part.endswith((" ", ".")):
            return None, f"Trailing space or dot in archive member: {raw_name!r}"
        if part.split(".", 1)[0].upper() in win_reserved:
            return None, f"Windows-reserved name in archive member: {raw_name!r}"
    if parts[0] != EXPECTED_ROOT:
        return None, (
            f"Archive member {raw_name!r} is outside required root '{EXPECTED_ROOT}/'"
        )
    if is_dir and not raw_name.endswith("/"):
        return None, f"Directory entry without trailing slash: {raw_name!r}"
    return trimmed, None


def _check_forbidden_member(rel_parts: list[str], raw_name: str) -> str | None:
    """Return a blocking reason if rel_parts under AgenticGIS/ is a forbidden leak."""
    basename = rel_parts[-1]
    lower_name = basename.lower()
    if any(part in FORBIDDEN_PATH_COMPONENTS for part in rel_parts):
        return f"Forbidden repository/development path in archive: {raw_name}"
    if lower_name == ".env" or lower_name.startswith(".env."):
        return f"Forbidden environment file in archive: {raw_name}"
    if lower_name in FORBIDDEN_FILENAMES:
        return f"Forbidden test artifact in archive: {raw_name}"
    if lower_name.endswith(FORBIDDEN_SUFFIXES):
        return f"Forbidden compiled/database artifact in archive: {raw_name}"
    if (
        lower_name in PRIVATE_KEY_NAMES
        or lower_name.startswith("id_rsa.")
        or lower_name.endswith(PRIVATE_KEY_SUFFIXES)
    ):
        return f"Forbidden key/certificate file in archive: {raw_name}"
    if lower_name.endswith(NESTED_ARCHIVE_SUFFIXES):
        return f"Forbidden nested archive in package: {raw_name}"
    return None


def _check_unix_mode(info: zipfile.ZipInfo) -> tuple[str | None, list[dict[str, str]]]:
    """Inspect ZipInfo external_attr Unix mode; return (blocking_error, findings)."""
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if unix_mode == 0:
        return None, []

    file_type = stat.S_IFMT(unix_mode)
    if stat.S_ISLNK(unix_mode):
        return f"Symbolic link not permitted in archive: {info.filename}", []
    if file_type in (stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFBLK, stat.S_IFCHR):
        return f"Special device/pipe/socket not permitted in archive: {info.filename}", []

    is_dir = info.is_dir()
    if is_dir and file_type not in (0, stat.S_IFDIR):
        return f"Non-directory mode on directory entry: {info.filename}", []
    if not is_dir and file_type not in (0, stat.S_IFREG):
        return f"Non-regular file mode in archive: {info.filename}", []

    findings: list[dict[str, str]] = []
    perm_bits = unix_mode & 0o7777
    if not is_dir and (perm_bits & 0o111):
        findings.append({
            "category": "FILE_EXECUTABLE",
            "path": info.filename,
            "message": f"Executable permission bits ({oct(perm_bits)}) on packaged file",
        })
    if perm_bits & 0o7002:
        findings.append({
            "category": "FILE_UNUSUAL_MODE",
            "path": info.filename,
            "message": f"Unusual Unix mode bits ({oct(perm_bits)}) on packaged entry",
        })
    return None, findings


def _is_valid_http_url(raw_url: str) -> bool:
    url = raw_url.strip()
    if url.rstrip("/").lower() in FORBIDDEN_PLACEHOLDER_URLS:
        return False
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and bool(hostname)


def _validate_metadata(
    raw_bytes: bytes,
    file_members: set[str] | None,
) -> tuple[list[str], list[dict[str, str]]]:
    errors: list[str] = []
    warnings: list[dict[str, str]] = []
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"metadata.txt is not valid UTF-8: {exc}"], warnings

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text, source="metadata.txt")
    except configparser.Error as exc:
        lineno = getattr(exc, "lineno", None)
        loc = f" near line {lineno}" if lineno else ""
        return [f"Malformed metadata.txt syntax ({type(exc).__name__}{loc})"], warnings

    if not parser.has_section("general"):
        return ["metadata.txt is missing required [general] section"], warnings

    general = parser["general"]
    for key in REQUIRED_METADATA_KEYS:
        if not general.get(key, "").strip():
            errors.append(f"metadata.txt [general] is missing required non-empty '{key}'")

    author = general.get("author", "")
    if "/" in author:
        errors.append("metadata.txt [general] 'author' cannot contain '/'")

    for url_key in ("tracker", "repository"):
        val = general.get(url_key, "").strip()
        if val and not _is_valid_http_url(val):
            errors.append(f"metadata.txt [general] '{url_key}' must be a valid non-placeholder http(s) URL")

    homepage = general.get("homepage", "").strip()
    if homepage and not _is_valid_http_url(homepage):
        errors.append("metadata.txt [general] 'homepage' must be a valid non-placeholder http(s) URL")

    if "supportsqt6" in general:
        warnings.append({
            "category": "METADATA_DEPRECATED",
            "path": f"{EXPECTED_ROOT}/metadata.txt",
            "message": "Deprecated metadata key 'supportsQt6' present",
        })

    min_raw = general.get("qgisMinimumVersion", "").strip()
    min_ver = _parse_qgis_version(min_raw) if min_raw else None
    if min_raw and min_ver is None:
        errors.append("Invalid qgisMinimumVersion in metadata.txt")

    max_raw = general.get("qgisMaximumVersion", "").strip()
    max_ver = _parse_qgis_version(max_raw) if max_raw else None
    if max_raw and max_ver is None:
        errors.append("Invalid qgisMaximumVersion in metadata.txt")

    if min_ver is not None and max_ver is not None and min_ver > max_ver:
        errors.append(
            "metadata.txt qgisMinimumVersion exceeds qgisMaximumVersion"
        )

    icon_rel = general.get("icon", "").strip()
    if icon_rel:
        if "\\" in icon_rel or icon_rel.startswith("/") or ".." in icon_rel.split("/"):
            errors.append("Invalid metadata.txt icon path")
        elif file_members is not None:
            icon_rel_norm = icon_rel[2:] if icon_rel.startswith("./") else icon_rel
            expected_icon = f"{EXPECTED_ROOT}/{icon_rel_norm}"
            if expected_icon not in file_members:
                errors.append("metadata.txt icon target not found in archive")

    return errors, warnings


def _validate_init_entrypoint(raw_bytes: bytes) -> list[str]:
    try:
        source = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"__init__.py is not valid UTF-8: {exc}"]
    try:
        tree = ast.parse(source, filename="__init__.py")
    except SyntaxError as exc:
        return [f"__init__.py syntax error: {exc}"]
    has_factory = any(
        isinstance(node, ast.FunctionDef) and node.name == "classFactory"
        for node in tree.body
    )
    if not has_factory:
        return ["__init__.py does not define top-level classFactory(iface)"]
    return []


def validate_and_extract_archive(zip_path: Path, extract_to: Path) -> dict:
    """Validate a plugin ZIP archive before extraction and extract safely if valid."""
    report: dict = {
        "zip_path": str(zip_path),
        "zip_sha256": None,
        "extract_to": str(extract_to),
        "files_checked": 0,
        "blocking_errors": [],
        "file_findings": [],
        "extracted_files": 0,
        "ok": False,
    }

    if not zip_path.is_file():
        report["blocking_errors"].append(f"Archive file does not exist: {zip_path}")
        return report

    if extract_to.exists():
        if not extract_to.is_dir():
            report["blocking_errors"].append(
                f"Extraction target exists and is not a directory: {extract_to}"
            )
            return report
        if any(extract_to.iterdir()):
            report["blocking_errors"].append(
                f"Extraction target directory must be empty: {extract_to}"
            )
            return report

    report["zip_sha256"] = _sha256_file(zip_path)
    if zip_path.stat().st_size > PLUGIN_MAX_UPLOAD_SIZE:
        report["blocking_errors"].append(
            f"Archive size ({zip_path.stat().st_size} bytes) exceeds limit ({PLUGIN_MAX_UPLOAD_SIZE} bytes)"
        )
        return report

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile as exc:
        report["blocking_errors"].append(f"Invalid ZIP archive: {exc}")
        return report

    with zf:
        infos = zf.infolist()
        if not infos:
            report["blocking_errors"].append("Archive is empty")
            return report

        total_uncompressed = 0
        for info in infos:
            if info.file_size < 0 or info.file_size > PLUGIN_MAX_EXPANDED_SIZE:
                report["blocking_errors"].append(
                    f"Archive member exceeds expanded size limit ({PLUGIN_MAX_EXPANDED_SIZE} bytes): {info.filename}"
                )
                return report
            total_uncompressed += info.file_size
            if total_uncompressed > PLUGIN_MAX_EXPANDED_SIZE:
                report["blocking_errors"].append(
                    f"Archive expanded size exceeds limit ({PLUGIN_MAX_EXPANDED_SIZE} bytes)"
                )
                return report

        try:
            bad_member = zf.testzip()
        except (RuntimeError, zipfile.BadZipFile, NotImplementedError, OSError, EOFError, zlib.error) as exc:
            report["blocking_errors"].append(
                f"Failed to verify ZIP member integrity: {exc}"
            )
            return report
        if bad_member is not None:
            report["blocking_errors"].append(
                f"Corrupt ZIP member (CRC check failed): {bad_member}"
            )
            return report

        seen_raw: set[str] = set()
        seen_normalized: dict[str, str] = {}
        implied_dirs_ci: dict[str, str] = {}
        file_infos: list[tuple[zipfile.ZipInfo, str]] = []
        file_members: set[str] = set()

        for info in infos:
            raw_orig = getattr(info, "orig_filename", info.filename)
            if raw_orig != info.filename:
                report["blocking_errors"].append(
                    f"Archive member path changed during ZIP header normalization: {raw_orig!r}"
                )
                continue
            is_dir = info.is_dir()
            norm_path, path_err = _validate_member_path(info.filename, is_dir)
            if path_err:
                report["blocking_errors"].append(path_err)
                continue
            assert norm_path is not None

            if info.filename in seen_raw:
                report["blocking_errors"].append(f"Duplicate ZIP entry: {info.filename}")
                continue
            seen_raw.add(info.filename)

            ci_key = norm_path.lower()
            if ci_key in seen_normalized:
                report["blocking_errors"].append(
                    f"Duplicate normalized member path: {norm_path} collides with {seen_normalized[ci_key]}"
                )
                continue
            seen_normalized[ci_key] = norm_path

            mode_err, mode_findings = _check_unix_mode(info)
            if mode_err:
                report["blocking_errors"].append(mode_err)
                continue
            report["file_findings"].extend(mode_findings)

            rel_parts = norm_path.split("/")[1:]
            if rel_parts:
                leak_err = _check_forbidden_member(rel_parts, info.filename)
                if leak_err:
                    report["blocking_errors"].append(leak_err)
                    continue

            parts = norm_path.split("/")
            parent_depth = len(parts) + 1 if is_dir else len(parts)
            dir_collision = False
            for idx in range(1, parent_depth):
                dir_prefix = "/".join(parts[:idx])
                dir_key = dir_prefix.lower()
                existing_dir = implied_dirs_ci.get(dir_key)
                if existing_dir is not None and existing_dir != dir_prefix:
                    report["blocking_errors"].append(
                        f"Case-colliding directory prefix: {dir_prefix} collides with {existing_dir}"
                    )
                    dir_collision = True
                    break
                implied_dirs_ci[dir_key] = dir_prefix
            if dir_collision:
                continue

            if is_dir:
                continue

            report["files_checked"] += 1
            file_infos.append((info, norm_path))
            file_members.add(norm_path)

            basename = rel_parts[-1] if rel_parts else ""
            lower_name = basename.lower()
            if basename.startswith(".") and basename not in HIDDEN_ALLOWLIST:
                report["file_findings"].append({
                    "category": "FILE_HIDDEN",
                    "path": info.filename,
                    "message": "Hidden file detected",
                })
            if lower_name.endswith(SUSPICIOUS_SUFFIXES):
                report["file_findings"].append({
                    "category": "FILE_SUSPICIOUS",
                    "path": info.filename,
                    "message": f"Executable or binary file suffix detected ({Path(lower_name).suffix})",
                })

        metadata_member = f"{EXPECTED_ROOT}/metadata.txt"
        init_member = f"{EXPECTED_ROOT}/__init__.py"
        license_member = f"{EXPECTED_ROOT}/LICENSE"
        if metadata_member not in file_members:
            report["blocking_errors"].append(f"Missing required plugin manifest: {metadata_member}")
        if init_member not in file_members:
            report["blocking_errors"].append(f"Missing required plugin entry point: {init_member}")
        if license_member not in file_members:
            report["blocking_errors"].append(f"Missing required license file: {license_member}")

        # Verify metadata, entry point, and translation catalogs from archive bytes before writing.
        if metadata_member in file_members:
            meta_errors, meta_warnings = _validate_metadata(zf.read(metadata_member), file_members)
            report["blocking_errors"].extend(meta_errors)
            report["file_findings"].extend(meta_warnings)
        if init_member in file_members:
            report["blocking_errors"].extend(
                _validate_init_entrypoint(zf.read(init_member))
            )

        ts_members = sorted(
            m for m in file_members if m.startswith(f"{EXPECTED_ROOT}/i18n/") and m.endswith(".ts")
        )
        for ts_member in ts_members:
            qm_member = ts_member[:-3] + ".qm"
            if qm_member not in file_members:
                report["blocking_errors"].append(
                    f"Missing compiled catalog {qm_member} required by {ts_member}"
                )

        qm_members = sorted(m for m in file_members if m.endswith(".qm"))
        for qm_member in qm_members:
            header = zf.read(qm_member)[:len(QM_MAGIC)]
            if header != QM_MAGIC:
                report["blocking_errors"].append(
                    f"Invalid Qt QM catalog header in {qm_member}"
                )

        for _, norm_path in file_infos:
            if norm_path.lower() in implied_dirs_ci:
                report["blocking_errors"].append(
                    f"Archive path type collision (file vs directory): {norm_path}"
                )

        if report["blocking_errors"]:
            return report

        extract_to.mkdir(parents=True, exist_ok=True)
        extract_root = extract_to.resolve()
        extracted_bytes = 0
        for info, norm_path in file_infos:
            target = (extract_root / norm_path).resolve()
            if not target.is_relative_to(extract_root):
                report["blocking_errors"].append(
                    f"Extracted path escaped destination root: {norm_path}"
                )
                return report
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as dst:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    extracted_bytes += len(chunk)
                    if extracted_bytes > PLUGIN_MAX_EXPANDED_SIZE:
                        report["blocking_errors"].append(
                            f"Archive expanded size exceeds limit ({PLUGIN_MAX_EXPANDED_SIZE} bytes)"
                        )
                        return report
                    dst.write(chunk)
            report["extracted_files"] += 1

    report["ok"] = True
    return report


def _collect_python_files(target_dir: Path) -> list[Path]:
    return sorted(
        path for path in target_dir.rglob("*.py")
        if path.is_file() and "__pycache__" not in path.parts
    )


def _rel_display(raw_path: str, base_dir: Path) -> str:
    candidate = Path(raw_path)
    try:
        return candidate.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        prefix = str(base_dir).rstrip("/") + "/"
        if raw_path.startswith(prefix):
            return raw_path[len(prefix):]
        return raw_path


def _resolve_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    sibling = Path(sys.executable).parent / name
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return name


def _run_bandit(target_dir: Path) -> dict:
    result = {
        "name": "bandit",
        "blocking": True,
        "ok": False,
        "files_checked": 0,
        "blocking_findings": [],
        "informational_findings": [],
        "errors": [],
    }
    py_files = _collect_python_files(target_dir)
    result["files_checked"] = len(py_files)
    if not py_files:
        result["errors"].append("No Python files found to scan with Bandit")
        return result

    cmd = [_resolve_tool("bandit"), "-r", str(target_dir), "-f", "json", "--quiet"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        result["errors"].append("Bandit executable not found on PATH")
        return result
    except subprocess.TimeoutExpired:
        result["errors"].append("Bandit timed out after 120s")
        return result

    if proc.returncode not in (0, 1):
        stderr = (proc.stderr or "").strip()[:300]
        result["errors"].append(
            f"Bandit exited with unexpected code {proc.returncode}: {stderr or 'no stderr'}"
        )
        return result

    if not proc.stdout or not proc.stdout.strip():
        result["errors"].append(
            f"Bandit produced empty output (exit {proc.returncode})"
        )
        return result

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        result["errors"].append(f"Invalid Bandit JSON report: {exc}")
        return result

    if not isinstance(payload, dict):
        result["errors"].append("Bandit JSON root must be an object")
        return result

    raw_errors = payload.get("errors")
    raw_results = payload.get("results")
    if not isinstance(raw_errors, list) or not isinstance(raw_results, list):
        result["errors"].append("Bandit JSON missing 'results' or 'errors' lists")
        return result

    if raw_errors:
        result["errors"].append(f"Bandit reported {len(raw_errors)} scan error(s)")
        return result

    if proc.returncode == 1 and not raw_results:
        result["errors"].append("Bandit exited 1 with empty results list")
        return result

    for item in raw_results:
        if not isinstance(item, dict):
            result["errors"].append("Malformed Bandit finding entry")
            return result
        severity = str(item.get("issue_severity", "")).upper()
        test_id = str(item.get("test_id", ""))
        raw_message = str(item.get("issue_text", ""))
        if test_id in {"B105", "B106", "B107"} or "hardcoded password" in raw_message.lower():
            safe_message = f"Possible hardcoded credential/password ({test_id}; value redacted)"
        else:
            safe_message = raw_message
        entry = {
            "file": _rel_display(str(item.get("filename", "")), target_dir),
            "line": int(item.get("line_number", 0) or 0),
            "test_id": test_id,
            "severity": severity,
            "confidence": str(item.get("issue_confidence", "")).upper(),
            "message": safe_message,
        }
        if severity in ("MEDIUM", "HIGH"):
            result["blocking_findings"].append(entry)
        else:
            result["informational_findings"].append(entry)

    result["ok"] = not result["blocking_findings"] and not result["errors"]
    return result


def _normalize_secret_path(raw_path: str) -> str:
    norm = raw_path
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def _load_secrets_baseline(baseline_path: Path) -> tuple[set[tuple[str, str, str]], str | None]:
    """Parse a reviewed .secrets.baseline read-only into (file, type, hashed_secret) keys."""
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return set(), f"Invalid .secrets.baseline: {exc}"

    if not isinstance(payload, dict) or not isinstance(payload.get("results"), dict):
        return set(), ".secrets.baseline missing 'results' mapping"

    entries: set[tuple[str, str, str]] = set()
    for raw_file, hits in payload["results"].items():
        if not isinstance(raw_file, str) or not isinstance(hits, list):
            return set(), "Malformed file entry in .secrets.baseline"
        norm_file = _normalize_secret_path(raw_file)
        for hit in hits:
            if not isinstance(hit, dict):
                return set(), "Malformed hit entry in .secrets.baseline"
            hit_type = hit.get("type")
            hashed = hit.get("hashed_secret")
            if not isinstance(hit_type, str) or not hit_type or not isinstance(hashed, str) or not hashed:
                return set(), "Malformed secret record in .secrets.baseline"
            if hit.get("is_secret") is not False:
                continue
            entries.add((norm_file, hit_type, hashed))
    return entries, None


def _run_secrets(
    target_dir: Path,
    exclude_metadata: bool = True,
    baseline_path: Path | None = None,
) -> dict:
    result = {
        "name": "secrets",
        "blocking": True,
        "ok": False,
        "files_checked": 0,
        "baselined_count": 0,
        "blocking_findings": [],
        "errors": [],
    }

    all_files = [
        p for p in target_dir.rglob("*")
        if p.is_file()
    ]
    result["files_checked"] = len(all_files)

    baseline_entries: set[tuple[str, str, str]] = set()
    if baseline_path is not None:
        resolved_target = target_dir.resolve()
        resolved_baseline = baseline_path.resolve()
        if resolved_baseline.is_relative_to(resolved_target):
            result["errors"].append(
                "Secrets baseline must live outside the scanned plugin package"
            )
            return result
        if resolved_baseline.is_file():
            baseline_entries, baseline_err = _load_secrets_baseline(resolved_baseline)
            if baseline_err:
                result["errors"].append(baseline_err)
                return result

    # Never pass --baseline to detect-secrets 1.5.0: it rewrites the file and emits no JSON stdout.
    cmd = [
        _resolve_tool("detect-secrets"),
        "scan",
        "--no-verify",
        "--all-files",
    ]
    if exclude_metadata:
        cmd.extend(["--exclude-files", r"^(\./)?metadata\.txt$"])
    cmd.append(".")

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(target_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        result["errors"].append("detect-secrets executable not found on PATH")
        return result
    except subprocess.TimeoutExpired:
        result["errors"].append("detect-secrets timed out after 120s")
        return result

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:300]
        result["errors"].append(
            f"detect-secrets exited with code {proc.returncode}: {stderr or 'no stderr'}"
        )
        return result

    if not proc.stdout or not proc.stdout.strip():
        result["errors"].append("detect-secrets produced empty output")
        return result

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        result["errors"].append(f"Invalid detect-secrets JSON report: {exc}")
        return result

    if not isinstance(payload, dict) or not isinstance(payload.get("results"), dict):
        result["errors"].append("detect-secrets JSON missing 'results' mapping")
        return result

    # Never copy hashed_secret or matched secret fragments into CI logs or reports.
    for raw_file, hits in sorted(payload["results"].items()):
        if not isinstance(hits, list):
            result["errors"].append("Malformed detect-secrets file entry")
            return result
        norm_file = _normalize_secret_path(raw_file)
        for hit in hits:
            if not isinstance(hit, dict):
                result["errors"].append("Malformed detect-secrets hit entry")
                return result
            detector = str(hit.get("type", "Unknown"))
            hashed = hit.get("hashed_secret")
            if isinstance(hashed, str) and hashed and (norm_file, detector, hashed) in baseline_entries:
                result["baselined_count"] += 1
                continue
            result["blocking_findings"].append({
                "file": norm_file,
                "line": int(hit.get("line_number", 0) or 0),
                "detector": detector,
            })

    result["ok"] = not result["blocking_findings"] and not result["errors"]
    return result


def _run_compile(target_dir: Path) -> dict:
    result = {
        "name": "compile",
        "blocking": True,
        "ok": False,
        "files_checked": 0,
        "blocking_findings": [],
        "errors": [],
    }
    py_files = _collect_python_files(target_dir)
    result["files_checked"] = len(py_files)
    if not py_files:
        result["errors"].append("No Python files found to compile")
        return result

    for py_file in py_files:
        rel = _rel_display(str(py_file), target_dir)
        try:
            source = py_file.read_text(encoding="utf-8")
            compile(source, rel, "exec")
            ast.parse(source, filename=rel, feature_version=(3, 9))
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            result["blocking_findings"].append({
                "file": rel,
                "line": getattr(exc, "lineno", 0) or 0,
                "message": str(exc),
            })

    result["ok"] = not result["blocking_findings"] and not result["errors"]
    return result


def _is_import_guard_try(node: ast.Try) -> bool:
    for handler in node.handlers:
        if handler.type is None:
            return True
        types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        for typ in types:
            if isinstance(typ, ast.Name) and typ.id in ("ImportError", "ModuleNotFoundError", "Exception"):
                return True
    return False


def _is_import_error_handler(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return False
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(
        isinstance(typ, ast.Name) and typ.id in ("ImportError", "ModuleNotFoundError")
        for typ in types
    )


def _walk_static_ast(
    node: ast.AST,
    py_file: Path,
    rel_path: str,
    stdlib: set[str] | frozenset[str],
    findings: list[dict],
    in_guard: bool,
    in_import_fallback: bool,
    current_func: str | None = None,
) -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for field, value in ast.iter_fields(node):
            if field == "body":
                continue
            children = value if isinstance(value, list) else [value]
            for child in children:
                if isinstance(child, ast.AST):
                    _walk_static_ast(
                        child, py_file, rel_path, stdlib, findings,
                        in_guard, in_import_fallback, current_func,
                    )
        for child in node.body:
            _walk_static_ast(
                child, py_file, rel_path, stdlib, findings,
                False, False, node.name,
            )
        return
    if isinstance(node, ast.Try):
        guard_try = _is_import_guard_try(node)
        for child in node.body:
            _walk_static_ast(
                child, py_file, rel_path, stdlib, findings,
                in_guard or guard_try, in_import_fallback, current_func,
            )
        for handler in node.handlers:
            handler_fallback = _is_import_error_handler(handler)
            for child in handler.body:
                _walk_static_ast(
                    child, py_file, rel_path, stdlib, findings,
                    in_guard, in_import_fallback or handler_fallback, current_func,
                )
        for child in node.orelse + node.finalbody:
            _walk_static_ast(
                child, py_file, rel_path, stdlib, findings,
                in_guard, in_import_fallback, current_func,
            )
        return

    if isinstance(node, ast.Import):
        for alias in node.names:
            top = alias.name.split(".")[0]
            _check_import_name(
                top, alias.name, node.lineno, py_file, rel_path,
                stdlib, findings, in_guard, in_import_fallback, current_func,
            )
    elif isinstance(node, ast.ImportFrom):
        if node.level == 0 and node.module:
            top = node.module.split(".")[0]
            _check_import_name(
                top, node.module, node.lineno, py_file, rel_path,
                stdlib, findings, in_guard, in_import_fallback, current_func,
            )
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in FORBIDDEN_QT_METHODS:
            findings.append({
                "file": rel_path,
                "line": node.lineno,
                "rule": "QT_COMPAT",
                "message": f"Forbidden Qt5-only method '.{node.func.attr}()'",
            })

    for child in ast.iter_child_nodes(node):
        _walk_static_ast(
            child, py_file, rel_path, stdlib, findings,
            in_guard, in_import_fallback, current_func,
        )


def _check_import_name(
    top: str,
    full_name: str,
    lineno: int,
    py_file: Path,
    rel_path: str,
    stdlib: set[str] | frozenset[str],
    findings: list[dict],
    in_guard: bool,
    in_import_fallback: bool,
    current_func: str | None = None,
) -> None:
    if top in FORBIDDEN_QT_IMPORTS:
        findings.append({
            "file": rel_path,
            "line": lineno,
            "rule": "QT_COMPAT",
            "message": f"Direct Qt binding import '{full_name}' forbidden; use 'qgis.PyQt'",
        })
        return
    if top in stdlib or top in ALLOWED_TOP_LEVEL_IMPORTS:
        return
    if top in OPTIONAL_GUARDED_IMPORTS:
        caller_guarded = (rel_path, current_func, top) in CALLER_GUARDED_OPTIONAL_IMPORTS
        if not in_guard and not caller_guarded:
            findings.append({
                "file": rel_path,
                "line": lineno,
                "rule": "UNGUARDED_OPTIONAL_IMPORT",
                "message": f"Optional runtime import '{full_name}' must be guarded against ImportError",
            })
        return
    if in_import_fallback and (py_file.parent / f"{top}.py").is_file():
        return
    findings.append({
        "file": rel_path,
        "line": lineno,
        "rule": "UNEXPECTED_DEPENDENCY",
        "message": f"Unexpected third-party import '{full_name}' (plugin must stay zero-dependency)",
    })


def _run_static(target_dir: Path) -> dict:
    result = {
        "name": "static",
        "blocking": True,
        "ok": False,
        "files_checked": 0,
        "blocking_findings": [],
        "errors": [],
    }
    py_files = _collect_python_files(target_dir)
    result["files_checked"] = len(py_files)
    if not py_files:
        result["errors"].append("No Python files found for static AST checks")
        return result

    stdlib = (sys.stdlib_module_names - POST_PY39_STDLIB_MODULES) | {"__future__"}
    for py_file in py_files:
        rel = _rel_display(str(py_file), target_dir)
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            result["blocking_findings"].append({
                "file": rel,
                "line": getattr(exc, "lineno", 0) or 0,
                "rule": "SYNTAX",
                "message": str(exc),
            })
            continue
        _walk_static_ast(tree, py_file, rel, stdlib, result["blocking_findings"], False, False)

    result["ok"] = not result["blocking_findings"] and not result["errors"]
    return result


def _run_flake8(target_dir: Path, flake8_config: Path | None) -> dict:
    result = {
        "name": "flake8",
        "blocking": False,
        "ok": False,
        "files_checked": 0,
        "advisory_findings": [],
        "counts_by_code": {},
        "errors": [],
    }
    py_files = _collect_python_files(target_dir)
    result["files_checked"] = len(py_files)
    if not py_files:
        result["errors"].append("No Python files found to lint with Flake8")
        return result

    cmd = [_resolve_tool("flake8"), "--max-line-length=120"]
    cfg = flake8_config
    if cfg is None and (target_dir / ".flake8").is_file():
        cfg = target_dir / ".flake8"
    if cfg is not None:
        if not cfg.is_file():
            result["errors"].append(f"Flake8 config file not found: {cfg}")
            return result
        cmd.extend(["--config", str(cfg)])
    cmd.append("--format=%(path)s:%(row)d:%(col)d:%(code)s:%(text)s")
    cmd.extend(str(p) for p in py_files)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        result["errors"].append("Flake8 executable not found on PATH")
        return result
    except subprocess.TimeoutExpired:
        result["errors"].append("Flake8 timed out after 120s")
        return result

    if proc.returncode not in (0, 1):
        stderr = (proc.stderr or "").strip()[:300]
        result["errors"].append(
            f"Flake8 exited with unexpected code {proc.returncode}: {stderr or 'no stderr'}"
        )
        return result

    counts: Counter[str] = Counter()
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = FLAKE8_LINE_RE.match(line)
        if not match:
            continue
        code = match.group("code")
        counts[code] += 1
        result["advisory_findings"].append({
            "file": _rel_display(match.group("path"), target_dir),
            "line": int(match.group("row")),
            "col": int(match.group("col")),
            "code": code,
            "message": match.group("text").strip(),
        })

    if proc.returncode == 1 and not result["advisory_findings"]:
        stderr = (proc.stderr or "").strip()[:300]
        result["errors"].append(
            f"Flake8 exited 1 without parseable findings: {stderr or 'no stderr'}"
        )
        return result

    result["counts_by_code"] = dict(sorted(counts.items()))
    result["ok"] = True
    return result


def run_scans(
    target_dir: Path,
    check_mode: str,
    flake8_config: Path | None,
    include_metadata_in_secrets: bool = False,
    baseline_path: Path | None = None,
) -> dict:
    """Run selected repository-aligned scanners against an extracted plugin directory."""
    if not target_dir.is_dir():
        return {
            "target": str(target_dir),
            "checks": [],
            "ok": False,
            "error": f"Target directory does not exist: {target_dir}",
        }

    selected = {
        "all": ["bandit", "secrets", "compile", "static", "flake8"],
        "bandit": ["bandit"],
        "secrets": ["secrets"],
        "compile": ["compile"],
        "static": ["static"],
        "flake8": ["flake8"],
        "quality": ["compile", "static", "flake8"],
    }[check_mode]

    checks: list[dict] = []
    for name in selected:
        if name == "bandit":
            checks.append(_run_bandit(target_dir))
        elif name == "secrets":
            checks.append(
                _run_secrets(
                    target_dir,
                    exclude_metadata=not include_metadata_in_secrets,
                    baseline_path=baseline_path,
                )
            )
        elif name == "compile":
            checks.append(_run_compile(target_dir))
        elif name == "static":
            checks.append(_run_static(target_dir))
        elif name == "flake8":
            checks.append(_run_flake8(target_dir, flake8_config))

    ok = all(
        not check["errors"] and (check["ok"] if check["blocking"] else True)
        for check in checks
    )
    return {
        "target": str(target_dir),
        "checks": checks,
        "ok": ok,
    }


def _parse_pro_file(pro_path: Path) -> tuple[list[str], list[str]]:
    sources: list[str] = []
    translations: list[str] = []
    current_key: str | None = None
    for raw_line in pro_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cont = line.endswith("\\")
        chunk = line[:-1].strip() if cont else line
        if "=" in chunk and current_key is None:
            key, val = chunk.split("=", 1)
            key = key.strip()
            current_key = key if cont else None
            tokens = val.strip().split()
        else:
            key = current_key or ""
            tokens = chunk.split()
            if not cont:
                current_key = None
        if key == "SOURCES":
            sources.extend(tokens)
        elif key == "TRANSLATIONS":
            translations.extend(tokens)
    return sources, translations


def _ts_entries(ts_path: Path) -> set[tuple[str, str, str, bool]]:
    tree = ET.parse(ts_path)
    entries: set[tuple[str, str, str, bool]] = set()
    for ctx in tree.getroot().findall("context"):
        ctx_name = ctx.findtext("name") or ""
        for msg in ctx.findall("message"):
            tr = msg.find("translation")
            if tr is not None and tr.get("type") in ("vanished", "obsolete"):
                continue
            source = msg.findtext("source") or ""
            comment = msg.findtext("comment") or ""
            numerus = msg.get("numerus") == "yes"
            entries.add((ctx_name, source, comment, numerus))
    return entries


def _has_literal_translation_call(py_path: Path) -> bool:
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_translate = (
            (
                isinstance(func, ast.Attribute)
                and func.attr == "translate"
                and isinstance(func.value, ast.Name)
                and func.value.id in ("QCoreApplication", "QApplication")
            )
            or (isinstance(func, ast.Name) and func.id in ("translate", "QT_TRANSLATE_NOOP"))
        )
        if is_translate and len(node.args) >= 2 and all(
            isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            for arg in node.args[:2]
        ):
            return True
        is_self_tr = (
            isinstance(func, ast.Attribute)
            and func.attr == "tr"
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
        )
        if is_self_tr and node.args and (
            isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
        ):
            return True
    return False


def _find_unlisted_translation_sources(repo_root: Path, listed_sources: set[Path]) -> list[str]:
    excluded_dirs = {"__pycache__", "tests", "ci", "docs", "venv", "build", "dist"}
    unlisted: list[str] = []
    for py_path in sorted(repo_root.rglob("*.py")):
        rel_parts = py_path.relative_to(repo_root).parts
        if any(part.startswith(".") or part in excluded_dirs for part in rel_parts):
            continue
        resolved = py_path.resolve()
        if resolved in listed_sources:
            continue
        if _has_literal_translation_call(py_path):
            unlisted.append(py_path.relative_to(repo_root).as_posix())
    return unlisted


def _resolve_lrelease(qt_major: int) -> tuple[str | None, str | None]:
    candidates: list[str] = []
    for raw in (
        f"/usr/lib/qt{qt_major}/bin/lrelease",
        f"/usr/lib/x86_64-linux-gnu/qt{qt_major}/bin/lrelease",
        f"/usr/lib/aarch64-linux-gnu/qt{qt_major}/bin/lrelease",
    ):
        p = Path(raw)
        if p.is_file() and os.access(p, os.X_OK):
            candidates.append(str(p))
    for tool_name in (f"lrelease-qt{qt_major}", f"lrelease{qt_major}", "lrelease"):
        found = shutil.which(tool_name)
        if found and found not in candidates:
            candidates.append(found)

    version_re = re.compile(rf"\bversion\s+{qt_major}\.", re.IGNORECASE)
    for candidate in candidates:
        try:
            ver_proc = subprocess.run(
                [candidate, "-version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        output = (ver_proc.stdout or "") + "\n" + (ver_proc.stderr or "")
        if ver_proc.returncode == 0 and version_re.search(output):
            return candidate, None

    if candidates:
        return None, f"No Qt {qt_major} lrelease executable matched version {qt_major}.x"
    return None, f"Qt {qt_major} lrelease tool not found"


def check_i18n(repo_root: Path, qt_major: int) -> dict:
    """Verify Qt Linguist extraction parity and compiled QM loading."""
    report: dict = {
        "repo_root": str(repo_root),
        "qt_major": qt_major,
        "extracted_count": 0,
        "tracked_count": 0,
        "missing_in_tracked": [],
        "extra_in_tracked": [],
        "qm_loaded": False,
        "catalogs": [],
        "errors": [],
        "ok": False,
    }
    i18n_dir = repo_root / "i18n"
    pro_path = i18n_dir / "AgenticGIS.pro"
    if not pro_path.is_file():
        report["errors"].append(f"Missing project file: {pro_path}")
        return report

    rel_sources, rel_translations = _parse_pro_file(pro_path)
    if not rel_sources or not rel_translations:
        report["errors"].append("AgenticGIS.pro is missing SOURCES or TRANSLATIONS")
        return report

    abs_sources = [(i18n_dir / rel).resolve() for rel in rel_sources]
    missing_sources = [str(p) for p in abs_sources if not p.is_file()]
    if missing_sources:
        report["errors"].append(f"Missing source files from .pro: {missing_sources}")
        return report

    try:
        unlisted_sources = _find_unlisted_translation_sources(repo_root, set(abs_sources))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        report["errors"].append(f"Failed to scan translation sources: {exc}")
        return report
    if unlisted_sources:
        report["errors"].append(
            "Packaged modules with Qt translation calls missing from i18n/AgenticGIS.pro SOURCES: "
            + ", ".join(unlisted_sources)
        )
        return report

    catalog_paths: list[tuple[str, Path, Path]] = []
    for rel_ts in rel_translations:
        tracked_ts = (i18n_dir / rel_ts).resolve()
        tracked_qm = tracked_ts.with_suffix(".qm")
        catalog_paths.append((rel_ts, tracked_ts, tracked_qm))
        if not tracked_ts.is_file() or not tracked_qm.is_file():
            report["errors"].append(f"Missing tracked TS/QM catalog: {tracked_ts} / {tracked_qm}")
    declared_catalog_files = {p for _, ts, qm in catalog_paths for p in (ts, qm)}
    for pattern in ("*.ts", "*.qm"):
        for found_path in sorted(i18n_dir.rglob(pattern)):
            rel_parts = found_path.relative_to(i18n_dir).parts
            if any(part.startswith(".") or part == "__pycache__" for part in rel_parts):
                continue
            if found_path.resolve() not in declared_catalog_files:
                report["errors"].append(
                    "Unlisted or unpaired translation catalog in i18n/: "
                    + found_path.relative_to(i18n_dir).as_posix()
                )
    if report["errors"]:
        return report

    lrelease_bin, lrelease_err = _resolve_lrelease(qt_major)
    if lrelease_err or lrelease_bin is None:
        report["errors"].append(lrelease_err or f"Qt {qt_major} lrelease tool not found")
        return report

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        tmp_ts = tmp_dir / "extracted.ts"
        if qt_major == 5:
            tmp_pro = tmp_dir / "extract.pro"
            joined_sources = " \\\n    ".join(str(p) for p in abs_sources)
            tmp_pro.write_text(
                f"SOURCES = {joined_sources}\n\nTRANSLATIONS = {tmp_ts}\n",
                encoding="utf-8",
            )
            cmd = ["pylupdate5", str(tmp_pro)]
        else:
            cmd = ["pylupdate6", *(str(p) for p in abs_sources), "-ts", str(tmp_ts)]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            report["errors"].append(f"Extraction tool not found: {cmd[0]}")
            return report
        except subprocess.TimeoutExpired:
            report["errors"].append(f"Extraction tool timed out: {cmd[0]}")
            return report

        if proc.returncode != 0 or not tmp_ts.is_file():
            report["errors"].append(
                f"{cmd[0]} failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}"
            )
            return report

        extracted = _ts_entries(tmp_ts)
        report["extracted_count"] = len(extracted)

        for idx, (rel_ts, tracked_ts, tracked_qm) in enumerate(catalog_paths):
            tracked = _ts_entries(tracked_ts)
            missing = sorted(extracted - tracked)
            extra = sorted(tracked - extracted)
            if idx == 0:
                report["tracked_count"] = len(tracked)
            if missing and not report["missing_in_tracked"]:
                report["missing_in_tracked"] = [list(item) for item in missing]
            if extra and not report["extra_in_tracked"]:
                report["extra_in_tracked"] = [list(item) for item in extra]
            if missing or extra:
                report["errors"].append(
                    f"Qt {qt_major} extraction mismatch: "
                    f"{len(missing)} missing, {len(extra)} extra in {tracked_ts.name}"
                )

            tmp_qm = tmp_dir / f"reference_{idx}_{tracked_ts.stem}.qm"
            qm_parity = False
            try:
                lrel_proc = subprocess.run(
                    [lrelease_bin, str(tracked_ts), "-qm", str(tmp_qm)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                report["errors"].append(f"lrelease timed out after 60s for {tracked_ts.name}")
            else:
                if lrel_proc.returncode != 0 or not tmp_qm.is_file():
                    report["errors"].append(
                        f"lrelease failed for {tracked_ts.name} (exit {lrel_proc.returncode}): "
                        f"{(lrel_proc.stderr or lrel_proc.stdout).strip()[:300]}"
                    )
                elif tmp_qm.read_bytes() != tracked_qm.read_bytes():
                    report["errors"].append(
                        f"{tracked_qm.name} is out of date relative to {tracked_ts.name}; rebuild with lrelease"
                    )
                else:
                    qm_parity = True

            report["catalogs"].append({
                "catalog": rel_ts,
                "tracked_count": len(tracked),
                "missing_count": len(missing),
                "extra_count": len(extra),
                "qm_parity": qm_parity,
                "qm_loaded": False,
            })

    if report["errors"]:
        return report

    from qgis.PyQt.QtCore import QCoreApplication, QTranslator

    app = QCoreApplication.instance() or QCoreApplication([])
    app.applicationName()
    all_loaded = True
    for cat_entry, (_rel_ts, _tracked_ts, tracked_qm) in zip(report["catalogs"], catalog_paths):
        translator = QTranslator()
        if not translator.load(str(tracked_qm)) or translator.isEmpty():
            report["errors"].append(f"QTranslator failed to load {tracked_qm}")
            all_loaded = False
            continue
        if not QCoreApplication.installTranslator(translator):
            report["errors"].append(f"QCoreApplication failed to install {tracked_qm}")
            all_loaded = False
            continue
        try:
            if tracked_qm.name == "AgenticGIS_pt_BR.qm":
                translated = QCoreApplication.translate("ChatDock", "Ready")
                if translated != "Pronto":
                    report["errors"].append(
                        f"Expected translated ChatDock/Ready == 'Pronto', got {translated!r}"
                    )
                    all_loaded = False
                    continue
            cat_entry["qm_loaded"] = True
        finally:
            QCoreApplication.removeTranslator(translator)

    if not all_loaded or report["errors"]:
        return report

    report["qm_loaded"] = True
    report["ok"] = True
    return report


def _format_archive_summary(report: dict) -> str:
    status = "PASSED" if report["ok"] else "FAILED"
    lines = [
        f"## Archive Validation: {status}",
        f"- ZIP SHA-256: `{report['zip_sha256'] or 'N/A'}`",
        f"- Files checked: {report['files_checked']}",
        f"- Extracted files: {report['extracted_files']}",
        f"- Blocking errors: {len(report['blocking_errors'])}",
        f"- Informational file findings: {len(report['file_findings'])}",
    ]
    if report["blocking_errors"]:
        lines.append("\n### Blocking errors")
        for err in report["blocking_errors"][:25]:
            lines.append(f"- {err}")
    if report["file_findings"]:
        lines.append("\n### Informational file findings")
        for finding in report["file_findings"][:25]:
            lines.append(
                f"- `{finding['category']}` `{finding['path']}`: {finding['message']}"
            )
    return "\n".join(lines) + "\n"


def _format_scan_summary(report: dict) -> str:
    if "error" in report:
        return f"## Plugin Scan: FAILED\n- {report['error']}\n"
    status = "PASSED" if report["ok"] else "FAILED"
    lines = [f"## Plugin Scan: {status}"]
    for check in report["checks"]:
        name = check["name"]
        if check["errors"]:
            lines.append(f"- **{name}**: ERROR ({'; '.join(check['errors'])})")
            continue
        if name == "bandit":
            lines.append(
                f"- **Bandit**: {'PASSED' if check['ok'] else 'FAILED'} "
                f"({check['files_checked']} files; "
                f"{len(check['blocking_findings'])} medium/high, "
                f"{len(check['informational_findings'])} low)"
            )
            for f in check["blocking_findings"][:20]:
                lines.append(
                    f"  - BLOCKING `{f['test_id']}` [{f['severity']}/{f['confidence']}] "
                    f"`{f['file']}:{f['line']}`: {f['message']}"
                )
            for f in check["informational_findings"][:10]:
                lines.append(
                    f"  - INFO `{f['test_id']}` [{f['severity']}] "
                    f"`{f['file']}:{f['line']}`: {f['message']}"
                )
        elif name == "secrets":
            baselined = check.get("baselined_count", 0)
            baselined_note = f", {baselined} baselined" if baselined else ""
            lines.append(
                f"- **detect-secrets**: {'PASSED' if check['ok'] else 'FAILED'} "
                f"({check['files_checked']} files; "
                f"{len(check['blocking_findings'])} finding(s){baselined_note})"
            )
            for f in check["blocking_findings"][:20]:
                lines.append(
                    f"  - BLOCKING `{f['detector']}` `{f['file']}:{f['line']}`"
                )
        elif name == "compile":
            lines.append(
                f"- **Python compile**: {'PASSED' if check['ok'] else 'FAILED'} "
                f"({check['files_checked']} files; {len(check['blocking_findings'])} error(s))"
            )
            for f in check["blocking_findings"][:20]:
                lines.append(f"  - BLOCKING `{f['file']}:{f['line']}`: {f['message']}")
        elif name == "static":
            lines.append(
                f"- **Static guards (AST deps & Qt5/6)**: {'PASSED' if check['ok'] else 'FAILED'} "
                f"({check['files_checked']} files; {len(check['blocking_findings'])} finding(s))"
            )
            for f in check["blocking_findings"][:20]:
                lines.append(
                    f"  - BLOCKING `{f['rule']}` `{f['file']}:{f['line']}`: {f['message']}"
                )
        elif name == "flake8":
            lines.append(
                f"- **Flake8 (advisory)**: {len(check['advisory_findings'])} finding(s) "
                f"across {check['files_checked']} files"
            )
            if check["counts_by_code"]:
                summary_codes = ", ".join(
                    f"{code}: {count}" for code, count in check["counts_by_code"].items()
                )
                lines.append(f"  - Counts: {summary_codes}")
            for f in check["advisory_findings"][:15]:
                lines.append(
                    f"  - ADVISORY `{f['code']}` `{f['file']}:{f['line']}:{f['col']}`: {f['message']}"
                )
    return "\n".join(lines) + "\n"


def _format_i18n_summary(report: dict) -> str:
    status = "PASSED" if report["ok"] else "FAILED"
    lines = [
        f"## Qt {report['qt_major']} Linguist Check: {status}",
        f"- Catalogs checked: {len(report.get('catalogs', []))}",
        f"- Extracted entries: {report['extracted_count']}",
        f"- Tracked entries: {report['tracked_count']}",
        f"- QM loaded & verified: {report['qm_loaded']}",
    ]
    for err in report["errors"]:
        lines.append(f"- ERROR: {err}")
    for item in report["missing_in_tracked"][:15]:
        lines.append(f"- Missing in tracked TS: {item}")
    for item in report["extra_in_tracked"][:15]:
        lines.append(f"- Extra in tracked TS: {item}")
    return "\n".join(lines) + "\n"


def _write_optional_outputs(
    report: dict,
    summary_text: str,
    json_out: str | None,
    summary_out: str | None,
) -> None:
    if json_out:
        out_path = Path(json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if summary_out:
        sum_path = Path(summary_out)
        sum_path.parent.mkdir(parents=True, exist_ok=True)
        with sum_path.open("a", encoding="utf-8") as handle:
            handle.write(summary_text)


def verify_manifest(
    zip_path: Path,
    manifest_path: Path,
    expected_commit: str | None = None,
    expected_sha256: str | None = None,
) -> dict:
    """Verify a downloaded ZIP matches package-manifest.json and expected commit/SHA-256."""
    report: dict = {
        "zip_path": str(zip_path),
        "manifest_path": str(manifest_path),
        "zip_sha256": None,
        "commit_sha": None,
        "errors": [],
        "ok": False,
    }
    if not zip_path.is_file():
        report["errors"].append(f"Archive does not exist: {zip_path}")
        return report
    if not manifest_path.is_file():
        report["errors"].append(f"Manifest does not exist: {manifest_path}")
        return report
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        report["errors"].append(f"Invalid package manifest JSON: {exc}")
        return report

    if not isinstance(manifest, dict):
        report["errors"].append("Package manifest root must be a JSON object")
        return report

    manifest_sha = manifest.get("zip_sha256")
    manifest_commit = manifest.get("commit_sha")
    if not isinstance(manifest_sha, str) or not manifest_sha:
        report["errors"].append("Package manifest missing 'zip_sha256'")
        return report
    if not isinstance(manifest_commit, str) or not manifest_commit:
        report["errors"].append("Package manifest missing 'commit_sha'")
        return report

    actual_sha = _sha256_file(zip_path)
    report["zip_sha256"] = actual_sha
    report["commit_sha"] = manifest_commit

    if actual_sha != manifest_sha:
        report["errors"].append(
            f"ZIP SHA-256 mismatch: actual {actual_sha} != manifest {manifest_sha}"
        )
    if expected_sha256 is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            report["errors"].append("Invalid expected SHA-256 format")
        elif actual_sha != expected_sha256:
            report["errors"].append(
                f"ZIP SHA-256 mismatch: actual {actual_sha} != expected {expected_sha256}"
            )
    if expected_commit is not None:
        if not expected_commit.strip():
            report["errors"].append("Expected commit SHA must not be empty")
        elif manifest_commit != expected_commit:
            report["errors"].append(
                f"Commit SHA mismatch: manifest {manifest_commit} != expected {expected_commit}"
            )

    report["ok"] = not report["errors"]
    return report


def _parse_metadata_bool(raw: str, key: str) -> tuple[bool | None, str | None]:
    val = raw.strip().lower()
    if val in ("true", "yes", "1"):
        return True, None
    if val in ("false", "no", "0", ""):
        return False, None
    return None, (
        f"Invalid boolean value for '{key}' in metadata.txt; "
        "expected true/false/yes/no/1/0"
    )


def validate_release_metadata(metadata_path: Path, tag: str) -> dict:
    """Validate release tag format, metadata.txt version, and experimental/deprecated flags."""
    report: dict = {
        "metadata_path": str(metadata_path),
        "tag": tag,
        "version": None,
        "prerelease": False,
        "warnings": [],
        "errors": [],
        "ok": False,
    }
    match = RELEASE_TAG_RE.fullmatch(tag.strip())
    if not match:
        report["errors"].append(
            f"Tag {tag!r} must match vX.Y.Z or vX.Y.Z-{{alpha|beta|rc}}.N"
        )
        return report

    expected_version = tag.strip()[1:]
    prerelease = match.group(2) is not None
    report["version"] = expected_version
    report["prerelease"] = prerelease

    if not metadata_path.is_file():
        report["errors"].append(f"metadata.txt not found: {metadata_path}")
        return report

    raw_bytes = metadata_path.read_bytes()
    meta_errors, meta_warnings = _validate_metadata(raw_bytes, None)
    report["errors"].extend(meta_errors)
    report["warnings"].extend(w["message"] for w in meta_warnings)
    if meta_errors:
        return report

    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(raw_bytes.decode("utf-8"), source=str(metadata_path))
    general = parser["general"]

    meta_version = general.get("version", "").strip()
    if meta_version != expected_version:
        report["errors"].append(
            f"metadata.txt 'version' does not match tag version {expected_version!r}"
        )

    deprecated, dep_err = _parse_metadata_bool(general.get("deprecated", "False"), "deprecated")
    if dep_err:
        report["errors"].append(dep_err)
    elif deprecated:
        report["errors"].append("metadata.txt 'deprecated' must be False for releases")

    experimental, exp_err = _parse_metadata_bool(general.get("experimental", "False"), "experimental")
    if exp_err:
        report["errors"].append(exp_err)
    elif prerelease and not experimental:
        report["errors"].append(
            f"Prerelease tag {tag!r} requires metadata.txt experimental=True"
        )
    elif not prerelease and experimental:
        report["errors"].append(
            f"Stable tag {tag!r} requires metadata.txt experimental=False"
        )

    changelog = general.get("changelog", "").strip()
    if not changelog:
        report["warnings"].append("Advisory: metadata.txt has no 'changelog' entry")
    elif expected_version not in changelog:
        report["warnings"].append(
            f"Advisory: metadata.txt 'changelog' does not mention version {expected_version}"
        )

    report["ok"] = not report["errors"]
    return report


def _format_manifest_summary(report: dict) -> str:
    status = "PASSED" if report["ok"] else "FAILED"
    lines = [
        f"## Manifest Verification: {status}",
        f"- ZIP SHA-256: `{report['zip_sha256'] or 'N/A'}`",
        f"- Commit SHA: `{report['commit_sha'] or 'N/A'}`",
    ]
    for err in report["errors"]:
        lines.append(f"- ERROR: {err}")
    return "\n".join(lines) + "\n"


def _format_release_meta_summary(report: dict) -> str:
    status = "PASSED" if report["ok"] else "FAILED"
    lines = [
        f"## Release Metadata Check: {status}",
        f"- Tag: `{report['tag']}`",
        f"- Version: `{report['version'] or 'N/A'}`",
        f"- Prerelease: `{report['prerelease']}`",
    ]
    for warn in report["warnings"]:
        lines.append(f"- WARNING: {warn}")
    for err in report["errors"]:
        lines.append(f"- ERROR: {err}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgenticGIS CI plugin checks")
    sub = parser.add_subparsers(dest="command", required=True)

    p_archive = sub.add_parser("archive", help="Validate and extract a plugin ZIP")
    p_archive.add_argument("zip_path", type=Path)
    p_archive.add_argument("--extract-to", type=Path, required=True)
    p_archive.add_argument("--json-out")
    p_archive.add_argument("--summary-out")

    p_scan = sub.add_parser("scan", help="Run repository-aligned scanners")
    p_scan.add_argument("extracted_plugin", type=Path)
    p_scan.add_argument(
        "--check",
        choices=("all", "bandit", "secrets", "compile", "static", "flake8", "quality"),
        default="all",
    )
    p_scan.add_argument("--flake8-config", type=Path, default=None)
    p_scan.add_argument(
        "--baseline-file",
        type=Path,
        default=None,
        help="Optional reviewed .secrets.baseline outside the scanned plugin package",
    )
    p_scan.add_argument(
        "--include-metadata",
        action="store_true",
        help="Also scan metadata.txt during secrets detection (for pre-upload gating)",
    )
    p_scan.add_argument("--json-out")
    p_scan.add_argument("--summary-out")

    p_i18n = sub.add_parser("i18n", help="Verify Qt Linguist extraction parity and QM load")
    p_i18n.add_argument("repo_root", type=Path)
    p_i18n.add_argument("--qt-major", type=int, choices=(5, 6), required=True)
    p_i18n.add_argument("--json-out")
    p_i18n.add_argument("--summary-out")

    p_manifest = sub.add_parser("verify-manifest", help="Verify ZIP checksum and manifest commit")
    p_manifest.add_argument("zip_path", type=Path)
    p_manifest.add_argument("manifest_path", type=Path)
    p_manifest.add_argument("--expected-commit")
    p_manifest.add_argument("--expected-sha256")
    p_manifest.add_argument("--json-out")
    p_manifest.add_argument("--summary-out")

    p_rel = sub.add_parser("release-meta", help="Validate release tag against metadata.txt")
    p_rel.add_argument("metadata_path", type=Path)
    p_rel.add_argument("--tag", required=True)
    p_rel.add_argument("--github-output", type=Path)
    p_rel.add_argument("--json-out")
    p_rel.add_argument("--summary-out")

    args = parser.parse_args(argv)
    if args.command == "archive":
        report = validate_and_extract_archive(args.zip_path, args.extract_to)
        summary = _format_archive_summary(report)
    elif args.command == "scan":
        report = run_scans(
            args.extracted_plugin,
            args.check,
            args.flake8_config,
            include_metadata_in_secrets=args.include_metadata,
            baseline_path=args.baseline_file,
        )
        summary = _format_scan_summary(report)
    elif args.command == "i18n":
        report = check_i18n(args.repo_root, args.qt_major)
        summary = _format_i18n_summary(report)
    elif args.command == "verify-manifest":
        report = verify_manifest(
            args.zip_path,
            args.manifest_path,
            expected_commit=args.expected_commit,
            expected_sha256=args.expected_sha256,
        )
        summary = _format_manifest_summary(report)
    else:
        report = validate_release_metadata(args.metadata_path, args.tag)
        summary = _format_release_meta_summary(report)
        if report["ok"] and args.github_output:
            with args.github_output.open("a", encoding="utf-8") as handle:
                handle.write(f"version={report['version']}\n")
                handle.write(f"prerelease={'true' if report['prerelease'] else 'false'}\n")

    sys.stdout.write(summary)
    _write_optional_outputs(report, summary, args.json_out, args.summary_out)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
