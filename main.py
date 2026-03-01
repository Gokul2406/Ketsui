"""
AlgoTrading Event Server v3
============================
Market simulation with:
  - GBM price model (new mu function, sigma per asset)
  - News impact via calibrated J_map (take_beta_give_wideal + median k_calib)
  - News loaded from news.csv + newsbeta.csv (pipe-delimited)
  - Simplified trade execution at true_price (no order book)
  - 6 real assets: Dr. Reddy's, Pfizer, Coromandel, Nestle, HDFC, SBI
"""

import math
import time
import statistics
import sqlite3
import threading
import numpy as np
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Literal
import uvicorn

from config import (
    TEAMS, ADMIN_KEY, STARTING_CASH, MAX_POSITION_VALUE,
    ASSETS, TOTAL_FRAMES, FRAME_INTERVAL_SECONDS,
    DT, K_DECAY, KMU,
    NEWS_LAG_FRAMES, WSOURCE, mu,
    SIGMA_MAP_GAMMA, SIGMA_MAP_WINDOW
)

# ─────────────────────────────────────────────
# News CSV Loading
# ─────────────────────────────────────────────

def load_news_csvs():
    """
    Load news.csv and newsbeta.csv.
    Matches the simulation's CSV loading exactly:
      - Strips whitespace and filters empty lines
      - Zips both files together
      - deltat is dynamic: total_frames // len(combined_news)
      - Frame start/end per event: i*deltat and (i+1)*deltat

    news.csv columns (pipe-delimited, 0-indexed):
      0  - id
      1  - date
      2  - headline
      3  - base multiplier (float)
      4  - source name (key in WSOURCE)
      5-10 - per-asset raw impact weights (Dr. Reddy's → SBI)

    newsbeta.csv columns (pipe-delimited, 0-indexed):
      0-4  - metadata (ignored)
      5-10 - expected % price change per asset
    """
    with open("news.csv") as n:
        lines_n = [line.strip() for line in n.readlines() if line.strip()]
    with open("newsbeta.csv") as c:
        lines_c = [line.strip() for line in c.readlines() if line.strip()]

    combined = list(zip(lines_n, lines_c))
    deltat   = TOTAL_FRAMES // len(combined)   # dynamic, matches simulation

    param     = []
    idealb    = []
    news_meta = []

    for i, (line_n, line_c) in enumerate(combined):
        # Beta targets
        parts_c = line_c.split("|")
        idealb.append([float(parts_c[j]) for j in range(5, 11)])

        # Raw weights
        parts_n  = line_n.split("|")
        headline = parts_n[2].strip() if len(parts_n) > 2 else f"Market Event {i+1}"
        source   = parts_n[4].strip() if len(parts_n) > 4 else "Unknown"
        src_w    = WSOURCE.get(source, 1.0)    # safe fetch, default 1.0

        row = [float(parts_n[3])]              # index 0: base multiplier
        row.append(src_w)                       # index 1: source credibility
        for j in range(5, 11):                 # index 2-7: per-asset impacts
            row.append(float(parts_n[j]))
        row.append(i * deltat)                 # index 8: frame start
        row.append((i * deltat) + deltat)      # index 9: frame end

        param.append(row)
        news_meta.append({
            "headline":    headline,
            "source":      source,
            "frame_start": i * deltat,
            "deltat":      deltat,
        })

    return param, idealb, news_meta, deltat


# Load once at import time
PARAM, IDEALB, NEWS_META, NEWS_DELTAT = load_news_csvs()

# ─────────────────────────────────────────────
# J_map Calibration (take_beta_give_wideal)
# Directly ported from simulation code
# ─────────────────────────────────────────────

def int_mu(x: float) -> float:
    """Indefinite integral of mu(frame) with respect to continuous time."""
    return 0.001 * (1.2 * math.cos(KMU * x / 2) - math.cos(2 * KMU * x) / 3.2) / (1.35 * KMU)

