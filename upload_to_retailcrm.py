import json
import os
import time
import logging
import urllib.request
import urllib.parse
import urllib.error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RETAILCRM_URL = os.environ.get("RETAILCRM_URL", "").rstrip("/")
RETAILCRM_API_KEY = os.environ.get("RETAILCRM_API_KEY", "")
ORDERS_FILE = "mock_orders.json"
REQUEST_DELAY = 0.3  # seconds between requests


def create_order(order: dict, index: int) -> bool:
    """POST a single order to RetailCRM /api/v5/orders/create. Returns True on success."""
    endpoint = f"{RETAILCRM_URL}/api/v5/orders/create"

    # RetailCRM expects the order object as a JSON string in the 'order' form field
    payload = urllib.parse.urlencode({
        "apiKey": RETAILCRM_API_KEY,
        "site": os.environ.get("RETAILCRM_SITE", ""),
        "order": json.dumps(order, ensure_ascii=False),
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    customer_label = f"{order.get('firstName', '')} {order.get('lastName', '')}".strip()

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
            if body.get("success"):
                log.info(
                    "Order %d/%d CREATED  id=%s  customer='%s'",
                    index,
                    TOTAL,
                    body.get("id", "—"),
                    customer_label,
                )
                return True
            else:
                errors = body.get("errors") or body.get("errorMsg", "unknown error")
                log.warning(
                    "Order %d/%d REJECTED  customer='%s'  errors=%s",
                    index,
                    TOTAL,
                    customer_label,
                    errors,
                )
                return False
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        log.error(
            "Order %d/%d HTTP %s  customer='%s'  response=%s",
            index,
            TOTAL,
            exc.code,
            customer_label,
            body_text[:300],
        )
    except urllib.error.URLError as exc:
        log.error(
            "Order %d/%d NETWORK ERROR  customer='%s'  reason=%s",
            index,
            TOTAL,
            customer_label,
            exc.reason,
        )
    return False


def main() -> None:
    if not RETAILCRM_URL:
        raise SystemExit("ERROR: RETAILCRM_URL environment variable is not set.")
    if not RETAILCRM_API_KEY:
        raise SystemExit("ERROR: RETAILCRM_API_KEY environment variable is not set.")

    with open(ORDERS_FILE, encoding="utf-8") as f:
        orders = json.load(f)

    global TOTAL
    TOTAL = len(orders)
    log.info("Loaded %d orders from %s", TOTAL, ORDERS_FILE)

    success = 0
    failed = 0

    for i, order in enumerate(orders, start=1):
        if create_order(order, i):
            success += 1
        else:
            failed += 1
        if i < TOTAL:
            time.sleep(REQUEST_DELAY)

    log.info("Done. Success: %d  Failed: %d  Total: %d", success, failed, TOTAL)


if __name__ == "__main__":
    main()
