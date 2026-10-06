# Payment Emulator — гайд по проекту

Эмулятор платёжного провайдера (PSP mock) для тренировки агентов. **Деньги нигде
не двигаются** — это HTTP API, детерминированно возвращающее сценарии по тестовым
реквизитам, плюс веб-админка для просмотра истории. Реальных интеграций и боевых
систем здесь нет и быть не должно.

Два независимых контура:
- **Платежи** (`/check`, `/pay`, `/status`) — сценарий по суффиксу реквизита.
- **Выдача наличных по кодам** (`/api/v2/check`, `/api/block`, `/api/pay`) —
  коды из 12 цифр генерируются в админке с суммой к выдаче; поддерживаются
  частичная выдача, OTP-заглушка и принудительные сценарии ошибок.

## Стек

- **FastAPI** (Python 3.12+), всё в одном процессе
- **PostgreSQL** через async **SQLAlchemy 2.0** (`asyncpg`); схема — миграциями
  **Alembic** (`migrations/`), применяются автоматически при старте. Postgres
  нужен ради честной конкурентности: `SELECT … FOR UPDATE` сериализует
  параллельные `/api/block` и `/api/pay` по одному коду (на SQLite такого нет)
- **Админка**: серверный рендеринг **Jinja2** + **HTMX** (вендорится локально в
  `app/static/htmx.min.js`, без npm/CDN)
- **Auth**: HTTP Basic Auth для агентского API, сессионные куки для админки;
  обе группы учёток хранятся в БД (bcrypt) и управляются в админке
- **Фоновые переходы**: asyncio-задача в lifespan-хуке

## Запуск

### Docker (основной способ)

Весь стенд — эмулятор и PostgreSQL — одной командой:

```bash
docker compose up -d --build
```

- Админка: http://localhost:8000/admin  (первый вход `admin` / `admin` — смените
  пароль: клик по логину в шапке)
- Агентское API: Basic Auth `agent` / `agent-secret` (учётки — в «Пользователи»)
- Таблицы создаются и обновляются миграциями при старте (`AUTO_MIGRATE=true`).
- Код смонтирован в контейнер, uvicorn запущен с `--reload`: правки в `app/`
  подхватываются сразу. Пересобирать образ (`--build`) нужно только после смены
  `requirements*.txt`.
- Логи приложения пишутся в `logs/` на хосте; логи контейнера —
  `docker compose logs -f app`.
- Postgres доступен и с хоста на `localhost:5432` (psql, IDE, pytest из `.venv`).
  Если порты заняты: `APP_PORT=8001 POSTGRES_PORT=5433 docker compose up -d`.
- Остановить — `docker compose down`. Данные БД живут в volume `pgdata`;
  полностью стереть их — `docker compose down -v`.

> На macOS команда `docker` появляется в PATH, только если в Docker Desktop
> включено *Settings → Advanced → System (requires password)*; иначе добавьте
> `/Applications/Docker.app/Contents/Resources/bin` в PATH.

### Локально, без Docker

Нужен PostgreSQL 14+ с базами `payment_emulator` и `payment_emulator_test`:

```bash
psql -d postgres -c "CREATE ROLE payment_emulator LOGIN PASSWORD 'payment_emulator'" \
  -c "CREATE DATABASE payment_emulator OWNER payment_emulator" \
  -c "CREATE DATABASE payment_emulator_test OWNER payment_emulator"
```

