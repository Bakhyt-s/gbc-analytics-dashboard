# GBEmpire Analytics Dashboard

Мини-дашборд заказов для производителя фитнес-одежды **GBEmpire**. Позволяет загружать тестовые заказы в RetailCRM, синхронизировать их с Supabase и отображать аналитику на веб-дашборде. Telegram-бот присылает алерт при поступлении заказа на сумму свыше 50 000 ₸.

**Дашборд:** [gbempire-dashboard.vercel.app](https://gbempire-dashboard.vercel.app)
**Репозиторий:** [github.com/Bakhyt-s/gbc-analytics-dashboard](https://github.com/Bakhyt-s/gbc-analytics-dashboard)

---

## Стек

| Слой | Технология |
|---|---|
| CRM | RetailCRM API v5 |
| База данных | Supabase (PostgreSQL) |
| Хостинг дашборда | Vercel |
| Алерты | Telegram Bot API |
| Скрипты | Python 3 (без сторонних библиотек) |
| Разработка | Claude Code |

---

## Архитектура

```
mock_orders.json
      │
      ▼
upload_to_retailcrm.py   ──►   RetailCRM API
                                      │
                                      ▼
                          retailcrm_to_supabase.py  ──►  Supabase (таблица orders)
                                                               │
                                                               ▼
                                                    Vercel Dashboard (index.html)

RetailCRM API  ──►  telegram_bot.py  ──►  Telegram-алерт (заказ > 50 000 ₸)
```

1. `mock_orders.json` — тестовые заказы в формате RetailCRM.
2. `upload_to_retailcrm.py` — загружает заказы из JSON в RetailCRM через API.
3. `retailcrm_to_supabase.py` — забирает заказы из RetailCRM и делает upsert в Supabase.
4. `index.html` — статический дашборд, задеплоен на Vercel, читает данные из Supabase.
5. `telegram_bot.py` — опрашивает RetailCRM каждые 60 секунд, отправляет алерт если `total_sum > 50 000 ₸`.

---

## Переменные окружения

Создайте файл `.env` в корне проекта:

```env
# RetailCRM
RETAILCRM_URL=https://your-account.retailcrm.ru
RETAILCRM_API_KEY=your_retailcrm_api_key

# Supabase
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=your_supabase_anon_or_service_role_key

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Запуск скриптов с загрузкой `.env`:

```bash
export $(grep -v '^#' .env | xargs) && python retailcrm_to_supabase.py
export $(grep -v '^#' .env | xargs) && python telegram_bot.py
```

---

## Как использовал Claude Code

Весь проект разработан с помощью **Claude Code** — давал промпты на русском языке прямо в терминале.

**Примеры того, что сделал Claude Code:**

- **Нашёл и исправил баг с типом заказа.** При загрузке заказов RetailCRM отклонял запросы с ошибкой. Claude Code самостоятельно проанализировал ответ API, обнаружил, что передавался неверный `orderType: eshop-individual`, и заменил его на `main` — единственный тип, доступный в аккаунте.

- **Установил Vercel CLI и задеплоил проект.** По одному промпту Claude Code установил `vercel` глобально, прошёл через `vercel link`, настроил `vercel.json` и задеплоил дашборд на продакшн.

- **Написал все скрипты без сторонних зависимостей.** `retailcrm_to_supabase.py` и `telegram_bot.py` используют только стандартную библиотеку Python — `urllib`, `json`, `os` — чтобы не требовать `pip install`.

- **Реализовал дедупликацию в боте.** Добавил двухуровневую защиту от повторных алертов: lock-файл против двойного запуска и `processed_ids.json` для сохранения состояния между перезапусками.
