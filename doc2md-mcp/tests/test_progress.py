"""Tests for progress reporting in convert_pdf_to_markdown and convert_all_pdfs_in_folder.

Verifies that ctx.report_progress is called with correct messages containing
pass numbers [1/N], [2/2], page counters, OCR image counters, and model-loading status.
"""

import asyncio
import os
import pathlib
import re
from dataclasses import dataclass, field
from unittest.mock import patch, AsyncMock, MagicMock

import pymupdf
import pytest

from server import (
    convert_pdf_to_markdown,
    convert_all_pdfs_in_folder,
    _to_markdown_paged,
    EXPORT_SUBFOLDER,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeContext:
    """Mimics mcp.server.fastmcp.Context with recorded progress calls."""
    progress_calls: list = field(default_factory=list)
    info_calls: list = field(default_factory=list)
    warning_calls: list = field(default_factory=list)

    async def report_progress(self, *, progress, total, message=""):
        self.progress_calls.append({
            "progress": progress,
            "total": total,
            "message": message,
        })

    async def info(self, msg):
        self.info_calls.append(msg)

    async def warning(self, msg):
        self.warning_calls.append(msg)


def _messages(ctx: FakeContext) -> list[str]:
    return [c["message"] for c in ctx.progress_calls]


def _make_text_pdf(path: pathlib.Path, pages: int = 3) -> pathlib.Path:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} content.")
    doc.save(str(path))
    doc.close()
    return path


def _make_image_pdf(path: pathlib.Path, pages: int = 1) -> pathlib.Path:
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 400), 1)
    pix.set_rect(pix.irect, (255, 255, 255, 255))
    img_bytes = pix.tobytes("png")
    for _ in range(pages):
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(50, 50, 450, 450), stream=img_bytes)
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# _to_markdown_paged — on_progress
# ---------------------------------------------------------------------------

class TestToMarkdownPagedProgress:
    def test_calls_progress_for_each_page(self, tmp_path):
        pdf = _make_text_pdf(tmp_path / "test.pdf", pages=4)
        calls: list[tuple[int, int]] = []

        _to_markdown_paged(str(pdf), 4, on_progress=lambda d, t: calls.append((d, t)))

        assert len(calls) == 4
        assert calls == [(1, 4), (2, 4), (3, 4), (4, 4)]

    def test_no_crash_without_on_progress(self, tmp_path):
        pdf = _make_text_pdf(tmp_path / "test.pdf", pages=2)
        result = _to_markdown_paged(str(pdf), 2, on_progress=None)
        assert len(result) > 0

    def test_single_page(self, tmp_path):
        pdf = _make_text_pdf(tmp_path / "test.pdf", pages=1)
        calls: list[tuple[int, int]] = []

        _to_markdown_paged(str(pdf), 1, on_progress=lambda d, t: calls.append((d, t)))

        assert calls == [(1, 1)]


# ---------------------------------------------------------------------------
# convert_pdf_to_markdown — progress messages (text PDF, no OCR)
# ---------------------------------------------------------------------------

