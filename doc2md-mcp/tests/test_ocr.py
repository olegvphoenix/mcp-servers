"""Tests for OCR enrichment logic (_enrich_markdown_with_ocr)."""

import os
from unittest.mock import patch

import pytest

from server import _enrich_markdown_with_ocr


class TestEnrichMarkdownWithOcr:
    def test_replaces_image_with_ocr_text(self, tmp_path):
        img = tmp_path / "diagram.png"
        img.touch()
        md = f"Before\n![alt]({img})\nAfter"

        with patch("server._ocr_image_file", return_value="recognized text"):
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert "recognized text" in result
        assert "![alt]" not in result
        assert stats["images_ocr_ok"] == 1
        assert stats["images_total"] == 1
        assert stats["images_failed"] == []

    def test_file_not_found(self):
        md = "![alt](/nonexistent/image.png)"
        result, stats = _enrich_markdown_with_ocr(md, ["en"])
        assert "![alt]" not in result
        assert result.strip() == ""
        assert stats["images_ocr_ok"] == 0
        assert stats["images_missing"] == 1
        assert len(stats["images_failed"]) == 1

    def test_empty_ocr_result(self, tmp_path):
        img = tmp_path / "empty.png"
        img.touch()
        md = f"Text\n![x]({img})\nMore"

        with patch("server._ocr_image_file", return_value="   "):
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert "![x]" not in result
        assert stats["images_ocr_ok"] == 0
        assert stats["images_ocr_empty"] == 1
        assert "empty.png" in stats["images_failed"]

    def test_ocr_exception(self, tmp_path):
        img = tmp_path / "bad.png"
        img.touch()
        md = f"![x]({img})"

        with patch("server._ocr_image_file", side_effect=RuntimeError("OCR crashed")):
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert "![x]" not in result
        assert stats["images_ocr_ok"] == 0
        assert stats["images_ocr_error"] == 1
        assert "bad.png" in stats["images_failed"]

    def test_no_images(self):
        md = "Just plain text\nwith no images"
        result, stats = _enrich_markdown_with_ocr(md, ["en"])
        assert result == md
        assert stats["images_total"] == 0
        assert stats["images_failed"] == []

    def test_multiple_images(self, tmp_path):
        img1 = tmp_path / "a.png"
        img2 = tmp_path / "b.png"
        img1.touch()
        img2.touch()
        md = f"![first]({img1})\nMiddle\n![second]({img2})"

        def mock_ocr(path, langs=None):
            if "a.png" in path:
                return "alpha text"
            return "beta text"

        with patch("server._ocr_image_file", side_effect=mock_ocr):
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert "alpha text" in result
        assert "beta text" in result
        assert stats["images_ocr_ok"] == 2
        assert stats["images_total"] == 2

    def test_mixed_results(self, tmp_path):
        """One image recognized, one empty, one error."""
        img_ok = tmp_path / "ok.png"
        img_empty = tmp_path / "empty.png"
        img_err = tmp_path / "err.png"
        img_ok.touch()
        img_empty.touch()
        img_err.touch()
        md = f"![a]({img_ok})\n![b]({img_empty})\n![c]({img_err})\n![d](/missing.png)"

        def mock_ocr(path, langs=None):
            if "ok.png" in path:
                return "good text"
            if "empty.png" in path:
                return "  "
            raise RuntimeError("crash")

        with patch("server._ocr_image_file", side_effect=mock_ocr):
            result, stats = _enrich_markdown_with_ocr(md, ["en"])

        assert stats["images_total"] == 4
        assert stats["images_ocr_ok"] == 1
        assert stats["images_ocr_empty"] == 1
        assert stats["images_ocr_error"] == 1
        assert stats["images_missing"] == 1
        assert len(stats["images_failed"]) == 3
        assert "good text" in result
