# AI Product Builder — тестовое задание

Этот README — единственная точка входа в submission-пакет. Здесь собраны навигация, команды и проверенные результаты трёх частей задания:

- **Часть 1** — реализованный и аудированный Python-инструмент анализа и подбора Instagram-креаторов;
- **Часть 2** — продуктовая гипотеза автоматической генерации fashion-видео на Remotion;
- **Часть 3** — краткое описание трёх реально существующих AI-assisted прототипов.

## Быстрый просмотр

Если времени мало, достаточно открыть:

1. [Финальный Excel Части 1](output/phase_b/live-20260728T211907Z-manual-final/Блогеры_phase_b.xlsx)
2. [Финальный отчёт по подбору](output/phase_b/live-20260728T211907Z-manual-final/discovery_report.md)
3. [Runtime-промпты и шаблоны Части 1](docs/part-1-prompts.md)
4. [Часть 2 — Remotion product videos](docs/part-2-remotion-product-videos.md)
5. [Часть 3 — проекты и доказательства](docs/part-3-projects.md)
6. [Финальный аудит submission](docs/audits/final-submission-audit-2026-07-29.md)

## Часть 1. Анализ и подбор Instagram-креаторов

### Что реализовано

Python 3.12 CLI:

- валидирует и нормализует исходные JSON/XLSX;
- строит прозрачный портрет идеального креатора;
- отделяет creators, brand references, private, unresolved и insufficient-data профили;
- рассчитывает метрики и объяснимый score 0–100;
- поддерживает credential-free demo и guarded live provider через Apify;
- исключает source identities и historical aliases;
- выполняет deduplication, enrichment, account-type и eligibility проверки;
- сохраняет evidence provenance и human-in-the-loop решения;
- формирует персонализированные barter-offer drafts без отправки сообщений;
- экспортирует JSON, CSV, Markdown и XLSX.

Brand-reference accounts исключаются из creator statistics. Пропущенные значения не подменяются нулями, а engagement учитывает полноту пригодных наблюдений. Contact, commercial/PR, UGC, marketplace и native-integration сигналы оцениваются независимо.

### Установка и команды

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Phase A — полный credential-free анализ:

```powershell
.\.venv\Scripts\ai-product-builder demo
```

Детерминированный Phase B demo:

```powershell
.\.venv\Scripts\ai-product-builder phase-b validate-config `
  --config config/phase_b.demo.json

.\.venv\Scripts\ai-product-builder phase-b demo `
  --config config/phase_b.demo.json `
  --output-dir output/phase_b
```

Полный набор тестов:

```powershell
.\.venv\Scripts\python -m pytest
```

Live-конфигурация создаётся из [безопасного примера](config/phase_b.live.example.json). Секрет `APIFY_TOKEN` читается только из `.env`; файл `.env` не отслеживается Git.

```powershell
Copy-Item config/phase_b.live.example.json config/phase_b.live.json

.\.venv\Scripts\ai-product-builder phase-b validate-config `
  --config config/phase_b.live.json

.\.venv\Scripts\ai-product-builder phase-b run `
  --mode live `
  --config config/phase_b.live.json `
  --output-dir output/phase_b
```

Проверяющему не требуется запускать live mode: канонический результат уже сохранён. Для независимого offline-воспроизведения ручной финализации нужен новый пустой output-каталог:

```powershell
.\.venv\Scripts\ai-product-builder phase-b manual-finalize `
  --source-run output/phase_b/live-20260728T211907Z `
  --review-file data/reviews/phase_b/live-20260728T211907Z-manual-final.json `
  --output-dir output/phase_b/manual-final-check
```

Если live-процесс оборвался после уже завершённых Actor runs, CLI поддерживает отдельный recovery-путь по сохранённым run IDs без запуска новых Actors. Схему аргументов можно проверить локально:

```powershell
.\.venv\Scripts\ai-product-builder phase-b recover --help
```

### Архитектура