class TestSinglePdfProgressNoOcr:
    @pytest.mark.asyncio
    async def test_progress_stages_text_only(self, tmp_path):
        """Text-only PDF must report: Hashing -> Detecting -> [1/1] Parse -> Saving -> Done."""
        pdf = _make_text_pdf(tmp_path / "doc.pdf", pages=3)
        ctx = FakeContext()

        result = await convert_pdf_to_markdown(str(pdf), ctx=ctx)

        assert "Converted successfully" in result
        msgs = _messages(ctx)

        assert any("Hashing" in m for m in msgs), f"Missing 'Hashing' stage, got: {msgs}"
        assert any("Detecting OCR" in m for m in msgs), f"Missing 'Detecting OCR' stage, got: {msgs}"
        assert any("[1/1]" in m and "Parse" in m for m in msgs), f"Missing '[1/1] Parse' stage, got: {msgs}"
        assert any("Saving" in m for m in msgs), f"Missing 'Saving' stage, got: {msgs}"
        assert any("Done" in m for m in msgs), f"Missing 'Done' stage, got: {msgs}"

        assert not any("[2/" in m for m in msgs), f"No pass 2 expected for text-only PDF, got: {msgs}"

    @pytest.mark.asyncio
    async def test_page_counter_in_parse(self, tmp_path):
        """Parse progress messages must include page counters like 'Parse 1/3p'."""
        pdf = _make_text_pdf(tmp_path / "doc.pdf", pages=3)
        ctx = FakeContext()

        await convert_pdf_to_markdown(str(pdf), ctx=ctx)

        parse_msgs = [m for m in _messages(ctx) if "Parse" in m and "[1/" in m]
        assert len(parse_msgs) >= 1, "Expected at least one Parse progress message"
        assert any("/3p" in m for m in parse_msgs), f"Expected page count '/3p' in parse messages: {parse_msgs}"

    @pytest.mark.asyncio
    async def test_progress_values_monotonic(self, tmp_path):
        """Progress percentage values must be monotonically non-decreasing."""
        pdf = _make_text_pdf(tmp_path / "doc.pdf", pages=3)
        ctx = FakeContext()

        await convert_pdf_to_markdown(str(pdf), ctx=ctx)

        values = [c["progress"] for c in ctx.progress_calls]
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1], (
                f"Progress must be non-decreasing: {values[i - 1]} -> {values[i]} "
                f"(messages: {_messages(ctx)[i - 1]} -> {_messages(ctx)[i]})"
            )

    @pytest.mark.asyncio
    async def test_starts_at_0_ends_at_100(self, tmp_path):
        pdf = _make_text_pdf(tmp_path / "doc.pdf", pages=2)
        ctx = FakeContext()

        await convert_pdf_to_markdown(str(pdf), ctx=ctx)

        values = [c["progress"] for c in ctx.progress_calls]
        assert values[0] == 0, f"Must start at 0, got {values[0]}"
        assert values[-1] == 100, f"Must end at 100, got {values[-1]}"

    @pytest.mark.asyncio
    async def test_pdf_name_in_messages(self, tmp_path):
        pdf = _make_text_pdf(tmp_path / "report.pdf", pages=2)
        ctx = FakeContext()

        await convert_pdf_to_markdown(str(pdf), ctx=ctx)

        msgs = _messages(ctx)
        for m in msgs:
            assert "report.pdf" in m, f"PDF name must be in every message, missing in: '{m}'"


# ---------------------------------------------------------------------------
# convert_pdf_to_markdown — progress messages (PDF with OCR)
# ---------------------------------------------------------------------------

