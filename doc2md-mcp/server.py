"""DOC2MD — MCP server for converting documents (PDF, Swagger/OpenAPI, Web) to Markdown."""

__version__ = "0.4.0"

import asyncio
import getpass
import hashlib
import json
import logging
import logging.handlers
import os
import pathlib
import platform
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import pymupdf
import pymupdf4llm
import yaml
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("DOC2MD")

OUTPUT_DIR = os.environ.get("DOC2MD_OUTPUT_DIR", "")
EXPORT_SUBFOLDER = "doc2md_export"
LOG_FILENAME = "doc2md_log.json"

# ---------------------------------------------------------------------------
# Server audit log (JSONL, daily rotation, 90-day retention)
# ---------------------------------------------------------------------------

_SERVER_LOG_DIR = pathlib.Path(__file__).resolve().parent / "logs"
_SERVER_LOG_DIR.mkdir(exist_ok=True)

_audit_logger = logging.getLogger("doc2md.audit")
_audit_logger.setLevel(logging.DEBUG)
_audit_logger.propagate = False
_audit_handler = logging.handlers.TimedRotatingFileHandler(
    str(_SERVER_LOG_DIR / "doc2md_server.log"),
    when="midnight",
    backupCount=90,
    encoding="utf-8",
)
_audit_handler.setFormatter(logging.Formatter("%(message)s"))
_audit_logger.addHandler(_audit_handler)


def _extract_client_info(ctx: "Context | None") -> dict:
    """Safely extract client metadata from MCP Context."""
    info: dict = {
        "user": getpass.getuser(),
        "machine": platform.node(),
        "pid": os.getpid(),
        "client_id": None,
        "client_app": None,
        "client_version": None,
        "request_id": None,
    }
    if not ctx:
        return info
    try:
        info["client_id"] = ctx.client_id
    except Exception:
        pass
    try:
        info["request_id"] = ctx.request_id
    except Exception:
        pass
    try:
        params = ctx.session.client_params
        if params and params.clientInfo:
            info["client_app"] = params.clientInfo.name
            info["client_version"] = params.clientInfo.version
    except Exception:
        pass
    return info


class _AuditOp:
    """Tracks a single tool invocation: writes 'start' on creation,
    provides .end_ok() / .end_error() / .end_skip() for completion."""

    __slots__ = ("tool", "ctx", "args", "op_id", "_t0")

    def __init__(self, tool: str, ctx: "Context | None" = None, args: dict | None = None):
        self.tool = tool
        self.ctx = ctx
        self.args = args or {}
        self.op_id = uuid.uuid4().hex[:12]
        self._t0 = time.perf_counter()
        try:
            entry: dict = {
                "ts": _now_iso(), "level": "INFO", "server_version": __version__,
                "tool": tool, "status": "start", "operation_id": self.op_id,
                **_extract_client_info(ctx), "args": self.args,
            }
            _audit_logger.info(json.dumps(entry, ensure_ascii=False))
        except Exception:
            pass

    def _elapsed(self) -> float:
        return round(time.perf_counter() - self._t0, 2)

    def _write(self, status: str, result_summary: str = "",
               error: str = "", extra: dict | None = None) -> None:
        try:
            entry: dict = {
                "ts": _now_iso(),
                "level": "ERROR" if error else "INFO",
                "server_version": __version__,
                "tool": self.tool,
                "status": status,
                "operation_id": self.op_id,
                "duration_sec": self._elapsed(),
                **_extract_client_info(self.ctx),
                "args": self.args,
                "result_summary": result_summary,
            }
            if error:
                entry["error"] = error[:2000]
            if extra:
                entry["extra"] = extra
            _audit_logger.info(json.dumps(entry, ensure_ascii=False))
        except Exception:
            pass

    def end_ok(self, result_summary: str = "", extra: dict | None = None) -> None:
        self._write("end_ok", result_summary=result_summary, extra=extra)

    def end_error(self, result_summary: str = "", error: str = "") -> None:
        self._write("end_error", result_summary=result_summary, error=error)

    def end_skip(self, result_summary: str = "unchanged, skipped") -> None:
        self._write("end_skip", result_summary=result_summary)


# Backward-compatible aliases used by tests
def _audit_start(tool: str, ctx: "Context | None" = None, args: dict | None = None) -> str:
    op = _AuditOp(tool, ctx, args)
    return op.op_id

def _audit_log(tool: str, status: str, duration_sec: float,
               ctx: "Context | None" = None, args: dict | None = None,
               result_summary: str = "", error: str = "",
               extra: dict | None = None, operation_id: str = "") -> None:
    try:
        entry: dict = {
            "ts": _now_iso(), "level": "ERROR" if error else "INFO",
            "server_version": __version__, "tool": tool, "status": status,
            "operation_id": operation_id, "duration_sec": round(duration_sec, 2),
            **_extract_client_info(ctx), "args": args or {},
            "result_summary": result_summary,
        }
        if error:
            entry["error"] = error[:2000]
        if extra:
            entry["extra"] = extra
        _audit_logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Conversion log helpers
# ---------------------------------------------------------------------------

def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _export_dir_for(pdf_or_folder: str) -> pathlib.Path:
    p = pathlib.Path(pdf_or_folder)
    folder = p.parent if p.is_file() else p
    return folder / EXPORT_SUBFOLDER


def _log_path_for(pdf_or_folder: str) -> str:
    return str(_export_dir_for(pdf_or_folder) / LOG_FILENAME)


def _load_log(log_path: str) -> dict:
    if os.path.isfile(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def _save_log(log_path: str, log: dict) -> None:
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def _write_markdown(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_already_converted(log: dict, pdf_path: str, current_hash: str) -> bool:
    entry = log.get(pdf_path)
    if not entry:
        return False
    if entry.get("status") != "ok":
        return False
    if entry.get("source_hash") != current_hash:
        return False
    out = entry.get("output_path", "")
    if not os.path.isfile(out):
        return False
    return True


def _check_skip(log: dict, log_file: str, source_key: str,
                current_hash: str, force: bool) -> str | None:
    """Return skip message if already converted, else None. Updates log on skip."""
    if force or not _is_already_converted(log, source_key, current_hash):
        return None
    entry = log[source_key]
    entry["last_checked_at"] = _now_iso()
    entry["skip_count"] = entry.get("skip_count", 0) + 1
    _save_log(log_file, log)
    return (
        f"Skipped (already converted, file unchanged).\n"
        f"Output: {entry['output_path']}\n"
        f"Converted at: {entry['converted_at']}"
    )


def _format_ocr_label(ocr_stats: dict, compact: bool = False) -> str:
    """Build a human-readable OCR summary label from *ocr_stats*.

    *compact=False* (single file):  ``" (OCR: 2/3 recognized, 1 missing)"``
    *compact=True*  (batch):        ``"+OCR(2/3,1miss)"``
    Returns empty string when *ocr_stats* is empty or has no images.
    """
    ok = ocr_stats.get("images_ocr_ok", 0)
    total = ocr_stats.get("images_total", 0)
    if not total:
        return ""
    missing = ocr_stats.get("images_missing", 0)
    ocr_err = ocr_stats.get("images_ocr_error", 0)
    empty = ocr_stats.get("images_ocr_empty", 0)
    failed = total - ok

    if compact:
        label = f"+OCR({ok}/{total}"
        if failed:
            parts = []
            if missing:
                parts.append(f"{missing}miss")
            if ocr_err:
                parts.append(f"{ocr_err}err")
            if empty:
                parts.append(f"{empty}empty")
            label += "," + "/".join(parts)
        label += ")"
        return label

    label = f" (OCR: {ok}/{total} recognized"
    if failed:
        parts = []
        if missing:
            parts.append(f"{missing} missing")
        if ocr_err:
            parts.append(f"{ocr_err} error")
        if empty:
            parts.append(f"{empty} empty")
        label += ", " + "/".join(parts)
    label += ")"
    return label


def _pdf_metadata(pdf_path: str) -> dict:
    try:
        doc = pymupdf.open(pdf_path)
        meta = doc.metadata or {}
        info = {
            "pages": doc.page_count,
            "pdf_title": meta.get("title", ""),
            "pdf_author": meta.get("author", ""),
            "pdf_creator": meta.get("creator", ""),
            "pdf_created": meta.get("creationDate", ""),
        }
        doc.close()
        return {k: v for k, v in info.items() if v}
    except Exception:
        return {}


def _record_entry(
    log: dict,
    source_path: str,
    output_path: str,
    source_hash: str,
    status: str,
    chars: int = 0,
    lines: int = 0,
    error: str = "",
    duration_sec: float = 0.0,
    extra: dict | None = None,
) -> dict:
    entry = {
        "source_path": source_path,
        "output_path": output_path,
        "source_hash": source_hash,
        "source_size_bytes": os.path.getsize(source_path) if os.path.isfile(source_path) else 0,
        "status": status,
        "chars": chars,
        "lines": lines,
        "duration_sec": round(duration_sec, 2),
        "converted_at": _now_iso(),
        "converted_by": getpass.getuser(),
        "machine": platform.node(),
    }
    if extra:
        entry.update(extra)
    if error:
        entry["error"] = error
    log[source_path] = entry
    return entry


def _record_and_save(
    log_file: str,
    log: dict,
    source_path: str,
    output_path: str,
    source_hash: str,
    status: str,
    **kwargs,
) -> dict:
    entry = _record_entry(log, source_path, output_path, source_hash, status, **kwargs)
    _save_log(log_file, log)
    return entry


# ---------------------------------------------------------------------------
# Output path resolution
# ---------------------------------------------------------------------------

def _resolve_output_path(pdf_path: str, output_path: str | None) -> str:
    if output_path:
        return output_path
    p = pathlib.Path(pdf_path)
    if OUTPUT_DIR:
        return str(pathlib.Path(OUTPUT_DIR) / (p.stem + ".md"))
    return str(_export_dir_for(pdf_path) / (p.stem + ".md"))


# ---------------------------------------------------------------------------
# OCR helpers (lazy-loaded EasyOCR)
# ---------------------------------------------------------------------------

_ocr_reader: object | None = None
_ocr_reader_langs: list[str] | None = None


def _get_ocr_reader(languages: list[str] | None = None) -> object:
    global _ocr_reader, _ocr_reader_langs
    langs = languages or ["en"]
    if _ocr_reader is None or _ocr_reader_langs != langs:
        import easyocr
        _ocr_reader = easyocr.Reader(langs, gpu=False, verbose=False)
        _ocr_reader_langs = langs
    return _ocr_reader


def _ocr_image_file(image_path: str, languages: list[str] | None = None) -> str:
    import io
    import logging
    import numpy as np
    from PIL import Image

    img = np.array(Image.open(image_path))
    reader = _get_ocr_reader(languages)

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.WARNING)
    cv_logger = logging.getLogger("cv2")
    cv_logger.addHandler(handler)
    try:
        results = reader.readtext(img)
    finally:
        cv_logger.removeHandler(handler)

    cv_warnings = buf.getvalue().strip()
    if cv_warnings:
        import sys
        print(f"[OCR cv2 warn] {image_path}: {cv_warnings}", file=sys.stderr)

    return " ".join(item[1] for item in results if item[1].strip())


_IMG_REF_RE = re.compile(r"!\[([^\]]*)\]\(((?:[^()]*|\([^()]*\))*)\)")

_OCR_IMAGE_MIN_AREA = 100_000  # px — skip icons/logos, OCR only meaningful images


def _find_ocr_pages(pdf_path: str) -> list[int]:
    """Return 0-based page indices that contain large images worth OCR-ing."""
    doc = pymupdf.open(pdf_path)
    try:
        ocr_pages: list[int] = []
        for i in range(doc.page_count):
            page = doc[i]
            for img in page.get_images():
                xref = img[0]
                pix = pymupdf.Pixmap(doc, xref)
                area = pix.width * pix.height
                pix = None
                if area >= _OCR_IMAGE_MIN_AREA:
                    ocr_pages.append(i)
                    break
        return ocr_pages
    finally:
        doc.close()


def _snapshot_images(directory: str) -> set[str]:
    """Return set of image file paths currently in *directory*."""
    exts = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
    result: set[str] = set()
    if os.path.isdir(directory):
        for f in os.listdir(directory):
            if pathlib.Path(f).suffix.lower() in exts:
                result.add(os.path.join(directory, f))
    return result


def _cleanup_new_images(before: set[str], directory: str) -> int:
    """Delete image files that appeared in *directory* after *before* snapshot."""
    after = _snapshot_images(directory)
    new_images = after - before
    removed = 0
    for p in new_images:
        try:
            os.remove(p)
            removed += 1
        except OSError:
            pass
    return removed


_SAFE_DIRNAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f\xa0]')