```text
Phase A
raw JSON + XLSX
  → validation and typed normalization
  → creator/reference classification
  → robust metrics and evidence
  → transparent scoring
  → ideal_creator_profile.json

Phase B
ideal profile + source exclusions + campaign config
  → fixture or Apify provider
  → normalization and deduplication
  → recent-post enrichment
  → account type and eligibility
  → scoring and evidence provenance
  → structured manual review
  → final JSON / CSV / Markdown / XLSX drafts
```

Основной код находится в [`src/ai_product_builder`](src/ai_product_builder), Phase B — в [`src/ai_product_builder/phase_b`](src/ai_product_builder/phase_b), тесты — в [`tests`](tests).

Creator score остаётся прозрачной суммой шести компонентов: content/aesthetic fit — 30, native integration — 20, short-video consistency — 15, engagement — 15, barter feasibility — 10, recent activity — 10. Discovery confidence и data completeness хранятся отдельно и не подменяют score. Точные формулы, evidence и missing-data policy приведены в [scoring methodology](output/scoring_methodology.md).

### Результаты Phase A

- [Портрет идеального креатора](output/ideal_creator_profile.json)
- [Анализ исходных профилей](output/source_analysis.csv)
- [Data-quality report](output/data_quality_report.json)
- [Аналитический отчёт](output/analysis_report.md)
- [Scoring methodology](output/scoring_methodology.md)

### Канонический manual-final результат

Каталог: [`output/phase_b/live-20260728T211907Z-manual-final`](output/phase_b/live-20260728T211907Z-manual-final)

| Креатор | Score | Manual status | Outreach status |
|---|---:|---|---|
| `verkhovskaya_style` | 51.57 | `approved` | `not_sent` |
| `olganemka_stylist` | 54.66 | `approved` | `not_sent` |
| `angelashegiryan` | 57.70 | `pending` | `not_sent` |

Список намеренно не дополнен до пяти: после автоматических правил и ручной evidence-backed проверки осталось три качественных кандидата. Ни одно сообщение не отправлялось.

Ключевые артефакты:

- [Финальные creators — JSON](output/phase_b/live-20260728T211907Z-manual-final/new_creators.json)
- [Финальные creators — CSV](output/phase_b/live-20260728T211907Z-manual-final/new_creators.csv)
- [Barter-offer drafts](output/phase_b/live-20260728T211907Z-manual-final/barter_offer_drafts.md)
- [Evidence выбранных публикаций](output/phase_b/live-20260728T211907Z-manual-final/selected_post_evidence.json)
- [Structured manual-review audit](output/phase_b/live-20260728T211907Z-manual-final/manual_review_audit.json)
- [Exclusions](output/phase_b/live-20260728T211907Z-manual-final/excluded_candidates.csv)
- [Run manifest](output/phase_b/live-20260728T211907Z-manual-final/run_manifest.json)

### Промпты и аудит

[Промпты и шаблоны Части 1](docs/part-1-prompts.md) документируют фактическое runtime-поведение: deterministic Phase A analysis, общий offer template, canonical manual-review template, evidence requirements, validation и отсутствие LLM-вызовов в canonical run.

Структурированные решения ручной проверки сохранены в [review JSON](data/reviews/phase_b/live-20260728T211907Z-manual-final.json). Полный технический и security-аудит: [Part 1 final audit](docs/audits/part-1-final-audit-2026-07-29.md).

## Часть 2. Remotion product videos

[Открыть одностраничное описание продуктовой гипотезы](docs/part-2-remotion-product-videos.md).

Это предложение следующего применения существующего data-to-video подхода: данные SKU → проверка → AI-сценарий → Remotion → MP4 → QA → ручное утверждение. Fashion-адаптация и интеграция с конкретным маркетплейсом не заявлены как уже реализованный продукт.

## Часть 3. Проекты

[Открыть краткое описание трёх проектов](docs/part-3-projects.md):

- CS2 Data-to-Video Pipeline на Remotion;
- AURA;
- Taxi Copilot.

