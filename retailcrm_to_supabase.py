"""
retailcrm_to_supabase.py

Fetches all orders from RetailCRM API v5 and upserts them into Supabase
table `orders`.  No third-party libraries required.

Table schema (created automatically if absent):
    id            INTEGER PRIMARY KEY   -- RetailCRM internal order id
    order_number  TEXT                  -- human-readable number e.g. "63A"
    created_at    TIMESTAMPTZ           -- order creation time
    status        TEXT
    total_sum     NUMERIC
    customer_name TEXT

Required env variables (load from .env before running):
    RETAILCRM_URL       e.g. https://bakhytsultanov1.retailcrm.ru
    RETAILCRM_API_KEY
    SUPABASE_URL        e.g. https://xxxx.supabase.co
    SUPABASE_KEY        anon or service_role key
"""

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

# ── env ──────────────────────────────────────────────────────────────────────

def _require(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"ERROR: environment variable {name!r} is not set.")
    return v

RETAILCRM_URL = _require("RETAILCRM_URL").rstrip("/")
RETAILCRM_API_KEY = _require("RETAILCRM_API_KEY")
SUPABASE_URL = _require("SUPABASE_URL").rstrip("/")
SUPABASE_KEY = _require("SUPABASE_KEY")

PAGE_LIMIT = 100          # valid values: 20 | 50 | 100
REQUEST_DELAY = 0.25      # seconds between RetailCRM pages

# ── logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── SSL context (Windows doesn't ship with updated CA bundle) ─────────────────

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

# ── helpers ───────────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
-- Run this once in the Supabase SQL Editor (https://supabase.com/dashboard)
-- Project > SQL Editor > New query

CREATE TABLE IF NOT EXISTS public.orders (
    id            INTEGER      PRIMARY KEY,
    order_number  TEXT         NOT NULL,
    created_at    TIMESTAMPTZ,
    status        TEXT,
    total_sum     NUMERIC,
    customer_name TEXT
);
"""

def _http(method: str, url: str, *, headers: dict = None, body=None, timeout=15):
    """Thin wrapper around urllib.request. Returns parsed JSON body."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    with urllib.request.urlopen(req, context=_ssl_ctx, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else None


def _supabase_headers(*, prefer: str = None) -> dict:
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


# ── RetailCRM ─────────────────────────────────────────────────────────────────

def fetch_all_orders() -> list[dict]:
    """Paginate through /api/v5/orders and return every order."""
    orders = []
    page = 1

    while True:
        params = urllib.parse.urlencode({
            "apiKey": RETAILCRM_API_KEY,
            "limit": PAGE_LIMIT,
            "page": page,
        })
        url = f"{RETAILCRM_URL}/api/v5/orders?{params}"
        log.info("Fetching page %d …", page)

        try:
            data = _http("GET", url)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"RetailCRM HTTP {exc.code}: {exc.read().decode()}")

        batch = data.get("orders", [])
        orders.extend(batch)

        pagination = data.get("pagination", {})
        total_pages = pagination.get("totalPageCount", 1)
        log.info("  got %d orders (page %d/%d)", len(batch), page, total_pages)

        if page >= total_pages:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    log.info("Total orders fetched from RetailCRM: %d", len(orders))
    return orders


# ── field mapping ─────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> str:
    """Convert '2026-04-13 18:01:03' to '2026-04-13T18:01:03+00:00'."""
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%S+00:00")
    except (ValueError, TypeError):
        return None


def order_to_row(o: dict) -> dict:
    first = (o.get("firstName") or "").strip()
    last = (o.get("lastName") or "").strip()
    customer_name = f"{first} {last}".strip() or None

    return {
        "id":            o["id"],
        "order_number":  o.get("number"),
        "created_at":    _parse_dt(o.get("createdAt")),
        "status":        o.get("status"),
        "total_sum":     o.get("totalSumm"),
        "customer_name": customer_name,
    }


# ── Supabase ──────────────────────────────────────────────────────────────────

def check_table_exists() -> bool:
    """Return True if the orders table is reachable via PostgREST."""
    url = f"{SUPABASE_URL}/rest/v1/orders?limit=0"
    try:
        _http("GET", url, headers=_supabase_headers())
        return True
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404 or "42P01" in body:
            return False
        # Other error (auth, network) — re-raise
        raise SystemExit(f"Supabase error {exc.code}: {body}")


def upsert_rows(rows: list[dict]) -> None:
    """
    POST all rows in one request with Prefer: resolution=merge-duplicates.
    Supabase / PostgREST upserts on the PRIMARY KEY (id) by default.
    """
    url = f"{SUPABASE_URL}/rest/v1/orders"
    headers = _supabase_headers(prefer="return=minimal,resolution=merge-duplicates")

    try:
        _http("POST", url, headers=headers, body=rows)
        log.info("Upserted %d rows into Supabase `orders`.", len(rows))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Supabase upsert failed {exc.code}: {body}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. Check table
    if not check_table_exists():
        log.error("Table `orders` does not exist in Supabase.")
        log.error("Create it by running the following SQL in the Supabase dashboard:")
        print(CREATE_TABLE_SQL)
        raise SystemExit(
            "Go to https://supabase.com/dashboard > your project > SQL Editor, "
            "paste the SQL above, and re-run this script."
        )
    log.info("Table `orders` found in Supabase.")

    # 2. Fetch orders from RetailCRM
    orders = fetch_all_orders()
    if not orders:
        log.info("No orders returned from RetailCRM. Nothing to sync.")
        return

    # 3. Map to table rows
    rows = [order_to_row(o) for o in orders]

    # 4. Upsert into Supabase
    upsert_rows(rows)
    log.info("Sync complete.")


if __name__ == "__main__":
    main()