def _image_subdir(export_dir: str, pdf_path: str) -> str:
    """Return path to a subdirectory for images extracted from a PDF."""
    stem = pathlib.Path(pdf_path).stem
    safe = _SAFE_DIRNAME_RE.sub(" ", stem)
    safe = re.sub(r"\s+", " ", safe).strip("_. ")
    return os.path.join(export_dir, safe or "images")


def _cleanup_recognized_images(ok_paths: list[str]) -> int:
    """Delete only the images that were successfully OCR'd."""
    removed = 0
    for p in ok_paths:
        try:
            os.remove(p)
            removed += 1
        except OSError:
            pass
    return removed


def _to_markdown_paged(
    pdf_path: str,
    page_count: int,
    on_progress: "Callable[[int, int], None] | None" = None,
    **mk_kwargs,
) -> str:
    """Convert PDF to markdown page-by-page, calling *on_progress* between pages."""
    doc = pymupdf.open(pdf_path)
    hdr_info = None
    try:
        import pymupdf4llm.helpers.pymupdf_rag as _rag
        md_reader = _rag.IdentifyHeaders(doc)
        hdr_info = md_reader
    except Exception:
        pass

    chunks: list[str] = []
    for page_no in range(page_count):
        kwargs = {**mk_kwargs, "pages": [page_no]}
        if hdr_info is not None:
            kwargs["hdr_info"] = hdr_info
        part = pymupdf4llm.to_markdown(doc, **kwargs)
        chunks.append(part)
        if on_progress:
            on_progress(page_no + 1, page_count)
    doc.close()
    return "\n".join(chunks)


def _enrich_markdown_with_ocr(
    md_text: str,
    languages: list[str] | None = None,
    on_progress: "Callable[[int, int], None] | None" = None,
) -> tuple[str, dict]:
    """Replace image references with OCR text.

    Args:
        md_text: Markdown text containing image references.
        languages: Language codes for OCR engine.
        on_progress: Optional callback ``(processed, total)`` invoked after
            each image is handled.  Useful for live progress reporting.

    Returns (enriched_markdown, ocr_stats) where ocr_stats is a dict:
        images_total      — image refs found in markdown
        images_ocr_ok     — successfully recognized (non-empty text)
        images_ocr_empty  — OCR returned empty/whitespace
        images_ocr_error  — OCR raised an exception
        images_missing    — image file not found on disk
        images_skipped_small — images below _OCR_IMAGE_MIN_AREA threshold
        images_failed     — list of filenames that were not recognized
        images_ok_paths   — list of full paths of successfully recognized images
                            (used internally for selective cleanup, excluded from log)
        errors_detail     — list of {file, reason, detail} dicts for every failure
    """
    matches = list(_IMG_REF_RE.finditer(md_text))
    total_images = len(matches)

    stats: dict = {
        "images_total": 0,
        "images_ocr_ok": 0,
        "images_ocr_empty": 0,
        "images_ocr_error": 0,
        "images_missing": 0,
        "images_skipped_small": 0,
        "images_failed": [],
        "images_ok_paths": [],
        "errors_detail": [],
    }

    if not matches:
        return md_text, stats

    if on_progress:
        on_progress(0, total_images)
    _get_ocr_reader(languages)

    def _process_image(img_path: str) -> str:
        img_name = os.path.basename(img_path)
        stats["images_total"] += 1
        if not os.path.isfile(img_path):
            stats["images_missing"] += 1
            stats["images_failed"].append(img_name)
            stats["errors_detail"].append({
                "file": img_name,
                "path": img_path,
                "reason": "missing",
                "detail": f"File not found on disk: {img_path}",
            })
            return ""
        try:
            from PIL import Image
            with Image.open(img_path) as pil_img:
                w, h = pil_img.size
            if w * h < _OCR_IMAGE_MIN_AREA:
                stats["images_skipped_small"] += 1
                return ""
        except Exception:
            pass
        try:
            ocr_text = _ocr_image_file(img_path, languages)
        except Exception as exc:
            stats["images_ocr_error"] += 1
            stats["images_failed"].append(img_name)
            stats["errors_detail"].append({
                "file": img_name,
                "path": img_path,
                "reason": "ocr_error",
                "detail": f"{type(exc).__name__}: {exc}",
            })
            return ""
        if not ocr_text.strip():
            stats["images_ocr_empty"] += 1
            stats["images_failed"].append(img_name)
            stats["errors_detail"].append({
                "file": img_name,
                "path": img_path,
                "reason": "ocr_empty",
                "detail": "OCR returned empty/whitespace text",
            })
            return ""
        stats["images_ocr_ok"] += 1
        stats["images_ok_paths"].append(img_path)
        return ocr_text.strip()

    parts: list[str] = []
    last_end = 0
    for idx, match in enumerate(matches):
        parts.append(md_text[last_end:match.start()])
        replacement = _process_image(match.group(2))
        parts.append(replacement)
        last_end = match.end()
        if on_progress:
            on_progress(idx + 1, total_images)
    parts.append(md_text[last_end:])

    return "".join(parts), stats


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def convert_pdf_to_markdown(
    pdf_path: str,
    output_path: str | None = None,
    page_chunks: bool = False,
    force: bool = False,
    ocr: str = "auto",
    ocr_languages: str = "en",
    ctx: Context | None = None,
) -> str:
    """Convert a PDF file to Markdown and save the result.

    Args:
        pdf_path: Absolute path to the PDF file.
        output_path: Where to save the .md file. Defaults to export subfolder.
        page_chunks: If True, insert page separators in the output.
        force: If True, re-convert even if already converted.
        ocr: OCR mode — "auto" (detect images automatically, default), "always", or "off".
        ocr_languages: Comma-separated language codes for OCR, e.g. "en" or "en,ru". Default is "en".
    """
    audit = _AuditOp("convert_pdf_to_markdown", ctx, {"pdf_path": pdf_path, "ocr": ocr, "force": force})
    pdf_path = os.path.normpath(pdf_path)
    pdf_name = pathlib.Path(pdf_path).name
    if not os.path.isfile(pdf_path):
        result = f"Error: file not found: {pdf_path}"
        audit.end_error(result_summary=result)
        return result

    if ctx:
        await ctx.report_progress(progress=0, total=100, message=f"Hashing: {pdf_name}")

    log_file = _log_path_for(pdf_path)
    log = _load_log(log_file)
    current_hash = await asyncio.to_thread(_file_hash, pdf_path)

    skip_msg = _check_skip(log, log_file, pdf_path, current_hash, force)
    if skip_msg:
        audit.end_skip()
        return skip_msg

    out = _resolve_output_path(pdf_path, output_path)
    export_dir = str(pathlib.Path(out).parent)

    if ctx:
        await ctx.report_progress(progress=5, total=100, message=f"Detecting OCR pages: {pdf_name}")

    page_count = 0
    ocr_pages: list[int] = []
    if ocr == "always":
        doc_tmp = pymupdf.open(pdf_path)
        page_count = doc_tmp.page_count
        ocr_pages = list(range(page_count))
        doc_tmp.close()
    elif ocr == "auto":
        ocr_pages = await asyncio.to_thread(_find_ocr_pages, pdf_path)
    if not page_count:
        try:
            doc_tmp = pymupdf.open(pdf_path)
            page_count = doc_tmp.page_count
            doc_tmp.close()
        except Exception:
            pass
    use_ocr = bool(ocr_pages)
    pages_label = f" ({page_count}p)" if page_count else ""
    image_dir = ""

    total_passes = 2 if use_ocr else 1

    if ctx:
        stage = f"[1/{total_passes}] Parsing{' + img' if use_ocr else ''}{pages_label}: {pdf_name}"
        await ctx.report_progress(progress=10, total=100, message=stage)

    t0 = time.perf_counter()
    duration_parse = 0.0
    duration_ocr = 0.0
    try:
        mk_kwargs: dict = {"page_chunks": page_chunks}
        if use_ocr:
            image_dir = _image_subdir(export_dir, pdf_path)
            os.makedirs(image_dir, exist_ok=True)
            mk_kwargs.update(write_images=True, force_text=True, image_path=image_dir, dpi=200)

        t_parse = time.perf_counter()
        if ctx and page_count > 1:
            parse_progress: list = [0, page_count]

            def _on_parse_progress(done: int, total: int) -> None:
                parse_progress[0] = done
                parse_progress[1] = total

            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                None,
                lambda: _to_markdown_paged(pdf_path, page_count, _on_parse_progress, **mk_kwargs),
            )
            while not future.done():
                await asyncio.sleep(0.3)
                done, total = parse_progress
                pct = 10 + int(30 * done / max(total, 1))
                await ctx.report_progress(
                    progress=pct, total=100,
                    message=f"[1/{total_passes}] Parse {done}/{total}p{' + img' if use_ocr else ''}: {pdf_name}",
                )
            md_text = future.result()
        else:
            md_text = await asyncio.to_thread(pymupdf4llm.to_markdown, pdf_path, **mk_kwargs)
        duration_parse = time.perf_counter() - t_parse

        ocr_stats: dict = {}
        if use_ocr:
            if ctx:
                await ctx.report_progress(progress=40, total=100, message=f"[2/2] Loading OCR model{pages_label}: {pdf_name}")

            langs = [l.strip() for l in ocr_languages.split(",")]

            t_ocr = time.perf_counter()

            async def _ocr_with_progress(text: str, langs: list[str]) -> tuple[str, dict]:
                loop = asyncio.get_running_loop()
                result_holder: list = [None]

                def _run():
                    def _on_progress(done: int, total: int) -> None:
                        pct = 40 + int(50 * done / max(total, 1))
                        result_holder[0] = (done, total, pct)

                    return _enrich_markdown_with_ocr(text, langs, on_progress=_on_progress)

                future = loop.run_in_executor(None, _run)
                while not future.done():
                    await asyncio.sleep(0.5)
                    if result_holder[0] and ctx:
                        done, total, pct = result_holder[0]
                        if done == 0:
                            msg = f"[2/2] Loading OCR model ({total}img){pages_label}: {pdf_name}"
                        else:
                            msg = f"[2/2] OCR {done}/{total}img{pages_label}: {pdf_name}"
                        await ctx.report_progress(progress=pct, total=100, message=msg)
                return future.result()

            md_text, ocr_stats = await _ocr_with_progress(md_text, langs)
            duration_ocr = time.perf_counter() - t_ocr

            if ctx:
                await ctx.report_progress(progress=90, total=100, message=f"[2/2] OCR done{pages_label}: {pdf_name}")

            ok_paths = ocr_stats.pop("images_ok_paths", [])
            _cleanup_recognized_images(ok_paths)
            if os.path.isdir(image_dir) and not os.listdir(image_dir):
                os.rmdir(image_dir)
    except Exception as e:
        duration = time.perf_counter() - t0
        pdf_extra = {"pymupdf4llm_version": getattr(pymupdf4llm, "__version__", "unknown"), "ocr": use_ocr, **_pdf_metadata(pdf_path)}
        _record_and_save(log_file, log, pdf_path, out, current_hash, "error", error=str(e), duration_sec=duration, extra=pdf_extra)
        result = f"Error converting PDF: {e}"
        audit.end_error(error=str(e))
        return result
    duration = time.perf_counter() - t0

    if ctx:
        await ctx.report_progress(progress=95, total=100, message=f"Saving{pages_label}: {pdf_name}")

    _write_markdown(out, md_text)

    chars, lines = len(md_text), len(md_text.splitlines())
    errors_detail = ocr_stats.pop("errors_detail", [])
    pdf_extra = {
        "pymupdf4llm_version": getattr(pymupdf4llm, "__version__", "unknown"),
        "ocr": use_ocr,
        "duration_parse_sec": round(duration_parse, 2),
        "duration_ocr_sec": round(duration_ocr, 2),
        **ocr_stats,
        **_pdf_metadata(pdf_path),
    }
    if use_ocr and ocr_stats.get("images_failed") and os.path.isdir(image_dir):
        pdf_extra["images_dir"] = image_dir
    _record_and_save(log_file, log, pdf_path, out, current_hash, "ok", chars=chars, lines=lines, duration_sec=duration, extra=pdf_extra)

    if ctx:
        await ctx.report_progress(progress=100, total=100, message=f"Done{pages_label}: {pdf_name}")

    ocr_label = _format_ocr_label(ocr_stats) if use_ocr else ""

    duration_detail = f"Duration: {duration:.1f}s (parse: {duration_parse:.1f}s"
    if duration_ocr > 0:
        duration_detail += f", OCR: {duration_ocr:.1f}s"
    duration_detail += ")"
    result_lines = [
        f"Converted successfully{ocr_label}.",
        f"Output: {out}",
        f"Size: {chars} chars ({lines} lines)",
        duration_detail,
    ]
    if use_ocr and os.path.isdir(image_dir) and ocr_stats.get("images_failed"):
        result_lines.append(f"Unrecognized images dir: {image_dir}")
    if errors_detail:
        result_lines.append("Error details (first 10):")
        for ed in errors_detail[:10]:
            result_lines.append(f"  [{ed['reason']}] {ed['file']}: {ed['detail']}")
        if len(errors_detail) > 10:
            result_lines.append(f"  ... and {len(errors_detail) - 10} more")

    result = "\n".join(result_lines)
    _audit_extra = {
        "chars": chars, "lines": lines, "pages": pdf_extra.get("pages"),
        "ocr": use_ocr,
        "duration_parse_sec": round(duration_parse, 2),
        "duration_ocr_sec": round(duration_ocr, 2),
        **{k: v for k, v in ocr_stats.items() if k.startswith("images_")},
        **{k: v for k, v in pdf_extra.items() if k.startswith("pdf_")},
        "output_path": out,
    }
    _audit_summary = f"{chars} chars, {lines} lines, {duration:.1f}s"
    if use_ocr:
        _audit_summary += f", OCR {ocr_stats.get('images_ocr_ok', 0)}/{ocr_stats.get('images_total', 0)}"
    audit.end_ok(_audit_summary, extra=_audit_extra)
    return result