def take_beta_give_wideal(beta: float, news_index: int) -> float:
    """
    Given desired price ratio (beta) for a news event, compute the ideal
    cumulative weight W the J_map must deliver over the effect window.
    Uses dynamic NEWS_DELTAT (total_frames // n_events).
    """
    tstart   = NEWS_LAG_FRAMES + news_index * NEWS_DELTAT
    integral = int_mu((tstart + NEWS_DELTAT) * DT) - int_mu(tstart * DT)
    exp1     = 1 - math.exp(-K_DECAY * NEWS_DELTAT * DT)
    lnb      = math.log(beta)
    return (K_DECAY / exp1) * (lnb - integral)

# ─────────────────────────────────────────────
# Asset Class
# ─────────────────────────────────────────────

class Asset:
    def __init__(self, name: str, true_price: float, sigma: float,
                 newspos: int, decay_const: float = 0.01):
        self.name         = name
        self.true_price   = true_price
        self.sigma        = sigma
        self.newspos      = newspos
        self.decay_const  = decay_const
        self.history      = [true_price]
        self.J_map        = np.zeros(TOTAL_FRAMES)
        self.sigma_map    = np.zeros(TOTAL_FRAMES)   # per-frame volatility boost

    def make_J_map(self):
        """
        Calibrated J_map + sigma_map construction.
        Matches latest simulation exactly:
          1. Compute raw W[i] = base * source * asset_col
          2. For each event compute ratio |w_ideal| / |W_raw|
             NOTE: W[i] is NOT overwritten (unlike previous version)
          3. k_calib = median of valid ratios
          4. Apply calibrated asymmetric weights into J_map (neg×1.4, pos×0.6)
          5. Compute sigma_map = gamma * sqrt(moving_avg(J_map^2, window))
        """
        W = [plist[0] * plist[1] * plist[self.newspos] for plist in PARAM]

        ratios = []
        for i, b in enumerate(IDEALB):
            beta_pct = b[self.newspos - 2]
            beta     = 1 + (beta_pct / 100)
            if beta == 1 or W[i] == 0:
                continue
            w_ideal = take_beta_give_wideal(beta, i)
            ratios.append(abs(w_ideal) / abs(W[i]))
            # W[i] is intentionally NOT replaced here

        k_calib = statistics.median(ratios) if ratios else 1.0

        for i, plist in enumerate(PARAM):
            start  = int(plist[8]) + NEWS_LAG_FRAMES
            end    = int(plist[9])
            weight = k_calib * plist[0] * (plist[1] * plist[self.newspos])
            if weight < 0:
                weight *= 1.4   # bear news hits harder
            else:
                weight *= 0.6   # bull news dampened

            for f in range(start, min(end + 1, TOTAL_FRAMES)):
                self.J_map[f] += weight * math.exp(-self.decay_const * (f - start) * DT)

        # sigma_map: per-frame volatility boost driven by news magnitude
        J2          = self.J_map ** 2
        moving_avg  = np.convolve(J2, np.ones(SIGMA_MAP_WINDOW) / SIGMA_MAP_WINDOW, mode='same')
        self.sigma_map = SIGMA_MAP_GAMMA * np.sqrt(moving_avg)

    def update_price(self, frame: int):
        """Advance true_price one frame via GBM + news factor + news-driven volatility."""
        news_factor   = self.J_map[frame] * self.true_price * DT
        general_trend = self.true_price * mu(frame) * DT
        # sigma_map adds extra volatility around news events
        effective_sigma = self.sigma + self.sigma_map[frame]
        random_factor   = self.true_price * effective_sigma * math.sqrt(DT) * np.random.normal(0, 1)
        self.true_price += general_trend + random_factor + news_factor
        self.true_price  = max(0.01, round(self.true_price, 4))
        self.history.append(self.true_price)

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "price":       self.true_price,
            "prev_price":  self.history[-2] if len(self.history) >= 2 else self.true_price,
            "change":      round(self.true_price - self.history[-2], 4) if len(self.history) >= 2 else 0.0,
            "change_pct":  round(((self.true_price - self.history[-2]) / self.history[-2]) * 100, 4)
                           if len(self.history) >= 2 and self.history[-2] > 0 else 0.0,
        }

