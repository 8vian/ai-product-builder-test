# Часть 1. Runtime-промпты и шаблоны

Этот документ описывает только prompts/templates, которые фактически используются инструментом во время анализа и генерации офферов. История разработки с coding agents сюда не входит.

## 1. Анализ исходных профилей

**LLM не используется.**

Для анализа исходных профилей LLM-промпт не используется. Портрет и score формируются детерминированными правилами, метриками и evidence из сохранённых данных.

### Источники фактов

- `data/raw/instagram_profiles.json`;
- `data/raw/manual_verification_audit.json`;
- `data/raw/Блогеры.xlsx`.

Профили и публикации нормализуются в typed models. Затем Python-код рассчитывает аудиторию, медианы likes/comments, engagement и его полноту, форматы публикаций, активность и независимые content/commercial/contact-сигналы. Итоговый score складывается из шести детерминированных компонентов.

### Ограничения, ошибки и validation

- Отсутствующие значения не придумываются и не отправляются в LLM.
- Отрицательные like-count sentinels остаются missing.
- Профиль с менее чем шестью пригодными engagement-наблюдениями получает `insufficient_data` и не оценивается.
- Brand-reference accounts исключаются из creator statistics.
- Некорректные входные данные фиксируются validation/data-quality логикой; генеративного fallback нет.
- Компоненты score должны находиться в допустимых диапазонах и суммироваться в итоговый score; критичные формулы и edge cases проверяются тестами.

Связанные материалы:

- [Scoring methodology](../output/scoring_methodology.md)
- [Ideal creator profile](../output/ideal_creator_profile.json)
- [Phase A analysis code](../src/ai_product_builder/analysis.py)
- [Phase A reporting code](../src/ai_product_builder/reporting.py)
- [Основные Phase A тесты](../tests/test_analysis.py)
- [Risk-control и scoring тесты](../tests/test_risk_controls.py)

## 2. Общий Phase B offer generator

### Используется ли LLM

Нет. В текущем runtime нет LLM provider interface, LLM API-вызова, system prompt или user prompt для офферов. Имена `LLM_API_KEY`, `LLM_PROVIDER` и `LLM_MODEL` разрешены загрузчиком `.env` как зарезервированные настройки, но offer-generation код их не читает.

### Детерминированный русский шаблон

Ниже дословно экспортирован шаблон `_russian_offer` из `offers.py`; фигурные скобки показывают фактические переменные Python:

```text
Здравствуйте, {greeting_name}!

Увидели вашу недавнюю публикацию «{topic}»: {post.url}
Нам кажется, такой формат контента может естественно сочетаться с категорией «{campaign.product_category}».

Мы — {campaign.brand_name}. Хотим предложить для обсуждения {campaign.barter_item}: {campaign.product_name}. Если идея вам интересна, возможный формат — {campaign.desired_content_format}; детали и творческую подачу согласуем вместе.

Будем рады обсудить предложение, если оно вам подходит; никаких обязательств до согласования условий нет.

[Черновик: требуется ручная проверка и одобрение перед использованием.]
```

### Детерминированный английский шаблон

Если `campaign.language` не начинается с `ru`, используется `_english_offer`:

```text
Hello {greeting_name},

We noticed your recent post “{topic}”: {post.url}
Its format may be a natural fit for {campaign.product_category}.

We are {campaign.brand_name}, and would like to discuss {campaign.barter_item}: {campaign.product_name}. If the idea is interesting to you, a possible deliverable is {campaign.desired_content_format}; we would agree the details and creative approach together.

We would be happy to discuss it if it feels relevant—there is no obligation before the terms are agreed.

[Draft: manual review and approval are required before use.]
```

### Переменные и источники

| Переменная | Источник |
|---|---|
| `greeting_name` | сохранённый `profile.full_name`; fallback — точный `@username` |
| `topic` | сохранённый caption выбранной публикации; URL удаляются, whitespace нормализуется, длина ограничивается 110 символами |
| `post.url` | URL недавней публикации из сохранённого профиля |
| `campaign.brand_name` | campaign config |
| `campaign.product_name` | campaign config |
| `campaign.product_category` | campaign config |
| `campaign.barter_item` | campaign config |
| `campaign.desired_content_format` | campaign config |

### Evidence requirements и fallback

Публикация должна иметь Instagram post URL, непустой caption и дату в пределах настроенного окна, по умолчанию 90 дней. Сначала выбирается самая свежая публикация с direct content evidence. В live mode pipeline отдельно требует сигнал `fashion`; при его отсутствии fallback-публикация не используется. В общем demo-пути, если публикации с content evidence нет, допускается самая свежая валидная публикация с caption.

Если подходящей публикации нет, `EvidenceValidationError` переводит кандидата в `ineligible` с причиной `offer_grounding_evidence_missing`. LLM-fallback отсутствует.