@mcp.tool()
async def convert_all_pdfs_in_folder(
    folder_path: str,
    output_folder: str | None = None,
    recursive: bool = False,
    force: bool = False,
    ocr: str = "auto",
    ocr_languages: str = "en",
    ctx: Context | None = None,
) -> str:
    """Convert all PDF files in a folder to Markdown.

    Args:
        folder_path: Absolute path to the folder with PDFs.
        output_folder: Where to save .md files. Defaults to export subfolder.
        recursive: If True, search subfolders too.
        force: If True, re-convert even if already converted.
        ocr: OCR mode — "auto" (detect images automatically, default), "always", or "off".
        ocr_languages: Comma-separated language codes for OCR, e.g. "en" or "en,ru".
    """
    audit = _AuditOp("convert_all_pdfs_in_folder", ctx, {"folder_path": folder_path, "recursive": recursive, "ocr": ocr, "force": force})
    folder = pathlib.Path(os.path.normpath(folder_path))
    if not folder.is_dir():
        result = f"Error: directory not found: {folder}"
        audit.end_error(result_summary=result)
        return result

    pattern = "**/*.pdf" if recursive else "*.pdf"
    pdf_files = sorted(folder.glob(pattern))

    if not pdf_files:
        result = f"No PDF files found in {folder}"
        audit.end_ok(result_summary=result)
        return result

    if ctx:
        await ctx.info(f"Found {len(pdf_files)} PDF files in {folder}")
        await ctx.report_progress(progress=0, total=len(pdf_files), message="Scanning...")

    langs = [l.strip() for l in ocr_languages.split(",")]
    results = []
    converted = 0
    skipped = 0
    failed = 0

    for i, pdf in enumerate(pdf_files):
        pdf_str = str(pdf)
        if ctx:
            await ctx.report_progress(progress=i, total=len(pdf_files), message=f"Hashing: {pdf.name}")
        log_file = _log_path_for(pdf_str)
        log = _load_log(log_file)
        current_hash = await asyncio.to_thread(_file_hash, pdf_str)

        if output_folder:
            rel = pdf.relative_to(folder)
            out = str(pathlib.Path(output_folder) / rel.with_suffix(".md"))
        else:
            out = str(_export_dir_for(pdf_str) / (pdf.stem + ".md"))

        if _check_skip(log, log_file, pdf_str, current_hash, force):
            skipped += 1
            results.append(f"SKIP: {pdf.name} (unchanged since {log[pdf_str]['converted_at']})")
            if ctx:
                await ctx.info(f"[{i+1}/{len(pdf_files)}] SKIP: {pdf.name} (unchanged)")
                await ctx.report_progress(progress=i + 1, total=len(pdf_files), message=f"Skipped: {pdf.name}")
            continue

        if ctx:
            await ctx.info(f"[{i+1}/{len(pdf_files)}] Converting: {pdf.name}...")
            await ctx.report_progress(progress=i, total=len(pdf_files), message=f"Detecting OCR pages: {pdf.name}")

        page_count = 0
        if ocr == "always":
            doc_tmp = pymupdf.open(pdf_str)
            page_count = doc_tmp.page_count
            ocr_pages = list(range(page_count))
            doc_tmp.close()
        elif ocr == "auto":
            ocr_pages = await asyncio.to_thread(_find_ocr_pages, pdf_str)
        else:
            ocr_pages = []
        if not page_count:
            try:
                doc_tmp = pymupdf.open(pdf_str)
                page_count = doc_tmp.page_count
                doc_tmp.close()
            except Exception:
                pass
        use_ocr = bool(ocr_pages)
        total_passes = 2 if use_ocr else 1
        pages_label = f" ({page_count}p)" if page_count else ""
        export_dir = str(pathlib.Path(out).parent)
        image_dir = ""
        t0 = time.perf_counter()
        duration_parse = 0.0
        duration_ocr = 0.0
        try:
            mk_kwargs: dict = {}
            if use_ocr:
                image_dir = _image_subdir(export_dir, pdf_str)
                os.makedirs(image_dir, exist_ok=True)
                mk_kwargs.update(write_images=True, force_text=True, image_path=image_dir, dpi=200)

            t_parse = time.perf_counter()
            if ctx and page_count > 1:
                parse_progress: list = [0, page_count]

                def _on_parse_progress(done: int, total: int) -> None:
                    parse_progress[0] = done
                    parse_progress[1] = total

                _mk = mk_kwargs.copy()
                loop = asyncio.get_running_loop()
                future = loop.run_in_executor(
                    None,
                    lambda: _to_markdown_paged(pdf_str, page_count, _on_parse_progress, **_mk),
                )
                while not future.done():
                    await asyncio.sleep(0.3)
                    done, total = parse_progress
                    await ctx.report_progress(
                        progress=i, total=len(pdf_files),
                        message=f"[1/{total_passes}] Parse {done}/{total}p{' + img' if use_ocr else ''}: {pdf.name}",
                    )
                md_text = future.result()
            else:
                if ctx:
                    stage = f"[1/{total_passes}] Parsing{' + img' if use_ocr else ''}{pages_label}: {pdf.name}"
                    await ctx.report_progress(progress=i, total=len(pdf_files), message=stage)
                md_text = await asyncio.to_thread(pymupdf4llm.to_markdown, pdf_str, **mk_kwargs)
            duration_parse = time.perf_counter() - t_parse

            ocr_stats: dict = {}
            if use_ocr:
                if ctx:
                    await ctx.report_progress(progress=i, total=len(pdf_files), message=f"[2/2] Loading OCR model{pages_label}: {pdf.name}")

                ocr_progress_state: list = [0, 0]

                def _batch_ocr_progress(done: int, total: int) -> None:
                    ocr_progress_state[0] = done
                    ocr_progress_state[1] = total

                t_ocr = time.perf_counter()

                async def _run_ocr_with_progress() -> tuple[str, dict]:
                    loop = asyncio.get_running_loop()
                    future = loop.run_in_executor(
                        None, _enrich_markdown_with_ocr, md_text, langs, _batch_ocr_progress,
                    )
                    while not future.done():
                        await asyncio.sleep(0.5)
                        done, total = ocr_progress_state
                        if ctx and total > 0:
                            if done == 0:
                                msg = f"[2/2] Loading OCR model ({total}img){pages_label}: {pdf.name}"
                            else:
                                msg = f"[2/2] OCR {done}/{total}img{pages_label}: {pdf.name}"
                            await ctx.report_progress(
                                progress=i, total=len(pdf_files),
                                message=msg,
                            )
                    return future.result()

                md_text, ocr_stats = await _run_ocr_with_progress()
                duration_ocr = time.perf_counter() - t_ocr
                ok_paths = ocr_stats.pop("images_ok_paths", [])
                _cleanup_recognized_images(ok_paths)
                if os.path.isdir(image_dir) and not os.listdir(image_dir):
                    os.rmdir(image_dir)
            duration = time.perf_counter() - t0
            if ctx:
                await ctx.report_progress(progress=i, total=len(pdf_files), message=f"Saving{pages_label}: {pdf.name}")
            _write_markdown(out, md_text)
            chars, lines = len(md_text), len(md_text.splitlines())
            ocr_label = _format_ocr_label(ocr_stats, compact=True) if use_ocr else ""
            ocr_detail_lines: list[str] = []
            if use_ocr:
                errors_detail = ocr_stats.pop("errors_detail", [])
                if errors_detail:
                    for ed in errors_detail[:5]:
                        ocr_detail_lines.append(f"    [{ed['reason']}] {ed['file']}: {ed['detail']}")
                    if len(errors_detail) > 5:
                        ocr_detail_lines.append(f"    ... and {len(errors_detail) - 5} more")
            pdf_extra = {
                "pymupdf4llm_version": getattr(pymupdf4llm, "__version__", "unknown"),
                "ocr": use_ocr,
                "duration_parse_sec": round(duration_parse, 2),
                "duration_ocr_sec": round(duration_ocr, 2),
                **ocr_stats,
                **_pdf_metadata(pdf_str),
            }
            if use_ocr and ocr_stats.get("images_failed") and os.path.isdir(image_dir):
                pdf_extra["images_dir"] = image_dir
            _record_and_save(log_file, log, pdf_str, out, current_hash, "ok", chars=chars, lines=lines, duration_sec=duration, extra=pdf_extra)
            converted += 1
            timing = f"{duration:.1f}s(parse:{duration_parse:.1f}s"
            if duration_ocr > 0:
                timing += f",ocr:{duration_ocr:.1f}s"
            timing += ")"
            result_msg = f"OK{ocr_label}: {pdf.name} -> {out} ({chars} chars, {timing})"
            if ocr_detail_lines:
                result_msg += "\n" + "\n".join(ocr_detail_lines)
            results.append(result_msg)
            if ctx:
                info_msg = f"[{i+1}/{len(pdf_files)}] OK{ocr_label}: {pdf.name} ({chars} chars, {timing})"
                if ocr_detail_lines:
                    info_msg += "\n" + "\n".join(ocr_detail_lines)
                await ctx.info(info_msg)
        except Exception as e:
            duration = time.perf_counter() - t0
            pdf_extra = {"pymupdf4llm_version": getattr(pymupdf4llm, "__version__", "unknown"), "ocr": use_ocr, **_pdf_metadata(pdf_str)}
            _record_and_save(log_file, log, pdf_str, out, current_hash, "error", error=str(e), duration_sec=duration, extra=pdf_extra)
            failed += 1
            results.append(f"FAIL: {pdf.name} -> {e}")
            if ctx:
                await ctx.warning(f"[{i+1}/{len(pdf_files)}] FAIL: {pdf.name} -> {e}")
        if ctx:
            await ctx.report_progress(progress=i + 1, total=len(pdf_files), message=f"Done: {pdf.name}")

    summary = f"Total: {len(pdf_files)} | Converted: {converted} | Skipped: {skipped} | Failed: {failed}"
    if ctx:
        await ctx.info(summary)
        await ctx.report_progress(progress=len(pdf_files), total=len(pdf_files), message="Complete")
    result = summary + "\n" + "\n".join(results)
    _batch_extra = {
        "total_files": len(pdf_files), "converted": converted,
        "skipped": skipped, "failed": failed,
    }
    if failed:
        audit.end_error(result_summary=summary)
    else:
        audit.end_ok(summary, extra=_batch_extra)
    return result


