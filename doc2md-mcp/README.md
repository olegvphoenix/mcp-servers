# DOC2MD MCP Server

MCP-сервер для конвертации документов в Markdown. Поддерживает:
- **PDF** — через `pymupdf4llm` (с автоматическим OCR для документов с картинками)
- **Swagger / OpenAPI** (YAML, JSON) — собственный рендерер
- **Веб-страницы** — через Crawl4AI (headless-браузер, поддержка JS-рендеренных SPA)

Сконвертированные файлы помещаются в подпапку `doc2md_export/` рядом с исходниками, вместе с журналом конвертации `doc2md_log.json`.

## Установка зависимостей

```bash
pip install -r requirements.txt
crawl4ai-setup          # скачивает Chromium для Crawl4AI (~170 MB, один раз)
```

## Подключение к Cursor

Добавьте в `~/.cursor/mcp.json` (глобально) или `.cursor/mcp.json` (в проекте):

```json
{
  "mcpServers": {
    "doc2md": {
      "command": "python",
      "args": ["D:/AxxonSoft/Src/doc2md-mcp/server.py"]
    }
  }
}
```

## Доступные инструменты

### PDF

#### `convert_pdf_to_markdown`
Конвертирует PDF в Markdown и сохраняет файл.
- `pdf_path` — путь к PDF
- `output_path` (опционально) — куда сохранить .md
- `page_chunks` (опционально) — разделять по страницам
- `force` (опционально) — принудительная переконвертация

#### `convert_all_pdfs_in_folder`
Конвертирует все PDF в папке.
- `folder_path` — путь к папке
- `output_folder` (опционально) — куда сохранить
- `recursive` (опционально) — включая подпапки
- `force` (опционально) — принудительная переконвертация

#### `read_pdf_as_markdown`
Читает PDF и возвращает Markdown (без сохранения на диск).
- `pdf_path` — путь к PDF

### Swagger / OpenAPI

#### `convert_swagger_to_markdown`
Конвертирует Swagger/OpenAPI спецификацию (YAML/JSON) в читаемый Markdown.
- `swagger_path` — путь к файлу спецификации
- `output_path` (опционально) — куда сохранить .md
- `force` (опционально) — принудительная переконвертация

#### `convert_all_swagger_in_folder`
Конвертирует все Swagger/OpenAPI файлы в папке.
- `folder_path` — путь к папке
- `recursive` (опционально) — включая подпапки
- `force` (опционально) — принудительная переконвертация

### Веб-страницы

#### `convert_url_to_markdown`
Конвертирует веб-страницу в Markdown через headless-браузер (Crawl4AI). Поддерживает JS-рендеренные SPA (Postman Documenter и др.).
- `url` — адрес страницы
- `output_path` (опционально) — куда сохранить .md
- `output_dir` (опционально) — базовая папка для экспорта
- `wait_for` (опционально) — CSS-селектор, которого ждать перед извлечением (например, `css:.content`)
- `force` (опционально) — принудительная переконвертация

#### `convert_urls_to_markdown`
Пакетная конвертация списка URL.
- `urls` — список URL через запятую или перенос строки
- `output_dir` (опционально) — базовая папка для экспорта
- `wait_for` (опционально) — CSS-селектор (общий для всех URL)
- `force` (опционально) — принудительная переконвертация

### Журнал

#### `get_conversion_log`
Показывает журнал конвертации для указанной папки.
- `folder_path` — путь к папке

## Переменные окружения (опционально)

- `DOC2MD_OUTPUT_DIR` — папка по умолчанию для сохранения .md файлов