# ─────────────────────────────────────────────
# Game State
# ─────────────────────────────────────────────

class GameState:
    def __init__(self):
        self.lock                = threading.Lock()
        self.started             = False
        self.start_time          = None
        self.current_frame       = 0
        self.current_news        = None   # latest single news item shown to bots
        self.released_news: list = []     # all news released so far (ordered)
        self.released_keys: set  = set()

        self.assets: dict[str, Asset] = {}
        self.portfolios: dict[str, dict] = {}
        self._init_assets()
        self._init_portfolios()

    def _init_assets(self):
        self.assets = {}
        for cfg in ASSETS:
            a = Asset(
                name        = cfg["name"],
                true_price  = cfg["true_price"],
                sigma       = cfg["sigma"],
                newspos     = cfg["newspos"],
                decay_const = cfg.get("decay_const", 0.01),
            )
            a.make_J_map()
            self.assets[cfg["name"]] = a
        print(f"✅ J_maps built for {len(self.assets)} assets.")

    def _init_portfolios(self):
        for tid in TEAMS:
            self.portfolios[tid] = {
                "cash":     STARTING_CASH,
                "holdings": {name: 0 for name in self.assets},
            }

    def get_portfolio_value(self, team_id: str) -> float:
        p = self.portfolios[team_id]
        stock_value = sum(qty * self.assets[n].true_price
                          for n, qty in p["holdings"].items())
        return round(p["cash"] + stock_value, 2)

    def advance_frame(self):
        """
        Advance one simulation frame.
        Mirrors the per-frame logic in the simulation script.
        """
        with self.lock:
            if not self.started or self.current_frame >= TOTAL_FRAMES:
                return

            frame = self.current_frame

            # ── 1. Update all asset prices ────────────────────────────
            for asset in self.assets.values():
                asset.update_price(frame)

            # ── 2. Release news events on schedule ───────────────────
            for i, meta in enumerate(NEWS_META):
                release_frame = meta["frame_start"] + NEWS_LAG_FRAMES
                key = f"news_{i}"
                if frame >= release_frame and key not in self.released_keys:
                    self.released_keys.add(key)
                    event = {
                        "id":         key,
                        "index":      i,
                        "frame":      frame,
                        "headline":   meta["headline"],
                        "source":     meta["source"],
                        "assets_affected": [
                            a.name for a in self.assets.values()
                            if abs(PARAM[i][a.newspos]) > 0.01
                        ],
                    }
                    self.current_news = event
                    self.released_news.append(event)
                    print(f"[NEWS @ frame {frame}] {meta['headline']}")

            self.current_frame += 1
            if self.current_frame >= TOTAL_FRAMES:
                print("🏁 Simulation complete.")


state = GameState()

# ─────────────────────────────────────────────
# SQLite — Trade Logging
# ─────────────────────────────────────────────

DB_PATH = "trades.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT,
            team_id     TEXT,
            action      TEXT,
            asset       TEXT,
            quantity    INTEGER,
            price       REAL,
            total       REAL,
            cash_after  REAL,
            frame       INTEGER,
            news_ctx    TEXT
        )
    """)
    conn.commit()
    conn.close()

def log_trade(team_id, action, asset, quantity, price, total, cash_after, frame, news_ctx):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO trades
        (timestamp, team_id, action, asset, quantity, price, total, cash_after, frame, news_ctx)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (datetime.utcnow().isoformat(), team_id, action, asset,
          quantity, price, round(total, 4), cash_after, frame, news_ctx))
    conn.commit()
    conn.close()

def get_trade_history(team_id: str) -> list:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT timestamp, action, asset, quantity, price, total, frame
        FROM trades WHERE team_id=? ORDER BY id DESC LIMIT 50
    """, (team_id,)).fetchall()
    conn.close()
    return [
        {"timestamp": r[0], "action": r[1], "asset": r[2],
         "quantity": r[3], "price": r[4], "total": r[5], "frame": r[6]}
        for r in rows
    ]

# ─────────────────────────────────────────────
# Background Frame Advancer
# ─────────────────────────────────────────────