@mcp.tool()
def read_pdf_as_markdown(pdf_path: str) -> str:
    """Read a PDF file and return its content as Markdown (without saving to disk).

    Args:
        pdf_path: Absolute path to the PDF file.
    """
    audit = _AuditOp("read_pdf_as_markdown", args={"pdf_path": pdf_path})
    pdf_path = os.path.normpath(pdf_path)
    if not os.path.isfile(pdf_path):
        result = f"Error: file not found: {pdf_path}"
        audit.end_error(result_summary=result)
        return result

    try:
        md_text = pymupdf4llm.to_markdown(pdf_path)
    except Exception as e:
        audit.end_error(error=str(e))
        return f"Error converting PDF: {e}"

    max_chars = 100_000
    if len(md_text) > max_chars:
        md_text = md_text[:max_chars] + f"\n\n... (truncated, total {len(md_text)} chars)"

    audit.end_ok(f"{len(md_text)} chars", extra={"chars": len(md_text), "lines": len(md_text.splitlines())})
    return md_text


@mcp.tool()
def get_conversion_log(folder_path: str) -> str:
    """Read the conversion log for a folder. Shows status, errors, and timestamps.

    Args:
        folder_path: Absolute path to the folder to check.
    """
    audit = _AuditOp("get_conversion_log", args={"folder_path": folder_path})
    folder = pathlib.Path(os.path.normpath(folder_path))
    export_dir = folder / EXPORT_SUBFOLDER
    log_file = str(export_dir / LOG_FILENAME)

    if not os.path.isfile(log_file):
        result = f"No conversion log found in {folder}"
        audit.end_ok(result_summary=result)
        return result

    log = _load_log(log_file)
    if not log:
        result = f"Conversion log is empty in {folder}"
        audit.end_ok(result_summary=result)
        return result

    ok_count = sum(1 for e in log.values() if e.get("status") == "ok")
    err_count = sum(1 for e in log.values() if e.get("status") == "error")

    lines = [f"Conversion log: {folder}", f"Total entries: {len(log)} (ok: {ok_count}, errors: {err_count})", ""]

    for path, entry in log.items():
        status = entry.get("status", "?")
        ts = entry.get("converted_at", "?")
        name = pathlib.Path(path).name
        user = entry.get("converted_by", "?")
        machine = entry.get("machine", "?")
        pages = entry.get("pages", "?")
        dur = entry.get("duration_sec", "?")
        size_kb = round(entry.get("source_size_bytes", 0) / 1024)

        if status == "ok":
            lines.append(
                f"  OK   | {name} | {pages} pages | {entry.get('chars', 0)} chars "
                f"| {dur}s | {user}@{machine} | {ts}"
            )
        else:
            lines.append(
                f"  FAIL | {name} | {entry.get('error', '?')} "
                f"| {user}@{machine} | {ts}"
            )

    errors = {p: e for p, e in log.items() if e.get("status") == "error"}
    if errors:
        lines.append("")
        lines.append("=== ERRORS (details) ===")
        for path, entry in errors.items():
            lines.append(f"  File: {path}")
            lines.append(f"  Error: {entry.get('error', '?')}")
            lines.append(f"  Size: {round(entry.get('source_size_bytes', 0) / 1024)} KB")
            lines.append(f"  User: {entry.get('converted_by', '?')}@{entry.get('machine', '?')}")
            lines.append(f"  Time: {entry.get('converted_at', '?')}")
            lines.append(f"  Version: pymupdf4llm {entry.get('pymupdf4llm_version', '?')}")
            lines.append("")

    result = "\n".join(lines)
    audit.end_ok(f"{len(log)} entries")
    return result


# ---------------------------------------------------------------------------
# Swagger / OpenAPI helpers
# ---------------------------------------------------------------------------

_SWAGGER_EXTENSIONS = (".yaml", ".yml", ".json")
_SWAGGER_NAMES = {"swagger", "openapi"}


def _is_swagger_file(path: pathlib.Path) -> bool:
    if path.suffix.lower() not in _SWAGGER_EXTENSIONS:
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read(4096)
        if path.suffix.lower() == ".json":
            data = json.loads(raw if raw.rstrip().endswith("}") else raw + "}")
        else:
            data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return bool(set(data.keys()) & _SWAGGER_NAMES)
    except Exception:
        pass
    return False


def _parse_openapi(file_path: str) -> dict:
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    if file_path.lower().endswith(".json"):
        return json.loads(content)
    return yaml.safe_load(content)


def _resolve_ref(spec: dict, ref: str) -> dict | None:
    if not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    node = spec
    for p in parts:
        if isinstance(node, dict):
            node = node.get(p)
        else:
            return None
    return node if isinstance(node, dict) else None


