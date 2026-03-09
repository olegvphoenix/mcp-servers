# MCP Servers

A collection of [Model Context Protocol](https://modelcontextprotocol.io/) servers for use with AI-powered IDEs (Cursor, Claude Desktop, etc.).

## Servers

| Server | Description |
|--------|-------------|
| [confluence-mcp](confluence-mcp/) | Read-only access to Confluence Server/Data Center |
| [jira-mcp](jira-mcp/) | Read and write access to Jira Server/Data Center (with preview mode) |
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

### Tests

36 tests covering auth, HTML-to-text conversion, page formatting, all tool functions with mocked HTTP, and HTTP error handling for all endpoints.

```bash
cd confluence-mcp
pip install pytest
python -m pytest tests/ -v
```

---

## jira-mcp

MCP server for Jira (Server/Data Center) via REST API v2. Supports both read and write operations.

### Tools

**Read:**
- **get_issue** — get an issue by key (e.g. `PROJECT-123`)
- **search_issues** — search using JQL queries
- **get_issue_comments** — get all comments for an issue

**Write (with preview):**
- **create_issue** — create a new issue (summary, project, type, description, priority, assignee, labels, components, custom_fields). Defaults: project from `JIRA_DEFAULT_PROJECT`, issue type from `JIRA_DEFAULT_ISSUE_TYPE`, assignee from `JIRA_USERNAME`, custom fields merged from `JIRA_DEFAULT_CUSTOM_FIELDS`. Assignee is set via a separate API call after creation. Returns detailed Jira error messages on failure.
- **add_comment** — add a comment to an issue
- **transition_issue** — change issue status via workflow transition (e.g. Open → In Progress → Done)
- **assign_issue** — assign an issue to a user
- **link_issues** — create a link between two issues (Blocks, Relates, Duplicate, etc.)

All write tools execute immediately by default. Set `execute=False` to get a human-readable preview without making any changes.

### Environment variables

| Variable | Description |
|----------|-------------|
| `JIRA_URL` | Base URL, e.g. `https://jira.example.com` |
| `JIRA_USERNAME` | Username for Basic auth |
| `JIRA_PASSWORD` | Password for Basic auth |
| `JIRA_DEFAULT_PROJECT` | Default project key for `create_issue` (e.g. `ACR`). Optional. |
| `JIRA_DEFAULT_ISSUE_TYPE` | Default issue type for `create_issue` (e.g. `Bug`). Defaults to `Task`. Optional. |
| `JIRA_DEFAULT_CUSTOM_FIELDS` | JSON string with default custom fields merged into every `create_issue` call. Optional. Example: `{"customfield_11010": {"value": "Stable"}}` |

### Dependencies

```
mcp[cli]>=1.0.0
httpx>=0.27.0
```

### Tests

77 tests covering auth, formatting, all read tools, all write tools (preview and execute modes), error handling, auto-assign logic, custom fields, default configuration, and edge cases.

```bash
cd jira-mcp
pip install pytest
python -m pytest tests/ -v
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

196 tests covering all core logic — helpers, OCR pipeline, progress reporting, Swagger/OpenAPI, HTTP detection, tool functions (including read_pdf_as_markdown, get_conversion_log, convert_url_to_markdown, convert_urls_to_markdown), SSL context, skip logic, and full end-to-end conversions with real generated data.

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

### Option 1: Cursor Team Marketplace (recommended)

This repository is structured as a [Cursor multi-plugin marketplace](https://cursor.com/docs/plugins#team-marketplaces). Team admins can import it directly:

1. Go to **Cursor Dashboard → Settings → Plugins → Team Marketplaces**
2. Click **Import** and paste the repository URL:
   ```
   https://github.com/olegvphoenix/mcp-servers
   ```
3. Review the parsed plugins and assign them to distribution groups:

   | Plugin | Distribution |
   |--------|-------------|
   | jira-mcp | **Required** — automatically installed for everyone |
   | confluence-mcp | **Required** — automatically installed for everyone |
   | doc2md-mcp | **Optional** — developers choose whether to install |

After import, developers will see the plugins in the Cursor marketplace panel and can configure environment variables in their local settings.

### Option 2: Manual installation

Each server has its own `requirements.txt`. Install dependencies:

```bash
pip install -r confluence-mcp/requirements.txt
pip install -r jira-mcp/requirements.txt
pip install -r doc2md-mcp/requirements.txt
```

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
        "JIRA_PASSWORD": "your-password",
        "JIRA_DEFAULT_PROJECT": "PROJ",
        "JIRA_DEFAULT_ISSUE_TYPE": "Bug",
        "JIRA_DEFAULT_CUSTOM_FIELDS": "{\"customfield_11010\": {\"value\": \"Stable\"}}"
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

---

## Security notes

- **Credentials are never stored in the repository.** All passwords and URLs are read from environment variables at runtime. Never commit `.env` files or hardcode credentials in configuration.
- **SSL certificate verification is disabled** (`verify=False`) for Jira and Confluence HTTP clients, and for web page fetching in doc2md-mcp. This is intentional for corporate environments with self-signed certificates. If your servers use trusted CA-signed certificates, you can remove `verify=False` from the `_client()` functions in `jira-mcp/server.py` and `confluence-mcp/server.py`, and update `_make_ssl_context()` in `doc2md-mcp/server.py` to use default verification.
- **Confluence and doc2md servers are read-only.** They do not modify any data. Jira server supports write operations (create, comment, transition, assign, link) that execute immediately by default. Set `execute=False` to preview changes without applying them.
- **Audit logging** (doc2md-mcp) records tool invocations locally in `logs/doc2md_server.log`. Log files are excluded from Git via `.gitignore`.

## Author

**AxxonSoft** — aleh.vaitsekhovich@axxonsoft.dev

## License

MIT
