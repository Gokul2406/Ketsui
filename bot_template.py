"""
╔══════════════════════════════════════════════════════════════════╗
║              ALGOTRADING COMPETITION — BOT TEMPLATE           ║
║                                                                  ║
║  Your goal: write a bot that reads news, decides which stocks    ║
║  to buy or sell, and maximises your portfolio value over         ║
║  the 2-hour simulation.                                          ║
║                                                                  ║
║  Rules:                                                          ║
║    • Starting capital:  ₹10,00,000                               ║
║    • Max position size: ₹5,00,000 per stock                      ║
║    • Assets: Dr. Reddy's | Pfizer | Coromandel |                 ║
║              Nestle | HDFC | SBI                                  ║
║    • You are ranked by total portfolio value at game end         ║
║      (cash + value of all stock holdings)                        ║
╚══════════════════════════════════════════════════════════════════╝

SETUP
-----
1. Install dependencies:
       pip install requests

2. Fill in your API_KEY below.

3. Run:
       python bot.py

4. Your strategy goes inside the `strategy()` function.
   Everything else is wired up for you.
"""

import time
import requests

# ─────────────────────────────────────────────────────
# CONFIGURATION — fill these in before running
# ─────────────────────────────────────────────────────
SERVER_URL = "https://ketsui-production.up.railway.app/"   # e.g. "https://xyz.up.railway.app"
API_KEY    = "YOUR_API_KEY_HERE"             # e.g. "key-alpha-1234"

POLL_INTERVAL  = 5      # seconds between each loop iteration
MAX_POSITION   = 500000 # ₹5,00,000 per stock — enforced server-side too
STARTING_CASH  = 1000000
# ─────────────────────────────────────────────────────


HEADERS   = {"x-api-key": API_KEY}
seen_news = set()   # track news IDs already processed so we don't react twice


# ══════════════════════════════════════════════════════
# API HELPERS
# All server responses are documented below each call.
# ══════════════════════════════════════════════════════

def get_market() -> dict:
    """
    GET /market
    Returns a snapshot of all asset prices plus the latest news.

    Response shape:
    {
        "frame":           1042,          # current simulation frame (0 to 4784)
        "total_frames":    4784,
        "elapsed_seconds": 1563,          # seconds since game started
        "assets": {
            "HDFC": {
                "name":       "HDFC",
                "price":      902.14,     # current price (this is what you trade at)
                "prev_price": 901.88,     # price one frame ago
                "change":     0.26,       # price - prev_price
                "change_pct": 0.0288      # % change from prev frame
            },
            "SBI":         { ... },
            "Dr. Reddy's": { ... },
            "Pfizer":      { ... },
            "Coromandel":  { ... },
            "Nestle":      { ... }
        },
        "news": {                         # None if no news released yet
            "id":       "news_3",
            "index":    3,
            "frame":    840,
            "headline": "Nestle India posts record quarterly revenue...",
            "source":   "Department of Press White House"
        }
    }
    """
    r = requests.get(f"{SERVER_URL}/market", headers=HEADERS, timeout=5)
    r.raise_for_status()
    return r.json()


def get_all_news() -> list:
    """
    GET /news/all
    Returns every news event released so far, in order.
    Call this once on startup to catch up if the game already started.

    Response shape:
    {
        "count": 4,
        "news": [
            {
                "id":       "news_0",
                "index":    0,
                "frame":    6,
                "headline": "Dr. Reddy's Q3 net profit rises 18% YoY...",
                "source":   "Press Information Bureau"
            },
            ...
        ]
    }
    """
    r = requests.get(f"{SERVER_URL}/news/all", headers=HEADERS, timeout=5)
    r.raise_for_status()
    return r.json().get("news", [])


