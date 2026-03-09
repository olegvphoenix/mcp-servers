# MCP Servers

A collection of [Model Context Protocol](https://modelcontextprotocol.io/) servers for use with AI-powered IDEs (Cursor, Claude Desktop, etc.).

## Servers

| Server | Description |
|--------|-------------|
| [confluence-mcp](confluence-mcp/) | Read-only access to Confluence Server/Data Center |
| [jira-mcp](jira-mcp/) | Read-only access to Jira Server/Data Center |
| [doc2md-mcp](doc2md-mcp/) | Convert PDF, Swagger/OpenAPI, and web pages to Markdown |

---

## confluence-mcp

MCP server for reading Confluence pages via REST API (Server/Data Center).

### Tools

- **get_page** — get a page by numeric ID
- **get_page_by_title** — get a page by space key and exact title
- **search_pages** — search using CQL (Confluence Query Language)
- **get_page_children** — list child pages
- **list_spaces** — list all available spaces
- **get_page_comments** — get comments on a page

### Environment variables

| Variable | Description |
|----------|-------------|
| `CONFLUENCE_URL` | Base URL, e.g. `https://confluence.example.com` |
| `CONFLUENCE_USERNAME` | Username for Basic auth |
| `CONFLUENCE_PASSWORD` | Password for Basic auth |

### Dependencies

```
mcp[cli]>=1.0.0
httpx>=0.27.0
```

---

## jira-mcp

MCP server for reading Jira issues via REST API v2 (Server/Data Center).

### Tools

- **get_issue** — get an issue by key (e.g. `PROJECT-123`)
- **search_issues** — search using JQL queries
- **get_issue_comments** — get all comments for an issue

### Environment variables

| Variable | Description |
|----------|-------------|
| `JIRA_URL` | Base URL, e.g. `https://jira.example.com` |
| `JIRA_USERNAME` | Username for Basic auth |
| `JIRA_PASSWORD` | Password for Basic auth |

### Dependencies

```
mcp[cli]>=1.0.0
httpx>=0.27.0
```

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

166 tests covering all core logic — helpers, OCR pipeline, progress reporting, Swagger/OpenAPI, HTTP detection, tool functions, and full end-to-end conversions with real generated data.

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

## Setup

### Installation

Each server has its own `requirements.txt`. Install dependencies:

```bash
pip install -r confluence-mcp/requirements.txt
pip install -r jira-mcp/requirements.txt
pip install -r doc2md-mcp/requirements.txt
```

### Cursor IDE configuration

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "jira": {
      "command": "python",
      "args": ["<path-to>/mcp-servers/jira-mcp/server.py"],
      "env": {
        "JIRA_URL": "https://jira.example.com",
        "JIRA_USERNAME": "your-username",
        "JIRA_PASSWORD": "your-password"
      }
    },
    "confluence": {
      "command": "python",
      "args": ["<path-to>/mcp-servers/confluence-mcp/server.py"],
      "env": {
        "CONFLUENCE_URL": "https://confluence.example.com",
        "CONFLUENCE_USERNAME": "your-username",
        "CONFLUENCE_PASSWORD": "your-password"
      }
    },
    "doc2md": {
      "command": "python",
      "args": ["<path-to>/mcp-servers/doc2md-mcp/server.py"]
    }
  }
}
```

All servers use **stdio** transport and are started automatically by the IDE.

## License

MIT
