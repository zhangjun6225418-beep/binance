import json, os, smtplib, time
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.request import Request, urlopen

BASE = "https://fapi.binance.com"
VOLUME_FLOOR = float(os.getenv("VOLUME_FLOOR_USDT", "10000000"))
SCAN_LIMIT = max(10, min(500, int(os.getenv("SCAN_LIMIT", "200"))))
STATE_PATH = "state.json"

def get_json(path):
    req = Request(BASE + path, headers={"User-Agent": "HuozhongScreener/1.0"})
    with urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def klines(symbol, interval, limit=200):
    data = get_json(f"/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}")
    now_ms = int(time.time() * 1000)
    # Exclude the currently forming candle; use only closed candles.
    return [{"t": int(x[0]), "close": float(x[4]), "close_time": int(x[6])}
            for x in data if int(x[6]) < now_ms]

def ema_series(values, period):
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    v = sum(values[:period]) / period
    out = [None] * len(values)
    out[period - 1] = v
    for i in range(period, len(values)):
        v = values[i] * k + v * (1 - k)
        out[i] = v
    return out

def latest_ema(candles, period):
    vals = [x["close"] for x in candles]
    s = ema_series(vals, period)
    return s[-1] if s else None

def check_symbol(symbol):
    try:
        m15 = klines(symbol, "15m", 100)
        h4 = klines(symbol, "4h", 120)
        d1 = klines(symbol, "1d", 100)
        w1 = klines(symbol, "1w", 100)
        if min(len(m15), len(h4), len(d1), len(w1)) < 20:
            return []
        c15 = [x["close"] for x in m15]
        e15_7, e15_14 = ema_series(c15, 7)[-1], ema_series(c15, 14)[-1]
        c4 = [x["close"] for x in h4]
        h7, h14, h60 = ema_series(c4, 7), ema_series(c4, 14), ema_series(c4, 60)
        c_d = [x["close"] for x in d1]
        c_w = [x["close"] for x in w1]
        d7, d14 = ema_series(c_d, 7)[-1], ema_series(c_d, 14)[-1]
        w7, w14 = ema_series(c_w, 7)[-1], ema_series(c_w, 14)[-1]
        i = len(h4) - 1
        out = []
        common15 = e15_7 > e15_14
        # Screener A: 15m EMA7>EMA14; daily/weekly EMA7>EMA14;
        # last two closed 4h candles EMA7>EMA14; latest 4h close < EMA7.
        if (common15 and d7 > d14 and w7 > w14 and
            h7[i] > h14[i] and h7[i-1] > h14[i-1] and
            h4[i]["close"] < h7[i]):
            out.append({
                "rule": "A 多周期趋势筛选",
                "symbol": symbol, "price": h4[i]["close"],
                "15m_ema7": e15_7, "15m_ema14": e15_14,
                "4h_ema7": h7[i], "4h_ema14": h14[i],
                "4h_close": h4[i]["close"],
                "daily_ema7": d7, "daily_ema14": d14,
                "weekly_ema7": w7, "weekly_ema14": w14,
                "candle_time": h4[i]["t"]
            })
        # Screener B: 15m EMA7>EMA14 and 4h EMA7 <= EMA60*0.90.
        if common15 and h60[i] is not None and h7[i] <= h60[i] * 0.90:
            out.append({
                "rule": "B 4小时 EMA7低于EMA60至少10%",
                "symbol": symbol, "price": h4[i]["close"],
                "15m_ema7": e15_7, "15m_ema14": e15_14,
                "4h_ema7": h7[i], "4h_ema60": h60[i],
                "4h_gap_pct": (h7[i] / h60[i] - 1) * 100,
                "candle_time": h4[i]["t"]
            })
        return out
    except Exception as e:
        print(f"SKIP {symbol}: {e}")
        return []

def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"active": []}

def fmt(x):
    return f"{x:,.8g}" if isinstance(x, (int, float)) else str(x)

def send_email(items):
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.getenv("MAIL_FROM", user)
    recipient = os.environ["MAIL_TO"]
    msg = EmailMessage()
    msg["Subject"] = f"火种计划选币提醒：发现 {len(items)} 个新信号"
    msg["From"] = sender
    msg["To"] = recipient
    lines = ["火种计划云端选币器发现新标的（仅筛选提醒，不构成交易建议）：", ""]
    for x in items:
        lines += [f"【{x['rule']}】{x['symbol']}",
                  f"最新参考价（4H已收盘）: {fmt(x.get('price'))}",
                  f"15m EMA7/EMA14: {fmt(x.get('15m_ema7'))} / {fmt(x.get('15m_ema14'))}"]
        for key in ("4h_ema7", "4h_ema14", "4h_ema60", "4h_close",
                    "4h_gap_pct", "daily_ema7", "daily_ema14",
                    "weekly_ema7", "weekly_ema14"):
            if key in x:
                label = key.replace("_", " ")
                val = f"{fmt(x[key])}%" if key == "4h_gap_pct" else fmt(x[key])
                lines.append(f"{label}: {val}")
        lines += [f"币安合约图表: https://www.binance.com/en/futures/{x['symbol']}",
                  f"信号K线开盘时间(UTC ms): {x['candle_time']}", ""]
    lines += ["扫描周期：每15分钟。只使用已收盘K线；不会自动下单。"]
    msg.set_content("\n".join(lines))
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)

def main():
    print(f"Scan start UTC: {datetime.now(timezone.utc).isoformat()}")
    info = get_json("/fapi/v1/exchangeInfo")
    tickers = get_json("/fapi/v1/ticker/24hr")
    active = {s["symbol"] for s in info["symbols"]
              if s.get("status") == "TRADING" and
              s.get("contractType") == "PERPETUAL" and
              s.get("quoteAsset") == "USDT"}
    vols = {t["symbol"]: float(t.get("quoteVolume", 0)) for t in tickers}
    candidates = sorted(
        [s for s in active if vols.get(s, 0) >= VOLUME_FLOOR],
        key=lambda s: vols.get(s, 0), reverse=True
    )[:SCAN_LIMIT]
    print(f"Candidates meeting volume floor: {len(candidates)}")
    found = []
    for idx, symbol in enumerate(candidates, 1):
        found.extend(check_symbol(symbol))
        if idx % 20 == 0:
            print(f"Checked {idx}/{len(candidates)}; signals={len(found)}")
        time.sleep(0.05)
    # Deduplicate by rule + symbol; notify only when it newly enters the eligible set.
    state = load_state()
    previous = set(state.get("active", []))
    current_map = {}
    for item in found:
        key = item["rule"] + "|" + item["symbol"]
        current_map[key] = item
    new_keys = set(current_map) - previous
    new_items = [current_map[k] for k in sorted(new_keys)]
    if new_items:
        send_email(new_items)
        print(f"Email sent for {len(new_items)} new signal(s).")
    else:
        print("No new signals; no email sent.")
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"active": sorted(current_map.keys()),
                   "last_scan_utc": datetime.now(timezone.utc).isoformat()},
                  f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
