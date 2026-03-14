# IPCodex — Project Context & Technical Specification

## Product Vision

**IPCodex** — коммерческий standalone-продукт (платформа) для управления и версионирования API-документации физических устройств (IP-камеры, POS-терминалы, СКУД, IoT-устройства).

### Ключевая идея
Компании, интегрирующие физические устройства, тратят значительное время на работу с хаотичной документацией от производителей. IPCodex решает это, преобразуя документацию в структурированный Markdown, и делает её доступной для AI-помощников в IDE (Cursor, Antigravity, Windsurf, GitHub Copilot) через RAG + MCP.

### Elevator Pitch
IPCodex превращает хаотичную документацию физических устройств (PDF, Swagger, веб-страницы) в **структурированную базу знаний**, на основе которой **AI-помощники пишут рабочий код интеграции**. Прямых аналогов на рынке нет.

---

## Architecture Overview

### Phase 1 — Document Management Platform

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│  React Frontend │────▶│ FastAPI      │────▶│ PostgreSQL  │
│  (TypeScript)   │     │ Backend      │     │ (metadata)  │
└─────────────────┘     └──────┬───────┘     └─────────────┘
                               │
                        ┌──────▼───────┐
                        │ MinIO / S3   │
                        │ (Markdown)   │
                        └──────────────┘
```

**Компоненты:**
- **Frontend**: React + TypeScript SPA
- **Backend**: FastAPI + uvicorn (Python)
- **Database**: PostgreSQL 16 (метаданные устройств, версий, аудит)
- **Object Storage**: MinIO / AWS S3 (хранение Markdown файлов)
- **Background Jobs**: Celery / FastAPI BackgroundTasks (конвертация документов)
- **Deployment**: Docker Compose (single `docker-compose up`)

### Phase 2 — AI/RAG Layer

```
┌──────────────┐     ┌──────────────┐     ┌─────────────────┐
│ Chunking     │────▶│ Embedding    │────▶│ PostgreSQL +    │
│ Pipeline     │     │ Service      │     │ pgvector        │
└──────────────┘     └──────────────┘     └────────┬────────┘
                                                    │
                     ┌──────────────┐     ┌────────▼────────┐
                     │ MCP Server   │◀────│ RAG Engine      │
                     │ (AI IDEs)    │     │ (search+rerank) │
                     └──────────────┘     └────────┬────────┘
                                                    │
                                          ┌────────▼────────┐
                                          │ AI Chat         │
                                          │ (web interface) │
                                          └─────────────────┘
```

**Компоненты Phase 2:**
- **Chunking Pipeline**: Разбиение Markdown по заголовкам (H1/H2/H3) на семантические фрагменты
- **Embedding Service**: Векторизация чанков (OpenAI text-embedding-3-small или локальный all-MiniLM-L6-v2)
- **pgvector**: PostgreSQL extension для хранения и поиска по векторным embeddings
- **HNSW Index**: Cosine similarity search (работает на любом объёме, не требует минимума строк как IVFFlat)
- **MCP Server**: Model Context Protocol для интеграции с AI IDE (Cursor, Antigravity, Windsurf)
- **AI Chat**: Веб-интерфейс для общения с документацией через LLM

---

## Core Conversion Engine

Ядро конвертации уже реализовано в `doc2md-mcp/server.py`. Основные пайплайны:

### 1. PDF → Markdown
- Поддержка OCR для сканированных PDF (pytesseract / Tesseract)
- Пользователь указывает язык OCR
- Опциональный перевод на английский перед конвертацией
- Дедупликация по хешу содержимого

### 2. Swagger/OpenAPI → Markdown
- Парсинг JSON/YAML спецификаций
- Структурированный вывод: endpoints, параметры, responses
- Поддержка OpenAPI 3.x и Swagger 2.0

### 3. URL (Web Page) → Markdown
- Скрапинг веб-страниц
- Очистка HTML, извлечение контента
- Обработка JavaScript-rendered страниц

### Multilingual Support
- Входящая документация на **любом языке** — система определяет формат
- Опциональный перевод на английский **до** конвертации в Markdown
- OCR язык указывается пользователем

---

## S3 Key Structure

```
ipcodex/
  devices/{device_id}/
    {firmware_version}/{document_title}/
      v1.md
      v2.md
      v3.md
