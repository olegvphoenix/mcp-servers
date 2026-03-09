"""Shared fixtures for doc2md-mcp tests."""

import json
import sys
import pathlib
import textwrap
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from threading import Thread

import pymupdf
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Sample OpenAPI specs as dicts
# ---------------------------------------------------------------------------

OPENAPI_V3_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test Pet API", "version": "1.0.0", "description": "A sample pet store API"},
    "servers": [{"url": "https://api.example.com/v1"}],
    "paths": {
        "/pets": {
            "get": {
                "tags": ["pets"],
                "summary": "List all pets",
                "parameters": [
                    {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer"}}
                ],
                "responses": {"200": {"description": "A list of pets"}},
            }
        },
        "/pets/{petId}": {
            "get": {
                "tags": ["pets"],
                "summary": "Get pet by ID",
                "parameters": [
                    {"name": "petId", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "responses": {
                    "200": {"description": "A pet"},
                    "404": {"description": "Not found"},
                },
            }
        },
    },
    "components": {
        "schemas": {
            "Pet": {
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
                "required": ["id", "name"],
            }
        },
        "securitySchemes": {
            "apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
        },
    },
}

SWAGGER_V2_SPEC = {
    "swagger": "2.0",
    "info": {"title": "Test Pet API v2", "version": "2.0.0"},
    "host": "api.example.com",
    "basePath": "/v2",
    "schemes": ["https"],
    "paths": {
        "/pets": {
            "get": {
                "tags": ["pets"],
                "summary": "List all pets",
                "parameters": [
                    {"name": "limit", "in": "query", "type": "integer"}
                ],
                "responses": {"200": {"description": "A list of pets"}},
            }
        },
    },
    "definitions": {
        "Pet": {
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
            }
        }
    },
}


# ---------------------------------------------------------------------------
# PDF fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_text_pdf(tmp_path):
    """Create a simple 2-page PDF with known text."""
    path = tmp_path / "sample_text.pdf"
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Chapter 1: Introduction\n\nThis is a test document about animals.")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Chapter 2: Details\n\nCats and dogs are popular pets.")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def sample_image_pdf(tmp_path):
    """Create a PDF with an embedded image (white rect with text-like pattern).
    The page has very little real text so OCR auto-detection triggers."""
    path = tmp_path / "sample_image.pdf"
    doc = pymupdf.open()
    page = doc.new_page()

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 80), 1)
    pix.set_rect(pix.irect, (255, 255, 255, 255))
    img_bytes = pix.tobytes("png")

    rect = pymupdf.Rect(72, 72, 372, 152)
    page.insert_image(rect, stream=img_bytes)
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# Swagger file fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_swagger_yaml(tmp_path):
    """Create a sample OpenAPI 3.0 YAML file."""
    import yaml
    path = tmp_path / "openapi.yaml"
    path.write_text(yaml.dump(OPENAPI_V3_SPEC, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def sample_swagger_json(tmp_path):
    """Create a sample Swagger 2.0 JSON file."""
    path = tmp_path / "swagger.json"
    path.write_text(json.dumps(SWAGGER_V2_SPEC, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def plain_yaml(tmp_path):
    """Create a YAML file that is NOT a Swagger/OpenAPI spec."""
    path = tmp_path / "config.yaml"
    path.write_text("database:\n  host: localhost\n  port: 5432\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Local HTTP server fixture
# ---------------------------------------------------------------------------

class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def local_http_server(tmp_path_factory):
    """Start a local HTTP server serving test files."""
    import yaml
    serve_dir = tmp_path_factory.mktemp("http_serve")

    (serve_dir / "openapi.yaml").write_text(
        yaml.dump(OPENAPI_V3_SPEC, allow_unicode=True), encoding="utf-8"
    )
    (serve_dir / "openapi.json").write_text(
        json.dumps(OPENAPI_V3_SPEC, indent=2), encoding="utf-8"
    )

    swagger_ui_html = textwrap.dedent("""\
        <!DOCTYPE html>
        <html><head><title>Swagger UI</title></head>
        <body>
        <script>
        const ui = SwaggerUIBundle({
            url: "/openapi.yaml",
            dom_id: '#swagger-ui'
        })
        </script>
        </body></html>
    """)
    (serve_dir / "swagger_ui.html").write_text(swagger_ui_html, encoding="utf-8")

    (serve_dir / "plain.html").write_text(
        "<html><body><h1>Hello World</h1><p>Just a page</p></body></html>",
        encoding="utf-8",
    )

    handler = partial(_QuietHandler, directory=str(serve_dir))
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