def _type_str(schema: dict, spec: dict | None = None) -> str:
    if not schema:
        return ""
    if "$ref" in schema:
        ref = schema["$ref"]
        name = ref.rsplit("/", 1)[-1]
        return f"[{name}](#{name.lower()})"
    t = schema.get("type", "")
    fmt = schema.get("format", "")
    if t == "array":
        items = schema.get("items", {})
        return f"array of {_type_str(items, spec)}"
    if schema.get("enum"):
        vals = ", ".join(str(v) for v in schema["enum"])
        base = f"{t} ({fmt})" if fmt else t
        return f"{base} (enum: {vals})"
    if fmt:
        return f"{t} ({fmt})"
    return t


def _openapi_to_markdown(spec: dict) -> str:
    is_v3 = spec.get("openapi", "").startswith("3")
    info = spec.get("info", {})
    title = info.get("title", "API")
    version = info.get("version", "")
    description = info.get("description", "")

    lines: list[str] = []
    lines.append(f"# {title} v{version}")
    lines.append("")
    if description:
        lines.append(description)
        lines.append("")

    host = spec.get("host", "")
    base_path = spec.get("basePath", "")
    if is_v3:
        servers = spec.get("servers", [])
        if servers:
            lines.append(f"**Server:** {servers[0].get('url', '')}")
            lines.append("")
    elif host:
        schemes = spec.get("schemes", ["https"])
        lines.append(f"**Base URL:** {schemes[0]}://{host}{base_path}")
        lines.append("")

    contact = info.get("contact", {})
    if contact.get("email"):
        lines.append(f"**Contact:** {contact['email']}")
        lines.append("")

    tags_info = {t["name"]: t.get("description", "") for t in spec.get("tags", [])}

    paths = spec.get("paths", {})
    endpoints_by_tag: dict[str, list[tuple[str, str, dict]]] = {}
    for path_str, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete", "options", "head"):
            op = path_item.get(method)
            if not op:
                continue
            op_tags = op.get("tags", ["default"])
            for tag in op_tags:
                endpoints_by_tag.setdefault(tag, []).append((method, path_str, op))

    lines.append("---")
    lines.append("")

    for tag, endpoints in endpoints_by_tag.items():
        tag_desc = tags_info.get(tag, "")
        lines.append(f"## {tag}")
        lines.append("")
        if tag_desc:
            lines.append(tag_desc)
            lines.append("")

        for method, path_str, op in endpoints:
            summary = op.get("summary", "")
            desc = op.get("description", "")
            lines.append(f"### {method.upper()} {path_str}")
            lines.append("")
            if summary:
                lines.append(f"**{summary}**")
                lines.append("")
            if desc and desc != summary:
                lines.append(desc)
                lines.append("")

            params = op.get("parameters", [])
            if is_v3 and op.get("requestBody"):
                rb = op["requestBody"]
                rb_desc = rb.get("description", "Request body")
                content = rb.get("content", {})
                for ct, ct_val in content.items():
                    schema = ct_val.get("schema", {})
                    params.append({
                        "name": "body",
                        "in": "body",
                        "description": rb_desc,
                        "required": rb.get("required", False),
                        "_schema": schema,
                    })

            if params:
                lines.append("| Parameter | In | Type | Required | Description |")
                lines.append("|---|---|---|---|---|")
                for p in params:
                    name = p.get("name", "")
                    loc = p.get("in", "")
                    required = "yes" if p.get("required") else "no"
                    p_desc = p.get("description", "")
                    if "schema" in p:
                        p_type = _type_str(p["schema"], spec)
                    elif "_schema" in p:
                        p_type = _type_str(p["_schema"], spec)
                    else:
                        raw_type = p.get("type", "")
                        raw_fmt = p.get("format", "")
                        p_type = f"{raw_type} ({raw_fmt})" if raw_fmt else raw_type
                    lines.append(f"| {name} | {loc} | {p_type} | {required} | {p_desc} |")
                lines.append("")

            responses = op.get("responses", {})
            if responses:
                resp_parts = []
                for code, resp in sorted(responses.items()):
                    r_desc = resp.get("description", "") if isinstance(resp, dict) else ""
                    resp_parts.append(f"{code} ({r_desc})")
                lines.append(f"**Responses:** {', '.join(resp_parts)}")
                lines.append("")

            security = op.get("security", [])
            if security:
                sec_names = []
                for s in security:
                    if isinstance(s, dict):
                        sec_names.extend(s.keys())
                if sec_names:
                    lines.append(f"**Security:** {', '.join(sec_names)}")
                    lines.append("")

    defs = spec.get("definitions") or {}
    if is_v3:
        defs = spec.get("components", {}).get("schemas", {})

    if defs:
        lines.append("---")
        lines.append("")
        lines.append("## Models")
        lines.append("")

        for model_name, model_schema in sorted(defs.items()):
            lines.append(f"### {model_name}")
            lines.append("")
            model_desc = model_schema.get("description", "")
            if model_desc:
                lines.append(model_desc)
                lines.append("")

            props = model_schema.get("properties", {})
            required_fields = set(model_schema.get("required", []))
            if props:
                lines.append("| Field | Type | Required | Description |")
                lines.append("|---|---|---|---|")
                for field_name, field_schema in props.items():
                    f_type = _type_str(field_schema, spec)
                    f_desc = field_schema.get("description", "")
                    example = field_schema.get("example")
                    if example is not None and not f_desc:
                        f_desc = f"example: {example}"
                    elif example is not None:
                        f_desc += f" (example: {example})"
                    f_req = "yes" if field_name in required_fields else ""
                    lines.append(f"| {field_name} | {f_type} | {f_req} | {f_desc} |")
                lines.append("")

    sec_defs = spec.get("securityDefinitions") or {}
    if is_v3:
        sec_defs = spec.get("components", {}).get("securitySchemes", {})
    if sec_defs:
        lines.append("---")
        lines.append("")
        lines.append("## Security Schemes")
        lines.append("")
        for sec_name, sec_schema in sec_defs.items():
            sec_type = sec_schema.get("type", "")
            sec_in = sec_schema.get("in", "")
            sec_param = sec_schema.get("name", "")
            lines.append(f"- **{sec_name}**: {sec_type} (in: {sec_in}, name: {sec_param})")
        lines.append("")

    return "\n".join(lines)


def _swagger_metadata(spec: dict) -> dict:
    info = spec.get("info", {})
    return {
        "api_title": info.get("title", ""),
        "api_version": info.get("version", ""),
        "swagger_version": spec.get("swagger", spec.get("openapi", "")),
        "endpoints": sum(
            len([m for m in ("get", "post", "put", "patch", "delete", "options", "head") if m in ops])
            for ops in spec.get("paths", {}).values()
            if isinstance(ops, dict)
        ),
        "models": len(
            spec.get("definitions") or spec.get("components", {}).get("schemas", {}) or {}
        ),
    }


# ---------------------------------------------------------------------------
# Swagger Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def convert_swagger_to_markdown(
    swagger_path: str,
    output_path: str | None = None,
    force: bool = False,
) -> str:
    """Convert a Swagger/OpenAPI specification (YAML or JSON) to readable Markdown.

    Args:
        swagger_path: Absolute path to the swagger.yaml / openapi.json file.
        output_path: Where to save the .md file. Defaults to pdf2md_export/ subfolder.
        force: If True, re-convert even if already converted.
    """
    audit = _AuditOp("convert_swagger_to_markdown", args={"swagger_path": swagger_path, "force": force})
    swagger_path = os.path.normpath(swagger_path)
    if not os.path.isfile(swagger_path):
        result = f"Error: file not found: {swagger_path}"
        audit.end_error(result_summary=result)
        return result

    log_file = _log_path_for(swagger_path)
    log = _load_log(log_file)
    current_hash = _file_hash(swagger_path)

    skip_msg = _check_skip(log, log_file, swagger_path, current_hash, force)
    if skip_msg:
        audit.end_skip()
        return skip_msg

    out = output_path or str(
        _export_dir_for(swagger_path) / (pathlib.Path(swagger_path).stem + ".md")
    )

    t0 = time.perf_counter()
    try:
        spec = _parse_openapi(swagger_path)
        md_text = _openapi_to_markdown(spec)
    except Exception as e:
        duration = time.perf_counter() - t0
        _record_and_save(log_file, log, swagger_path, out, current_hash, "error", error=str(e), duration_sec=duration,
                         extra={"converter": "swagger2md"})
        audit.end_error(error=str(e))
        return f"Error converting Swagger: {e}"
    duration = time.perf_counter() - t0

    _write_markdown(out, md_text)

    chars = len(md_text)
    md_lines = len(md_text.splitlines())
    extra = {"converter": "swagger2md", **_swagger_metadata(spec)}
    _record_and_save(log_file, log, swagger_path, out, current_hash, "ok",
                     chars=chars, lines=md_lines, duration_sec=duration, extra=extra)

    result = (
        f"Converted successfully.\n"
        f"Output: {out}\n"
        f"Size: {chars} chars ({md_lines} lines)\n"
        f"Endpoints: {extra.get('endpoints', 0)} | Models: {extra.get('models', 0)}\n"
        f"Duration: {duration:.2f}s"
    )
    audit.end_ok(f"{chars} chars, {md_lines} lines, {extra.get('endpoints', 0)} endpoints, {duration:.1f}s",
                 extra={"chars": chars, "lines": md_lines, "output_path": out,
                         "endpoints": extra.get("endpoints", 0), "models": extra.get("models", 0)})
    return result


