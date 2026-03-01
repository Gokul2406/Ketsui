import time
import requests
import math
from collections import defaultdict, deque

# ─── CONFIG ───────────────────────────────────
BASE_URL      = "https://ketsui-production.up.railway.app/"
API_KEY       = "key-alpha-1234"
POLL_INTERVAL = 3
# ──────────────────────────────────────────────

HEADERS   = {"x-api-key": API_KEY}
seen_news = set()

# ─── STATE ────────────────────────────────────

price_history  = defaultdict(lambda: deque(maxlen=30))
return_history = defaultdict(lambda: deque(maxlen=30))
news_signal    = defaultdict(float)

DECAY = 0.85
BETA  = 1.0
GAMMA = 1.0
RISK_SCALE = 0.3

# ──────────────────────────────────────────────
# 🔹 STRUCTURAL IMPACT MAP (Derived from archive logic)
# ──────────────────────────────────────────────

KEYWORD_IMPACT = {

    # Earnings / financial
    "beats":        0.015,
    "miss":        -0.015,
    "record":       0.02,
    "growth":       0.012,
    "loss":        -0.018,
    "profit":       0.01,
    "dividend":     0.01,

    # Regulatory / legal
    "approval":     0.02,
    "ban":         -0.025,
    "investigation":-0.02,
    "fine":        -0.02,
    "recall":      -0.02,

    # Supply chain
    "shortage":    -0.015,
    "expansion":    0.015,
    "capacity":     0.012,

    # M&A
    "acquisition":  0.02,
    "merger":       0.02,
    "partnership":  0.015,

    # Sentiment
    "bullish":      0.012,
    "downgrade":   -0.015,
    "upgrade":      0.015,
    "selloff":     -0.02,
    "rally":        0.015,
}

# ──────────────────────────────────────────────
# 🔹 MATHEMATICAL COMPONENTS
# ──────────────────────────────────────────────

def compute_volatility(asset):
    returns = return_history[asset]
    if len(returns) < 5:
        return 0.02
    mean = sum(returns)/len(returns)
    var = sum((r - mean)**2 for r in returns)/len(returns)
    return max(math.sqrt(var), 0.02)


def process_news(news):
    headline = news["headline"].lower()
    affected = news.get("assets_affected", [])

    words = headline.split()

    for asset in affected:
        drift = 0
        for w in words:
            drift += KEYWORD_IMPACT.get(w, 0)
        news_signal[asset] += drift


def decay_news():
    for asset in news_signal:
        news_signal[asset] *= DECAY


# ──────────────────────────────────────────────
# 🔹 STRATEGY
# ──────────────────────────────────────────────

def decide(market, portfolio):
    actions = []
    assets  = market["assets"]
    cash    = portfolio["cash"]

    # Update price history
    for name, info in assets.items():
        price = info["price"]
        if price_history[name]:
            prev = price_history[name][-1]
            ret = (price - prev) / prev
            return_history[name].append(ret)
        price_history[name].append(price)

    # Process new news
    news = market.get("news")
    if news and news["id"] not in seen_news:
        seen_news.add(news["id"])
        process_news(news)

    decay_news()

    total_value = portfolio["portfolio_value"]

    for name, info in assets.items():
        if len(price_history[name]) < 10:
            continue

        price = info["price"]

        # Momentum
        momentum = (price_history[name][-1] - price_history[name][-5]) / price_history[name][-5]

        # Mean reversion
        moving_avg = sum(price_history[name]) / len(price_history[name])
        deviation  = (price - moving_avg) / moving_avg

        # Expected return
        expected_return = (
            news_signal[name] +
            BETA * momentum -
            GAMMA * deviation
        )

        vol = compute_volatility(name)

        weight = (expected_return / (vol**2)) * RISK_SCALE
        target_value = weight * total_value

        current_qty = portfolio["holdings"].get(name, {}).get("quantity", 0)
        current_value = current_qty * price

        diff_value = target_value - current_value
        qty = int(diff_value / price)

        if qty > 0 and cash >= qty * price:
            actions.append((name, "buy", qty))
            cash -= qty * price
        elif qty < 0 and current_qty > 0:
            sell_qty = min(abs(qty), current_qty)
            actions.append((name, "sell", sell_qty))

    return actions


# ──────────────────────────────────────────────
# 🔹 MAIN LOOP
# ──────────────────────────────────────────────

def run():
    print("Structural Real-Time News Quant Bot Running...")

    while True:
        try:
            market = requests.get(f"{BASE_URL}/market", headers=HEADERS).json()
            portfolio = requests.get(f"{BASE_URL}/portfolio", headers=HEADERS).json()

            actions = decide(market, portfolio)

            for asset, action, qty in actions:
                requests.post(f"{BASE_URL}/action", headers=HEADERS, json={
                    "asset": asset,
                    "action": action,
                    "quantity": qty
                })

        except Exception as e:
            print("Error:", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