def get_portfolio() -> dict:
    """
    GET /portfolio
    Returns your current cash, stock holdings, P&L, and last 50 trades.

    Response shape:
    {
        "team":  "Team Alpha",
        "cash":  650000.00,
        "holdings": {
            "HDFC":        { "quantity": 200, "price": 902.14, "value": 180428.0 },
            "SBI":         { "quantity": 0,   "price": 1201.5, "value": 0.0 },
            "Dr. Reddy's": { "quantity": 0,   "price": 1303.2, "value": 0.0 },
            "Pfizer":      { "quantity": 0,   "price": 5060.1, "value": 0.0 },
            "Coromandel":  { "quantity": 0,   "price": 2220.8, "value": 0.0 },
            "Nestle":      { "quantity": 0,   "price": 9965.3, "value": 0.0 }
        },
        "portfolio_value": 830428.0,      # cash + total stock value
        "starting_cash":   1000000.0,
        "pnl":             -169572.0,     # portfolio_value - starting_cash
        "pnl_pct":         -16.96,
        "recent_trades":   [ ... ]        # last 50 trades (newest first)
    }
    """
    r = requests.get(f"{SERVER_URL}/portfolio", headers=HEADERS, timeout=5)
    r.raise_for_status()
    return r.json()


def get_leaderboard() -> list:
    """
    GET /leaderboard
    See where all teams currently stand.

    Response shape:
    {
        "frame": 1042,
        "leaderboard": [
            { "rank": 1, "team": "Team Beta",  "portfolio_value": 1120000.0, "pnl": 120000.0, "pnl_pct": 12.0 },
            { "rank": 2, "team": "Team Alpha", "portfolio_value": 1080000.0, "pnl":  80000.0, "pnl_pct":  8.0 },
            ...
        ],
        "prices": { "HDFC": 902.14, "SBI": 1201.5, ... }
    }
    """
    r = requests.get(f"{SERVER_URL}/leaderboard", headers=HEADERS, timeout=5)
    r.raise_for_status()
    return r.json().get("leaderboard", [])


def buy(asset: str, quantity: int) -> dict:
    """
    POST /action  { action: "buy", asset: "...", quantity: N }

    Executes immediately at the current market price.

    Possible errors (HTTP 400):
      - "Insufficient funds"        — you don't have enough cash
      - "Position limit exceeded"   — would push your holding in this stock
                                      above ₹5,00,000

    Success response:
    {
        "status":          "ok",
        "action":          "buy",
        "asset":           "HDFC",
        "price":           902.14,       # price you paid per unit
        "quantity":        100,
        "total":           90214.0,      # total cash deducted
        "cash_remaining":  559786.0,
        "portfolio_value": 740214.0
    }
    """
    r = requests.post(f"{SERVER_URL}/action", headers=HEADERS, timeout=5,
                      json={"action": "buy", "asset": asset, "quantity": quantity})
    r.raise_for_status()
    return r.json()


def sell(asset: str, quantity: int) -> dict:
    """
    POST /action  { action: "sell", asset: "...", quantity: N }

    Possible errors (HTTP 400):
      - "Insufficient holdings" — you don't own that many units

    Success response: same shape as buy(), cash_remaining increases.
    """
    r = requests.post(f"{SERVER_URL}/action", headers=HEADERS, timeout=5,
                      json={"action": "sell", "asset": asset, "quantity": quantity})
    r.raise_for_status()
    return r.json()


# ══════════════════════════════════════════════════════
# HELPER UTILITIES
# ══════════════════════════════════════════════════════

