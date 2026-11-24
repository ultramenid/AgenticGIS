"""Focused unit and integration tests for ci/check_plugin.py."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ci import check_plugin  # noqa: E402

VALID_METADATA = """[general]
name=AgenticGIS
qgisMinimumVersion=3.22
qgisMaximumVersion=4.99
description=An in-QGIS agentic chat assistant.
about=AgenticGIS adds a chat dock to QGIS.
version=0.4.6
author=Test Author
email=author@example.invalid
tracker=https://example.invalid/issues
repository=https://example.invalid/repo
icon=resources/icon.png
"""

VALID_INIT = """def classFactory(iface):
    return None
"""


def _write_zip_entry(
    zf: zipfile.ZipFile,
    name: str,
    data: bytes,
    unix_mode: int = stat.S_IFREG | 0o644,
) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = unix_mode << 16
    zf.writestr(info, data)


def _build_valid_zip(dest: Path, extra_entries: list[tuple[str, bytes, int]] | None = None,
                     omit_members: set[str] | None = None,
                     metadata_override: bytes | None = None) -> Path:
    omit = omit_members or set()
    base_entries: list[tuple[str, bytes, int]] = [
        ("AgenticGIS/__init__.py", VALID_INIT.encode("utf-8"), stat.S_IFREG | 0o644),
        ("AgenticGIS/LICENSE", b"GPL-2.0-or-later\n", stat.S_IFREG | 0o644),
        ("AgenticGIS/metadata.txt", metadata_override or VALID_METADATA.encode("utf-8"), stat.S_IFREG | 0o644),
        ("AgenticGIS/plugin.py", b"class AgenticGisPlugin:\n    pass\n", stat.S_IFREG | 0o644),
        ("AgenticGIS/resources/icon.png", b"\x89PNG\r\n\x1a\n", stat.S_IFREG | 0o644),
        ("AgenticGIS/i18n/AgenticGIS_pt_BR.ts", b"<TS version='2.1'></TS>", stat.S_IFREG | 0o644),
        ("AgenticGIS/i18n/AgenticGIS_pt_BR.qm", check_plugin.QM_MAGIC + b"\x00\x00", stat.S_IFREG | 0o644),
    ]
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data, mode in base_entries:
            if name not in omit:
                _write_zip_entry(zf, name, data, mode)
        for name, data, mode in extra_entries or []:
            _write_zip_entry(zf, name, data, mode)
    return dest


def _has_usable_git_repo() -> bool:
    if shutil.which("git") is None:
        return False
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0 and probe.stdout.strip() == "true"


class ArchiveCheckTests(unittest.TestCase):
    def test_valid_zip_passes_and_excludes_dev_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            zip_path = _build_valid_zip(tmp_dir / "AgenticGIS.zip")
            extract_dir = tmp_dir / "extracted"

            report = check_plugin.validate_and_extract_archive(zip_path, extract_dir)
            self.assertTrue(report["ok"], report["blocking_errors"])
            self.assertEqual(report["blocking_errors"], [])
            self.assertEqual(report["file_findings"], [])
            self.assertTrue((extract_dir / "AgenticGIS" / "metadata.txt").is_file())
            self.assertFalse((extract_dir / "AgenticGIS" / "tests").exists())
            self.assertFalse((extract_dir / "AgenticGIS" / "ci").exists())

    def test_real_git_archive_exports_clean_plugin_only(self):
        if not _has_usable_git_repo():
            self.skipTest("usable git checkout required")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            zip_path = tmp_dir / "AgenticGIS.zip"
            proc = subprocess.run(
                ["git", "archive", "--format=zip", "--prefix=AgenticGIS/", "--output", str(zip_path), "HEAD"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            extract_dir = tmp_dir / "extracted"
            report = check_plugin.validate_and_extract_archive(zip_path, extract_dir)
            self.assertTrue(report["ok"], report["blocking_errors"])
            plugin_root = extract_dir / "AgenticGIS"
            for excluded in ("tests", "ci", "docs", ".github", ".flake8", ".gitattributes", ".gitignore"):
                self.assertFalse((plugin_root / excluded).exists(), f"{excluded} must not ship")

    def test_missing_qm_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            zip_path = _build_valid_zip(
                tmp_dir / "missing_qm.zip",
                omit_members={"AgenticGIS/i18n/AgenticGIS_pt_BR.qm"},
            )
            extract_dir = tmp_dir / "extracted"
            report = check_plugin.validate_and_extract_archive(zip_path, extract_dir)
            self.assertFalse(report["ok"])
            self.assertTrue(any("Missing compiled catalog" in e for e in report["blocking_errors"]))
            self.assertFalse(extract_dir.exists())

    def test_corrupt_qm_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            zip_path = _build_valid_zip(
                tmp_dir / "bad_qm.zip",
                omit_members={"AgenticGIS/i18n/AgenticGIS_pt_BR.qm"},
                extra_entries=[("AgenticGIS/i18n/AgenticGIS_pt_BR.qm", b"not-a-qm-file", stat.S_IFREG | 0o644)],
            )
            report = check_plugin.validate_and_extract_archive(zip_path, tmp_dir / "extracted")
            self.assertFalse(report["ok"])
            self.assertTrue(any("QM catalog header" in e for e in report["blocking_errors"]))

    def test_duplicate_normalized_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            zip_path = _build_valid_zip(
                tmp_dir / "dup.zip",
                extra_entries=[
                    ("AgenticGIS/Plugin.py", b"x = 1\n", stat.S_IFREG | 0o644),
                ],
            )
            report = check_plugin.validate_and_extract_archive(zip_path, tmp_dir / "extracted")
            self.assertFalse(report["ok"])
            self.assertTrue(any("Duplicate normalized member" in e for e in report["blocking_errors"]))

            # Implicit directory case collisions (without explicit dir entries) are rejected
            dir_dup_zip = _build_valid_zip(
                tmp_dir / "dir_dup.zip",
                extra_entries=[
                    ("AgenticGIS/core/a.py", b"a = 1\n", stat.S_IFREG | 0o644),
                    ("AgenticGIS/Core/b.py", b"b = 2\n", stat.S_IFREG | 0o644),
                ],
            )
            rep_dir_dup = check_plugin.validate_and_extract_archive(dir_dup_zip, tmp_dir / "ext_dir_dup")
            self.assertFalse(rep_dir_dup["ok"])
            self.assertTrue(
                any("Case-colliding directory prefix" in e for e in rep_dir_dup["blocking_errors"]),
                rep_dir_dup,
            )

    def test_unsafe_members_are_rejected_before_extraction(self):
        unsafe_cases = [
            ("AgenticGIS/../escape.py", b"x = 1\n", stat.S_IFREG | 0o644),
            ("/etc/passwd", b"root:x:0:0\n", stat.S_IFREG | 0o644),
            ("OtherRoot/plugin.py", b"x = 1\n", stat.S_IFREG | 0o644),
            ("AgenticGIS/link.py", b"/etc/passwd", stat.S_IFLNK | 0o777),
            ("AgenticGIS/resources/aux.txt", b"x\n", stat.S_IFREG | 0o644),
            ("AgenticGIS/con.py", b"x = 1\n", stat.S_IFREG | 0o644),
            ("AgenticGIS/bad:name.txt", b"x\n", stat.S_IFREG | 0o644),
            ("AgenticGIS/trailing.", b"x\n", stat.S_IFREG | 0o644),
        ]
        for member_name, data, mode in unsafe_cases:
            with self.subTest(member=member_name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_dir = Path(tmp)
                    zip_path = _build_valid_zip(
                        tmp_dir / "unsafe.zip",
                        extra_entries=[(member_name, data, mode)],
                    )
                    extract_dir = tmp_dir / "extracted"
                    extract_dir.mkdir()
                    report = check_plugin.validate_and_extract_archive(zip_path, extract_dir)
                    self.assertFalse(report["ok"])
                    self.assertEqual(list(extract_dir.iterdir()), [])

            # NUL-truncated ZipInfo.orig_filename is rejected before using truncated filename
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                nul_zip = _build_valid_zip(
                    tmp_dir / "nul_hdr.zip",
                    extra_entries=[("AgenticGIS/nulX.py", b"x = 1\n", stat.S_IFREG | 0o644)],
                )
                nul_zip.write_bytes(
                    nul_zip.read_bytes().replace(b"AgenticGIS/nulX.py", b"AgenticGIS/nul\x00.py")
                )
                ext_nul = tmp_dir / "ext_nul"
                rep_nul = check_plugin.validate_and_extract_archive(nul_zip, ext_nul)
                self.assertFalse(rep_nul["ok"])
                self.assertTrue(
                    any("normalization" in e or "NUL" in e for e in rep_nul["blocking_errors"]),
                    rep_nul,
                )
                self.assertFalse(ext_nul.exists())

    def test_forbidden_leaks_are_rejected(self):
        forbidden = [
            "AgenticGIS/.git/config",
            "AgenticGIS/.env",
            "AgenticGIS/.secrets.baseline",
            "AgenticGIS/id_rsa",
            "AgenticGIS/core/__pycache__/x.cpython-312.pyc",
            "AgenticGIS/tests/test_x.py",
            "AgenticGIS/nested.zip",
        ]
        for member_name in forbidden:
            with self.subTest(member=member_name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_dir = Path(tmp)
                    zip_path = _build_valid_zip(
                        tmp_dir / "leak.zip",
                        extra_entries=[(member_name, b"secret", stat.S_IFREG | 0o644)],
                    )
                    report = check_plugin.validate_and_extract_archive(zip_path, tmp_dir / "extracted")
                    self.assertFalse(report["ok"])
                    self.assertTrue(any("Forbidden" in e for e in report["blocking_errors"]))

    def test_malformed_metadata_and_missing_icon_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            bad_meta = b"[general]\nname=AgenticGIS\nqgisMinimumVersion=4.0\nqgisMaximumVersion=3.22\n"
            zip_path = _build_valid_zip(tmp_dir / "bad_meta.zip", metadata_override=bad_meta)
            report = check_plugin.validate_and_extract_archive(zip_path, tmp_dir / "extracted")
            self.assertFalse(report["ok"])
            self.assertTrue(any("exceeds qgisMaximumVersion" in e for e in report["blocking_errors"]))

            zip_no_icon = _build_valid_zip(
                tmp_dir / "no_icon.zip",
                omit_members={"AgenticGIS/resources/icon.png"},
            )
            report2 = check_plugin.validate_and_extract_archive(zip_no_icon, tmp_dir / "extracted2")
            self.assertFalse(report2["ok"])
            self.assertTrue(any("icon target not found" in e for e in report2["blocking_errors"]))

    def test_executable_hidden_suspicious_and_unusual_mode_are_reported_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            zip_path = _build_valid_zip(
                tmp_dir / "findings.zip",
                omit_members={"AgenticGIS/resources/icon.png"},
                extra_entries=[
                    ("AgenticGIS/resources/icon.png", b"\x89PNG\r\n\x1a\n", stat.S_IFREG | 0o755),
                    ("AgenticGIS/.hidden_note", b"note", stat.S_IFREG | 0o644),
                    ("AgenticGIS/helper.sh", b"echo hi\n", stat.S_IFREG | 0o4644),
                ],
            )
            extract_dir = tmp_dir / "extracted"
            report = check_plugin.validate_and_extract_archive(zip_path, extract_dir)
            self.assertTrue(report["ok"], report["blocking_errors"])
            categories = {(f["category"], f["path"]) for f in report["file_findings"]}
            self.assertIn(("FILE_EXECUTABLE", "AgenticGIS/resources/icon.png"), categories)
            self.assertIn(("FILE_HIDDEN", "AgenticGIS/.hidden_note"), categories)
            self.assertIn(("FILE_SUSPICIOUS", "AgenticGIS/helper.sh"), categories)
            self.assertIn(("FILE_UNUSUAL_MODE", "AgenticGIS/helper.sh"), categories)
            # Extracted files must not inherit executable or setuid bits.
            extracted_mode = (extract_dir / "AgenticGIS" / "helper.sh").stat().st_mode
            self.assertEqual(extracted_mode & (stat.S_ISUID | stat.S_IXUSR), 0)

    def test_validator_parity_crc_size_license_urls_and_deprecated_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)

            # Missing LICENSE is rejected
            no_lic = _build_valid_zip(tmp_dir / "no_lic.zip", omit_members={"AgenticGIS/LICENSE"})
            rep_lic = check_plugin.validate_and_extract_archive(no_lic, tmp_dir / "ext_lic")
            self.assertFalse(rep_lic["ok"])
            self.assertTrue(any("LICENSE" in e for e in rep_lic["blocking_errors"]))

            # Size cap exceeded is rejected
            sized = _build_valid_zip(tmp_dir / "sized.zip")
            with patch.object(check_plugin, "PLUGIN_MAX_UPLOAD_SIZE", 10):
                rep_size = check_plugin.validate_and_extract_archive(sized, tmp_dir / "ext_size")
            self.assertFalse(rep_size["ok"])
            self.assertTrue(any("exceeds limit" in e for e in rep_size["blocking_errors"]))

            with patch.object(check_plugin, "PLUGIN_MAX_EXPANDED_SIZE", 50), \
                 patch("zipfile.ZipFile.testzip", side_effect=AssertionError("testzip must not run")):
                rep_bomb = check_plugin.validate_and_extract_archive(sized, tmp_dir / "ext_bomb")
            self.assertFalse(rep_bomb["ok"])
            self.assertTrue(any("expanded size" in e for e in rep_bomb["blocking_errors"]), rep_bomb)

            # Corrupt CRC is rejected before extraction
            crc_zip = _build_valid_zip(tmp_dir / "crc.zip")
            raw = bytearray(crc_zip.read_bytes())
            idx = raw.find(b"AgenticGisPlugin")
            self.assertNotEqual(idx, -1)
            raw[idx] ^= 0xFF
            crc_zip.write_bytes(bytes(raw))
            rep_crc = check_plugin.validate_and_extract_archive(crc_zip, tmp_dir / "ext_crc")
            self.assertFalse(rep_crc["ok"])
            self.assertTrue(any("CRC check failed" in e for e in rep_crc["blocking_errors"]))

            # Slash in author & placeholder URL are rejected
            bad_author_meta = VALID_METADATA.replace("author=Test Author", "author=Test/Author").encode("utf-8")
            bad_author = _build_valid_zip(tmp_dir / "bad_author.zip", metadata_override=bad_author_meta)
            rep_author = check_plugin.validate_and_extract_archive(bad_author, tmp_dir / "ext_author")
            self.assertFalse(rep_author["ok"])
            self.assertTrue(any("author" in e for e in rep_author["blocking_errors"]))

            for bad_tracker in (
                "http://bugs",
                "https://[unclosed/issues",
                "https://:443/issues",
                "https://example.org:abc/issues",
            ):
                with self.subTest(bad_tracker=bad_tracker):
                    malformed_meta = VALID_METADATA.replace(
                        "tracker=https://example.invalid/issues", f"tracker={bad_tracker}"
                    ).encode("utf-8")
                    malformed_zip = _build_valid_zip(tmp_dir / "bad_url.zip", metadata_override=malformed_meta)
                    rep_malformed = check_plugin.validate_and_extract_archive(malformed_zip, tmp_dir / "ext_bad_url")
                    self.assertFalse(rep_malformed["ok"])
                    self.assertTrue(any("tracker" in e for e in rep_malformed["blocking_errors"]))

            # Invalid metadata URL or syntax error must not echo embedded credentials
            secret_url_token = "SECRET_" + "URL_CREDENTIAL_99"
            secret_url_meta = VALID_METADATA.replace(
                "tracker=https://example.invalid/issues",
                f"tracker=ftp://user:{secret_url_token}@example.invalid/issues",
            ).encode("utf-8")
            secret_url_zip = _build_valid_zip(tmp_dir / "secret_url.zip", metadata_override=secret_url_meta)
            rep_secret_url = check_plugin.validate_and_extract_archive(secret_url_zip, tmp_dir / "ext_secret_url")
            self.assertFalse(rep_secret_url["ok"])
            self.assertNotIn(secret_url_token, json.dumps(rep_secret_url))

            bad_ini_meta = f"not_a_section = {secret_url_token}\n".encode("utf-8")
            bad_ini_zip = _build_valid_zip(tmp_dir / "bad_ini.zip", metadata_override=bad_ini_meta)
            rep_bad_ini = check_plugin.validate_and_extract_archive(bad_ini_zip, tmp_dir / "ext_bad_ini")
            self.assertFalse(rep_bad_ini["ok"])
            self.assertNotIn(secret_url_token, json.dumps(rep_bad_ini))

            bad_icon_meta = VALID_METADATA.replace(
                "icon=resources/icon.png", f"icon=resources/{secret_url_token}.png"
            ).encode("utf-8")
            bad_icon_zip = _build_valid_zip(tmp_dir / "bad_icon.zip", metadata_override=bad_icon_meta)
            rep_bad_icon = check_plugin.validate_and_extract_archive(bad_icon_zip, tmp_dir / "ext_bad_icon")
            self.assertFalse(rep_bad_icon["ok"])
            self.assertNotIn(secret_url_token, json.dumps(rep_bad_icon))

            # File vs directory prefix collision is rejected before creating destination
            collision_zip = _build_valid_zip(
                tmp_dir / "collision.zip",
                extra_entries=[
                    ("AgenticGIS/assets", b"file-first", stat.S_IFREG | 0o644),
                    ("AgenticGIS/assets/logo.png", b"png", stat.S_IFREG | 0o644),
                ],
            )
            ext_collision = tmp_dir / "ext_collision"
            rep_col = check_plugin.validate_and_extract_archive(collision_zip, ext_collision)
            self.assertFalse(rep_col["ok"])
            self.assertTrue(any("path type collision" in e for e in rep_col["blocking_errors"]))
            self.assertFalse(ext_collision.exists())

            # Deprecated supportsQt6 produces informational warning without blocking
            qt6_meta = (VALID_METADATA + "supportsQt6=True\n").encode("utf-8")
            qt6_zip = _build_valid_zip(tmp_dir / "qt6_warn.zip", metadata_override=qt6_meta)
            rep_qt6 = check_plugin.validate_and_extract_archive(qt6_zip, tmp_dir / "ext_qt6")
            self.assertTrue(rep_qt6["ok"], rep_qt6["blocking_errors"])
            self.assertTrue(
                any(f["category"] == "METADATA_DEPRECATED" for f in rep_qt6["file_findings"])
            )

            # Unreadable/unsupported ZIP member (testzip raises) returns blocking error instead of traceback
            unreadable_zip = _build_valid_zip(tmp_dir / "unreadable.zip")
            with patch("zipfile.ZipFile.testzip", side_effect=NotImplementedError("unsupported compression")):
                rep_unreadable = check_plugin.validate_and_extract_archive(
                    unreadable_zip, tmp_dir / "ext_unreadable"
                )
            self.assertFalse(rep_unreadable["ok"])
            self.assertTrue(
                any("Failed to verify ZIP member integrity" in e for e in rep_unreadable["blocking_errors"]),
                rep_unreadable,
            )


class ScannerAdapterTests(unittest.TestCase):
    def _make_target(self, tmp: str) -> Path:
        target = Path(tmp) / "AgenticGIS"
        target.mkdir()
        (target / "plugin.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
        return target

    def test_bandit_adapter_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_target(tmp)

            # Pass
            ok_proc = subprocess.CompletedProcess([], 0, json.dumps({"errors": [], "results": []}), "")
            with patch("ci.check_plugin.subprocess.run", return_value=ok_proc):
                res = check_plugin._run_bandit(target)
            self.assertTrue(res["ok"])

            # Nonzero due to medium/high findings -> blocking
            med_payload = {
                "errors": [],
                "results": [{
                    "filename": str(target / "plugin.py"),
                    "line_number": 1,
                    "test_id": "B602",
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                    "issue_text": "subprocess shell=True",
                }],
            }
            med_proc = subprocess.CompletedProcess([], 1, json.dumps(med_payload), "")
            with patch("ci.check_plugin.subprocess.run", return_value=med_proc):
                res = check_plugin._run_bandit(target)
            self.assertFalse(res["ok"])
            self.assertEqual(len(res["blocking_findings"]), 1)

            # Low severity only -> informational, passes
            low_payload = {
                "errors": [],
                "results": [{
                    "filename": str(target / "plugin.py"),
                    "line_number": 1,
                    "test_id": "B110",
                    "issue_severity": "LOW",
                    "issue_confidence": "HIGH",
                    "issue_text": "try_except_pass",
                }],
            }
            low_proc = subprocess.CompletedProcess([], 1, json.dumps(low_payload), "")
            with patch("ci.check_plugin.subprocess.run", return_value=low_proc):
                res = check_plugin._run_bandit(target)
            self.assertTrue(res["ok"])
            self.assertEqual(len(res["informational_findings"]), 1)

            # Missing tool -> error
            with patch("ci.check_plugin.subprocess.run", side_effect=FileNotFoundError()):
                res = check_plugin._run_bandit(target)
            self.assertFalse(res["ok"])
            self.assertTrue(res["errors"])

            # Invalid JSON -> error
            bad_json = subprocess.CompletedProcess([], 0, "{not-json", "")
            with patch("ci.check_plugin.subprocess.run", return_value=bad_json):
                res = check_plugin._run_bandit(target)
            self.assertFalse(res["ok"])
            self.assertTrue(res["errors"])

            # Hardcoded credential message in B105 must be redacted from report and summary
            secret_literal = "BANDIT_" + "CREDENTIAL_VALUE_42"
            b105_payload = {
                "errors": [],
                "results": [{
                    "filename": str(target / "plugin.py"),
                    "line_number": 1,
                    "test_id": "B105",
                    "issue_severity": "LOW",
                    "issue_confidence": "MEDIUM",
                    "issue_text": f"Possible hardcoded password: '{secret_literal}'",
                }],
            }
            b105_proc = subprocess.CompletedProcess([], 1, json.dumps(b105_payload), "")
            with patch("ci.check_plugin.subprocess.run", return_value=b105_proc):
                report = check_plugin.run_scans(target, "bandit", None)
            self.assertTrue(report["ok"])
            summary = check_plugin._format_scan_summary(report)
            serialized = json.dumps(report) + "\n" + summary
            self.assertNotIn(secret_literal, serialized)
            self.assertIn("value redacted", serialized)

    def test_detect_secrets_adapter_cases_and_no_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_target(tmp)
            secret_hash = "deadbeef" * 5
            raw_sentinel = "FIXTURE_" + "SECRET_VALUE_98765"

            # Zero exit WITH findings -> MUST FAIL, and never emit raw/hashed secrets
            findings_payload = {
                "results": {
                    "./plugin.py": [{
                        "type": "Secret Keyword",
                        "filename": "./plugin.py",
                        "hashed_secret": secret_hash,
                        "line_number": 1,
                        "is_secret": raw_sentinel,
                    }],
                },
            }
            proc = subprocess.CompletedProcess([], 0, json.dumps(findings_payload), "")
            with patch("ci.check_plugin.subprocess.run", return_value=proc):
                report = check_plugin.run_scans(target, "secrets", None)
            self.assertFalse(report["ok"])
            summary = check_plugin._format_scan_summary(report)
            serialized = json.dumps(report) + "\n" + summary
            self.assertNotIn(secret_hash, serialized)
            self.assertNotIn(raw_sentinel, serialized)
            self.assertIn("Secret Keyword", summary)

            # Finding reported on a binary-named file MUST still fail if detect-secrets scanned it
            binary_named_payload = {
                "results": {
                    "./payload.dll": [{
                        "type": "Secret Keyword",
                        "filename": "./payload.dll",
                        "hashed_secret": secret_hash,
                        "line_number": 3,
                    }],
                },
            }
            dll_proc = subprocess.CompletedProcess([], 0, json.dumps(binary_named_payload), "")
            with patch("ci.check_plugin.subprocess.run", return_value=dll_proc):
                dll_report = check_plugin.run_scans(target, "secrets", None)
            self.assertFalse(dll_report["ok"])
            self.assertEqual(dll_report["checks"][0]["blocking_findings"][0]["file"], "payload.dll")

            # Missing tool -> error
            with patch("ci.check_plugin.subprocess.run", side_effect=FileNotFoundError()):
                res = check_plugin._run_secrets(target)
            self.assertFalse(res["ok"])
            self.assertTrue(res["errors"])

            # Invalid JSON -> error
            bad_json = subprocess.CompletedProcess([], 0, "not-json", "")
            with patch("ci.check_plugin.subprocess.run", return_value=bad_json):
                res = check_plugin._run_secrets(target)
            self.assertFalse(res["ok"])
            self.assertTrue(res["errors"])

    def test_detect_secrets_baseline_comparison_is_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_target(tmp)
            known_hash = "1111222233334444555566667777888899990000"
            confirmed_secret_hash = "aaaabbbbccccddddeeeeffff0000111122223333"
            new_hash = "9999888877776666555544443333222211110000"

            baseline_data = {
                "results": {
                    "plugin.py": [
                        {"type": "Secret Keyword", "hashed_secret": known_hash, "is_secret": False},
                        {"type": "Secret Keyword", "hashed_secret": confirmed_secret_hash, "is_secret": True},
                        {"type": "Secret Keyword", "hashed_secret": new_hash},
                    ],
                    "./.env": [
                        {"type": "Secret Keyword", "hashed_secret": known_hash, "is_secret": False},
                    ],
                },
            }
            baseline_path = Path(tmp) / ".secrets.baseline"
            baseline_bytes = json.dumps(baseline_data).encode("utf-8")
            baseline_path.write_bytes(baseline_bytes)

            # Matching baselined false-positive passes without passing --baseline or rewriting file
            scan_ok = {
                "results": {
                    "./plugin.py": [{"type": "Secret Keyword", "hashed_secret": known_hash, "line_number": 5}],
                    ".env": [{"type": "Secret Keyword", "hashed_secret": known_hash, "line_number": 1}],
                },
            }
            with patch(
                "ci.check_plugin.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, json.dumps(scan_ok), ""),
            ) as mock_run:
                res = check_plugin._run_secrets(target, baseline_path=baseline_path)
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["baselined_count"], 2)
            self.assertIn("--no-verify", mock_run.call_args.args[0])
            self.assertNotIn("--baseline", mock_run.call_args.args[0])
            self.assertEqual(baseline_path.read_bytes(), baseline_bytes)

            # Baseline inside target_dir is rejected
            inner_baseline = target / ".secrets.baseline"
            inner_baseline.write_bytes(baseline_bytes)
            res_inner = check_plugin._run_secrets(target, baseline_path=inner_baseline)
            self.assertFalse(res_inner["ok"])
            self.assertTrue(res_inner["errors"])
            inner_baseline.unlink()

            # Hidden baseline entry ".env" MUST NOT suppress non-hidden "env"
            scan_nonhidden = {
                "results": {
                    "./env": [{"type": "Secret Keyword", "hashed_secret": known_hash, "line_number": 1}],
                },
            }
            with patch(
                "ci.check_plugin.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, json.dumps(scan_nonhidden), ""),
            ):
                res_env = check_plugin._run_secrets(target, baseline_path=baseline_path)
            self.assertFalse(res_env["ok"])
            self.assertEqual(res_env["blocking_findings"][0]["file"], "env")

            # Entry marked is_secret=True or unaudited (missing is_secret) still fails
            for active_hash in (confirmed_secret_hash, new_hash):
                with self.subTest(active_hash=active_hash):
                    scan_fail = {
                        "results": {
                            "plugin.py": [{"type": "Secret Keyword", "hashed_secret": active_hash, "line_number": 7}],
                        },
                    }
                    with patch(
                        "ci.check_plugin.subprocess.run",
                        return_value=subprocess.CompletedProcess([], 0, json.dumps(scan_fail), ""),
                    ):
                        res = check_plugin._run_secrets(target, baseline_path=baseline_path)
                    self.assertFalse(res["ok"])
                    self.assertEqual(len(res["blocking_findings"]), 1)

            # Malformed baseline fails with error
            baseline_path.write_text("{bad-json", encoding="utf-8")
            res = check_plugin._run_secrets(target, baseline_path=baseline_path)
            self.assertFalse(res["ok"])
            self.assertTrue(res["errors"])

    def test_flake8_and_compile_adapter_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_target(tmp)

            # Advisory Flake8 findings pass without failing the check
            adv_stdout = f"{target / 'plugin.py'}:1:81:E501:line too long (130 > 120 characters)\n"
            adv_proc = subprocess.CompletedProcess([], 1, adv_stdout, "")
            with patch("ci.check_plugin.subprocess.run", return_value=adv_proc):
                report = check_plugin.run_scans(target, "flake8", None)
            self.assertTrue(report["ok"])
            self.assertEqual(report["checks"][0]["counts_by_code"], {"E501": 1})

            # Missing Flake8 binary -> error
            with patch("ci.check_plugin.subprocess.run", side_effect=FileNotFoundError()):
                report = check_plugin.run_scans(target, "flake8", None)
            self.assertFalse(report["ok"])

            # Flake8 exit 1 without parseable findings -> error
            empty_fail = subprocess.CompletedProcess([], 1, "", "bad config")
            with patch("ci.check_plugin.subprocess.run", return_value=empty_fail):
                report = check_plugin.run_scans(target, "flake8", None)
            self.assertFalse(report["ok"])

            # Compile catches syntax error and Python 3.9 syntax floor violations
            (target / "py310_only.py").write_text(
                "def check(x):\n    match x:\n        case 1:\n            return 1\n",
                encoding="utf-8",
            )
            py39_report = check_plugin.run_scans(target, "compile", None)
            self.assertFalse(py39_report["ok"])

            (target / "py310_only.py").unlink()
            (target / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            compile_report = check_plugin.run_scans(target, "compile", None)
            self.assertFalse(compile_report["ok"])

    def test_static_ast_dependency_and_qt_compat_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_target(tmp)
            # Guarded optional import (top-level and function-local) and sibling fallback pass
            (target / "guarded.py").write_text(
                "from qgis.PyQt.QtCore import Qt\n"
                "try:\n"
                "    import ee\n"
                "except ImportError:\n"
                "    ee = None\n"
                "def fn_guarded():\n"
                "    try:\n"
                "        import ee\n"
                "    except ImportError:\n"
                "        ee = None\n"
                "    return ee\n"
                "try:\n"
                "    from . import helper\n"
                "except ImportError:\n"
                "    import helper\n",
                encoding="utf-8",
            )
            (target / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            ok_rep = check_plugin.run_scans(target, "static", None)
            self.assertTrue(ok_rep["ok"], ok_rep)

            # Unguarded optional import (top-level and function-local) fails
            (target / "unguarded_ee.py").write_text("import ee\n", encoding="utf-8")
            ee_rep = check_plugin.run_scans(target, "static", None)
            self.assertFalse(ee_rep["ok"])
            (target / "unguarded_ee.py").unlink()

            (target / "unguarded_fn_ee.py").write_text(
                "def bad():\n    import ee\n    return ee\n",
                encoding="utf-8",
            )
            fn_ee_rep = check_plugin.run_scans(target, "static", None)
            self.assertFalse(fn_ee_rep["ok"])
            self.assertEqual(
                fn_ee_rep["checks"][0]["blocking_findings"][0]["rule"],
                "UNGUARDED_OPTIONAL_IMPORT",
            )
            (target / "unguarded_fn_ee.py").unlink()

            # Third-party dependency and post-Python-3.9 stdlib module fail
            (target / "dep.py").write_text("import requests\n", encoding="utf-8")
            dep_rep = check_plugin.run_scans(target, "static", None)
            self.assertFalse(dep_rep["ok"])
            (target / "dep.py").unlink()

            (target / "py311_stdlib.py").write_text("import tomllib\n", encoding="utf-8")
            toml_rep = check_plugin.run_scans(target, "static", None)
            self.assertFalse(toml_rep["ok"])
            self.assertEqual(
                toml_rep["checks"][0]["blocking_findings"][0]["rule"],
                "UNEXPECTED_DEPENDENCY",
            )
            (target / "py311_stdlib.py").unlink()

            # Direct PyQt5/6 import and .exec_() fail
            (target / "qt_bad.py").write_text(
                "from PyQt5.QtCore import QObject\n"
                "def run(dlg):\n"
                "    return dlg.exec_()\n",
                encoding="utf-8",
            )
            qt_rep = check_plugin.run_scans(target, "static", None)
            self.assertFalse(qt_rep["ok"])
            rules = {f["rule"] for f in qt_rep["checks"][0]["blocking_findings"]}
            self.assertEqual(rules, {"QT_COMPAT"})
            (target / "qt_bad.py").unlink()

            # Decorators and default arguments are checked in enclosing scope too
            (target / "qt_sig_bad.py").write_text(
                "@dec(dlg.exec_())\n"
                "async def run(x=dlg.print_()):\n"
                "    return x\n",
                encoding="utf-8",
            )
            sig_rep = check_plugin.run_scans(target, "static", None)
            self.assertFalse(sig_rep["ok"])
            sig_lines = sorted(f["line"] for f in sig_rep["checks"][0]["blocking_findings"])
            self.assertEqual(sig_lines, [1, 2])
            (target / "qt_sig_bad.py").unlink()

            # Non-ImportError exception handler does NOT inherit sibling-import exemption
            (target / "handler_bad.py").write_text(
                "try:\n"
                "    from . import helper\n"
                "except ImportError:\n"
                "    import helper\n"
                "except ValueError:\n"
                "    import helper\n",
                encoding="utf-8",
            )
            handler_rep = check_plugin.run_scans(target, "static", None)
            self.assertFalse(handler_rep["ok"])
            handler_findings = handler_rep["checks"][0]["blocking_findings"]
            self.assertEqual(len(handler_findings), 1)
            self.assertEqual(handler_findings[0]["line"], 6)
            self.assertEqual(handler_findings[0]["rule"], "UNEXPECTED_DEPENDENCY")
            (target / "handler_bad.py").unlink()

    def test_manifest_verification_and_release_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            zip_path = _build_valid_zip(tmp_dir / "AgenticGIS.zip")
            sha = check_plugin._sha256_file(zip_path)
            manifest = tmp_dir / "package-manifest.json"
            manifest.write_text(
                json.dumps({"commit_sha": "abc123", "zip_sha256": sha}),
                encoding="utf-8",
            )
            self.assertTrue(
                check_plugin.verify_manifest(zip_path, manifest, "abc123", sha)["ok"]
            )
            self.assertFalse(
                check_plugin.verify_manifest(zip_path, manifest, "wrong", sha)["ok"]
            )
            self.assertFalse(
                check_plugin.verify_manifest(zip_path, manifest, "abc123", "0" * 64)["ok"]
            )
            self.assertFalse(
                check_plugin.verify_manifest(zip_path, manifest, "abc123", "")["ok"]
            )
            self.assertFalse(
                check_plugin.verify_manifest(zip_path, manifest, "", sha)["ok"]
            )

            meta_path = tmp_dir / "metadata.txt"
            meta_path.write_text(
                VALID_METADATA + "experimental=False\ndeprecated=False\nchangelog=0.4.6 initial\n",
                encoding="utf-8",
            )
            ok_rel = check_plugin.validate_release_metadata(meta_path, "v0.4.6")
            self.assertTrue(ok_rel["ok"], ok_rel)
            self.assertFalse(ok_rel["prerelease"])

            self.assertFalse(check_plugin.validate_release_metadata(meta_path, "v0.4.7")["ok"])
            self.assertFalse(check_plugin.validate_release_metadata(meta_path, "0.4.6")["ok"])
            self.assertFalse(check_plugin.validate_release_metadata(meta_path, "v0.4.6-rc.1")["ok"])

            pre_meta = tmp_dir / "pre_metadata.txt"
            pre_meta.write_text(
                VALID_METADATA.replace("version=0.4.6", "version=0.5.0-rc.1")
                + "experimental=True\ndeprecated=False\n",
                encoding="utf-8",
            )
            ok_pre = check_plugin.validate_release_metadata(pre_meta, "v0.5.0-rc.1")
            self.assertTrue(ok_pre["ok"], ok_pre)
            self.assertTrue(ok_pre["prerelease"])

            dep_meta = tmp_dir / "dep_metadata.txt"
            dep_meta.write_text(
                VALID_METADATA + "experimental=False\ndeprecated=True\n",
                encoding="utf-8",
            )
            self.assertFalse(check_plugin.validate_release_metadata(dep_meta, "v0.4.6")["ok"])

            # Rejected metadata values (version/experimental/deprecated) must not leak into summary/errors
            secret_ver = "SECRET_TOKEN_VERSION_123"
            secret_exp = "SECRET_TOKEN_EXP_456"
            secret_dep = "SECRET_TOKEN_DEP_789"
            secret_meta = tmp_dir / "secret_metadata.txt"
            secret_meta.write_text(
                VALID_METADATA.replace("version=0.4.6", f"version={secret_ver}")
                + f"experimental={secret_exp}\ndeprecated={secret_dep}\n",
                encoding="utf-8",
            )
            sec_rep = check_plugin.validate_release_metadata(secret_meta, "v0.4.6")
            self.assertFalse(sec_rep["ok"])
            sec_out = json.dumps(sec_rep) + "\n" + check_plugin._format_release_meta_summary(sec_rep)
            for leaked in (secret_ver, secret_exp, secret_dep):
                self.assertNotIn(leaked, sec_out)

    def test_check_i18n_validates_all_catalogs(self):
        pt_ts = (
            "<TS version='2.1'><context><name>ChatDock</name>"
            "<message><source>Ready</source><translation>Pronto</translation></message>"
            "</context></TS>\n"
        )
        es_ts = (
            "<TS version='2.1'><context><name>ChatDock</name>"
            "<message><source>Ready</source><translation>Listo</translation></message>"
            "</context></TS>\n"
        )
        stale_ts = (
            "<TS version='2.1'><context><name>ChatDock</name>"
            "<message><source>Missing</source><translation>Falta</translation></message>"
            "</context></TS>\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "plugin.py").write_text("# dummy source\n", encoding="utf-8")
            i18n = repo / "i18n"
            i18n.mkdir()
            (i18n / "AgenticGIS.pro").write_text(
                "SOURCES = ../plugin.py\n"
                "TRANSLATIONS = AgenticGIS_pt_BR.ts \\\n"
                "    AgenticGIS_es.ts\n",
                encoding="utf-8",
            )
            (i18n / "AgenticGIS_pt_BR.ts").write_text(pt_ts, encoding="utf-8")
            (i18n / "AgenticGIS_pt_BR.qm").write_bytes(b"QM_PT_BR")
            (i18n / "AgenticGIS_es.ts").write_text(stale_ts, encoding="utf-8")
            (i18n / "AgenticGIS_es.qm").write_bytes(b"QM_ES")

            def fake_subproc(cmd, **_kwargs):
                if cmd[0] == "pylupdate6":
                    out_ts = Path(cmd[cmd.index("-ts") + 1])
                    out_ts.write_text(pt_ts, encoding="utf-8")
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                if Path(cmd[0]).name == "lrelease":
                    if cmd[1:] == ["-version"]:
                        return subprocess.CompletedProcess(cmd, 0, "lrelease version 6.8.2\n", "")
                    in_ts = Path(cmd[1])
                    out_qm = Path(cmd[cmd.index("-qm") + 1])
                    data = b"QM_PT_BR" if in_ts.stem.endswith("pt_BR") else b"QM_ES"
                    out_qm.write_bytes(data)
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                raise AssertionError(f"Unexpected command: {cmd}")

            # Second catalog string mismatch MUST fail even though pt_BR matches
            with patch("ci.check_plugin.shutil.which", return_value="/usr/bin/lrelease"), \
                 patch("ci.check_plugin.subprocess.run", side_effect=fake_subproc):
                bad_ts = check_plugin.check_i18n(repo, 6)
            self.assertFalse(bad_ts["ok"])
            self.assertTrue(any("AgenticGIS_es.ts" in e for e in bad_ts["errors"]), bad_ts)

            # Fix second TS, leave second QM stale -> MUST fail
            (i18n / "AgenticGIS_es.ts").write_text(es_ts, encoding="utf-8")
            (i18n / "AgenticGIS_es.qm").write_bytes(b"STALE_ES")
            with patch("ci.check_plugin.shutil.which", return_value="/usr/bin/lrelease"), \
                 patch("ci.check_plugin.subprocess.run", side_effect=fake_subproc):
                bad_qm = check_plugin.check_i18n(repo, 6)
            self.assertFalse(bad_qm["ok"])
            self.assertTrue(any("AgenticGIS_es.qm" in e for e in bad_qm["errors"]), bad_qm)

            # Matching second QM -> passes, checking both catalogs
            (i18n / "AgenticGIS_es.qm").write_bytes(b"QM_ES")
            installed = []

            class FakeTranslator:
                def __init__(self):
                    self.path = None

                def load(self, path):
                    self.path = Path(path)
                    return True

                def isEmpty(self):
                    return False

            class FakeCoreApp:
                _active = None

                @classmethod
                def instance(cls):
                    return cls()

                def applicationName(self):
                    return "fake"

                @classmethod
                def installTranslator(cls, tr):
                    cls._active = tr.path.name
                    installed.append(cls._active)
                    return True

                @classmethod
                def removeTranslator(cls, tr):
                    if cls._active == tr.path.name:
                        cls._active = None

                @classmethod
                def translate(cls, ctx, src):
                    if ctx == "ChatDock" and src == "Ready" and cls._active == "AgenticGIS_pt_BR.qm":
                        return "Pronto"
                    return src

            fake_qtcore = types.ModuleType("qgis.PyQt.QtCore")
            fake_qtcore.QCoreApplication = FakeCoreApp
            fake_qtcore.QTranslator = FakeTranslator
            fake_pyqt = types.ModuleType("qgis.PyQt")
            fake_pyqt.QtCore = fake_qtcore
            fake_qgis = types.ModuleType("qgis")
            fake_qgis.PyQt = fake_pyqt

            with patch("ci.check_plugin.shutil.which", return_value="/usr/bin/lrelease"), \
                 patch("ci.check_plugin.subprocess.run", side_effect=fake_subproc), \
                 patch.dict(
                     sys.modules,
                     {"qgis": fake_qgis, "qgis.PyQt": fake_pyqt, "qgis.PyQt.QtCore": fake_qtcore},
                 ):
                ok_rep = check_plugin.check_i18n(repo, 6)
            self.assertTrue(ok_rep["ok"], ok_rep)
            self.assertTrue(ok_rep["qm_loaded"])
            self.assertEqual(len(ok_rep["catalogs"]), 2)
            self.assertEqual(installed, ["AgenticGIS_pt_BR.qm", "AgenticGIS_es.qm"])

            # Vanished/obsolete TS entries must NOT count toward parity when string is reintroduced
            for inactive_type in ("vanished", "obsolete"):
                with self.subTest(inactive_type=inactive_type):
                    (i18n / "AgenticGIS_es.ts").write_text(
                        "<TS version='2.1'><context><name>ChatDock</name>"
                        f"<message><source>Ready</source><translation type='{inactive_type}'>Listo</translation></message>"
                        "</context></TS>\n",
                        encoding="utf-8",
                    )
                    with patch("ci.check_plugin.shutil.which", return_value="/usr/bin/lrelease"), \
                         patch("ci.check_plugin.subprocess.run", side_effect=fake_subproc):
                        inactive_rep = check_plugin.check_i18n(repo, 6)
                    self.assertFalse(inactive_rep["ok"])
                    self.assertTrue(
                        any("AgenticGIS_es.ts" in e for e in inactive_rep["errors"]),
                        inactive_rep,
                    )

            # Restore es.ts, add non-literal forwarding helper (ok), then add unlisted literal call (fails)
            (i18n / "AgenticGIS_es.ts").write_text(es_ts, encoding="utf-8")
            (repo / "gui").mkdir()
            (repo / "gui" / "forwarding.py").write_text(
                "from qgis.PyQt.QtCore import QCoreApplication\n"
                "def show(ctx, src):\n"
                "    return QCoreApplication.translate(ctx, src)\n",
                encoding="utf-8",
            )
            with patch("ci.check_plugin.shutil.which", return_value="/usr/bin/lrelease"), \
                 patch("ci.check_plugin.subprocess.run", side_effect=fake_subproc), \
                 patch.dict(
                     sys.modules,
                     {"qgis": fake_qgis, "qgis.PyQt": fake_pyqt, "qgis.PyQt.QtCore": fake_qtcore},
                 ):
                fwd_rep = check_plugin.check_i18n(repo, 6)
            self.assertTrue(fwd_rep["ok"], fwd_rep)

            (repo / "unlisted_mod.py").write_text(
                "from qgis.PyQt.QtCore import QCoreApplication\n"
                "MSG = QCoreApplication.translate('ChatDock', 'Unlisted string')\n",
                encoding="utf-8",
            )
            unlisted_rep = check_plugin.check_i18n(repo, 6)
            self.assertFalse(unlisted_rep["ok"])
            self.assertTrue(
                any("unlisted_mod.py" in e for e in unlisted_rep["errors"]),
                unlisted_rep,
            )

            # Disambiguation <comment> mismatch must fail parity even when source & context match
            (repo / "unlisted_mod.py").unlink()
            (i18n / "AgenticGIS_es.ts").write_text(
                "<TS version='2.1'><context><name>ChatDock</name>"
                "<message><source>Ready</source><comment>old-disambiguation</comment>"
                "<translation>Listo</translation></message>"
                "</context></TS>\n",
                encoding="utf-8",
            )
            with patch("ci.check_plugin.shutil.which", return_value="/usr/bin/lrelease"), \
                 patch("ci.check_plugin.subprocess.run", side_effect=fake_subproc):
                disambig_rep = check_plugin.check_i18n(repo, 6)
            self.assertFalse(disambig_rep["ok"])
            self.assertTrue(
                any("AgenticGIS_es.ts" in e for e in disambig_rep["errors"]),
                disambig_rep,
            )

            # Unlisted/unpaired translation catalog in i18n/ MUST fail
            (i18n / "AgenticGIS_es.ts").write_text(es_ts, encoding="utf-8")
            (i18n / "AgenticGIS_fr.qm").write_bytes(b"QM_FR")
            unlisted_cat = check_plugin.check_i18n(repo, 6)
            self.assertFalse(unlisted_cat["ok"])
            self.assertTrue(
                any("AgenticGIS_fr.qm" in e for e in unlisted_cat["errors"]),
                unlisted_cat,
            )

            # Wrong-major generic lrelease MUST be rejected for qt_major=6
            with (
                patch("pathlib.Path.is_file", return_value=False),
                patch(
                    "ci.check_plugin.shutil.which",
                    side_effect=lambda name: "/usr/bin/lrelease" if name == "lrelease" else None,
                ),
                patch(
                    "ci.check_plugin.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, "lrelease version 5.15.13\n", ""),
                ),
            ):
                wrong_bin, wrong_err = check_plugin._resolve_lrelease(6)
            self.assertIsNone(wrong_bin)
            self.assertIsNotNone(wrong_err)
            self.assertIn("version 6.x", wrong_err)


class RealScannerIntegrationTests(unittest.TestCase):
    def _require_tools(self) -> None:
        tools = ("bandit", "detect-secrets", "flake8")
        missing = [
            tool for tool in tools
            if shutil.which(tool) is None and not (Path(sys.executable).parent / tool).is_file()
        ]
        if missing:
            if os.environ.get("AGENTICGIS_REQUIRE_REAL_SCANNERS") == "1":
                self.fail(f"Required scanner binaries missing from PATH: {missing}")
            self.skipTest(f"Scanner binaries not installed in this environment: {missing}")

    def test_real_scanners_on_clean_and_synthetic_packages(self):
        self._require_tools()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # 1. Fixed clean plugin fixture passes all scanners (decoupled from PR HEAD regressions)
            zip_path = _build_valid_zip(tmp_dir / "clean.zip")
            extract_dir = tmp_dir / "clean_ext"
            arch = check_plugin.validate_and_extract_archive(zip_path, extract_dir)
            self.assertTrue(arch["ok"], arch["blocking_errors"])
            clean_scan = check_plugin.run_scans(
                extract_dir / "AgenticGIS",
                "all",
                ROOT / ".flake8",
                include_metadata_in_secrets=True,
            )
            self.assertTrue(clean_scan["ok"], json.dumps(clean_scan, indent=2))

            # 2. Synthetic package verifying real scanners catch intended failures without leaking secrets
            synth = tmp_dir / "synth"
            synth.mkdir()
            secret_token = "ghp_" + "A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R8"
            password_literal = "pw_" + "REAL_BANDIT_SECRET_77"
            (synth / "bad_bandit.py").write_text(
                "import subprocess\n\n"
                f'password = "{password_literal}"\n\n'
                "def run(cmd):\n    return subprocess.Popen(cmd, shell=True)\n",
                encoding="utf-8",
            )
            (synth / "leak.py").write_text(
                f'api_key = "{secret_token}"\n',
                encoding="utf-8",
            )
            (synth / "metadata.txt").write_text(
                VALID_METADATA + f'about=leaked "{secret_token}"\n',
                encoding="utf-8",
            )
            (synth / "resources").mkdir()
            (synth / "resources" / ".secrets.baseline.bak").write_text(
                f'backup_token = "{secret_token}"\n',
                encoding="utf-8",
            )
            (synth / "style.py").write_text(
                "x=1+" + "2" * 130 + "\n",
                encoding="utf-8",
            )

            bandit_rep = check_plugin.run_scans(synth, "bandit", None)
            self.assertFalse(bandit_rep["ok"])
            self.assertTrue(
                any(f["test_id"] == "B602" for f in bandit_rep["checks"][0]["blocking_findings"])
            )
            bandit_text = json.dumps(bandit_rep) + "\n" + check_plugin._format_scan_summary(bandit_rep)
            self.assertNotIn(password_literal, bandit_text)

            secrets_rep = check_plugin.run_scans(synth, "secrets", None, include_metadata_in_secrets=True)
            self.assertFalse(secrets_rep["ok"])
            files_flagged = {f["file"] for f in secrets_rep["checks"][0]["blocking_findings"]}
            self.assertIn("leak.py", files_flagged)
            self.assertIn("metadata.txt", files_flagged)
            self.assertIn("resources/.secrets.baseline.bak", files_flagged)
            secrets_text = json.dumps(secrets_rep) + "\n" + check_plugin._format_scan_summary(secrets_rep)
            self.assertNotIn(secret_token, secrets_text)

            # Unaudited generated baseline (without is_secret=False) must still block
            ds_bin = check_plugin._resolve_tool("detect-secrets")
            baseline_proc = subprocess.run(
                [
                    ds_bin,
                    "scan",
                    "--all-files",
                    "--exclude-files",
                    r"^(\./)?metadata\.txt$",
                    "--exclude-files",
                    r"^(\./)?\.secrets\.baseline$",
                    ".",
                ],
                cwd=str(synth),
                check=True,
                capture_output=True,
                text=True,
            )
            baseline_payload = json.loads(baseline_proc.stdout)
            baseline_file = tmp_dir / ".secrets.baseline"
            baseline_file.write_text(json.dumps(baseline_payload, indent=2), encoding="utf-8")
            unaudited_rep = check_plugin.run_scans(synth, "secrets", None, baseline_path=baseline_file)
            self.assertFalse(unaudited_rep["ok"])

            # Only an explicitly reviewed baseline (is_secret=False) suppresses known entries read-only
            for hits in baseline_payload["results"].values():
                for hit in hits:
                    hit["is_secret"] = False
            reviewed_text = json.dumps(baseline_payload, indent=2)
            baseline_file.write_text(reviewed_text, encoding="utf-8")
            baseline_bytes = baseline_file.read_bytes()

            baselined_rep = check_plugin.run_scans(synth, "secrets", None, baseline_path=baseline_file)
            self.assertTrue(baselined_rep["ok"], baselined_rep)
            self.assertGreater(baselined_rep["checks"][0]["baselined_count"], 0)
            self.assertEqual(baseline_file.read_bytes(), baseline_bytes)

            second_secret = "AKIA" + "IOSFODNN7" + "EXAMPLENEW"
            (synth / "leak2.py").write_text(f'aws_id = "{second_secret}"\n', encoding="utf-8")
            new_secret_rep = check_plugin.run_scans(synth, "secrets", None, baseline_path=baseline_file)
            self.assertFalse(new_secret_rep["ok"])
            new_secret_text = json.dumps(new_secret_rep) + "\n" + check_plugin._format_scan_summary(new_secret_rep)
            self.assertNotIn(secret_token, new_secret_text)
            self.assertNotIn(second_secret, new_secret_text)

            flake_rep = check_plugin.run_scans(synth, "flake8", None)
            self.assertTrue(flake_rep["ok"])
            self.assertGreater(len(flake_rep["checks"][0]["advisory_findings"]), 0)


if __name__ == "__main__":
    unittest.main()
