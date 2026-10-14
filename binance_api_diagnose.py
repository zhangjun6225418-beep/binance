#!/usr/bin/env python3
"""Read-only connectivity diagnostic for Binance USDⓈ-M Futures public API.
No trading, no API keys, no email, and no changes to screener rules.
"""
import urllib.error
import urllib.request

BASE = "https://fapi.binance.com"
PATHS = ("/fapi/v1/ping", "/fapi/v1/time", "/fapi/v1/exchangeInfo")

for path in PATHS:
    url = BASE + path
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; read-only-api-diagnostic/1.0)"},
        method="GET",
    )
    print(f"\n--- {url} ---")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read(500).decode("utf-8", errors="replace")
            print(f"HTTP_STATUS={response.status}")
            print(f"BODY_START={body[:500]}")
    except urllib.error.HTTPError as exc:
        body = exc.read(1000).decode("utf-8", errors="replace")
        print(f"HTTP_STATUS={exc.code}")
        print(f"RESPONSE_BODY={body[:1000] or '(empty)'}")
    except Exception as exc:
        print(f"ERROR_TYPE={type(exc).__name__}")
        print(f"ERROR={exc}")
