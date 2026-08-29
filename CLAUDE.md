# Payment Emulator — гайд для Claude Code

Эмулятор платёжного провайдера (PSP mock) для тренировки агентов. **Деньги нигде
не двигаются** — это HTTP API, детерминированно возвращающее сценарии по тестовым
реквизитам, плюс веб-админка для просмотра истории. Реальных интеграций и боевых
систем здесь нет и быть не должно.

## Стек

- **FastAPI** (Python 3.12+), всё в одном процессе
- **SQLite** через async **SQLAlchemy 2.0** (`aiosqlite`), один файл `emulator.db`
- **Админка**: серверный рендеринг **Jinja2** + **HTMX** (вендорится локально в
  `app/static/htmx.min.js`, без npm/CDN)
- **Auth**: HTTP Basic Auth для агентского API; сессионные куки для админки
- **Фоновые переходы**: asyncio-задача в lifespan-хуке

## Запуск

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

- Админка: http://127.0.0.1:8000/admin  (seed-логин `admin` / `admin`)
- Агентское API: Basic Auth `agent` / `agent-secret`
- БД `emulator.db` создаётся при первом старте (в `.gitignore`)

Креды и секреты меняются через переменные окружения или `.env` (см.
[app/config.py](app/config.py)): `API_USERNAME`, `API_PASSWORD`,
`ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SESSION_SECRET`, `DATABASE_URL`.

## Сценарии по суффиксу реквизита

Поведение платежа определяется последними 4 цифрами реквизита. Источник истины —
[app/scenarios.py](app/scenarios.py), в роутах логика не хардкодится.

| Суффикс | Сценарий | Поведение |
|---|---|---|
| `0001` | `instant_success` | `success` сразу |
| `0002` | `instant_decline` | `failed` сразу (decline) |
| `0003` | `delayed_success` | `pending` → `success` через задержку |
| `0004` | `delayed_failure` | `pending` → `failed` через задержку |
| `0005` | `timeout_unknown` | `pending` → `unknown` (зависает, эмуляция таймаута) |
| любой другой | `default_success` | `success` сразу |

Задержки не хардкодятся: стартовые значения (0003/0004 = 10с, 0005 = 15с) сидятся
в таблицу `scenario_settings` и меняются на лету через `/admin/settings` —
фоновая задача читает актуальную задержку на каждой итерации, перезапуск не нужен.

## Эндпоинты

### Агентское API (HTTP Basic Auth)
- `POST /check` — проверка реквизитов без создания платежа. Результат в поле
  `status` (`allowed` | `declined`), HTTP всегда 200. Возвращает также
  `holder_name` (ФИО, детерминированно по реквизиту) и эхо `currency`.
- `POST /pay` — инициирует платёж. Обязателен заголовок `Idempotency-Key`
  (повтор с тем же ключом возвращает тот же платёж). Ответ — подтверждение
  приёма `status: "accepted"`; реальный исход узнаётся через `/status`.
- `GET /status/{payment_id}` — текущий статус платежа.

### Админка (сессионная авторизация)
- `GET /admin/login`, `POST /admin/login`, `GET /admin/logout`
- `GET /admin/payments` — список с фильтром по статусу и поиском по id/реквизиту
- `GET /admin/payments/{id}` — деталь + таймлайн истории; пока платёж не финален,
  блок статуса сам обновляется по HTMX (`/admin/payments/{id}/status-block`)
- `GET /admin/settings`, `POST /admin/settings` — редактирование задержек

## Жизненный цикл платежа

`/pay` всегда подтверждает приём (`accepted`). Далее:
- мгновенные сценарии (0001/0002/default) сразу в финальном статусе;
- отложенные (0003/0004/0005) висят в `pending`, фоновая задача переводит их в
  финал по истечении `created_at + задержка(суффикс)`.

История переходов пишется в `payment_status_history` (это и есть «лог» админки):
`accepted` → `pending` → финал, либо `accepted` → финал для мгновенных.

Финальные статусы (`success`, `failed`, `unknown`) фоновой задачей больше не
двигаются.

## Структура

```
app/
├── main.py          # точка входа: lifespan, middleware, роутеры
├── config.py        # настройки (env/.env)
├── database.py      # async engine/session
├── db_init.py       # создание таблиц + seed (сценарии, админ)
├── models.py        # Payment, PaymentStatusHistory, ScenarioSetting, AdminUser
├── scenarios.py     # таблица сценариев (источник истины)
├── schemas.py       # pydantic-схемы агентского API
├── payments.py      # создание платежа + идемпотентность
├── holder.py        # детерминированная генерация ФИО
├── background.py    # asyncio-задача автоперехода статусов
├── api_auth.py      # HTTP Basic Auth (агентское API)
├── admin_auth.py    # сессионная авторизация (админка)
├── routes_api.py    # /check, /pay, /status
├── routes_admin.py  # /admin/*
├── templating.py    # Jinja2 + фильтры money/dt
├── templates/       # base, login, payments_list, payment_detail, _status_block, settings
└── static/          # htmx.min.js (вендор)
```

## Тестовые реквизиты (примеры)

```bash
# check
curl -u agent:agent-secret -X POST localhost:8000/check \
  -H "Content-Type: application/json" \
  -d '{"requisite":"4111111111110001","amount":10000,"currency":"RUB"}'

# pay (нужен Idempotency-Key); amount — в минимальных единицах (10000 = 100.00)
curl -u agent:agent-secret -X POST localhost:8000/pay \
  -H "Content-Type: application/json" -H "Idempotency-Key: demo-1" \
  -d '{"requisite":"4111111111110003","amount":10000}'

# status
curl -u agent:agent-secret localhost:8000/status/<payment_id>
```