@mcp.tool()
async def convert_all_swagger_in_folder(
    folder_path: str,
    recursive: bool = False,
    force: bool = False,
    ctx: Context | None = None,
) -> str:
    """Convert all Swagger/OpenAPI files in a folder to Markdown.

    Args:
        folder_path: Absolute path to the folder.
        recursive: If True, search subfolders too.
        force: If True, re-convert even if already converted.
    """
    audit = _AuditOp("convert_all_swagger_in_folder", ctx, {"folder_path": folder_path, "recursive": recursive, "force": force})
    folder = pathlib.Path(os.path.normpath(folder_path))
    if not folder.is_dir():
        result = f"Error: directory not found: {folder}"
        audit.end_error(result_summary=result)
        return result

    candidates: list[pathlib.Path] = []
    for ext in _SWAGGER_EXTENSIONS:
        pattern = f"**/*{ext}" if recursive else f"*{ext}"
        candidates.extend(folder.glob(pattern))

    swagger_files = sorted(f for f in set(candidates) if _is_swagger_file(f))

    if not swagger_files:
        result = f"No Swagger/OpenAPI files found in {folder}"
        audit.end_ok(result_summary=result)
        return result

    if ctx:
        await ctx.info(f"Found {len(swagger_files)} Swagger/OpenAPI files in {folder}")
        await ctx.report_progress(progress=0, total=len(swagger_files), message="Scanning...")

    results = []
    converted = 0
    skipped = 0
    failed = 0

    for i, sf in enumerate(swagger_files):
        sf_str = str(sf)
        log_file = _log_path_for(sf_str)
        log = _load_log(log_file)
        current_hash = await asyncio.to_thread(_file_hash, sf_str)
        out = str(_export_dir_for(sf_str) / (sf.stem + ".md"))

        if _check_skip(log, log_file, sf_str, current_hash, force):
            skipped += 1
            results.append(f"SKIP: {sf.name} (unchanged since {log[sf_str]['converted_at']})")
            if ctx:
                await ctx.info(f"[{i+1}/{len(swagger_files)}] SKIP: {sf.name} (unchanged)")
                await ctx.report_progress(progress=i + 1, total=len(swagger_files), message=f"Skipped: {sf.name}")
            continue

        if ctx:
            await ctx.info(f"[{i+1}/{len(swagger_files)}] Converting: {sf.name}...")
            await ctx.report_progress(progress=i, total=len(swagger_files), message=f"Converting: {sf.name}")

        t0 = time.perf_counter()
        try:
            spec = await asyncio.to_thread(_parse_openapi, sf_str)
            md_text = _openapi_to_markdown(spec)
            duration = time.perf_counter() - t0
            _write_markdown(out, md_text)
            chars = len(md_text)
            md_lines = len(md_text.splitlines())
            extra = {"converter": "swagger2md", **_swagger_metadata(spec)}
            _record_and_save(log_file, log, sf_str, out, current_hash, "ok",
                             chars=chars, lines=md_lines, duration_sec=duration, extra=extra)
            converted += 1
            results.append(f"OK: {sf.name} -> {out} ({chars} chars, {duration:.2f}s)")
            if ctx:
                await ctx.info(f"[{i+1}/{len(swagger_files)}] OK: {sf.name} ({chars} chars, {duration:.2f}s)")
        except Exception as e:
            duration = time.perf_counter() - t0
            _record_and_save(log_file, log, sf_str, out, current_hash, "error", error=str(e), duration_sec=duration,
                              extra={"converter": "swagger2md"})
            failed += 1
            results.append(f"FAIL: {sf.name} -> {e}")
            if ctx:
                await ctx.warning(f"[{i+1}/{len(swagger_files)}] FAIL: {sf.name} -> {e}")
        if ctx:
            await ctx.report_progress(progress=i + 1, total=len(swagger_files), message=f"Done: {sf.name}")

    summary = f"Total: {len(swagger_files)} | Converted: {converted} | Skipped: {skipped} | Failed: {failed}"
    if ctx:
        await ctx.info(summary)
        await ctx.report_progress(progress=len(swagger_files), total=len(swagger_files), message="Complete")
    result = summary + "\n" + "\n".join(results)
    _sw_extra = {"total_files": len(swagger_files), "converted": converted, "skipped": skipped, "failed": failed}
    if failed:
        audit.end_error(result_summary=summary)
    else:
        audit.end_ok(summary, extra=_sw_extra)
    return result


# ---------------------------------------------------------------------------
# HTTP API helpers (fetch URL, detect Swagger UI, parse remote spec)
# ---------------------------------------------------------------------------

import ssl
import urllib.request
import urllib.error

_FETCH_TIMEOUT = 30
_MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MB

_COMMON_SPEC_PATHS = [
    "/swagger.json",
    "/openapi.json",
    "/openapi.yaml",
    "/v2/api-docs",
    "/v3/api-docs",
    "/api/swagger.json",
    "/api/openapi.json",
    "/api-docs",
    "/docs/openapi.json",
]

_SWAGGER_UI_PATTERNS = re.compile(
    r"swagger-ui|SwaggerUIBundle|swagger-ui-bundle|"
    r"spec-url=|redoc\.standalone|Redoc\.init",
    re.IGNORECASE,
)

_SPEC_URL_EXTRACTORS = [
    re.compile(r"""SwaggerUIBundle\s*\(\s*\{[^}]*?url\s*:\s*["']([^"']+)["']""", re.DOTALL),
    re.compile(r"""spec-url\s*=\s*["']([^"']+)["']""", re.IGNORECASE),
    re.compile(r"""Redoc\.init\s*\(\s*["']([^"']+)["']""", re.DOTALL),
    re.compile(r"""url\s*:\s*["'](\/[^"']*(?:swagger|openapi|api-docs)[^"']*)["']""", re.IGNORECASE),
]


def _make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch_url(url: str, accept: str = "*/*") -> tuple[bytes, str, str]:
    """Fetch URL content. Returns (body_bytes, content_type, final_url)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) doc2md-mcp/1.0",
        "Accept": accept,
    })
    ctx = _make_ssl_context()
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT, context=ctx) as resp:
        ct = resp.headers.get("Content-Type", "")
        final_url = resp.url
        body = resp.read(_MAX_RESPONSE_BYTES)
    return body, ct, final_url


def _try_parse_as_openapi(raw: bytes, content_type: str) -> dict | None:
    """Try to parse raw bytes as an OpenAPI/Swagger spec.
    Returns the parsed dict if it looks like a valid spec, else None."""
    text = raw.decode("utf-8", errors="replace")
    data = None
    if "json" in content_type or text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass
    if data is None:
        try:
            data = yaml.safe_load(text)
        except Exception:
            pass
    if isinstance(data, dict) and (set(data.keys()) & _SWAGGER_NAMES):
        return data
    return None


def _detect_swagger_spec_url(html: str, page_url: str) -> str | None:
    """Given an HTML page, try to find the Swagger/OpenAPI spec URL."""
    if not _SWAGGER_UI_PATTERNS.search(html):
        return None
    for pattern in _SPEC_URL_EXTRACTORS:
        m = pattern.search(html)
        if m:
            spec_url = m.group(1)
            if spec_url.startswith("/"):
                parsed = urlparse(page_url)
                spec_url = f"{parsed.scheme}://{parsed.netloc}{spec_url}"
            elif not spec_url.startswith("http"):
                base = page_url.rsplit("/", 1)[0]
                spec_url = f"{base}/{spec_url}"
            return spec_url

    parsed = urlparse(page_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    for path in _COMMON_SPEC_PATHS:
        probe_url = base + path
        try:
            body, ct, _ = _fetch_url(probe_url, accept="application/json, application/yaml")
            if _try_parse_as_openapi(body, ct) is not None:
                return probe_url
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Web page helpers (Crawl4AI)
# ---------------------------------------------------------------------------

_POSTMAN_DOMAIN = "documenter.getpostman.com"
_SAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_FILENAME_LEN = 120


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _url_to_filename(url: str, title: str | None = None) -> str:
    if title and title.strip():
        name = title.strip()
    else:
        parsed = urlparse(url)
        name = parsed.netloc + parsed.path
    name = _SAFE_FILENAME_RE.sub("_", name).strip("_. ")
    if len(name) > _MAX_FILENAME_LEN:
        name = name[:_MAX_FILENAME_LEN]
    return name + ".md" if name else "page.md"


def _resolve_web_output_path(
    url: str,
    title: str | None,
    output_path: str | None,
    output_dir: str | None,
) -> str:
    if output_path:
        return output_path
    fname = _url_to_filename(url, title)
    base = output_dir or OUTPUT_DIR or os.getcwd()
    export = pathlib.Path(base) / EXPORT_SUBFOLDER
    return str(export / fname)


def _web_log_path(output_dir: str | None) -> str:
    base = output_dir or OUTPUT_DIR or os.getcwd()
    return str(pathlib.Path(base) / EXPORT_SUBFOLDER / LOG_FILENAME)


_POSTMAN_DELAY = 5.0


def _detect_wait_for(url: str, user_wait_for: str | None) -> str | None:
    """Return a CSS wait_for selector or None.

    Postman Documenter doesn't have a stable CSS class to wait for,
    so we rely on delay_before_return_html instead (see _POSTMAN_DELAY).
    """
    if user_wait_for is not None:
        return user_wait_for if user_wait_for else None
    return None


async def _crawl_url(
    url: str,
    wait_for: str | None = None,
    delay: float = 0,
) -> tuple[str, str]:
    """Crawl a single URL and return (markdown, page_title)."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    from crawl4ai.content_filter_strategy import PruningContentFilter

    md_gen = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(threshold=0.4, threshold_type="fixed")
    )

    run_cfg_kwargs: dict = {
        "cache_mode": CacheMode.BYPASS,
        "markdown_generator": md_gen,
    }
    if wait_for:
        run_cfg_kwargs["wait_for"] = wait_for
    if delay:
        run_cfg_kwargs["delay_before_return_html"] = delay

    browser_cfg = BrowserConfig(headless=True)
    run_cfg = CrawlerRunConfig(**run_cfg_kwargs)

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)
        if not result.success:
            raise RuntimeError(result.error_message or "Crawl failed")
        md = result.markdown
        if hasattr(md, "fit_markdown") and md.fit_markdown:
            md_text = md.fit_markdown
        elif hasattr(md, "raw_markdown"):
            md_text = md.raw_markdown
        else:
            md_text = str(md)
        title = ""
        if result.metadata and isinstance(result.metadata, dict):
            title = result.metadata.get("title", "")
        return md_text, title


