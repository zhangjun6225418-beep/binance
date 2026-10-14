import json
import os
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://fapi.binance.com"
VOLUME_FLOOR = float(os.getenv("VOLUME_FLOOR_USDT", "10000000"))
SCAN_LIMIT = max(10, min(500, int(os.getenv("SCAN_LIMIT", "200"))))
STATE_PATH = "state.json"
USER_AGENT = "HuozhongScreener/1.1"


def get_json(path):
    url = BASE + path
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"Binance API failed: HTTP {exc.code}; URL={url}; "
            f"response={body}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Cannot reach Binance API; URL={url}; reason={exc.reason}"
        ) from exc


def klines(symbol, interval, limit=200):
    data = get_json(
        f"/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    )
    now_ms = int(time.time() * 1000)
    return [
        {"t": int(row[0]), "close": float(row[4]), "close_time": int(row[6])}
        for row in data
        if int(row[6]) < now_ms
    ]


def ema_series(values, period):
    if len(values) < period:
        return []
    alpha = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    result = [None] * len(values)
    result[period - 1] = current
    for index in range(period, len(values)):
        current = values[index] * alpha + current * (1 - alpha)
        result[index] = current
    return result


def check_symbol(symbol):
    try:
        m15 = klines(symbol, "15m", 100)
        h4 = klines(symbol, "4h", 120)
        d1 = klines(symbol, "1d", 100)
        w1 = klines(symbol, "1w", 100)

        if min(len(m15), len(h4), len(d1), len(w1)) < 20:
            print(f"SKIP {symbol}: not enough closed candles")
            return []

        c15 = [row["close"] for row in m15]
        ema15_7 = ema_series(c15, 7)[-1]
        ema15_14 = ema_series(c15, 14)[-1]

        c4 = [row["close"] for row in h4]
        ema4_7 = ema_series(c4, 7)
        ema4_14 = ema_series(c4, 14)
        ema4_60 = ema_series(c4, 60)

        cd = [row["close"] for row in d1]
        ema_d_7 = ema_series(cd, 7)[-1]
        ema_d_14 = ema_series(cd, 14)[-1]

        cw = [row["close"] for row in w1]
        ema_w_7 = ema_series(cw, 7)[-1]
        ema_w_14 = ema_series(cw, 14)[-1]

        index = len(h4) - 1
        results = []
        common_15m = ema15_7 > ema15_14

        # A: 15m、日线、周线 EMA7 > EMA14；
        # 最近两根已收盘4H K线 EMA7 > EMA14；
        # 最新已收盘4H收盘价低于EMA7。
        if (
            common_15m
            and ema_d_7 > ema_d_14
            and ema_w_7 > ema_w_14
            and ema4_7[index] is not None
            and ema4_14[index] is not None
            and ema4_7[index - 1] is not None
            and ema4_14[index - 1] is not None
            and ema4_7[index] > ema4_14[index]
            and ema4_7[index - 1] > ema4_14[index - 1]
            and h4[index]["close"] < ema4_7[index]
        ):
            results.append({
                "rule": "A 多周期趋势筛选",
                "symbol": symbol,
                "price": h4[index]["close"],
                "15m_ema7": ema15_7,
                "15m_ema14": ema15_14,
                "4h_ema7": ema4_7[index],
                "4h_ema14": ema4_14[index],
                "4h_close": h4[index]["close"],
                "daily_ema7": ema_d_7,
                "daily_ema14": ema_d_14,
                "weekly_ema7": ema_w_7,
                "weekly_ema14": ema_w_14,
                "candle_time": h4[index]["t"],
            })

        # B: 15m EMA7 > EMA14，且4H EMA7低于或等于EMA60的90%。
        if (
            common_15m
            and ema4_7[index] is not None
            and ema4_60[index] is not None
            and ema4_60[index] > 0
            and ema4_7[index] <= ema4_60[index] * 0.90
        ):
            results.append({
                "rule": "B 4小时 EMA7低于EMA60至少10%",
                "symbol": symbol,
                "price": h4[index]["close"],
                "15m_ema7": ema15_7,
                "15m_ema14": ema15_14,
                "4h_ema7": ema4_7[index],
                "4h_ema60": ema4_60[index],
                "4h_gap_pct": (
                    ema4_7[index] / ema4_60[index] - 1
                ) * 100,
                "candle_time": h4[index]["t"],
            })

        return results

    except Exception as exc:
        print(f"ERROR checking {symbol}: {exc}")
        return []


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as file:
            state = json.load(file)
        if isinstance(state, dict):
            return state
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"active": []}


