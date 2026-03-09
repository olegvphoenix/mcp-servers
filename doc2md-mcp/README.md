# DOC2MD MCP Server

MCP server for converting documents to Markdown. Supports:
- **PDF** — via `pymupdf4llm` (with automatic OCR for documents containing images)
- **Swagger / OpenAPI** (YAML, JSON) — custom renderer
- **Web pages** — via Crawl4AI (headless browser, JS-rendered SPA support)

Converted files are saved to a `doc2md_export/` subfolder next to the source files, along with a conversion log `doc2md_log.json`.

## Installation

```bash
pip install -r requirements.txt
crawl4ai-setup          # downloads Chromium for Crawl4AI (~170 MB, one-time)
```

## Cursor IDE Configuration

Add to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per-project):

```json
{
  "mcpServers": {
    "doc2md": {
      "command": "python",
      "args": ["<path-to>/mcp-servers/doc2md-mcp/server.py"]
    }
  }
}
```

## Tools

### PDF

#### `convert_pdf_to_markdown`
Convert a PDF file to Markdown and save the result.
- `pdf_path` — path to the PDF file
- `output_path` (optional) — where to save the .md file
- `page_chunks` (optional) — insert page separators in the output
- `force` (optional) — re-convert even if already converted
- `ocr` (optional) — OCR mode: `"auto"` (detect images >= 100k px automatically), `"always"`, `"off"`. Default: `"auto"`
- `ocr_languages` (optional) — comma-separated language codes for OCR, e.g. `"en"` or `"en,ru"`. Default: `"en"`

#### `convert_all_pdfs_in_folder`
Convert all PDF files in a folder.
- `folder_path` — path to the folder
- `output_folder` (optional) — where to save .md files
- `recursive` (optional) — include subfolders
- `force` (optional) — re-convert even if already converted
- `ocr` (optional) — OCR mode: `"auto"`, `"always"`, `"off"`. Default: `"auto"`
- `ocr_languages` (optional) — comma-separated language codes for OCR. Default: `"en"`

#### `read_pdf_as_markdown`
Read a PDF file and return its content as Markdown (without saving to disk).
- `pdf_path` — path to the PDF file

### Swagger / OpenAPI

#### `convert_swagger_to_markdown`
Convert a Swagger/OpenAPI specification (YAML/JSON) to readable Markdown.
- `swagger_path` — path to the spec file
- `output_path` (optional) — where to save the .md file
- `force` (optional) — re-convert even if already converted

#### `convert_all_swagger_in_folder`
Convert all Swagger/OpenAPI files in a folder.
- `folder_path` — path to the folder
- `recursive` (optional) — include subfolders
- `force` (optional) — re-convert even if already converted

### Web Pages

#### `convert_url_to_markdown`
Convert a web page to Markdown via headless browser (Crawl4AI). Supports JS-rendered SPAs (Postman Documenter, etc.).
- `url` — page URL
- `output_path` (optional) — where to save the .md file
- `output_dir` (optional) — base folder for export
- `wait_for` (optional) — CSS selector to wait for before extraction (e.g. `css:.content`)
- `force` (optional) — re-convert even if already converted

#### `convert_urls_to_markdown`
Batch-convert a list of URLs.
- `urls` — newline-separated or comma-separated list of URLs
- `output_dir` (optional) — base folder for export
- `wait_for` (optional) — CSS selector (applied to all URLs)
- `force` (optional) — re-convert even if already converted

### Conversion Log

#### `get_conversion_log`
View the conversion log for a given folder.
- `folder_path` — path to the folder

### Server Audit Log

#### `get_server_log`
View recent server audit log entries with optional filtering.
- `last_n` (optional) — number of entries to return (default 50, max 500)
- `user` (optional) — filter by OS username (substring match)
- `tool` (optional) — filter by tool name (substring match)
- `status` (optional) — filter by status: `"ok"`, `"error"`, or `"skip"`

## Server Audit Log

All tool invocations are logged to `logs/doc2md_server.log` (JSONL format, daily rotation, 90-day retention) next to `server.py`.

Each entry includes:
- **Timestamp** (`ts`) — ISO 8601 UTC
- **Tool** (`tool`) — which tool was called
- **Status** (`status`) — `ok`, `error`, or `skip`
- **Duration** (`duration_sec`) — wall clock time
- **User** (`user`) — OS username of the process owner
- **Machine** (`machine`) — hostname
- **PID** (`pid`) — process ID (distinguishes concurrent stdio sessions)
- **Client info** (`client_id`, `client_app`, `client_version`) — MCP client metadata (e.g. "Cursor 0.48.1")
- **Request ID** (`request_id`) — unique MCP request identifier
- **Arguments** (`args`) — tool input parameters
- **Result summary** (`result_summary`) — first 500 chars of the result
- **Error** (`error`) — error message (only on failure)

Example entry:
```json
{"ts":"2026-03-09T14:23:01+00:00","level":"INFO","tool":"convert_pdf_to_markdown","status":"ok","duration_sec":12.3,"user":"olegv","machine":"DESKTOP-ABC","pid":12345,"client_id":null,"client_app":"Cursor","client_version":"0.48.1","request_id":"req-uuid","args":{"pdf_path":"D:/docs/manual.pdf","ocr":"auto"},"result_summary":"Converted successfully."}
```

**Note**: In stdio transport mode, each client starts its own server process. IP address is not available (no network connection); the `machine` field identifies the workstation. When migrating to HTTP/SSE transport, IP and authentication data can be added by extending `_extract_client_info()`.

## Progress Reporting

During PDF conversion the server sends granular progress updates via MCP `report_progress`:

- **Hashing** — computing SHA-256 hash of the file
- **Detecting OCR pages** — identifying pages with large images
- **[1/N] Parse X/Yp** — page-by-page PDF parsing (N=1 without OCR, N=2 with OCR)
- **[2/2] Loading OCR model** — loading the EasyOCR model (first run)
- **[2/2] OCR X/Yimg** — extracting text from images
- **[2/2] OCR done** — OCR complete
- **Saving** — writing the .md file
- **Done** — conversion finished

The conversion log (`doc2md_log.json`) records three separate timings:
- `duration_sec` — total time
- `duration_parse_sec` — PDF parsing time
- `duration_ocr_sec` — OCR time

## Tests

166 tests covering: helpers, OCR pipeline, Swagger/OpenAPI, HTTP detection, progress reporting, tool functions, and end-to-end conversions.

```bash
cd doc2md-mcp
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

Markers:
- `e2e` — integration tests with real PDF/Swagger/HTTP conversions
- `slow` — tests that load the OCR model (EasyOCR)

Run without slow tests:

```bash
python -m pytest tests/ -v -m "not slow"
```

## Environment Variables (optional)

- `DOC2MD_OUTPUT_DIR` — default folder for saving .md files
