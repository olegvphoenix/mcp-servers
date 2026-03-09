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
- `ocr` (опционально) — режим OCR: `"auto"` (определяет картинки >= 100k px автоматически), `"always"`, `"off"`. По умолчанию `"auto"`
- `ocr_languages` (опционально) — языки OCR через запятую, например `"en"` или `"en,ru"`. По умолчанию `"en"`

#### `convert_all_pdfs_in_folder`
Конвертирует все PDF в папке.
- `folder_path` — путь к папке
- `output_folder` (опционально) — куда сохранить
- `recursive` (опционально) — включая подпапки
- `force` (опционально) — принудительная переконвертация
- `ocr` (опционально) — режим OCR: `"auto"`, `"always"`, `"off"`. По умолчанию `"auto"`
- `ocr_languages` (опционально) — языки OCR через запятую. По умолчанию `"en"`

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

## Прогресс-репортинг

При конвертации PDF сервер отправляет гранулярные обновления прогресса через MCP `report_progress`:

- **Hashing** — вычисление SHA-256 хеша файла
- **Detecting OCR pages** — определение страниц с крупными картинками
- **[1/N] Parse X/Yp** — постраничный парсинг PDF (N=1 без OCR, N=2 с OCR)
- **[2/2] Loading OCR model** — загрузка модели EasyOCR (первый запуск)
- **[2/2] OCR X/Yimg** — распознавание текста из картинок
- **[2/2] OCR done** — OCR завершён
- **Saving** — сохранение .md файла
- **Done** — конвертация завершена

В журнале конвертации (`doc2md_log.json`) логгируются три отдельных тайминга:
- `duration_sec` — общее время
- `duration_parse_sec` — время парсинга PDF
- `duration_ocr_sec` — время OCR

## Тесты

166 тестов покрывают: хелперы, OCR-пайплайн, Swagger/OpenAPI, HTTP-детектирование, прогресс-репортинг, tool-функции и E2E-конвертации.

```bash
cd doc2md-mcp
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

Маркеры:
- `e2e` — интеграционные тесты с реальными PDF/Swagger/HTTP конвертациями
- `slow` — тесты с загрузкой модели OCR (EasyOCR)

Запуск без slow-тестов:

```bash
python -m pytest tests/ -v -m "not slow"
```

## Переменные окружения (опционально)

- `DOC2MD_OUTPUT_DIR` — папка по умолчанию для сохранения .md файлов