class TestSinglePdfProgressWithOcr:
    @pytest.mark.asyncio
    async def test_two_pass_stages(self, tmp_path):
        """PDF with OCR must show [1/2] for parsing and [2/2] for OCR."""
        pdf = _make_image_pdf(tmp_path / "img.pdf", pages=2)
        ctx = FakeContext()

        with patch("server._ocr_image_file", return_value="text"):
            result = await convert_pdf_to_markdown(str(pdf), ocr="always", ctx=ctx)

        assert "Converted successfully" in result
        msgs = _messages(ctx)

        assert any("[1/2]" in m for m in msgs), f"Missing '[1/2]' pass, got: {msgs}"
        assert any("[2/2]" in m for m in msgs), f"Missing '[2/2]' pass, got: {msgs}"

    @pytest.mark.asyncio
    async def test_ocr_loading_model_message(self, tmp_path):
        """Before processing images, must report 'Loading OCR model'."""
        pdf = _make_image_pdf(tmp_path / "img.pdf", pages=1)
        ctx = FakeContext()

        with patch("server._ocr_image_file", return_value="text"):
            await convert_pdf_to_markdown(str(pdf), ocr="always", ctx=ctx)

        msgs = _messages(ctx)
        assert any("Loading OCR model" in m for m in msgs), (
            f"Missing 'Loading OCR model' message, got: {msgs}"
        )

    @pytest.mark.asyncio
    async def test_ocr_image_counter(self, tmp_path):
        """OCR phase must include image count like 'OCR 1/2img'."""
        pdf = _make_image_pdf(tmp_path / "img.pdf", pages=2)
        ctx = FakeContext()

        with patch("server._ocr_image_file", return_value="text"):
            await convert_pdf_to_markdown(str(pdf), ocr="always", ctx=ctx)

        msgs = _messages(ctx)
        ocr_msgs = [m for m in msgs if "OCR" in m and "img" in m and "[2/2]" in m]
        assert len(ocr_msgs) >= 1, f"Expected OCR image counter messages, got: {msgs}"

    @pytest.mark.asyncio
    async def test_ocr_done_message(self, tmp_path):
        """After all images processed, must report 'OCR done'."""
        pdf = _make_image_pdf(tmp_path / "img.pdf", pages=1)
        ctx = FakeContext()

        with patch("server._ocr_image_file", return_value="text"):
            await convert_pdf_to_markdown(str(pdf), ocr="always", ctx=ctx)

        msgs = _messages(ctx)
        assert any("OCR done" in m for m in msgs), f"Missing 'OCR done' message, got: {msgs}"

    @pytest.mark.asyncio
    async def test_progress_ends_at_100_with_ocr(self, tmp_path):
        pdf = _make_image_pdf(tmp_path / "img.pdf", pages=1)
        ctx = FakeContext()

        with patch("server._ocr_image_file", return_value="text"):
            await convert_pdf_to_markdown(str(pdf), ocr="always", ctx=ctx)

        values = [c["progress"] for c in ctx.progress_calls]
        assert values[-1] == 100

    @pytest.mark.asyncio
    async def test_img_count_in_loading_message(self, tmp_path):
        """Loading message should contain total image count like '(2img)'."""
        pdf = _make_image_pdf(tmp_path / "img.pdf", pages=2)
        ctx = FakeContext()

        with patch("server._ocr_image_file", return_value="text"):
            await convert_pdf_to_markdown(str(pdf), ocr="always", ctx=ctx)

        msgs = _messages(ctx)
        loading_msgs = [m for m in msgs if "Loading OCR model" in m]
        assert any("img" in m for m in loading_msgs), (
            f"Loading message must include image count, got: {loading_msgs}"
        )


# ---------------------------------------------------------------------------
# convert_all_pdfs_in_folder — progress messages
# ---------------------------------------------------------------------------