def frame_advancer():
    while True:
        time.sleep(FRAME_INTERVAL_SECONDS)
        if state.started:
            state.advance_frame()

# ─────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────

def get_team(x_api_key: str = Header(...)) -> str:
    for tid, info in TEAMS.items():
        if info["api_key"] == x_api_key:
            return tid
    raise HTTPException(status_code=401, detail="Invalid API key.")

def get_admin(x_api_key: str = Header(...)):
    if x_api_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Admin access only.")

def require_started():
    if not state.started:
        raise HTTPException(status_code=400, detail="Game has not started yet.")

# ─────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    t = threading.Thread(target=frame_advancer, daemon=True)
    t.start()
    print("✅ Server ready. Waiting for admin to start the game.")
    yield

app = FastAPI(
    title="AlgoTrading Event Server",
    description="GBM market simulation with calibrated news impact. 6 assets, CSV-driven news.",
    version="3.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

class ActionRequest(BaseModel):
    asset:    str                              = Field(..., description="Asset name e.g. 'Dr. Reddy\\'s', 'HDFC'")
    action:   Literal["buy", "sell", "hold"]
    quantity: int                              = Field(default=1, ge=0)

# ─────────────────────────────────────────────
# Bot Endpoints
# ─────────────────────────────────────────────

@app.get("/", tags=["Health"])
def root():
    return {
        "status":       "running",
        "game_started": state.started,
        "assets":       list(state.assets.keys()),
        "frame":        state.current_frame,
        "total_frames": TOTAL_FRAMES,
        "docs":         "/docs",
    }

@app.get("/market", tags=["Game"])
def get_market(team_id: str = Depends(get_team)):
    """
    Current market snapshot.
    Returns price, change, change_pct for all assets, plus latest news.
    """
    require_started()
    elapsed = round(time.time() - state.start_time)
    return {
        "frame":           state.current_frame,
        "total_frames":    TOTAL_FRAMES,
        "elapsed_seconds": elapsed,
        "assets":          {name: a.to_dict() for name, a in state.assets.items()},
        "news":            state.current_news,
    }

@app.get("/news", tags=["Game"])
def get_latest_news(team_id: str = Depends(get_team)):
    """Latest released news event."""
    require_started()
    if not state.current_news:
        return {"news": None, "message": "No news released yet."}
    return {"news": state.current_news, "frame": state.current_frame}

@app.get("/news/all", tags=["Game"])
def get_all_news(team_id: str = Depends(get_team)):
    """
    All news events released so far, in order.
    Use this on startup to catch up if your bot missed earlier events.
    """
    require_started()
    return {
        "count": len(state.released_news),
        "news":  state.released_news,
    }

@app.post("/action", tags=["Game"])
def take_action(body: ActionRequest, team_id: str = Depends(get_team)):
    """
    Submit a trading action.

    Trades execute directly at the current true_price.
    No order book, no partial fills, no carry-overs.

    - buy:  deduct cash, add shares
    - sell: add cash, remove shares
    - hold: no change, recorded for audit
    """
    require_started()

    asset = state.assets.get(body.asset)
    if not asset:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown asset '{body.asset}'. Available: {list(state.assets.keys())}"
        )

    with state.lock:
        portfolio = state.portfolios[team_id]
        price     = asset.true_price
        quantity  = body.quantity
        total     = round(price * quantity, 4)
        news_ctx  = state.current_news["headline"] if state.current_news else "No news"

        if body.action == "hold":
            return {
                "status":          "ok",
                "action":          "hold",
                "asset":           body.asset,
                "price":           price,
                "quantity":        0,
                "total":           0.0,
                "cash_remaining":  round(portfolio["cash"], 2),
                "portfolio_value": state.get_portfolio_value(team_id),
            }

        if quantity <= 0:
            raise HTTPException(status_code=400, detail="Quantity must be > 0.")

        if body.action == "buy":
            if portfolio["cash"] < total:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient funds. Need ₹{total:,.2f}, have ₹{portfolio['cash']:,.2f}."
                )
            # Position limit check: current holding value + new purchase cannot exceed MAX_POSITION_VALUE
            current_holding_value = portfolio["holdings"].get(body.asset, 0) * price
            if current_holding_value + total > MAX_POSITION_VALUE:
                allowed = MAX_POSITION_VALUE - current_holding_value
                max_qty = int(allowed // price)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Position limit exceeded. Max ₹{MAX_POSITION_VALUE:,.0f} per stock. "
                        f"You currently hold ₹{current_holding_value:,.2f} of {body.asset}. "
                        f"You can buy at most {max_qty} more unit(s) (₹{max_qty * price:,.2f})."
                    )
                )
            portfolio["cash"]                  = round(portfolio["cash"] - total, 4)
            portfolio["holdings"][body.asset] += quantity

        elif body.action == "sell":
            owned = portfolio["holdings"].get(body.asset, 0)
            if owned < quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient holdings. You own {owned} units of {body.asset}."
                )
            portfolio["cash"]                  = round(portfolio["cash"] + total, 4)
            portfolio["holdings"][body.asset] -= quantity

        log_trade(team_id, body.action, body.asset,
                  quantity, price, total, portfolio["cash"],
                  state.current_frame, news_ctx)

        return {
            "status":          "ok",
            "action":          body.action,
            "asset":           body.asset,
            "price":           price,
            "quantity":        quantity,
            "total":           total,
            "cash_remaining":  round(portfolio["cash"], 2),
            "portfolio_value": state.get_portfolio_value(team_id),
        }