(или только база из Docker: `docker compose up -d postgres`). Дальше:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # при необходимости поправить DATABASE_URL
.venv/bin/uvicorn app.main:app --reload
```

Настройки — переменные окружения или `.env` (см. [app/config.py](app/config.py)):
`DATABASE_URL`, `AUTO_MIGRATE`, `SESSION_SECRET`, `BCRYPT_ROUNDS`.
`ADMIN_USERNAME`/`ADMIN_PASSWORD` и `API_USERNAME`/`API_PASSWORD` задают только
**первые** учётки на пустой базе; дальше пользователи управляются в админке
(см. «Пользователи»). В Docker
`DATABASE_URL` задан в `docker-compose.yml` и имеет приоритет над `.env`.

### Миграции (Alembic)

```bash
.venv/bin/alembic upgrade head                              # применить вручную
.venv/bin/alembic revision --autogenerate -m "add column"   # после правки app/models.py
```

URL базы Alembic берёт из `app.config` (`DATABASE_URL` / `.env`), не из
`alembic.ini`. Автогенерацию делайте по пустой/актуальной базе и просматривайте
результат перед коммитом.

> Переход с SQLite: старый `emulator.db` больше не используется, данные не
> переносятся (это тестовые данные). Файл можно удалить.

## Тесты и нагрузка

```bash
docker compose exec app python -m pytest   # в контейнере
.venv/bin/python -m pytest                 # или с хоста (нужен requirements-dev.txt)
```

Тесты (`tests/`) гоняются на отдельной базе `payment_emulator_test` (URL
переопределяется переменной окружения `TEST_DATABASE_URL`; имя базы обязано
содержать `test` — тесты делают `DROP SCHEMA`). Схема накатывается миграциями
Alembic один раз за прогон, перед каждым тестом таблицы очищаются `TRUNCATE`.
Покрыты: сценарии/ФИО, агентское API платежей, идемпотентность, атомарный
переход, логирование, админка, весь контур выдач (включая гонку параллельных
`/api/block`) и управление учётками (сессии после смены пароля, кэш Basic Auth).
Тесты ставят `BCRYPT_ROUNDS=4`, иначе сиды учёток перед каждым тестом заметно
замедляют прогон.

Нагрузочный тест против запущенного сервера:

```bash
.venv/bin/python loadtest.py --url http://127.0.0.1:8000 --concurrency 30 --iterations 20
```

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

## Выдача наличных по кодам

Эмулирует сервер выдачи для терминала: оператор генерирует в админке код
(PIN из 12 цифр) с суммой, терминал проходит `check → block → pay`.
Источник истины по кодам `state`, сценариям и OTP —
[app/payout_scenarios.py](app/payout_scenarios.py), логика —
[app/payouts.py](app/payouts.py).

Общие правила API выдач:
- HTTP Basic Auth (те же учётки агентов, что у платёжного API), HTTP-код **всегда 200**,
  результат — в поле `state`.
- Невалидное тело (нет обязательного поля, не тот тип) → `{"state": -100}`;
  причина видна в журнале API-запросов админки.
- `pin` и `pointId` принимаются строкой или числом. Суммы — **целые, в единицах
  валюты** (500 = 500 сом), в отличие от `/pay` платежей (там минимальные единицы).
- `currency` — числовой код; должен совпадать с валютой кода, иначе «не найден».

### Жизненный цикл кода

```
active ──block──▶ blocked ──pay(amount = остаток)──▶ paid
   ▲                 │
   └──pay(amount < остатка): выдано amount, остаток вернулся на код
