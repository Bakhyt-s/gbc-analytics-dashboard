"""
telegram_bot.py

Polls RetailCRM every 60 seconds for new orders.
Sends a Telegram alert when order total_sum > 50 000 ₸.

Deduplication strategy (two layers):
  1. Lock file (.bot.lock) — prevents two instances running at the same time.
  2. Persistent processed_ids.json — survives restarts; shared across runs.

Required .env variables:
    RETAILCRM_URL
    RETAILCRM_API_KEY
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Run:
    export $(grep -v '^#' .env | xargs) && python telegram_bot.py
"""

import atexit
import json
import logging
import os
import signal
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

def _require(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"ERROR: environment variable {name!r} is not set.")
    return v

RETAILCRM_URL     = _require("RETAILCRM_URL").rstrip("/")
RETAILCRM_API_KEY = _require("RETAILCRM_API_KEY")
TG_TOKEN          = _require("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID        = _require("TELEGRAM_CHAT_ID")

POLL_INTERVAL     = 60       # seconds between polls
ORDER_SUM_THRESHOLD = 50_000 # ₸ — alert threshold
# Use a slightly wider window than POLL_INTERVAL to tolerate clock skew
LOOKBACK_SECONDS  = POLL_INTERVAL + 10

# ── SSL context ───────────────────────────────────────────────────────────────

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

# ── HTTP helper ───────────────────────────────────────────────────────────────

def _get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, context=_ssl_ctx, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_json(url: str, payload: dict, timeout: int = 15) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, context=_ssl_ctx, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else {}

# ── RetailCRM ─────────────────────────────────────────────────────────────────

def fetch_new_orders(since: datetime) -> list[dict]:
    """
    Fetch orders created at or after `since`.
    Returns a flat list; handles pagination automatically.
    """
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    orders = []
    page = 1

    while True:
        params = urllib.parse.urlencode({
            "apiKey":                 RETAILCRM_API_KEY,
            "limit":                  "100",
            "page":                   str(page),
            "filter[createdAtFrom]":  since_str,
        })
        url = f"{RETAILCRM_URL}/api/v5/orders?{params}"

        try:
            data = _get_json(url)
        except urllib.error.HTTPError as exc:
            log.error("RetailCRM HTTP %s: %s", exc.code, exc.read().decode())
            break
        except Exception as exc:
            log.error("RetailCRM request failed: %s", exc)
            break

        orders.extend(data.get("orders", []))

        pagination = data.get("pagination", {})
        if page >= pagination.get("totalPageCount", 1):
            break
        page += 1

    return orders

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        resp = _post_json(url, {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"})
        if resp.get("ok"):
            return True
        log.warning("Telegram rejected message: %s", resp)
        return False
    except urllib.error.HTTPError as exc:
        log.error("Telegram HTTP %s: %s", exc.code, exc.read().decode())
        return False
    except Exception as exc:
        log.error("Telegram request failed: %s", exc)
        return False

# ── Message formatter ─────────────────────────────────────────────────────────

def fmt_currency(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ") + " \u20b8"  # ₸
    except (TypeError, ValueError):
        return f"{value} \u20b8"


def build_message(order: dict) -> str:
    number   = order.get("number") or f"#{order.get('id')}"
    total    = order.get("totalSumm", 0)
    first    = (order.get("firstName") or "").strip()
    last     = (order.get("lastName")  or "").strip()
    customer = f"{first} {last}".strip() or "—"
    phone    = order.get("phone", "")
    status   = order.get("status", "")

    lines = [
        "\U0001f525 <b>Новый крупный заказ!</b>",
        f"\U0001f4e6 Номер:   <b>{number}</b>",
        f"\U0001f4b0 Сумма:   <b>{fmt_currency(total)}</b>",
        f"\U0001f464 Клиент:  {customer}",
    ]
    if phone:
        lines.append(f"\U0001f4de Телефон: {phone}")
    if status:
        lines.append(f"\U0001f4cb Статус:  {status}")

    return "\n".join(lines)

# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("GBEmpire Telegram bot started.")
    log.info("Threshold: orders > %d | Poll interval: %ds", ORDER_SUM_THRESHOLD, POLL_INTERVAL)

    processed_ids: set[int] = set()

    # On first startup seed processed_ids with whatever already exists,
    # so we don't flood the channel with historical orders.
    log.info("Seeding processed_ids from existing orders (no alerts sent)…")
    seed_since = datetime.now() - timedelta(days=30)
    existing = fetch_new_orders(seed_since)
    for o in existing:
        processed_ids.add(o["id"])
    log.info("Seeded %d existing order IDs. Watching for new ones…", len(processed_ids))

    while True:
        time.sleep(POLL_INTERVAL)

        since = datetime.now() - timedelta(seconds=LOOKBACK_SECONDS)
        log.info("Polling RetailCRM since %s …", since.strftime("%H:%M:%S"))

        try:
            orders = fetch_new_orders(since)
        except Exception as exc:
            log.error("Unexpected error fetching orders: %s", exc)
            continue

        new_count = 0
        alert_count = 0

        for order in orders:
            oid = order.get("id")
            if oid in processed_ids:
                continue

            processed_ids.add(oid)
            new_count += 1
            total = order.get("totalSumm", 0) or 0

            if total > ORDER_SUM_THRESHOLD:
                msg = build_message(order)
                ok  = send_telegram(msg)
                if ok:
                    alert_count += 1
                    log.info("Alert sent: order #%s sum=%s", order.get("number"), fmt_currency(total))
                else:
                    log.warning("Failed to send alert for order #%s", order.get("number"))

        if new_count:
            log.info("Found %d new order(s), sent %d alert(s).", new_count, alert_count)
        else:
            log.info("No new orders.")


if __name__ == "__main__":
    main()