@app.get("/portfolio", tags=["Game"])
def get_portfolio(team_id: str = Depends(get_team)):
    """Your cash, holdings, P&L, and recent trades."""
    require_started()
    with state.lock:
        p  = state.portfolios[team_id]
        pv = state.get_portfolio_value(team_id)
        return {
            "team":    TEAMS[team_id]["name"],
            "cash":    round(p["cash"], 2),
            "holdings": {
                name: {
                    "quantity": qty,
                    "price":    round(state.assets[name].true_price, 4),
                    "value":    round(qty * state.assets[name].true_price, 2),
                }
                for name, qty in p["holdings"].items()
            },
            "portfolio_value": pv,
            "starting_cash":   STARTING_CASH,
            "pnl":             round(pv - STARTING_CASH, 2),
            "pnl_pct":         round(((pv - STARTING_CASH) / STARTING_CASH) * 100, 2),
            "recent_trades":   get_trade_history(team_id),
        }

@app.get("/leaderboard", tags=["Game"])
def get_leaderboard(team_id: str = Depends(get_team)):
    """All teams ranked by portfolio value."""
    require_started()
    with state.lock:
        board = sorted(
            [
                {
                    "team":            TEAMS[tid]["name"],
                    "portfolio_value": state.get_portfolio_value(tid),
                    "pnl":             round(state.get_portfolio_value(tid) - STARTING_CASH, 2),
                    "pnl_pct":         round(
                        ((state.get_portfolio_value(tid) - STARTING_CASH) / STARTING_CASH) * 100, 2
                    ),
                }
                for tid in TEAMS
            ],
            key=lambda x: x["portfolio_value"],
            reverse=True,
        )
        for i, entry in enumerate(board):
            entry["rank"] = i + 1
    return {
        "frame":       state.current_frame,
        "leaderboard": board,
        "prices":      {name: round(a.true_price, 4) for name, a in state.assets.items()},
    }

# ─────────────────────────────────────────────
# Admin Endpoints
# ─────────────────────────────────────────────

@app.post("/admin/start", tags=["Admin"])
def start_game(_=Depends(get_admin)):
    with state.lock:
        if state.started:
            return {"status": "already started", "frame": state.current_frame}
        state.started    = True
        state.start_time = time.time()
    print("🚀 Game started!")
    return {
        "status":                  "game started",
        "total_frames":            TOTAL_FRAMES,
        "frame_interval_seconds":  FRAME_INTERVAL_SECONDS,
        "estimated_duration_mins": round((TOTAL_FRAMES * FRAME_INTERVAL_SECONDS) / 60, 1),
    }

