"""
example_bot.py — Starter bot template (v3)
===========================================
Works with the GBM simulation server (v3).

Key changes from v2:
- Assets are now: "Dr. Reddy's", "Pfizer", "Coromandel", "Nestle", "HDFC", "SBI"
- No order book — trades execute at current true_price directly
- No carry-overs — orders are always fully filled
- Use GET /news/all on startup to catch up on any missed news
- GET /market returns price, change, change_pct per asset

Usage:
    python example_bot.py
"""

import time
import requests

# ─── CONFIG ───────────────────────────────────
BASE_URL      = "http://localhost:8000"
API_KEY       = "key-alpha-1234"
POLL_INTERVAL = 5   # seconds between decision loops
# ──────────────────────────────────────────────

HEADERS      = {"x-api-key": API_KEY}
seen_news    = set()   # track news IDs we've already reacted to

# ─── API helpers ──────────────────────────────

def get_market() -> dict:
    r = requests.get(f"{BASE_URL}/market", headers=HEADERS)
    r.raise_for_status()
    return r.json()

def get_all_news() -> list:
    """Call this on startup to catch up on all released news."""
    r = requests.get(f"{BASE_URL}/news/all", headers=HEADERS)
    r.raise_for_status()
    return r.json().get("news", [])

def get_portfolio() -> dict:
    r = requests.get(f"{BASE_URL}/portfolio", headers=HEADERS)
    r.raise_for_status()
    return r.json()

def take_action(asset: str, action: str, quantity: int) -> dict:
    """
    Execute a buy/sell/hold at current true_price.
    Always fully fills — no partial fills or carry-overs.
    """
    r = requests.post(f"{BASE_URL}/action", headers=HEADERS, json={
        "asset":    asset,
        "action":   action,
        "quantity": quantity,
    })
    r.raise_for_status()
    return r.json()

# ─── Strategy ─────────────────────────────────

def decide(market: dict, portfolio: dict) -> list[tuple]:
    """
    ─────────────────────────────────────────────
    YOUR STRATEGY GOES HERE
    ─────────────────────────────────────────────
    Return list of (asset, action, quantity).
    Example: [("HDFC", "buy", 10), ("SBI", "sell", 5)]

    Available data:
      market["assets"]["HDFC"]["price"]        → current true_price
      market["assets"]["HDFC"]["change"]       → absolute change this frame
      market["assets"]["HDFC"]["change_pct"]   → % change this frame
      market["news"]                            → latest news (or None)
      portfolio["cash"]                         → available cash
      portfolio["holdings"]["HDFC"]["quantity"] → shares you own
    ─────────────────────────────────────────────
    """
    actions  = []
    news     = market.get("news")
    assets   = market["assets"]
    cash     = portfolio["cash"]

    # ── Example: react to new news ────────────────────────────────────
    if news and news["id"] not in seen_news:
        seen_news.add(news["id"])
        headline  = news["headline"].lower()
        affected  = news.get("assets_affected", [])
        print(f"\n📰 NEW NEWS: {news['headline']}")
        print(f"   Source: {news['source']} | Affects: {affected}")

        positives = ["beats", "record", "growth", "wins", "contract", "dividend",
                     "rally", "approvals", "expansion", "partnership", "bullish"]
        negatives = ["miss", "cut", "decline", "recall", "inquiry", "npa",
                     "shortage", "selloff", "concern", "pressure", "warning"]

        for asset_name in affected:
            if asset_name not in assets:
                continue

            price    = assets[asset_name]["price"]
            is_bull  = any(w in headline for w in positives)
            is_bear  = any(w in headline for w in negatives)

            if is_bull:
                affordable = int(cash * 0.2 / price)   # invest 20% of cash
                if affordable > 0:
                    actions.append((asset_name, "buy", affordable))
                    cash -= affordable * price

            elif is_bear:
                owned = portfolio["holdings"].get(asset_name, {}).get("quantity", 0)
                if owned > 0:
                    actions.append((asset_name, "sell", owned))

    return actions


# ─── Main loop ────────────────────────────────

def run():
    print("🤖 Bot starting...")
    print(f"   Server: {BASE_URL}\n")

    # Wait for game to start
    while True:
        try:
            info = requests.get(f"{BASE_URL}/", headers=HEADERS).json()
            if info.get("game_started"):
                print("✅ Game live!\n")
                # Catch up on any news released before bot started
                past_news = get_all_news()
                for n in past_news:
                    seen_news.add(n["id"])
                    print(f"   [CATCHUP] {n['headline']}")
                break
            print(f"⏳ Waiting... (frame {info.get('frame', 0)})")
            time.sleep(3)
        except Exception as e:
            print(f"❌ Can't reach server: {e}")
            time.sleep(5)

    # Trading loop
    while True:
        try:
            market    = get_market()
            portfolio = get_portfolio()

            frame = market["frame"]
            pv    = portfolio["portfolio_value"]
            pnl   = portfolio["pnl"]
            sign  = "+" if pnl >= 0 else ""

            print(f"\n[Frame {frame:04d}] 💰 ${pv:,.2f} | PnL: {sign}${pnl:,.2f} ({sign}{portfolio['pnl_pct']}%)")
            for name, info in market["assets"].items():
                chg = info['change_pct']
                arrow = "▲" if chg > 0 else "▼" if chg < 0 else "●"
                print(f"   {name:<15} {info['price']:>10.4f}  {arrow} {chg:+.4f}%")

            actions = decide(market, portfolio)

            if not actions:
                print("   📊 Hold.")
            else:
                for asset, action, qty in actions:
                    try:
                        r = take_action(asset, action, qty)
                        print(f"   ✅ {action.upper()} {qty}x {asset} @ {r['price']:.4f} | Cash: ${r['cash_remaining']:,.2f}")
                    except requests.HTTPError as e:
                        print(f"   ❌ {action.upper()} {asset}: {e.response.json().get('detail', str(e))}")

        except requests.HTTPError as e:
            print(f"❌ HTTP {e.response.status_code}: {e.response.text}")
        except Exception as e:
            print(f"❌ Error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
