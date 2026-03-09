"""Tests for server audit logging (_audit_log, _extract_client_info,
get_server_log, _log_server_lifecycle) and extra metadata in audit entries.
"""

import getpass
import json
import os
import pathlib
import platform
import sys
import time
from dataclasses import dataclass, field
from unittest.mock import patch, MagicMock

import pymupdf
import pytest

from server import (
    __version__,
    _AuditOp,
    _audit_start,
    _audit_log,
    _extract_client_info,
    _log_server_lifecycle,
    _collect_environment,
    _audit_logger,
    get_server_log,
    convert_pdf_to_markdown,
    convert_swagger_to_markdown,
    convert_all_pdfs_in_folder,
    EXPORT_SUBFOLDER,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _ClientInfo:
    name = "TestApp"
    version = "1.2.3"


class _ClientParams:
    clientInfo = _ClientInfo()


class _Session:
    client_params = _ClientParams()


@dataclass
class FakeContext:
    """Minimal MCP Context stub for audit tests."""
    progress_calls: list = field(default_factory=list)
    info_calls: list = field(default_factory=list)
    warning_calls: list = field(default_factory=list)

    client_id: str = "test-client-id"
    request_id: str = "test-request-id"
    session: object = field(default_factory=lambda: _Session())

    async def report_progress(self, *, progress, total, message=""):
        self.progress_calls.append({"progress": progress, "total": total, "message": message})

    async def info(self, msg):
        self.info_calls.append(msg)

    async def warning(self, msg):
        self.warning_calls.append(msg)


def _make_text_pdf(path: pathlib.Path, pages: int = 2) -> pathlib.Path:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} content.")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def audit_log_dir(tmp_path):
    """Redirect _audit_logger to a temp file and return (log_dir, log_path)."""
    import logging

    log_dir = tmp_path / "test_logs"
    log_dir.mkdir()
    log_path = log_dir / "doc2md_server.log"

    handler = logging.FileHandler(str(log_path), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))

    _audit_logger.handlers.clear()
    _audit_logger.addHandler(handler)

    yield log_dir, log_path

    _audit_logger.handlers.clear()


def _read_log_entries(log_path: pathlib.Path) -> list[dict]:
    if not log_path.is_file():
        return []
    entries = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


# ===========================================================================
# _extract_client_info
# ===========================================================================

class TestExtractClientInfo:
    def test_none_context(self):
        info = _extract_client_info(None)
        assert info["user"] == getpass.getuser()
        assert info["machine"] == platform.node()
        assert info["pid"] == os.getpid()
        assert info["client_id"] is None
        assert info["client_app"] is None
        assert info["client_version"] is None
        assert info["request_id"] is None

    def test_with_fake_context(self):
        ctx = FakeContext()
        info = _extract_client_info(ctx)
        assert info["client_id"] == "test-client-id"
        assert info["request_id"] == "test-request-id"
        assert info["client_app"] == "TestApp"
        assert info["client_version"] == "1.2.3"
        assert info["user"] == getpass.getuser()

    def test_broken_context_does_not_raise(self):
        ctx = MagicMock()
        ctx.client_id = property(lambda _: (_ for _ in ()).throw(RuntimeError("no")))
        del ctx.request_id
        del ctx.session
        info = _extract_client_info(ctx)
        assert info["user"] == getpass.getuser()


# ===========================================================================
# _audit_log
# ===========================================================================

class TestAuditStart:
    def test_returns_operation_id(self, audit_log_dir):
        _, log_path = audit_log_dir
        op_id = _audit_start("test_tool", args={"path": "/a.pdf"})
        assert isinstance(op_id, str)
        assert len(op_id) == 12

    def test_writes_start_entry(self, audit_log_dir):
        _, log_path = audit_log_dir
        op_id = _audit_start("test_tool", args={"path": "/a.pdf"})
        entries = _read_log_entries(log_path)
        assert len(entries) == 1
        e = entries[0]
        assert e["status"] == "start"
        assert e["tool"] == "test_tool"
        assert e["operation_id"] == op_id
        assert e["args"]["path"] == "/a.pdf"
        assert e["server_version"] == __version__
        assert "duration_sec" not in e

    def test_start_with_context(self, audit_log_dir):
        _, log_path = audit_log_dir
        ctx = FakeContext()
        op_id = _audit_start("test_tool", ctx, {"key": "val"})
        entries = _read_log_entries(log_path)
        e = entries[0]
        assert e["client_id"] == "test-client-id"
        assert e["client_app"] == "TestApp"

    def test_unique_ids(self, audit_log_dir):
        _, log_path = audit_log_dir
        ids = {_audit_start("t") for _ in range(20)}
        assert len(ids) == 20