```

- `expired` не хранится: это `active` с истёкшим `expires_at` (срок задаётся при
  генерации). Заблокированный код не истекает — выдачу можно завершить.
- `pay` с `amount = 0` просто снимает блокировку (ничего не выдано).
- `pay` с `amount` больше остатка → `-100`, состояние не меняется.
- `block`/`pay` берут строку под `SELECT … FOR UPDATE`: из N параллельных
  `block` по одному PIN успешен ровно один, остальные получают `1`.

### `POST /api/v2/check`

`{"pin": "123456789012", "currency": 10, "pointId": "123"}` — состояние не меняет.

| state | Когда |
|---|---|
| `0` | код найден и доступен (в т.ч. если сейчас заблокирован). Тело: `amount` (текущий остаток), `phone`, `fio`, `exchangeId` (nullable), `otpNeeded`, `rate`, `commission` |
| `1` | PIN не найден / не 12 цифр / валюта не совпадает |
| `2` | код полностью выплачен |
| `3` | срок истёк |
| `9` / `10` / `-100` | принудительный сценарий кода (см. ниже) |

### `POST /api/block`

`{"pin": "123456789012", "pointId": "123", "otp": "7890"}`

Порядок проверок: не найден → `1`; принудительный сценарий; выплачен → `2`;
уже заблокирован (любой точкой) → `1`; истёк → `3`; OTP (если `otpNeeded`);
успех → `0`, код закрепляется за `pointId`.

**OTP-заглушка** (только если у кода `otpNeeded = true`, иначе поле игнорируется):

| otp | state |
|---|---|
| нет / пустой | `11` OTP MISSING |
| `0000` | успех |
| `0002` | `12` OTP EXPIRED |
| любой другой (`0001`, …) | `13` OTP INCORRECT |

### `POST /api/pay`

`{"timestamp": "2022-12-12T00:00:00", "pin": "…", "currency": 10, "amount": 200, "pointId": "123"}`

| state | Когда |
|---|---|
| `0` + `amount` | выдача зафиксирована; `amount` — эхо выданной суммы |
| `1` | PIN не найден / валюта не совпадает |
| `4` | код не заблокирован **этой** точкой (не блокировали, блок другой точкой, повторный `pay` после успешного) |
| `-100` | `amount` больше остатка, принудительный сценарий, внутренняя ошибка |

`timestamp` сохраняется как есть в истории кода. Повторный `pay` не
идемпотентен: после успешной выдачи блок снят, повтор получит `4`.

### Принудительные сценарии кода

Ответы, которые не получить из состояния кода, задаются полем «Сценарий» при
генерации (и меняются на карточке кода на лету):

| Сценарий | check | block | pay |
|---|---|---|---|
| `normal` | по состоянию | по состоянию | по состоянию |
| `scenario_blocked` | `9` | `9` | по состоянию |
| `limited` | `10` | по состоянию | по состоянию |
| `scenario_expired` | по состоянию | `8` | по состоянию |
| `internal_error` | `-100` | `-100` | `-100` |

### Админка выдач (`/admin/payouts`)

- Форма генерации: сумма, валюта, количество (1–100), срок действия, сценарий,
  телефон/ФИО/exchangeId (пусто = `null`), курс, комиссия, «нужен OTP».
  Сгенерированные PIN показываются сразу после создания.
- Список с поиском по PIN и фильтром `active | blocked | paid | expired`.
- Карточка кода: остаток, кем заблокирован, история (генерация, блокировка,
  выдачи с `timestamp` терминала, действия админа), лог API-запросов по PIN,
  действия: сменить сценарий/OTP, «Снять блокировку» (зависший блок), «Истечь сейчас».

## Пользователи

Две независимые группы учёток, обе в БД (пароли — bcrypt), обе управляются
на странице **«Пользователи»** (`/admin/users`):

**Администраторы** — вход в веб-админку.
- Первый админ создаётся автоматически из `ADMIN_USERNAME` / `ADMIN_PASSWORD`
  (по умолчанию `admin` / `admin`), **только если админов в базе нет**. Дальше эти
  переменные не читаются: сменённый пароль не затирается при рестарте, удалённый
  `admin` не воскресает.
- Свой пароль — «Мой аккаунт» (клик по логину в шапке, `/admin/account`),
  с вводом текущего. Другим админам пароль сбрасывается без текущего.
- Нельзя удалить себя и последнего админа.
- После смены пароля или удаления все сессии этого админа становятся
  невалидными (в куке хранится HMAC-штамп пароля, сверяется на каждом запросе).
  Текущая сессия при смене своего пароля сохраняется.

**Агенты API** — HTTP Basic Auth для `/check`, `/pay`, `/status` и `/api/*`.
- Первый агент создаётся из `API_USERNAME` / `API_PASSWORD` (`agent` /
  `agent-secret`), только если агентов в базе нет.
- Создать агента, сменить пароль (пусто → сгенерировать 24 символа),
  отключить/включить, удалить. Пароль показывается **один раз** сразу после
  создания/смены — в ответе страницы, в сессию он не кладётся.
- Проверка bcrypt кэшируется в памяти процесса на 30 минут (иначе каждый запрос
  стоил бы ~0.2 с CPU). Изменения агентов в админке сбрасывают кэш — отключение
  и смена пароля действуют сразу. При нескольких воркерах uvicorn остальные
  процессы увидят изменение не позже чем через 30 минут.
- Логин агента пишется в журнал API-запросов (`agent=…`), по нему работает поиск.

Минимальная длина пароля — 8 символов. В логине нельзя `:` (ломает Basic Auth).

## Эндпоинты

### Агентское API (HTTP Basic Auth)
- `POST /check` — проверка реквизитов без создания платежа. Результат в поле
  `status` (`allowed` | `declined`), HTTP всегда 200. Возвращает также
  `holder_name` (ФИО, детерминированно по реквизиту) и эхо `currency`.
- `POST /pay` — инициирует платёж. Обязательно поле `idempotency_key` в теле
  запроса (повтор с тем же ключом возвращает тот же платёж). Ответ —
  подтверждение приёма `status: "accepted"`; реальный исход узнаётся через
  `/status`.
- `GET /status/{payment_id}` — текущий статус платежа.
- `POST /api/v2/check`, `POST /api/block`, `POST /api/pay` — выдача наличных
  по кодам (см. раздел выше).

### Админка (сессионная авторизация)
- `GET /admin/login`, `POST /admin/login`, `GET /admin/logout`
- `GET /admin/payments` — список с фильтром по статусу и поиском по id/реквизиту
- `GET /admin/payments/{id}` — деталь + таймлайн истории + окно API-запросов по
  платежу; пока платёж не финален, блоки статуса и логов сами обновляются по
  HTMX (`/admin/payments/{id}/status-block`, `/admin/payments/{id}/logs-block`)
- `GET /admin/payouts` — коды выдачи: генерация (`POST /admin/payouts/generate`),
  список, карточка `GET /admin/payouts/{id}`, действия
  `POST /admin/payouts/{id}/update|unblock|expire`
- `GET /admin/requests` — журнал всех агентских API-запросов (`/check` виден
  только здесь — он не создаёт платёж), фильтр по эндпоинту и поиск
- `GET /admin/settings`, `POST /admin/settings` — редактирование задержек
- `GET /admin/users` — админы и агенты API; `POST /admin/users/admins`,
  `…/admins/{id}/password|delete`, `POST /admin/users/agents`,
  `…/agents/{id}/password|toggle|delete`
- `GET /admin/account`, `POST /admin/account/password` — смена своего пароля

## Логирование

- **Общий файловый лог**: папка `logs/`, отдельный файл на каждый день
  (`emulator-YYYY-MM-DD.log`, время в UTC). Пишутся все HTTP-запросы (кроме
  статики, `/health` и HTMX-поллеров админки) и события фоновой задачи.
- **Лог API-запросов в БД** (`api_request_log`): полное тело запроса и наш ответ
  по каждому `/check`, `/pay`, `/status` и `/api/*` выдач (эндпоинты
  `payout_check|payout_block|payout_pay`, PIN пишется в поле `requisite`).
  `/pay` и `/status` привязаны к `payment_id`, `/check` — нет. Таблица подрезается до последних ~5000 записей.
  Сбой записи лога не роняет сам запрос (логирование best-effort).

> ⚠️ Реквизит и PIN кода выдачи попадают в лог как есть — и в БД, и в открытый файл `logs/`. Это
> допустимо, потому что здесь только тестовые реквизиты; настоящие номера карт
> сюда направлять нельзя (иначе PAN окажется в открытых логах).

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
├── database.py      # async engine/session (PostgreSQL, asyncpg)
├── db_init.py       # миграции Alembic при старте + seed (сценарии, первый админ и агент)
├── models.py        # Payment, PaymentStatusHistory, PayoutCode, PayoutEvent, ScenarioSetting, AdminUser, AgentAccount
├── scenarios.py     # таблица сценариев платежей (источник истины)
├── payout_scenarios.py  # коды state, сценарии и OTP-заглушка выдач (источник истины)
├── schemas.py       # pydantic-схемы агентского API и API выдач
├── payments.py      # создание платежа + идемпотентность
├── payouts.py       # коды выдачи: генерация, check/block/pay, действия админа
├── holder.py        # детерминированная генерация ФИО
├── background.py    # asyncio-задача автоперехода статусов
├── api_auth.py      # HTTP Basic Auth по agent_accounts + кэш проверок
├── admin_auth.py    # сессионная авторизация (админка), штамп пароля в сессии
├── users.py         # учётки: админы и агенты (создание, пароли, удаление)
├── routes_api.py    # /check, /pay, /status
├── routes_payout.py # /api/v2/check, /api/block, /api/pay
├── routes_admin.py  # /admin/* (платежи, выдачи, журнал, задержки)
├── routes_users.py  # /admin/users, /admin/account
├── templating.py    # Jinja2 + фильтры money/dt
├── templates/       # base, login, payments/payouts list+detail, requests, settings, users, account
└── static/          # htmx.min.js (вендор)
migrations/          # Alembic: env.py + versions/
Dockerfile           # образ эмулятора (python:3.12-slim + requirements-dev)
docker-compose.yml   # стенд: app (uvicorn --reload) + postgres
```

## Примеры запросов

```bash
# check
curl -u agent:agent-secret -X POST localhost:8000/check \
  -H "Content-Type: application/json" \
  -d '{"requisite":"4111111111110001","amount":10000,"currency":"RUB"}'

# pay (idempotency_key — в теле); amount — в минимальных единицах (10000 = 100.00)
curl -u agent:agent-secret -X POST localhost:8000/pay \
  -H "Content-Type: application/json" \
  -d '{"requisite":"4111111111110003","amount":10000,"idempotency_key":"demo-1"}'

# status
curl -u agent:agent-secret localhost:8000/status/<payment_id>

# --- выдача наличных (PIN берётся из админки /admin/payouts) ---
curl -u agent:agent-secret -X POST localhost:8000/api/v2/check \
  -H "Content-Type: application/json" \
  -d '{"pin":"123456789012","currency":10,"pointId":"123"}'

curl -u agent:agent-secret -X POST localhost:8000/api/block \
  -H "Content-Type: application/json" \
  -d '{"pin":"123456789012","pointId":"123","otp":"0000"}'

# выдали 400 из 1000 — 600 вернутся на код
curl -u agent:agent-secret -X POST localhost:8000/api/pay \
  -H "Content-Type: application/json" \
  -d '{"timestamp":"2022-12-12T00:00:00","pin":"123456789012","currency":10,"amount":400,"pointId":"123"}'
```