```

---

## Data Model (PostgreSQL)

### Core Tables
- **devices** — каталог устройств (manufacturer, model, category)
- **firmware_versions** — версии прошивок для каждого устройства
- **documents** — метаданные документов (title, format, s3_key, hash)
- **document_versions** — история версий каждого документа
- **import_jobs** — аудит-лог всех импортов (status, source, timestamps)
- **users** — пользователи системы

### Phase 2 Tables
- **chunks** — семантические фрагменты документов
- **embeddings** — векторные представления (pgvector column)
- **chat_sessions** — сессии AI чата
- **chat_messages** — сообщения в чатах

---

## Business Model

### Revenue Streams

1. **B2B SaaS Subscription**
   - Free: 3 устройства, базовый импорт
   - Pro ($99/мес): 50 устройств, AI Chat, MCP
   - Team ($299/мес): 200 устройств, приоритетная поддержка
   - Enterprise ($999+/мес): безлимит, on-premise, SLA

2. **On-Premise License**
   - Единоразово от $5K + годовая поддержка 20%
   - Для компаний с требованиями безопасности

3. **Vendor Marketplace**
   - Производители устройств публикуют официальную документацию
   - Комиссия 15-30% с подписки

4. **AI API (usage-based)**
   - $0.01-0.05 за запрос к RAG
   - Для интеграции в сторонние продукты

### Market Size
- **TAM**: $2.4B (глобальный рынок технической документации)
- **SAM**: $340M (IoT/физические устройства)
- **SOM**: $12M (первые 3 года, интеграторы видеонаблюдения + СКУД)

### Target Audience
1. Системные интеграторы видеонаблюдения и СКУД
2. IoT-платформы и разработчики
3. Производители устройств (Hikvision, Dahua, Axis, etc.)
4. Enterprise с большим парком устройств

---

## Infrastructure Costs

### MVP / Starter ($50-100/мес)
- VPS 4 vCPU / 8GB RAM — $30-50
- PostgreSQL (managed) — $15-25
- MinIO (self-hosted) — $0
- OpenAI API — $5-15
- Подходит для 1-20 устройств, 1-5 пользователей

### Production / Pro ($200-400/мес)
- VPS 8 vCPU / 16GB RAM — $80-120
- PostgreSQL + pgvector — $50-80
- S3 storage — $20-40
- AI APIs (embedding + chat) — $50-100
- Подходит для 100+ устройств, 10-50 пользователей

### Enterprise ($800-2000+/мес)
- Kubernetes cluster — $300-600
- Managed PostgreSQL HA — $200-400
- S3 / dedicated storage — $100-300
- AI APIs + self-hosted models — $200-500
- Подходит для 1000+ устройств, 100+ пользователей

**On-premise**: нет облачных затрат, только оборудование. Локальные embedding модели (all-MiniLM) устраняют расходы на OpenAI API.

---

## Competitive Landscape

### Adjacent Market Players

| Product | Type | Funding | What They Do | IPCodex Difference |
|---------|------|---------|-------------|-------------------|
| **Context7** (Upstash) | MCP for software libs | Upstash-backed | Доставляет документацию софтверных библиотек (React, Next.js) в AI IDE через MCP | Только софт-библиотеки. Нет PDF/OCR, нет устройств, нет конвертации |
| **Documentation.AI** | Docs creation platform | #1 Product Hunt | AI-платформа для создания документации продуктов. AI-агент, MCP, llms.txt | Пользователи пишут свои docs. Нет импорта из PDF, нет OCR, нет версионирования по прошивкам |
| **Mintlify** | Docs platform | YC W22, $18.5M | Красивая документация для разработчиков. Swagger import, AI search | Нет PDF/OCR, нет firmware versioning, нет MCP |
| **GitBook** | Docs platform | Est. 2014 | Git-based документация, AI search, командная работа | Нет PDF/OCR, git branches ≠ firmware versions, нет MCP |
| **ReadMe.com** | API docs | $34M raised | API-документация с playground, Swagger import, analytics | Нет PDF/OCR, нет firmware versioning, нет MCP |

### Ключевой вывод
Рынок AI-документации **валидирован** хорошо профинансированными компаниями (Mintlify $18.5M, ReadMe $34M). Они доказывают, что разработчики платят за инструменты документации. IPCodex занимает **незанятую вертикаль** — документация физических устройств — применяя проверенные паттерны (MCP, RAG, AI Chat) к рынку без специализированного решения.

### Потенциальные партнёрства
- **Context7**: IPCodex-документация устройств может быть проиндексирована Context7, делая hardware APIs доступными через их MCP рядом с софтверными библиотеками
- **Documentation.AI**: Вендоры могут использовать Documentation.AI для публикации документации устройств, пока IPCodex обрабатывает конвертацию и AI-обогащение

Подробный анализ: `IPCodex/comparison.html`

---

## Competitive Advantages

1. **Нет прямых конкурентов** — ни один продукт не специализируется на API-документации физических устройств + AI
2. **Валидированный рынок** — Adjacent players (Context7, Documentation.AI, Mintlify) подтверждают спрос на AI-документацию
3. **Вертикальная ниша** — глубокая экспертиза в домене (видеонаблюдение, СКУД, IoT)
4. **Network effects** — чем больше документации, тем ценнее база знаний
5. **Vendor lock-in** — интеграторы зависят от накопленной базы
6. **MCP Protocol** — нативная интеграция для AI IDE (как Context7, но для устройств)
7. **On-premise** — критично для security-sensitive интеграторов (чего нет у конкурентов)

---

## Tech Stack Summary

| Layer | Technology |
|-------|-----------|
| Frontend | React + TypeScript |
| Backend | Python, FastAPI, uvicorn |
| Database | PostgreSQL 16 + pgvector |
| Object Storage | MinIO / AWS S3 |
| Background Jobs | Celery / BackgroundTasks |
| OCR | Tesseract / pytesseract |
| Embedding | OpenAI text-embedding-3-small / all-MiniLM-L6-v2 |
| Vector Search | pgvector (HNSW index, cosine similarity) |
| AI Chat | LLM (GPT-4 / Claude / self-hosted) |
| MCP Server | Model Context Protocol (Python SDK) |
| Deployment | Docker Compose / Kubernetes |
| SSL | Configurable via env (default: strict) |

---

## Key Technical Decisions

1. **HNSW vs IVFFlat**: Выбран HNSW для pgvector — работает на любом объёме данных, не требует минимального количества строк (IVFFlat требует sqrt(n) для кластеризации)
2. **Chunking strategy**: По заголовкам Markdown (H1/H2/H3) — сохраняет семантическую целостность API-секций
3. **Embedding model**: OpenAI text-embedding-3-small для облака, all-MiniLM-L6-v2 для on-premise (без внешних зависимостей)
4. **S3 versioning**: Собственная нумерация v1/v2/v3 вместо S3 native versioning — для удобства UI и diff
5. **Translation before conversion**: Перевод на английский до Markdown-конвертации, а не после — лучшее качество OCR/парсинга
6. **Single docker-compose**: Вся платформа запускается одной командой для MVP

---

## File Structure (Current)

```
IPCodex/
├── ipcodex.html              # Интерактивная презентация (EN/RU, light/dark)
├── comparison.html            # Детальный конкурентный анализ
├── ipcodex-logo.png           # Логотип для светлой темы (512px PNG)
├── ipcodex-logo-dark.png      # Логотип для тёмной темы (512px PNG)
├── ipcodex-plan-backup.html   # Резервная копия ранней версии
├── CONTEXT.md                 # Этот файл — полный контекст проекта
```

---

## Presentation Features (ipcodex.html)

- **Интерактивная одностраничная презентация** для инвесторов/партнёров
- **Двуязычность**: EN (default) / RU с переключателем
- **Темы**: Светлая (default) / Тёмная с переключателем
- **Логотип**: Гексагон + `</>` с градиентом blue→purple→pink, разные версии для тем
- **Секции**: Hero, Проблемы, Цели, Elevator Pitch, Demo Flow, Revenue Model, ROI Calculator, TAM/SAM/SOM, Target Audience, ICP, GTM, Competitive Landscape, Revenue Roadmap, Key Metrics, Moat, Competitive Advantages, Tech Details (tabs: Architecture, Database, S3, Phase 2), Infrastructure Costs
- **Интерактивность**: Анимированный Demo Flow, ROI Calculator, аккордеоны, scroll animations, progress navigation
- **Мобильная адаптивность**: Оптимизировано для iPhone и планшетов
- **Автор**: Войтехович Олег (LinkedIn: https://www.linkedin.com/in/aleh-vaitsekhovich-067557a9/)

---

## GitHub Pages

- **URL**: https://olegvphoenix.github.io/mcp-servers/
- **index.html** в корне репозитория делает redirect на `IPCodex/ipcodex.html`
- Ветка: `main`, папка: `/` (root)

---

## Next Steps (Planned)

1. **Backend MVP**: FastAPI + PostgreSQL + MinIO (Docker Compose)
2. **Import Wizard UI**: React компонент для загрузки документов
3. **Device Catalog**: CRUD для устройств и прошивок
4. **Markdown Viewer**: Рендеринг сконвертированных документов с diff
5. **Phase 2 — RAG**: pgvector + chunking + embedding pipeline
6. **Phase 2 — AI Chat**: Веб-интерфейс для общения с документацией
7. **Phase 2 — MCP Server**: Интеграция с AI IDE

---

## Existing Codebase to Reuse

Файл `doc2md-mcp/server.py` содержит готовые функции конвертации:
- `convert_pdf_to_markdown()` — PDF → Markdown с OCR
- `convert_swagger_to_markdown()` — OpenAPI/Swagger → Markdown
- `convert_url_to_markdown()` — Web page → Markdown
- Progress reporting, deduplication, audit logging
- Environment variables для настройки
- SSL handling (configurable: strict/relaxed)

Эти функции будут импортированы и использованы бэкендом FastAPI.