class TestAuditLog:
    def test_basic_entry(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        _audit_log("test_tool", "end_ok", 1.234, result_summary="all good", operation_id="abc123")
        entries = _read_log_entries(log_path)
        assert len(entries) == 1
        e = entries[0]
        assert e["tool"] == "test_tool"
        assert e["status"] == "end_ok"
        assert e["operation_id"] == "abc123"
        assert e["duration_sec"] == 1.23
        assert e["level"] == "INFO"
        assert e["result_summary"] == "all good"
        assert "ts" in e
        assert e["user"] == getpass.getuser()
        assert e["server_version"] == __version__
        assert "error" not in e
        assert "extra" not in e

    def test_error_entry(self, audit_log_dir):
        _, log_path = audit_log_dir
        _audit_log("test_tool", "end_error", 0.5, error="something broke")
        entries = _read_log_entries(log_path)
        assert len(entries) == 1
        e = entries[0]
        assert e["level"] == "ERROR"
        assert e["status"] == "end_error"
        assert e["error"] == "something broke"

    def test_extra_field(self, audit_log_dir):
        _, log_path = audit_log_dir
        _audit_log("test_tool", "end_ok", 0.1, extra={"chars": 1000, "lines": 50, "pages": 3})
        entries = _read_log_entries(log_path)
        assert len(entries) == 1
        ex = entries[0]["extra"]
        assert ex["chars"] == 1000
        assert ex["lines"] == 50
        assert ex["pages"] == 3

    def test_extra_not_present_when_none(self, audit_log_dir):
        _, log_path = audit_log_dir
        _audit_log("test_tool", "end_ok", 0.1)
        entries = _read_log_entries(log_path)
        assert "extra" not in entries[0]

    def test_with_context(self, audit_log_dir):
        _, log_path = audit_log_dir
        ctx = FakeContext()
        _audit_log("test_tool", "end_ok", 0.1, ctx=ctx)
        entries = _read_log_entries(log_path)
        e = entries[0]
        assert e["client_id"] == "test-client-id"
        assert e["client_app"] == "TestApp"
        assert e["client_version"] == "1.2.3"
        assert e["request_id"] == "test-request-id"

    def test_args_recorded(self, audit_log_dir):
        _, log_path = audit_log_dir
        _audit_log("test_tool", "end_ok", 0.1, args={"pdf_path": "/some/file.pdf", "force": True})
        entries = _read_log_entries(log_path)
        assert entries[0]["args"]["pdf_path"] == "/some/file.pdf"
        assert entries[0]["args"]["force"] is True

    def test_multiple_entries(self, audit_log_dir):
        _, log_path = audit_log_dir
        for i in range(5):
            _audit_log(f"tool_{i}", "end_ok", float(i))
        entries = _read_log_entries(log_path)
        assert len(entries) == 5
        assert [e["tool"] for e in entries] == [f"tool_{i}" for i in range(5)]

    def test_error_truncated_at_2000(self, audit_log_dir):
        _, log_path = audit_log_dir
        long_error = "x" * 5000
        _audit_log("test_tool", "end_error", 0.1, error=long_error)
        entries = _read_log_entries(log_path)
        assert len(entries[0]["error"]) == 2000

    def test_start_end_pair(self, audit_log_dir):
        _, log_path = audit_log_dir
        op_id = _audit_start("test_tool", args={"path": "/a.pdf"})
        _audit_log("test_tool", "end_ok", 1.5, result_summary="done", operation_id=op_id)
        entries = _read_log_entries(log_path)
        assert len(entries) == 2
        assert entries[0]["status"] == "start"
        assert entries[0]["operation_id"] == op_id
        assert entries[1]["status"] == "end_ok"
        assert entries[1]["operation_id"] == op_id


# ===========================================================================
# _log_server_lifecycle
# ===========================================================================

class TestServerLifecycle:
    def test_server_start(self, audit_log_dir):
        _, log_path = audit_log_dir
        _log_server_lifecycle("server_start")
        entries = _read_log_entries(log_path)
        assert len(entries) == 1
        e = entries[0]
        assert e["event"] == "server_start"
        assert e["level"] == "INFO"
        assert e["server_version"] == __version__
        assert e["user"] == getpass.getuser()
        assert e["machine"] == platform.node()
        assert e["pid"] == os.getpid()
        assert "ts" in e

    def test_server_start_has_environment(self, audit_log_dir):
        _, log_path = audit_log_dir
        _log_server_lifecycle("server_start")
        entries = _read_log_entries(log_path)
        env = entries[0]["environment"]
        assert "os" in env
        assert "arch" in env
        assert "python" in env
        assert "python_impl" in env
        assert "python_path" in env
        assert "cwd" in env
        assert "server_dir" in env
        assert "packages" in env
        assert "pymupdf" in env["packages"]
        assert "pymupdf4llm" in env["packages"]
        assert "env_vars" in env

    def test_server_stop_no_environment(self, audit_log_dir):
        _, log_path = audit_log_dir
        _log_server_lifecycle("server_stop")
        entries = _read_log_entries(log_path)
        assert entries[0]["event"] == "server_stop"
        assert entries[0]["server_version"] == __version__
        assert "environment" not in entries[0]

    def test_start_and_stop(self, audit_log_dir):
        _, log_path = audit_log_dir
        _log_server_lifecycle("server_start")
        _log_server_lifecycle("server_stop")
        entries = _read_log_entries(log_path)
        assert len(entries) == 2
        assert entries[0]["event"] == "server_start"
        assert "environment" in entries[0]
        assert entries[1]["event"] == "server_stop"
        assert "environment" not in entries[1]


# ===========================================================================
# _collect_environment
# ===========================================================================

class TestCollectEnvironment:
    def test_returns_os_info(self):
        env = _collect_environment()
        assert env["os"] == platform.platform()
        assert env["arch"] == platform.machine()

    def test_returns_python_info(self):
        env = _collect_environment()
        assert env["python"] == platform.python_version()
        assert env["python_impl"] == platform.python_implementation()
        assert env["python_path"] == sys.executable

    def test_returns_paths(self):
        env = _collect_environment()
        assert env["cwd"] == os.getcwd()
        assert "server_dir" in env

    def test_returns_packages(self):
        env = _collect_environment()
        pkgs = env["packages"]
        assert "pymupdf" in pkgs
        assert "pymupdf4llm" in pkgs
        assert "yaml" in pkgs
        assert "mcp" in pkgs
        for name, ver in pkgs.items():
            assert isinstance(ver, str)
            assert len(ver) > 0

    def test_returns_env_vars(self):
        env = _collect_environment()
        assert "env_vars" in env
        assert "PATH" in env["env_vars"]


# ===========================================================================
# get_server_log
# ===========================================================================

class TestGetServerLog:
    def test_no_log_file(self, tmp_path):
        with patch("server._SERVER_LOG_DIR", tmp_path / "nonexistent"):
            result = get_server_log()
        assert "No server audit log found" in result

    def test_empty_log(self, tmp_path, audit_log_dir):
        log_dir, log_path = audit_log_dir
        log_path.write_text("", encoding="utf-8")
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log()
        assert "empty" in result.lower() or "No matching" in result

    def test_returns_entries(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        _audit_log("tool_a", "end_ok", 1.0, result_summary="done A")
        _audit_log("tool_b", "end_error", 2.0, error="fail B")
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log()
        assert "2 entries" in result
        assert "tool_a" in result
        assert "tool_b" in result
        assert "OK" in result
        assert "FAIL" in result

    def test_filter_by_tool(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        _audit_log("convert_pdf", "end_ok", 1.0)
        _audit_log("convert_swagger", "end_ok", 2.0)
        _audit_log("convert_pdf", "end_ok", 3.0)
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log(tool="swagger")
        assert "1 entries" in result
        assert "convert_swagger" in result
        assert "convert_pdf" not in result

    def test_filter_by_status_shorthand(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        _audit_log("tool_a", "end_ok", 1.0)
        _audit_log("tool_b", "end_error", 2.0, error="oops")
        _audit_log("tool_c", "end_skip", 0.1)
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log(status="error")
        assert "1 entries" in result
        assert "tool_b" in result

    def test_filter_by_status_exact(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        _audit_start("tool_x", args={"a": 1})
        _audit_log("tool_x", "end_ok", 1.0)
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log(status="start")
        assert "1 entries" in result
        assert ">>>" in result

    def test_filter_by_user(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        _audit_log("tool_a", "end_ok", 1.0)
        current_user = getpass.getuser()
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log(user=current_user[:3])
        assert "1 entries" in result
        with patch("server._SERVER_LOG_DIR", log_dir):
            result_none = get_server_log(user="nonexistentuser12345")
        assert "No matching" in result_none

    def test_last_n_limit(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        for i in range(10):
            _audit_log(f"tool_{i}", "end_ok", float(i))
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log(last_n=3)
        assert "3 entries" in result

    def test_newest_first(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        _audit_log("first_tool", "end_ok", 1.0)
        _audit_log("second_tool", "end_ok", 2.0)
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log()
        lines = result.split("\n")
        tool_lines = [l for l in lines if "| " in l and ("first_tool" in l or "second_tool" in l)]
        assert "second_tool" in tool_lines[0]
        assert "first_tool" in tool_lines[1]

    def test_extra_displayed(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        _audit_log("convert_pdf", "end_ok", 1.0, extra={"chars": 5000, "lines": 200, "pages": 10, "ocr": True})
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log()
        assert "chars=5000" in result
        assert "lines=200" in result
        assert "pages=10" in result
        assert "ocr=True" in result

    def test_skip_entry_shows_skip_icon(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        _audit_log("convert_pdf", "end_skip", 0.1, result_summary="unchanged, skipped")
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log()
        assert "SKIP" in result

    def test_start_entry_shows_arrow_and_args(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        _audit_start("convert_pdf", args={"pdf_path": "/a/b.pdf", "ocr": True})
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log()
        assert ">>>" in result
        assert "/a/b.pdf" in result

    def test_operation_id_shown(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        op_id = _audit_start("convert_pdf", args={"pdf_path": "/a.pdf"})
        _audit_log("convert_pdf", "end_ok", 1.0, operation_id=op_id)
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log()
        assert f"op={op_id}" in result
        assert result.count(f"op={op_id}") == 2

    def test_error_shown_in_output(self, audit_log_dir):
        log_dir, log_path = audit_log_dir
        _audit_log("convert_pdf", "end_error", 0.5, error="file not found")
        with patch("server._SERVER_LOG_DIR", log_dir):
            result = get_server_log()
        assert "ERR: file not found" in result


# ===========================================================================
# Integration: audit log from convert_pdf_to_markdown
# ===========================================================================

class TestAuditFromPdfConvert:
    @pytest.fixture(autouse=True)
    def setup_audit(self, audit_log_dir):
        self.log_dir, self.log_path = audit_log_dir

    async def test_success_writes_start_and_end(self, tmp_path):
        pdf = _make_text_pdf(tmp_path / "test.pdf")
        ctx = FakeContext()
        await convert_pdf_to_markdown(str(pdf), ctx=ctx)

        entries = _read_log_entries(self.log_path)
        pdf_entries = [e for e in entries if e.get("tool") == "convert_pdf_to_markdown"]
        start_entries = [e for e in pdf_entries if e["status"] == "start"]
        end_entries = [e for e in pdf_entries if e["status"] == "end_ok"]
        assert len(start_entries) == 1
        assert len(end_entries) == 1
        assert start_entries[0]["operation_id"] == end_entries[0]["operation_id"]
        assert start_entries[0]["args"]["pdf_path"]

        e = end_entries[0]
        assert e["result_summary"]
        assert "\n" not in e["result_summary"]
        ex = e["extra"]
        assert ex["chars"] > 0
        assert ex["lines"] > 0
        assert ex["pages"] == 2
        assert ex["ocr"] is False
        assert "duration_parse_sec" in ex
        assert "duration_ocr_sec" in ex
        assert "output_path" in ex

    async def test_error_writes_start_and_end(self, tmp_path):
        ctx = FakeContext()
        await convert_pdf_to_markdown(str(tmp_path / "nonexistent.pdf"), ctx=ctx)

        entries = _read_log_entries(self.log_path)
        pdf_entries = [e for e in entries if e.get("tool") == "convert_pdf_to_markdown"]
        start_entries = [e for e in pdf_entries if e["status"] == "start"]
        err_entries = [e for e in pdf_entries if e["status"] == "end_error"]
        assert len(start_entries) == 1
        assert len(err_entries) == 1
        assert start_entries[0]["operation_id"] == err_entries[0]["operation_id"]
        assert "not found" in err_entries[0]["result_summary"].lower()

    async def test_skip_writes_start_and_end(self, tmp_path):
        pdf = _make_text_pdf(tmp_path / "test.pdf")
        ctx = FakeContext()
        await convert_pdf_to_markdown(str(pdf), ctx=ctx)
        await convert_pdf_to_markdown(str(pdf), ctx=ctx)

        entries = _read_log_entries(self.log_path)
        skip_entries = [e for e in entries if e.get("tool") == "convert_pdf_to_markdown" and e["status"] == "end_skip"]
        assert len(skip_entries) == 1
        assert skip_entries[0]["result_summary"] == "unchanged, skipped"
        assert skip_entries[0]["operation_id"]

    async def test_audit_has_client_info(self, tmp_path):
        pdf = _make_text_pdf(tmp_path / "test.pdf")
        ctx = FakeContext()
        await convert_pdf_to_markdown(str(pdf), ctx=ctx)

        entries = _read_log_entries(self.log_path)
        start = [e for e in entries if e["status"] == "start"][0]
        assert start["client_id"] == "test-client-id"
        assert start["client_app"] == "TestApp"
        end = [e for e in entries if e["status"] == "end_ok"][0]
        assert end["client_id"] == "test-client-id"
        assert end["client_version"] == "1.2.3"


# ===========================================================================
# Integration: audit log from convert_swagger_to_markdown
# ===========================================================================

class TestAuditFromSwaggerConvert:
    @pytest.fixture(autouse=True)
    def setup_audit(self, audit_log_dir):
        self.log_dir, self.log_path = audit_log_dir

    def test_success_writes_start_and_end(self, sample_swagger_yaml):
        convert_swagger_to_markdown(str(sample_swagger_yaml))

        entries = _read_log_entries(self.log_path)
        sw_entries = [e for e in entries if e.get("tool") == "convert_swagger_to_markdown"]
        starts = [e for e in sw_entries if e["status"] == "start"]
        ends = [e for e in sw_entries if e["status"] == "end_ok"]
        assert len(starts) == 1
        assert len(ends) == 1
        assert starts[0]["operation_id"] == ends[0]["operation_id"]

        e = ends[0]
        assert "\n" not in e["result_summary"]
        assert "chars" in e["result_summary"]
        ex = e["extra"]
        assert ex["chars"] > 0
        assert ex["lines"] > 0
        assert "endpoints" in ex
        assert "output_path" in ex

    def test_skip_writes_start_and_end(self, sample_swagger_yaml):
        convert_swagger_to_markdown(str(sample_swagger_yaml))
        convert_swagger_to_markdown(str(sample_swagger_yaml))

        entries = _read_log_entries(self.log_path)
        skip_entries = [e for e in entries if e.get("tool") == "convert_swagger_to_markdown" and e["status"] == "end_skip"]
        assert len(skip_entries) == 1
        assert skip_entries[0]["result_summary"] == "unchanged, skipped"
        assert skip_entries[0]["operation_id"]


# ===========================================================================
# Integration: audit log from convert_all_pdfs_in_folder
# ===========================================================================

class TestAuditFromBatchConvert:
    @pytest.fixture(autouse=True)
    def setup_audit(self, audit_log_dir):
        self.log_dir, self.log_path = audit_log_dir

    async def test_batch_writes_start_and_end(self, tmp_path):
        _make_text_pdf(tmp_path / "a.pdf")
        _make_text_pdf(tmp_path / "b.pdf")
        ctx = FakeContext()
        await convert_all_pdfs_in_folder(str(tmp_path), ctx=ctx)

        entries = _read_log_entries(self.log_path)
        batch_entries = [e for e in entries if e.get("tool") == "convert_all_pdfs_in_folder"]
        starts = [e for e in batch_entries if e["status"] == "start"]
        ends = [e for e in batch_entries if e["status"] == "end_ok"]
        assert len(starts) == 1
        assert len(ends) == 1
        assert starts[0]["operation_id"] == ends[0]["operation_id"]

        ex = ends[0]["extra"]
        assert ex["total_files"] == 2
        assert ex["converted"] == 2
        assert ex["skipped"] == 0
        assert ex["failed"] == 0

    async def test_empty_folder_writes_start_and_end(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        ctx = FakeContext()
        await convert_all_pdfs_in_folder(str(empty), ctx=ctx)

        entries = _read_log_entries(self.log_path)
        batch_entries = [e for e in entries if e.get("tool") == "convert_all_pdfs_in_folder"]
        starts = [e for e in batch_entries if e["status"] == "start"]
        ends = [e for e in batch_entries if e["status"] == "end_ok"]
        assert len(starts) == 1
        assert len(ends) == 1
        assert starts[0]["operation_id"] == ends[0]["operation_id"]
        assert "No PDF" in ends[0]["result_summary"]