@app.post("/admin/reset", tags=["Admin"])
def reset_game(_=Depends(get_admin)):
    with state.lock:
        state.started        = False
        state.start_time     = None
        state.current_frame  = 0
        state.current_news   = None
        state.released_news  = []
        state.released_keys  = set()
        state._init_assets()
        state._init_portfolios()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM trades")
    conn.commit()
    conn.close()
    return {"status": "reset complete"}

@app.get("/admin/status", tags=["Admin"])
def admin_status(_=Depends(get_admin)):
    with state.lock:
        elapsed = round(time.time() - state.start_time) if state.started else 0
        return {
            "started":          state.started,
            "current_frame":    state.current_frame,
            "total_frames":     TOTAL_FRAMES,
            "elapsed_seconds":  elapsed,
            "news_released":    len(state.released_news),
            "current_news":     state.current_news,
            "assets":           {name: a.to_dict() for name, a in state.assets.items()},
            "portfolios": {
                tid: {
                    "name":        TEAMS[tid]["name"],
                    "cash":        state.portfolios[tid]["cash"],
                    "holdings":    state.portfolios[tid]["holdings"],
                    "total_value": state.get_portfolio_value(tid),
                }
                for tid in TEAMS
            },
        }

@app.post("/admin/skip-frames", tags=["Admin"])
def skip_frames(count: int = 1, _=Depends(get_admin)):
    """Fast-forward N frames instantly. Useful for testing news triggers."""
    if not 1 <= count <= 1000:
        raise HTTPException(status_code=400, detail="count must be 1–1000.")
    if not state.started:
        raise HTTPException(status_code=400, detail="Game not started.")
    for _ in range(count):
        state.advance_frame()
    return {"status": "ok", "new_frame": state.current_frame, "news_released": len(state.released_news)}

@app.post("/admin/release-news", tags=["Admin"])
def release_news_manual(index: int, _=Depends(get_admin)):
    """Manually inject a news event by its row index in news.csv (0-based)."""
    if index >= len(NEWS_META):
        raise HTTPException(status_code=400, detail=f"Index out of range. Max: {len(NEWS_META)-1}")
    with state.lock:
        meta  = NEWS_META[index]
        key   = f"news_{index}"
        event = {
            "id":              key,
            "index":           index,
            "frame":           state.current_frame,
            "headline":        meta["headline"],
            "source":          meta["source"],
            "assets_affected": [
                a.name for a in state.assets.values()
                if abs(PARAM[index][a.newspos]) > 0.01
            ],
        }
        state.current_news = event
        state.released_keys.add(key)
        if event not in state.released_news:
            state.released_news.append(event)
    return {"status": "released", "news": event}

@app.post("/admin/set-price", tags=["Admin"])
def set_price(asset_name: str, price: float, _=Depends(get_admin)):
    """Manually override an asset's price."""
    asset = state.assets.get(asset_name)
    if not asset:
        raise HTTPException(status_code=400, detail=f"Unknown asset: {asset_name}")
    with state.lock:
        asset.true_price = round(price, 4)
        asset.history.append(asset.true_price)
    return {"status": "ok", "asset": asset_name, "new_price": price}

@app.get("/admin/news-schedule", tags=["Admin"])
def news_schedule(_=Depends(get_admin)):
    """See when each news event is scheduled to release (in frames and estimated real time)."""
    schedule = []
    for i, meta in enumerate(NEWS_META):
        release_frame = meta["frame_start"] + NEWS_LAG_FRAMES
        eta_seconds   = max(0, (release_frame - state.current_frame) * FRAME_INTERVAL_SECONDS)
        schedule.append({
            "index":         i,
            "headline":      meta["headline"],
            "source":        meta["source"],
            "release_frame": release_frame,
            "released":      f"news_{i}" in state.released_keys,
            "eta_seconds":   round(eta_seconds),
            "eta_minutes":   round(eta_seconds / 60, 1),
        })
    return {"total_events": len(schedule), "schedule": schedule}

# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
