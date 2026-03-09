"""Tests for conversion log helpers, path utilities, and image snapshot/cleanup."""

import hashlib
import json
import os
import pathlib

import pytest

from server import (
    _file_hash,
    _export_dir_for,
    _log_path_for,
    _load_log,
    _save_log,
    _now_iso,
    _is_already_converted,
    _record_entry,
    _resolve_output_path,
    _snapshot_images,
    _cleanup_new_images,
    _image_subdir,
    _cleanup_recognized_images,
    EXPORT_SUBFOLDER,
    LOG_FILENAME,
)


# ---------------------------------------------------------------------------
# _file_hash
# ---------------------------------------------------------------------------

class TestFileHash:
    def test_known_content(self, tmp_path):
        f = tmp_path / "test.txt"
        content = b"hello world"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert _file_hash(str(f)) == expected

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert _file_hash(str(f)) == expected

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"aaa")
        f2.write_bytes(b"bbb")
        assert _file_hash(str(f1)) != _file_hash(str(f2))


# ---------------------------------------------------------------------------
# _export_dir_for / _log_path_for
# ---------------------------------------------------------------------------

class TestExportDir:
    def test_file_path(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.touch()
        result = _export_dir_for(str(f))
        assert result == tmp_path / EXPORT_SUBFOLDER

    def test_folder_path(self, tmp_path):
        result = _export_dir_for(str(tmp_path))
        assert result == tmp_path / EXPORT_SUBFOLDER

    def test_log_path(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.touch()
        result = _log_path_for(str(f))
        assert result == str(tmp_path / EXPORT_SUBFOLDER / LOG_FILENAME)


# ---------------------------------------------------------------------------
# _load_log / _save_log
# ---------------------------------------------------------------------------

class TestLoadSaveLog:
    def test_roundtrip(self, tmp_path):
        log_path = str(tmp_path / "log.json")
        data = {"key": {"status": "ok", "chars": 100}}
        _save_log(log_path, data)
        loaded = _load_log(log_path)
        assert loaded == data

    def test_load_nonexistent(self, tmp_path):
        result = _load_log(str(tmp_path / "missing.json"))
        assert result == {}

    def test_load_corrupt_json(self, tmp_path):
        f = tmp_path / "corrupt.json"
        f.write_text("{broken json", encoding="utf-8")
        result = _load_log(str(f))
        assert result == {}

    def test_save_creates_directory(self, tmp_path):
        log_path = str(tmp_path / "subdir" / "log.json")
        _save_log(log_path, {"a": 1})
        assert os.path.isfile(log_path)


# ---------------------------------------------------------------------------
# _now_iso
# ---------------------------------------------------------------------------

class TestNowIso:
    def test_format(self):
        result = _now_iso()
        assert "+00:00" in result
        assert "T" in result


# ---------------------------------------------------------------------------
# _is_already_converted
# ---------------------------------------------------------------------------

class TestIsAlreadyConverted:
    def test_no_entry(self):
        assert _is_already_converted({}, "file.pdf", "abc") is False

    def test_error_status(self, tmp_path):
        log = {"file.pdf": {"status": "error", "source_hash": "abc", "output_path": str(tmp_path / "out.md")}}
        assert _is_already_converted(log, "file.pdf", "abc") is False

    def test_hash_mismatch(self, tmp_path):
        out = tmp_path / "out.md"
        out.touch()
        log = {"file.pdf": {"status": "ok", "source_hash": "old_hash", "output_path": str(out)}}
        assert _is_already_converted(log, "file.pdf", "new_hash") is False

    def test_output_missing(self):
        log = {"file.pdf": {"status": "ok", "source_hash": "abc", "output_path": "/nonexistent/out.md"}}
        assert _is_already_converted(log, "file.pdf", "abc") is False

    def test_all_ok(self, tmp_path):
        out = tmp_path / "out.md"
        out.write_text("content", encoding="utf-8")
        log = {"file.pdf": {"status": "ok", "source_hash": "abc", "output_path": str(out)}}
        assert _is_already_converted(log, "file.pdf", "abc") is True


# ---------------------------------------------------------------------------
# _record_entry
# ---------------------------------------------------------------------------

class TestRecordEntry:
    def test_basic_fields(self):
        log = {}
        entry = _record_entry(log, "src.pdf", "out.md", "hash123", "ok", chars=500, lines=20, duration_sec=1.5)
        assert entry["source_path"] == "src.pdf"
        assert entry["output_path"] == "out.md"
        assert entry["source_hash"] == "hash123"
        assert entry["status"] == "ok"
        assert entry["chars"] == 500
        assert entry["lines"] == 20
        assert entry["duration_sec"] == 1.5
        assert "converted_at" in entry
        assert "converted_by" in entry
        assert "machine" in entry
        assert log["src.pdf"] is entry

    def test_extra_fields(self):
        log = {}
        entry = _record_entry(log, "src.pdf", "out.md", "h", "ok", extra={"ocr": True, "pages": 5})
        assert entry["ocr"] is True
        assert entry["pages"] == 5

    def test_error_field(self):
        log = {}
        entry = _record_entry(log, "src.pdf", "out.md", "h", "error", error="boom")
        assert entry["error"] == "boom"


# ---------------------------------------------------------------------------
# _resolve_output_path
# ---------------------------------------------------------------------------

class TestResolveOutputPath:
    def test_explicit_output_path(self):
        assert _resolve_output_path("any.pdf", "/custom/out.md") == "/custom/out.md"

    def test_default_export_dir(self, tmp_path):
        pdf = tmp_path / "report.pdf"
        pdf.touch()
        result = _resolve_output_path(str(pdf), None)
        expected = str(tmp_path / EXPORT_SUBFOLDER / "report.md")
        assert result == expected


# ---------------------------------------------------------------------------
# _snapshot_images / _cleanup_new_images
# ---------------------------------------------------------------------------

class TestSnapshotImages:
    def test_finds_images(self, tmp_path):
        (tmp_path / "a.png").touch()
        (tmp_path / "b.jpg").touch()
        (tmp_path / "c.txt").touch()
        result = _snapshot_images(str(tmp_path))
        names = {os.path.basename(p) for p in result}
        assert names == {"a.png", "b.jpg"}

    def test_empty_dir(self, tmp_path):
        assert _snapshot_images(str(tmp_path)) == set()

    def test_nonexistent_dir(self):
        assert _snapshot_images("/nonexistent/dir") == set()


class TestCleanupNewImages:
    def test_deletes_only_new(self, tmp_path):
        (tmp_path / "old.png").touch()
        before = _snapshot_images(str(tmp_path))

        (tmp_path / "new1.png").touch()
        (tmp_path / "new2.jpg").touch()
        (tmp_path / "keep.txt").touch()

        removed = _cleanup_new_images(before, str(tmp_path))
        assert removed == 2
        assert (tmp_path / "old.png").exists()
        assert not (tmp_path / "new1.png").exists()
        assert not (tmp_path / "new2.jpg").exists()
        assert (tmp_path / "keep.txt").exists()

    def test_nothing_new(self, tmp_path):
        (tmp_path / "old.png").touch()
        before = _snapshot_images(str(tmp_path))
        removed = _cleanup_new_images(before, str(tmp_path))
        assert removed == 0
        assert (tmp_path / "old.png").exists()


# ---------------------------------------------------------------------------
# _image_subdir
# ---------------------------------------------------------------------------

class TestImageSubdir:
    def test_basic(self, tmp_path):
        result = _image_subdir(str(tmp_path), "D:/docs/report.pdf")
        assert result.endswith("report")
        assert str(tmp_path) in result

    def test_sanitizes_special_chars(self, tmp_path):
        result = _image_subdir(str(tmp_path), 'C:/a/file<with>special:chars.pdf')
        name = os.path.basename(result)
        assert "<" not in name
        assert ">" not in name
        assert ":" not in name

    def test_spaces_preserved(self, tmp_path):
        result = _image_subdir(str(tmp_path), "C:/a/My Report 2024.pdf")
        name = os.path.basename(result)
        assert "My Report 2024" == name

    def test_nbsp_replaced_with_space(self, tmp_path):
        """Non-breaking space \\xa0 in filename must be replaced with regular space."""
        result = _image_subdir(str(tmp_path), "C:/a/C#\xa0Access\xa0Push\xa0Demo.pdf")
        name = os.path.basename(result)
        assert "\xa0" not in name, "\\xa0 must be removed from directory name"
        assert "C# Access Push Demo" == name

    def test_multiple_spaces_collapsed(self, tmp_path):
        result = _image_subdir(str(tmp_path), "C:/a/My  \xa0 Report.pdf")
        name = os.path.basename(result)
        assert "My Report" == name

    def test_empty_stem_fallback(self, tmp_path):
        result = _image_subdir(str(tmp_path), "C:/a/...pdf")
        name = os.path.basename(result)
        assert name == "images"


# ---------------------------------------------------------------------------
# _cleanup_recognized_images
# ---------------------------------------------------------------------------

class TestCleanupRecognizedImages:
    def test_deletes_only_listed(self, tmp_path):
        ok = tmp_path / "recognized.png"
        keep = tmp_path / "failed.png"
        ok.touch()
        keep.touch()

        removed = _cleanup_recognized_images([str(ok)])
        assert removed == 1
        assert not ok.exists()
        assert keep.exists()

    def test_nonexistent_path(self, tmp_path):
        removed = _cleanup_recognized_images([str(tmp_path / "ghost.png")])
        assert removed == 0

    def test_empty_list(self):
        removed = _cleanup_recognized_images([])
        assert removed == 0
