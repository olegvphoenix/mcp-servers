# MCP Servers

A collection of [Model Context Protocol](https://modelcontextprotocol.io/) servers for use with AI-powered IDEs (Cursor, Claude Desktop, etc.).

## Servers

| Server | Description |
|--------|-------------|
| [doc2md-mcp](doc2md-mcp/) | Convert PDF, Swagger/OpenAPI, and web pages to Markdown |

---

## doc2md-mcp

MCP server for converting various document formats to Markdown. Supports PDF files (with OCR for scanned documents), Swagger/OpenAPI specs, and web pages.

### Tools

**PDF:**
- **convert_pdf_to_markdown** — convert a single PDF file
- **convert_all_pdfs_in_folder** — batch-convert all PDFs in a folder
- **read_pdf_as_markdown** — read PDF as Markdown without saving to disk
- **get_conversion_log** — view conversion log (status, errors, timestamps)
- **get_server_log** — view server audit log (all tool calls, users, durations)

**Swagger/OpenAPI:**
- **convert_swagger_to_markdown** — convert a local YAML/JSON spec file
- **convert_all_swagger_in_folder** — batch-convert all specs in a folder

**Web / HTTP API:**
- **convert_api_url_to_markdown** — auto-detect and convert API docs URL (Swagger UI, ReDoc, raw spec, or generic page)
- **convert_url_to_markdown** — convert a web page to Markdown via headless browser
- **convert_urls_to_markdown** — batch-convert multiple URLs

### Environment variables

| Variable | Description |
|----------|-------------|
| `DOC2MD_OUTPUT_DIR` | Default output directory for converted files. Optional. |

### Features

- **OCR** — automatic text extraction from scanned PDFs using EasyOCR (auto-detection by image area >= 100k px, configurable `ocr` mode and `ocr_languages`)
- **Granular progress** — real-time progress via MCP `report_progress`: page-by-page parsing `[1/N] Parse X/Yp`, per-image OCR `[2/2] OCR X/Yimg`, model loading status
- **Deduplication** — SHA-256 hash-based skip logic to avoid redundant conversions
- **Conversion log** — `doc2md_log.json` tracks status, hashes, split timing (total/parse/OCR), and skip history
- **Server audit log** — JSONL log of all tool invocations with user, machine, client app, duration, args, and result (daily rotation, 90-day retention)

### Dependencies

```
mcp[cli]>=1.0.0
pymupdf4llm>=0.3.0
pyyaml>=6.0
easyocr>=1.7.0
crawl4ai>=0.8.0
```

### Tests

242 tests covering all core logic — helpers, OCR pipeline, progress reporting, Swagger/OpenAPI, HTTP detection, tool functions (including read_pdf_as_markdown, get_conversion_log, convert_url_to_markdown, convert_urls_to_markdown), SSL context, skip logic, audit logging, and full end-to-end conversions with real generated data.

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

---

## Running all tests

All 242 tests can be run from the workspace root:

```bash
pip install pytest pytest-asyncio
python -m pytest -v
```

The root `pytest.ini` and `conftest.py` handle module isolation so that each sub-project's `server.py` is correctly resolved.

---

## Setup

### Option 1: uvx from Git (recommended)

No local clone required. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) once, then add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "doc2md": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/olegvphoenix/mcp-servers.git#subdirectory=doc2md-mcp",
        "doc2md-mcp"
      ],
      "env": {
        "DOC2MD_OUTPUT_DIR": ""
      }
    }
  }
}
```

`uvx` automatically downloads the repository, installs dependencies, and runs the server. No manual `pip install` or local paths needed.

### Option 2: Cursor Team MCP (for teams)

Team admins can add MCP servers centrally in **Cursor Settings → MCP Servers → Add MCP** (or **Edit mcp.json**). Use the same `uvx` configuration as above.

### Option 3: Manual installation (local clone)

Clone the repository and run servers directly:

```bash
git clone https://github.com/olegvphoenix/mcp-servers.git
pip install -r doc2md-mcp/requirements.txt
```

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "doc2md": {
      "command": "python",
      "args": ["<path-to>/mcp-servers/doc2md-mcp/server.py"],
      "env": {
        "DOC2MD_OUTPUT_DIR": ""
      }
    }
  }
}
```

All servers use **stdio** transport and are started automatically by the IDE.

---

## Security notes

- **Credentials are never stored in the repository.** All passwords and URLs are read from environment variables at runtime. Never commit `.env` files or hardcode credentials in configuration.
- **SSL certificate verification is disabled** (`verify=False`) for web page fetching in doc2md-mcp. This is intentional for corporate environments with self-signed certificates. If your servers use trusted CA-signed certificates, you can update `_make_ssl_context()` in `doc2md-mcp/server.py` to use default verification.
- **doc2md server is read-only.** It does not modify any data.
- **Audit logging** (doc2md-mcp) records tool invocations locally in `logs/doc2md_server.log`. Log files are excluded from Git via `.gitignore`.

## Author

**AxxonSoft** — aleh.vaitsekhovich@axxonsoft.dev

## License

MIT