### Запрещённые утверждения

Validator отклоняет формулировки о том, что автор уже любит бренд, принимает или согласен на бартер, уже пользовался товаром, имеет определённую аудиторию или высокую вовлечённость, а также любые гарантии результата. Аналогичные английские claims также запрещены. Дополнительно проверяется каждый literal из `campaign.prohibited_claims`.

### Post-generation validation

`validate_offer_draft` требует:

- URL публикации принадлежит кандидату, является Instagram URL и присутствует в тексте;
- brand, product, barter item и requested format присутствуют в тексте;
- статус равен `pending`;
- personalization имеет direct caption evidence с тем же URL;
- content-fit утверждение имеет отдельный direct evidence-сигнал;
- текст не содержит встроенные unsupported claims и campaign prohibited claims.

При любой ошибке draft не сохраняется. У созданного результата `generation_mode=deterministic_template`; `CandidateResult` допускает только `outreach_status=not_sent`.

Код и тесты:

- [Offer generator и validator](../src/ai_product_builder/phase_b/offers.py)
- [Pipeline integration](../src/ai_product_builder/phase_b/pipeline.py)
- [Offer model и draft-only guard](../src/ai_product_builder/phase_b/models.py)
- [Offer unit tests](../tests/test_phase_b_core.py)
- [Pipeline tests](../tests/test_phase_b_pipeline.py)

## 3. Канонический manual-final offer template

Канонический результат сформирован не общим шаблоном выше, а отдельным `_offer_text` из `manual_finalization.py`. Он также полностью детерминирован.

### Точный шаблон

```text
{first_name}, здравствуйте!

Мы — LD Latte, бренд женской одежды. {offer_personalization}

Хотим предложить бартер: товар из новой коллекции LD Latte в обмен на согласованный Reels или нативный обзор. Нам важно сохранить ваш авторский стиль: без жёсткого сценария, но с предварительным согласованием выбранной вещи, ключевых акцентов и состава контента.

{offer_closing}
```

Если `offer_closing` отсутствует, дословный fallback:

```text
Если вам откликается идея, обсудим размер, подходящую модель, сроки и формат публикации.
```

### Переменные и факты

`first_name`, `offer_personalization`, `offer_closing`, `selected_post_url`, `post_evidence_summary`, decision reason и manual status поступают из [structured review JSON](../data/reviews/phase_b/live-20260728T211907Z-manual-final.json). Профиль и публикация берутся из сохранённого `enriched_candidates.jsonl` исходного run.

`first_name` и `offer_personalization` обязательны. Выбранный URL должен точно существовать среди сохранённых публикаций кандидата, а caption должен содержать прямой clothing/fashion сигнал. `post_evidence_summary` обязателен и сохраняется как direct offer-personalization evidence. Персонализация в review JSON была подготовлена и утверждена человеком; она не генерировалась LLM.

Код не проверяет `offer_personalization` и `post_evidence_summary` на дословное совпадение с caption: доверенной границей для этих двух полей является structured human review.

### Ошибки, validation и статус

- Missing personalization, неизвестный post URL, отсутствие fashion/clothing evidence или неполный review останавливают finalization через `InputValidationError`; генеративного fallback нет.
- Все кандидаты сохранённого eligible pool должны иметь review decision; выбираются ровно три записи с уникальным порядком 1–3.
- Rejected кандидаты получают пустой offer и `offer_generation_mode=none`.
- Выбранные записи получают `offer_generation_mode=deterministic_manual_review_template`, review status `approved` или `pending` и `outreach_status=not_sent`.
- Общий `validate_offer_draft` повторно не вызывается для `_offer_text`; безопасность canonical текста обеспечивается фиксированным шаблоном, structured human review и manual-finalization тестами. Это текущее ограничение реализации.

Канонические доказательства:

- [Фактические drafts](../output/phase_b/live-20260728T211907Z-manual-final/barter_offer_drafts.md)
- [Финальные записи и generation mode](../output/phase_b/live-20260728T211907Z-manual-final/new_creators.json)
- [Selected post evidence](../output/phase_b/live-20260728T211907Z-manual-final/selected_post_evidence.json)
- [Manual-review audit](../output/phase_b/live-20260728T211907Z-manual-final/manual_review_audit.json)
- [Manual-finalization implementation](../src/ai_product_builder/phase_b/manual_finalization.py)
- [Manual-finalization tests](../tests/test_phase_b_manual_finalization.py)

## 4. Использование LLM в canonical run

**LLM enhancement не использовался.** В canonical records указан `offer_generation_mode=deterministic_manual_review_template`. В текущем коде отсутствуют LLM offer provider и system/user prompts, поэтому deterministic fallback был не аварийной заменой LLM, а единственным реализованным способом генерации. Все три результата остаются drafts, `outreach_status=not_sent`; один draft имеет status `pending`.