def _run_async(coro):
    """Run an async coroutine from synchronous MCP tool."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# HTTP API tool (auto-detect Swagger / web docs)
# ---------------------------------------------------------------------------

@mcp.tool()
async def convert_api_url_to_markdown(
    url: str,
    output_path: str | None = None,
    output_dir: str | None = None,
    force: bool = False,
    ctx: Context | None = None,
) -> str:
    """Convert an HTTP API documentation URL to Markdown.

    Auto-detects the content type:
    - Direct Swagger/OpenAPI spec (JSON/YAML) — parsed into structured Markdown
    - Swagger UI / ReDoc page — spec URL extracted from HTML, then parsed
    - Generic web page — rendered via headless browser (Crawl4AI) fallback

    Args:
        url: HTTP(S) URL pointing to API docs, Swagger UI, or a raw OpenAPI spec.
        output_path: Where to save the .md file. Auto-generated if omitted.
        output_dir: Base folder for export. Defaults to DOC2MD_OUTPUT_DIR or cwd.
        force: If True, re-convert even if URL was already converted.
    """
    audit = _AuditOp("convert_api_url_to_markdown", ctx, {"url": url, "force": force})
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        result = "Error: URL must start with http:// or https://"
        audit.end_error(result_summary=result)
        return result

    log_file = _web_log_path(output_dir)
    log = _load_log(log_file)
    url_key = url
    current_hash = _url_hash(url)

    skip_msg = _check_skip(log, log_file, url_key, current_hash, force)
    if skip_msg:
        audit.end_skip()
        return skip_msg

    t0 = time.perf_counter()
    detection_method = "unknown"
    spec: dict | None = None
    md_text: str = ""
    page_title: str = ""

    # --- Step 1: fetch the URL ---
    if ctx:
        await ctx.info(f"Step 1/4: Fetching {url}...")
        await ctx.report_progress(progress=0, total=4, message=f"Fetching: {url[:60]}")
    try:
        raw_body, content_type, final_url = await asyncio.to_thread(
            _fetch_url, url, "application/json, application/yaml, text/html, */*"
        )
    except Exception as e:
        duration = time.perf_counter() - t0
        out = _resolve_web_output_path(url, None, output_path, output_dir)
        _record_and_save(log_file, log, url_key, out, current_hash, "error",
                         error=f"HTTP fetch failed: {e}", duration_sec=duration,
                         extra={"source_type": "api_url"})
        if ctx:
            await ctx.error(f"HTTP fetch failed: {e}")
        audit.end_error(error=str(e))
        return f"Error fetching URL: {e}"

    if ctx:
        await ctx.report_progress(progress=1, total=4, message="Detecting content type...")

    # --- Step 2: try direct OpenAPI parse ---
    if ctx:
        await ctx.info("Step 2/4: Detecting content type...")
    spec = _try_parse_as_openapi(raw_body, content_type)
    if spec:
        detection_method = "direct_openapi_spec"
        if ctx:
            await ctx.info("Detected: Direct OpenAPI/Swagger spec")
    else:
        # --- Step 3: if HTML, look for Swagger UI / ReDoc ---
        body_text = raw_body.decode("utf-8", errors="replace")
        if "html" in content_type.lower() or body_text.lstrip().startswith("<!") or "<html" in body_text[:500].lower():
            if ctx:
                await ctx.info("HTML page detected, looking for Swagger UI / ReDoc...")
            spec_url = await asyncio.to_thread(_detect_swagger_spec_url, body_text, final_url)
            if spec_url:
                if ctx:
                    await ctx.info(f"Found spec URL: {spec_url}")
                try:
                    spec_body, spec_ct, _ = await asyncio.to_thread(
                        _fetch_url, spec_url, "application/json, application/yaml"
                    )
                    spec = _try_parse_as_openapi(spec_body, spec_ct)
                    if spec:
                        detection_method = "swagger_ui_extracted"
                        if ctx:
                            await ctx.info("Detected: Swagger spec extracted from HTML page")
                except Exception:
                    pass

    if ctx:
        await ctx.report_progress(progress=2, total=4, message="Converting to Markdown...")

    # --- Step 3: convert ---
    if ctx:
        await ctx.info("Step 3/4: Converting to Markdown...")
    if spec:
        try:
            md_text = _openapi_to_markdown(spec)
            info = spec.get("info", {})
            page_title = info.get("title", "")
        except Exception as e:
            duration = time.perf_counter() - t0
            out = _resolve_web_output_path(url, None, output_path, output_dir)
            _record_and_save(log_file, log, url_key, out, current_hash, "error",
                             error=f"OpenAPI conversion failed: {e}", duration_sec=duration,
                             extra={"source_type": "api_url", "detection": detection_method})
            if ctx:
                await ctx.error(f"OpenAPI conversion failed: {e}")
            audit.end_error(error=str(e))
            return f"Error converting OpenAPI spec: {e}"
    else:
        detection_method = "crawl4ai_fallback"
        if ctx:
            await ctx.info("No OpenAPI spec found, falling back to Crawl4AI...")
        try:
            md_text, page_title = await _crawl_url(url, None, 2.0)
        except Exception as e:
            duration = time.perf_counter() - t0
            out = _resolve_web_output_path(url, None, output_path, output_dir)
            _record_and_save(log_file, log, url_key, out, current_hash, "error",
                             error=f"Crawl4AI fallback failed: {e}", duration_sec=duration,
                             extra={"source_type": "api_url", "detection": detection_method})
            if ctx:
                await ctx.error(f"Crawl4AI fallback failed: {e}")
            audit.end_error(error=str(e))
            return f"Error: could not parse as OpenAPI and Crawl4AI fallback failed: {e}"

    if ctx:
        await ctx.report_progress(progress=3, total=4, message="Saving...")

    duration = time.perf_counter() - t0

    if not md_text or not md_text.strip():
        out = _resolve_web_output_path(url, page_title, output_path, output_dir)
        _record_and_save(log_file, log, url_key, out, current_hash, "error",
                         error="Empty content after conversion", duration_sec=duration,
                         extra={"source_type": "api_url", "detection": detection_method})
        if ctx:
            await ctx.error("Conversion produced empty content")
        audit.end_error(error="Empty content")
        return "Error: conversion produced empty content."

    # --- Step 4: save ---
    if ctx:
        await ctx.info("Step 4/4: Saving Markdown...")
    out = _resolve_web_output_path(url, page_title, output_path, output_dir)
    _write_markdown(out, md_text)

    chars, md_lines = len(md_text), len(md_text.splitlines())

    extra: dict = {
        "source_type": "api_url",
        "detection": detection_method,
        "page_title": page_title,
    }
    if spec:
        extra.update(_swagger_metadata(spec))

    _record_and_save(log_file, log, url_key, out, current_hash, "ok",
                     chars=chars, lines=md_lines, duration_sec=duration, extra=extra)

    if ctx:
        await ctx.report_progress(progress=4, total=4, message="Complete")

    method_label = {
        "direct_openapi_spec": "Direct OpenAPI spec",
        "swagger_ui_extracted": "Extracted from Swagger UI / ReDoc",
        "crawl4ai_fallback": "Web page (Crawl4AI)",
    }.get(detection_method, detection_method)

    result_lines = [
        "Converted successfully.",
        f"Detection: {method_label}",
        f"Output: {out}",
    ]
    if page_title:
        result_lines.append(f"Title: {page_title}")
    result_lines.append(f"Size: {chars} chars ({md_lines} lines)")
    if spec:
        result_lines.append(
            f"Endpoints: {extra.get('endpoints', 0)} | Models: {extra.get('models', 0)}"
        )
    result_lines.append(f"Duration: {duration:.1f}s")

    if ctx:
        await ctx.info(f"Done: {method_label} -> {chars} chars, {duration:.1f}s")
    result = "\n".join(result_lines)
    _api_extra: dict = {"chars": chars, "lines": md_lines, "output_path": out,
                        "detection": detection_method, "page_title": page_title or ""}
    if spec:
        _api_extra["endpoints"] = extra.get("endpoints", 0)
        _api_extra["models"] = extra.get("models", 0)
    _api_summary = f"{chars} chars, {md_lines} lines, {method_label}, {duration:.1f}s"
    audit.end_ok(_api_summary, extra=_api_extra)
    return result


# ---------------------------------------------------------------------------
# Web page tools
# ---------------------------------------------------------------------------

@mcp.tool()
def convert_url_to_markdown(
    url: str,
    output_path: str | None = None,
    output_dir: str | None = None,
    wait_for: str | None = None,
    force: bool = False,
) -> str:
    """Convert a web page (including JS-rendered SPAs like Postman Documenter) to Markdown.

    Uses a headless browser (Crawl4AI) to render the page and extract content.

    Args:
        url: Web page URL to convert.
        output_path: Where to save the .md file. Auto-generated if omitted.
        output_dir: Base folder for export. Defaults to DOC2MD_OUTPUT_DIR or cwd.
        wait_for: CSS selector to wait for before extraction (e.g. "css:.content").
                  Auto-detected for known domains (Postman).
        force: If True, re-convert even if URL was already converted.
    """
    audit = _AuditOp("convert_url_to_markdown", args={"url": url, "force": force})
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        result = "Error: URL must start with http:// or https://"
        audit.end_error(result_summary=result)
        return result

    log_file = _web_log_path(output_dir)
    log = _load_log(log_file)
    url_key = url
    current_hash = _url_hash(url)

    skip_msg = _check_skip(log, log_file, url_key, current_hash, force)
    if skip_msg:
        audit.end_skip()
        return skip_msg

    effective_wait = _detect_wait_for(url, wait_for)
    is_postman = _POSTMAN_DOMAIN in urlparse(url).netloc
    delay = _POSTMAN_DELAY if is_postman else 1.0

    t0 = time.perf_counter()
    try:
        md_text, page_title = _run_async(_crawl_url(url, effective_wait, delay))
    except Exception as e:
        duration = time.perf_counter() - t0
        out = _resolve_web_output_path(url, None, output_path, output_dir)
        extra = {"source_type": "url", "crawl4ai_wait_for": effective_wait or ""}
        _record_and_save(log_file, log, url_key, out, current_hash, "error",
                         error=str(e), duration_sec=duration, extra=extra)
        audit.end_error(error=str(e))
        return f"Error crawling URL: {e}"
    duration = time.perf_counter() - t0

    if not md_text or not md_text.strip():
        out = _resolve_web_output_path(url, page_title, output_path, output_dir)
        extra = {"source_type": "url", "page_title": page_title}
        _record_and_save(log_file, log, url_key, out, current_hash, "error",
                         error="Empty content after crawl", duration_sec=duration, extra=extra)
        audit.end_error(error="Empty content")
        return "Error: page returned empty content. Try specifying wait_for parameter."

    out = _resolve_web_output_path(url, page_title, output_path, output_dir)
    _write_markdown(out, md_text)

    chars, md_lines = len(md_text), len(md_text.splitlines())

    try:
        from crawl4ai.__version__ import __version__ as c4a_ver
    except Exception:
        c4a_ver = "unknown"

    extra = {
        "source_type": "url",
        "page_title": page_title,
        "crawl4ai_version": c4a_ver,
        "crawl4ai_wait_for": effective_wait or "",
    }
    _record_and_save(log_file, log, url_key, out, current_hash, "ok",
                     chars=chars, lines=md_lines, duration_sec=duration, extra=extra)

    result = (
        f"Converted successfully.\n"
        f"Output: {out}\n"
        f"Title: {page_title}\n"
        f"Size: {chars} chars ({md_lines} lines)\n"
        f"Duration: {duration:.1f}s"
    )
    audit.end_ok(f"{chars} chars, {md_lines} lines, {duration:.1f}s",
                 extra={"chars": chars, "lines": md_lines, "output_path": out, "page_title": page_title})
    return result


@mcp.tool()
async def convert_urls_to_markdown(
    urls: str,
    output_dir: str | None = None,
    wait_for: str | None = None,
    force: bool = False,
    ctx: Context | None = None,
) -> str:
    """Convert multiple web pages to Markdown (batch processing).

    Args:
        urls: Newline-separated or comma-separated list of URLs.
        output_dir: Base folder for export. Defaults to DOC2MD_OUTPUT_DIR or cwd.
        wait_for: CSS selector to wait for (applied to all URLs).
        force: If True, re-convert even already converted URLs.
    """
    audit = _AuditOp("convert_urls_to_markdown", ctx, {"urls_count": len(re.split(r"[\n,]+", urls)), "force": force})
    url_list = [u.strip() for u in re.split(r"[\n,]+", urls) if u.strip()]
    if not url_list:
        result = "Error: no URLs provided."
        audit.end_error(result_summary=result)
        return result

    if ctx:
        await ctx.info(f"Processing {len(url_list)} URLs...")
        await ctx.report_progress(progress=0, total=len(url_list), message="Starting...")

    results: list[str] = []
    converted = 0
    skipped = 0
    failed = 0

    for i, u in enumerate(url_list):
        short_url = urlparse(u).netloc + urlparse(u).path[:40] if u.startswith("http") else u[:50]
        if not u.startswith(("http://", "https://")):
            results.append(f"SKIP: {u} (not a valid URL)")
            skipped += 1
            if ctx:
                await ctx.info(f"[{i+1}/{len(url_list)}] SKIP: {u} (invalid URL)")
                await ctx.report_progress(progress=i + 1, total=len(url_list), message=f"Skipped: {short_url}")
            continue

        log_file = _web_log_path(output_dir)
        log = _load_log(log_file)
        current_hash = _url_hash(u)

        if _check_skip(log, log_file, u, current_hash, force):
            results.append(f"SKIP: {u} (already converted at {log[u]['converted_at']})")
            skipped += 1
            if ctx:
                await ctx.info(f"[{i+1}/{len(url_list)}] SKIP: {u} (already converted)")
                await ctx.report_progress(progress=i + 1, total=len(url_list), message=f"Skipped: {short_url}")
            continue

        if ctx:
            await ctx.info(f"[{i+1}/{len(url_list)}] Crawling: {u}...")
            await ctx.report_progress(progress=i, total=len(url_list), message=f"Crawling: {short_url}")

        effective_wait = _detect_wait_for(u, wait_for)
        delay = _POSTMAN_DELAY if _POSTMAN_DOMAIN in urlparse(u).netloc else 1.0

        t0 = time.perf_counter()
        try:
            md_text, page_title = await _crawl_url(u, effective_wait, delay)
            duration = time.perf_counter() - t0

            if not md_text or not md_text.strip():
                raise RuntimeError("Empty content after crawl")

            out = _resolve_web_output_path(u, page_title, None, output_dir)
            _write_markdown(out, md_text)

            chars, md_lines = len(md_text), len(md_text.splitlines())
            extra = {"source_type": "url", "page_title": page_title}
            _record_and_save(log_file, log, u, out, current_hash, "ok",
                             chars=chars, lines=md_lines, duration_sec=duration, extra=extra)
            converted += 1
            results.append(f"OK: {u} -> {out} ({chars} chars, {duration:.1f}s)")
            if ctx:
                await ctx.info(f"[{i+1}/{len(url_list)}] OK: {u} ({chars} chars, {duration:.1f}s)")
        except Exception as e:
            duration = time.perf_counter() - t0
            out = _resolve_web_output_path(u, None, None, output_dir)
            extra = {"source_type": "url"}
            _record_and_save(log_file, log, u, out, current_hash, "error",
                              error=str(e), duration_sec=duration, extra=extra)
            failed += 1
            results.append(f"FAIL: {u} -> {e}")
            if ctx:
                await ctx.warning(f"[{i+1}/{len(url_list)}] FAIL: {u} -> {e}")
        if ctx:
            await ctx.report_progress(progress=i + 1, total=len(url_list), message=f"Done: {short_url}")

    summary = f"Total: {len(url_list)} | Converted: {converted} | Skipped: {skipped} | Failed: {failed}"
    if ctx:
        await ctx.info(summary)
        await ctx.report_progress(progress=len(url_list), total=len(url_list), message="Complete")
    result = summary + "\n" + "\n".join(results)
    _urls_extra = {"total_urls": len(url_list), "converted": converted, "skipped": skipped, "failed": failed}
    if failed:
        audit.end_error(result_summary=summary)
    else:
        audit.end_ok(summary, extra=_urls_extra)
    return result


# ---------------------------------------------------------------------------
# Server audit log viewer
# ---------------------------------------------------------------------------

@mcp.tool()
def get_server_log(
    last_n: int = 50,
    user: str | None = None,
    tool: str | None = None,
    status: str | None = None,
) -> str:
    """View recent server audit log entries. Optionally filter by user, tool name, or status.

    Args:
        last_n: Number of most recent entries to return (default 50, max 500).
        user: Filter by OS username (case-insensitive substring match).
        tool: Filter by tool name (case-insensitive substring match).
        status: Filter by status: "start", "end_ok", "end_error", "end_skip",
                or shorthand "ok"/"error"/"skip" (matches end_ok/end_error/end_skip).
    """
    log_path = _SERVER_LOG_DIR / "doc2md_server.log"
    if not log_path.is_file():
        return "No server audit log found."

    last_n = min(max(last_n, 1), 500)

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except Exception as e:
        return f"Error reading server log: {e}"

    if not all_lines:
        return "Server audit log is empty."

    entries: list[dict] = []
    for line in reversed(all_lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if user and user.lower() not in entry.get("user", "").lower():
            continue
        if tool and tool.lower() not in entry.get("tool", "").lower():
            continue
        if status:
            entry_status = entry.get("status", "")
            if status in ("ok", "error", "skip"):
                if entry_status != f"end_{status}":
                    continue
            elif entry_status != status:
                continue
        entries.append(entry)
        if len(entries) >= last_n:
            break

    if not entries:
        return "No matching entries found."

    lines: list[str] = [f"Server audit log ({len(entries)} entries, newest first):", ""]
    for e in entries:
        ts = e.get("ts", "?")
        t = e.get("tool", e.get("event", "?"))
        s = e.get("status", "?")
        op_id = e.get("operation_id", "")
        u = e.get("user", "?")
        m = e.get("machine", "?")
        app = e.get("client_app", "?")

        _STATUS_ICONS = {
            "start": ">>>", "end_ok": " OK", "end_skip": "SKIP",
            "end_error": "FAIL", "server_start": " UP", "server_stop": "DOWN",
        }
        icon = _STATUS_ICONS.get(s, s.upper()[:4])

        if s == "start":
            args_str = json.dumps(e.get("args", {}), ensure_ascii=False)
            if len(args_str) > 200:
                args_str = args_str[:200] + "..."
            line = f"  {icon:4s} | {ts} | {t} | {u}@{m} ({app}) | op={op_id} | {args_str}"
        else:
            dur = e.get("duration_sec", "?")
            summary = e.get("result_summary", "")
            first_line = summary.split("\n")[0][:120] if summary else ""
            line = f"  {icon:4s} | {ts} | {t} | {dur}s | {u}@{m} ({app})"
            if op_id:
                line += f" | op={op_id}"
            if first_line:
                line += f" | {first_line}"
            err = e.get("error")
            if err:
                line += f" | ERR: {err[:100]}"
            ex = e.get("extra")
            if ex:
                parts = []
                for k in ("chars", "lines", "pages", "ocr",
                           "duration_parse_sec", "duration_ocr_sec",
                           "images_total", "images_ocr_ok",
                           "pdf_author", "pdf_creator",
                           "endpoints", "models",
                           "total_files", "converted", "skipped", "failed",
                           "page_title", "output_path"):
                    if k in ex and ex[k] not in (None, "", 0, False):
                        parts.append(f"{k}={ex[k]}")
                if parts:
                    line += " | " + ", ".join(parts)
        lines.append(line)

    return "\n".join(lines)


def _collect_environment() -> dict:
    """Collect runtime environment details for the server_start log entry."""
    env: dict = {}
    try:
        env["os"] = platform.platform()
        env["os_version"] = platform.version()
        env["arch"] = platform.machine()
        env["python"] = platform.python_version()
        env["python_impl"] = platform.python_implementation()
        env["python_path"] = sys.executable
        env["cwd"] = os.getcwd()
        env["server_dir"] = str(pathlib.Path(__file__).resolve().parent)

        pkg_versions: dict[str, str] = {}
        for mod_name in ("pymupdf", "pymupdf4llm", "yaml", "easyocr",
                         "torch", "PIL", "crawl4ai", "mcp"):
            try:
                mod = sys.modules.get(mod_name) or __import__(mod_name)
                ver = getattr(mod, "__version__", None) or getattr(mod, "VERSION", None)
                if ver is not None and isinstance(ver, str):
                    pkg_versions[mod_name] = ver
                else:
                    from importlib.metadata import version as pkg_ver
                    pkg_versions[mod_name] = pkg_ver(mod_name)
            except ImportError:
                pkg_versions[mod_name] = "not installed"
            except Exception:
                pass
        env["packages"] = pkg_versions

        env_vars: dict[str, str] = {}
        for var in ("DOC2MD_OUTPUT_DIR", "VIRTUAL_ENV", "CONDA_DEFAULT_ENV",
                     "PATH", "PYTHONPATH", "MCP_TRANSPORT"):
            val = os.environ.get(var)
            if val is not None:
                env_vars[var] = val[:500]
        env["env_vars"] = env_vars
    except Exception:
        pass
    return env


def _log_server_lifecycle(event: str) -> None:
    """Write a server start/stop entry to the audit log."""
    try:
        entry: dict = {
            "ts": _now_iso(),
            "level": "INFO",
            "event": event,
            "server_version": __version__,
            "user": getpass.getuser(),
            "machine": platform.node(),
            "pid": os.getpid(),
        }
        if event == "server_start":
            entry["environment"] = _collect_environment()
        _audit_logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception:
        pass


if __name__ == "__main__":
    import atexit
    _log_server_lifecycle("server_start")
    atexit.register(_log_server_lifecycle, "server_stop")
    mcp.run()