class TestBatchPdfProgress:
    @pytest.mark.asyncio
    async def test_batch_reports_per_file_progress(self, tmp_path):
        """Batch conversion must report progress for each file."""
        pdf_dir = tmp_path / "batch"
        pdf_dir.mkdir()
        for name in ["a", "b"]:
            _make_text_pdf(pdf_dir / f"{name}.pdf", pages=2)

        ctx = FakeContext()
        result = await convert_all_pdfs_in_folder(str(pdf_dir), ctx=ctx)

        assert "Converted: 2" in result
        msgs = _messages(ctx)

        assert any("a.pdf" in m for m in msgs), f"Missing a.pdf in progress, got: {msgs}"
        assert any("b.pdf" in m for m in msgs), f"Missing b.pdf in progress, got: {msgs}"

    @pytest.mark.asyncio
    async def test_batch_shows_scanning(self, tmp_path):
        pdf_dir = tmp_path / "batch"
        pdf_dir.mkdir()
        _make_text_pdf(pdf_dir / "x.pdf", pages=1)

        ctx = FakeContext()
        await convert_all_pdfs_in_folder(str(pdf_dir), ctx=ctx)

        msgs = _messages(ctx)
        assert any("Scanning" in m for m in msgs), f"Missing 'Scanning' message, got: {msgs}"

    @pytest.mark.asyncio
    async def test_batch_shows_complete(self, tmp_path):
        pdf_dir = tmp_path / "batch"
        pdf_dir.mkdir()
        _make_text_pdf(pdf_dir / "x.pdf", pages=1)

        ctx = FakeContext()
        await convert_all_pdfs_in_folder(str(pdf_dir), ctx=ctx)

        msgs = _messages(ctx)
        assert any("Complete" in m for m in msgs), f"Missing 'Complete' message, got: {msgs}"

    @pytest.mark.asyncio
    async def test_batch_parse_pass_label(self, tmp_path):
        """Batch must include [1/1] or [1/2] for parsing pass."""
        pdf_dir = tmp_path / "batch"
        pdf_dir.mkdir()
        _make_text_pdf(pdf_dir / "doc.pdf", pages=3)

        ctx = FakeContext()
        await convert_all_pdfs_in_folder(str(pdf_dir), ctx=ctx)

        msgs = _messages(ctx)
        assert any("[1/1]" in m for m in msgs), f"Missing '[1/1]' parse pass, got: {msgs}"

    @pytest.mark.asyncio
    async def test_batch_ocr_two_passes(self, tmp_path):
        """Batch with OCR files must show [1/2] and [2/2]."""
        pdf_dir = tmp_path / "batch"
        pdf_dir.mkdir()
        _make_image_pdf(pdf_dir / "img.pdf", pages=2)

        ctx = FakeContext()

        with patch("server._ocr_image_file", return_value="text"):
            result = await convert_all_pdfs_in_folder(str(pdf_dir), ocr="always", ctx=ctx)

        assert "Converted: 1" in result
        msgs = _messages(ctx)
        assert any("[1/2]" in m for m in msgs), f"Missing '[1/2]' in batch OCR, got: {msgs}"
        assert any("[2/2]" in m for m in msgs), f"Missing '[2/2]' in batch OCR, got: {msgs}"

    @pytest.mark.asyncio
    async def test_batch_skipped_file_message(self, tmp_path):
        """Already-converted files must show 'Skipped' in progress."""
        pdf_dir = tmp_path / "batch"
        pdf_dir.mkdir()
        _make_text_pdf(pdf_dir / "doc.pdf", pages=1)

        await convert_all_pdfs_in_folder(str(pdf_dir))

        ctx = FakeContext()
        await convert_all_pdfs_in_folder(str(pdf_dir), ctx=ctx)

        msgs = _messages(ctx)
        assert any("Skipped" in m for m in msgs), f"Missing 'Skipped' for cached file, got: {msgs}"

    @pytest.mark.asyncio
    async def test_batch_ocr_loading_model(self, tmp_path):
        """Batch OCR must report 'Loading OCR model' before image processing."""
        pdf_dir = tmp_path / "batch"
        pdf_dir.mkdir()
        _make_image_pdf(pdf_dir / "img.pdf", pages=1)

        ctx = FakeContext()

        with patch("server._ocr_image_file", return_value="text"):
            await convert_all_pdfs_in_folder(str(pdf_dir), ocr="always", ctx=ctx)

        msgs = _messages(ctx)
        assert any("Loading OCR model" in m for m in msgs), (
            f"Missing 'Loading OCR model' in batch, got: {msgs}"
        )


# ---------------------------------------------------------------------------
# convert_pdf_to_markdown — no ctx (should not fail)
# ---------------------------------------------------------------------------

class TestSinglePdfNoCtx:
    @pytest.mark.asyncio
    async def test_works_without_ctx(self, tmp_path):
        """Converting without ctx must not crash."""
        pdf = _make_text_pdf(tmp_path / "noctx.pdf", pages=2)
        result = await convert_pdf_to_markdown(str(pdf))
        assert "Converted successfully" in result