def max_affordable(price: float, cash: float, current_holding_value: float) -> int:
    """
    How many units can you buy without exceeding cash OR the ₹5,00,000
    position limit?
    """
    room_cash     = cash
    room_position = max(0, MAX_POSITION - current_holding_value)
    room          = min(room_cash, room_position)
    return int(room // price)


def holding_value(portfolio: dict, asset: str) -> float:
    """Current market value of your position in one asset."""
    h = portfolio["holdings"].get(asset, {})
    return h.get("value", 0.0)


def holding_qty(portfolio: dict, asset: str) -> int:
    """How many units of an asset you currently hold."""
    return portfolio["holdings"].get(asset, {}).get("quantity", 0)


# ══════════════════════════════════════════════════════
# YOUR STRATEGY
# ══════════════════════════════════════════════════════

def strategy(market: dict, portfolio: dict) -> None:
    """
    This is the only function you need to edit.

    Called every POLL_INTERVAL seconds with fresh market and portfolio data.
    Use buy() and sell() to act. You can call them multiple times per cycle.

    Parameters
    ----------
    market : dict
        Full response from GET /market. Key fields:
          market["frame"]                         → current frame number
          market["assets"]["HDFC"]["price"]       → current price
          market["assets"]["HDFC"]["change"]      → absolute change this frame
          market["assets"]["HDFC"]["change_pct"]  → % change this frame
          market["news"]                          → latest news dict or None

    portfolio : dict
        Full response from GET /portfolio. Key fields:
          portfolio["cash"]                              → available cash
          portfolio["portfolio_value"]                   → total value
          portfolio["pnl"]                               → profit / loss vs start
          portfolio["holdings"]["HDFC"]["quantity"]      → units held
          portfolio["holdings"]["HDFC"]["value"]         → current market value

    Tips
    ----
    • News is released roughly every 15 seconds. The headline and source
      are your only signal — figure out which stocks are affected.
    • Price changes (change_pct) can signal momentum to follow or mean-
      revert against.
    • Watch your cash: if it hits 0 you can't buy anything.
    • Watch your position limit: max ₹5,00,000 per stock.
    • You are ranked on TOTAL portfolio value at game end, not just cash.
      Holding a rising stock is just as good as cashing out.
    """

    news      = market.get("news")
    assets    = market["assets"]
    cash      = portfolio["cash"]

    # ── Step 1: React to new news ──────────────────────────────────────────

    # ── Step 2: Continuous price-based logic (runs every cycle) ───────────

    pass  # ← remove this once you add your logic above


# ══════════════════════════════════════════════════════
# MAIN LOOP — do not edit below this line
# ══════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  KETSUI BOT STARTING")
    print(f"  Server : {SERVER_URL}")
    print("=" * 60)

    # Wait for the game to start
    while True:
        try:
            r = requests.get(f"{SERVER_URL}/", headers=HEADERS, timeout=5)
            info = r.json()
            if info.get("game_started"):
                print("\n✅ Game is live! Entering trading loop.\n")
                break
            print(f"  ⏳ Waiting for game to start... (frame {info.get('frame', 0)})")
        except Exception as e:
            print(f"  ❌ Cannot reach server: {e}")
        time.sleep(3)

    # Catch up on any news released before the bot connected
    try:
        past = get_all_news()
        if past:
            print(f"  📋 Catching up on {len(past)} past news event(s):")
            for n in past:
                seen_news.add(n["id"])
                print(f"     [{n['id']}] {n['headline']}")
        print()
    except Exception as e:
        print(f"  ⚠ Could not fetch past news: {e}")

    # Main trading loop
    while True:
        try:
            market    = get_market()
            portfolio = get_portfolio()

            # ── Status print ──────────────────────────────────────────────
            frame = market["frame"]
            total = market["total_frames"]
            pv    = portfolio["portfolio_value"]
            pnl   = portfolio["pnl"]
            pct   = portfolio["pnl_pct"]
            sign  = "+" if pnl >= 0 else ""

            print(f"[{frame:04d}/{total}] "
                  f"Capital: ₹{pv:>12,.2f}  |  "
                  f"PnL: {sign}₹{pnl:>10,.2f} ({sign}{pct:.2f}%)  |  "
                  f"Cash: ₹{portfolio['cash']:>12,.2f}")

            # ── Run your strategy ─────────────────────────────────────────
            strategy(market, portfolio)

        except requests.HTTPError as e:
            body = {}
            try: body = e.response.json()
            except: pass
            print(f"  ❌ HTTP {e.response.status_code}: {body.get('detail', str(e))}")

        except requests.ConnectionError:
            print("  ❌ Lost connection to server, retrying...")

        except Exception as e:
            print(f"  ❌ Unexpected error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