def fmt(value):
    return f"{value:,.8g}" if isinstance(value, (int, float)) else str(value)


def send_email(items):
    # 必须与 GitHub Actions Secrets 名称一致。
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    recipient = os.environ["EMAIL_TO"]

    message = EmailMessage()
    message["Subject"] = f"火种计划选币提醒：发现 {len(items)} 个新信号"
    message["From"] = user
    message["To"] = recipient

    lines = [
        "火种计划云端选币器发现新标的（仅筛选提醒，不构成交易建议）：",
        "",
    ]

    fields = (
        "4h_ema7",
        "4h_ema14",
        "4h_ema60",
        "4h_close",
        "4h_gap_pct",
        "daily_ema7",
        "daily_ema14",
        "weekly_ema7",
        "weekly_ema14",
    )

    for item in items:
        lines.extend([
            f"【{item['rule']}】{item['symbol']}",
            f"最新参考价（4H已收盘）: {fmt(item.get('price'))}",
            "15m EMA7/EMA14: "
            f"{fmt(item.get('15m_ema7'))} / {fmt(item.get('15m_ema14'))}",
        ])

        for key in fields:
            if key in item:
                label = key.replace("_", " ")
                value = (
                    f"{fmt(item[key])}%"
                    if key == "4h_gap_pct"
                    else fmt(item[key])
                )
                lines.append(f"{label}: {value}")

        lines.extend([
            f"币安合约图表: https://www.binance.com/en/futures/{item['symbol']}",
            f"信号K线开盘时间(UTC毫秒): {item['candle_time']}",
            "",
        ])

    lines.append("每15分钟扫描；仅使用已收盘K线；不会自动下单。")
    message.set_content("\n".join(lines))

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(user, password)
        server.send_message(message)


def main():
    print(f"Scan start UTC: {datetime.now(timezone.utc).isoformat()}")
    print(f"Volume floor: {VOLUME_FLOOR:,.0f} USDT; scan limit: {SCAN_LIMIT}")

    # 若运行环境无法访问币安，在这里显示具体HTTP错误并停止。
    exchange_info = get_json("/fapi/v1/exchangeInfo")
    tickers = get_json("/fapi/v1/ticker/24hr")

    active_symbols = {
        item["symbol"]
        for item in exchange_info["symbols"]
        if item.get("status") == "TRADING"
        and item.get("contractType") == "PERPETUAL"
        and item.get("quoteAsset") == "USDT"
    }

    volumes = {
        item["symbol"]: float(item.get("quoteVolume", 0))
        for item in tickers
    }

    candidates = sorted(
        [
            symbol
            for symbol in active_symbols
            if volumes.get(symbol, 0) >= VOLUME_FLOOR
        ],
        key=lambda symbol: volumes.get(symbol, 0),
        reverse=True,
    )[:SCAN_LIMIT]

    print(f"Candidates meeting volume floor: {len(candidates)}")

    found = []
    for number, symbol in enumerate(candidates, 1):
        found.extend(check_symbol(symbol))
        if number % 20 == 0 or number == len(candidates):
            print(f"Checked {number}/{len(candidates)}; signals={len(found)}")
        time.sleep(0.05)

    state = load_state()
    previous = set(state.get("active", []))
    current_map = {}

    for item in found:
        key = item["rule"] + "|" + item["symbol"]
        current_map[key] = item

    new_keys = set(current_map) - previous
    new_items = [current_map[key] for key in sorted(new_keys)]

    if new_items:
        send_email(new_items)
        print(f"Email sent for {len(new_items)} new signal(s).")
    else:
        print("No new signals; no email sent.")

    with open(STATE_PATH, "w", encoding="utf-8") as file:
        json.dump(
            {
                "active": sorted(current_map.keys()),
                "last_scan_utc": datetime.now(timezone.utc).isoformat(),
            },
            file,
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    main()