Все три описаны как AI-assisted прототипы с разделением моей продуктовой роли, подтверждённых результатов и текущих ограничений.

Короткий набор проверенных доказательств:

- **CS2 / Remotion:** [post-match MP4](docs/assets/remotion/cs2-post-match-falcons-vs-9z.mp4), [bracket MP4](docs/assets/remotion/cs2-tournament-bracket.mp4) и [кадр статистического экрана](docs/assets/remotion/cs2-post-match-preview.png).
- **AURA:** [Today](docs/assets/aura/aura-today.jpg), [решения](docs/assets/aura/aura-decisions.jpg) и [рабочий контекст](docs/assets/aura/aura-work-context.jpg).
- **Taxi Copilot:** [параметры позиции](docs/assets/taxi-copilot/taxi-stay-position.jpg), [итоги смены](docs/assets/taxi-copilot/taxi-shift-summary.jpg) и [рекомендация STAY/MOVE](docs/assets/taxi-copilot/taxi-stay-move-recommendation.jpg).

Публичные ссылки намеренно не придуманы. Код проектов находится в приватных репозиториях; доступ и демонстрация — по запросу.

## Time log

Точный таймер не вёлся. Время восстановлено приблизительно по истории работы и включает анализ, постановку задач AI coding agents, ручную проверку, исправления, тестирование и подготовку материалов.

| Часть | Приблизительное время | Что вошло |
|---|---:|---|
| 1 | ≈ 16 часов | Анализ данных, реализация Phase A и Phase B, live-поиск, ручная проверка, исправления и тесты |
| 2 | ≈ 1 час | Формулировка продуктовой гипотезы, пилота, метрик и ограничений |
| 3 | ≈ 1,5 часа | Отбор проектов, проверка подтверждений, подготовка описаний и материалов |
| **Всего** | **≈ 18,5 часа** | Все три части submission |

Подтверждённый дополнительный spend live-поиска Части 1 — `$0.1359`; это расход провайдера, а не оценка рабочего времени.

## Статус и ограничения

- **Часть 1:** реализована, протестирована и независимо аудирована; canonical shortlist содержит три профиля. Instagram-ссылки и доступность профилей могут измениться после сохранённого run.
- **Часть 2:** продуктовая гипотеза и план пилота, не реализованный marketplace-продукт. Перед реальным внедрением обязательный prerequisite — проверить актуальные требования конкретного маркетплейса к видео, форматам, длительности и процессу загрузки.
- **Часть 3:** описание локальных демонстрационных прототипов, не production-сервисов.
- В проекте нет автоматической отправки outreach; все предложения остаются drafts.
- Google Sheets adapter покрыт тестами, но authenticated live-запись в рамках финального аудита не выполнялась.
- Для live discovery требуется Apify и явная конфигурация бюджета; demo и проверка canonical результата не требуют credentials.

## Checklist для проверяющего

1. Открыть [канонический Excel](output/phase_b/live-20260728T211907Z-manual-final/Блогеры_phase_b.xlsx) и проверить листы `Исходник` и `Новые блоггеры`.
2. Сверить три строки с [new_creators.json](output/phase_b/live-20260728T211907Z-manual-final/new_creators.json) и [discovery report](output/phase_b/live-20260728T211907Z-manual-final/discovery_report.md).
3. Проверить evidence и статусы в [manual-review audit](output/phase_b/live-20260728T211907Z-manual-final/manual_review_audit.json).
4. Сверить фактические runtime-шаблоны по документу [Промпты и шаблоны Части 1](docs/part-1-prompts.md).
5. Прочитать [финальный аудит submission](docs/audits/final-submission-audit-2026-07-29.md).
6. При необходимости установить Python 3.12 dependencies и выполнить `python -m pytest`.
7. Перейти из этого README к [Части 2](docs/part-2-remotion-product-videos.md), [Части 3](docs/part-3-projects.md) и приложенным доказательствам.
