"""
config.py — Edit this to configure your event.
"""
import math

# ─────────────────────────────────────────────
# Admin & Auth
# ─────────────────────────────────────────────
ADMIN_KEY          = "admin-super-secret-key-change-this"
STARTING_CASH      = 100_000.0   # INR 1,00,000
MAX_POSITION_VALUE = 50_000.0    # Max INR 50,000 worth of any single stock

TEAMS = {
    "team_alpha":   {"name": "milkshake",   "api_key": "milshakesuckswhatisthisusernamebro"},
    "team_beta":    {"name": "corandvisory",    "api_key": "badusername12345"},
    "team_gamma":   {"name": "cackle",   "api_key": "idkwhatcacklemeansbutsurebro"},
    "team_delta":   {"name": "eulersballs",   "api_key": "veryinterestingchoiceofname69"},
    "team_epsilon": {"name": "peepeepoopoo", "api_key": "poopoopeepee1234"},
    "team_idk": {"name": "team_scrutari", "api_key": "scrutariwasaniceevent123"},
}

# ─────────────────────────────────────────────
# Simulation constants (match simulation code exactly)
# ─────────────────────────────────────────────
TOTAL_FRAMES           = 4784
DT                     = 0.375    # minutes per frame
K_DECAY                = 0.01     # used in take_beta_give_wideal
# NOTE: DELTA_T_NEWS is now dynamic — computed in main.py as
# total_frames // len(news_events), matching the simulation exactly.
KMU                    = 0.0026   # mu frequency constant (updated)
NEWS_LAG_FRAMES        = 6        # lag before J_map kicks in per event

# Sigma_map parameters (new — per-frame volatility boost from news)
SIGMA_MAP_GAMMA  = 1.2    # amplification factor
SIGMA_MAP_WINDOW = 800    # moving average window for J^2 smoothing

# 4784 frames × 1.5s = ~2 hours
FRAME_INTERVAL_SECONDS = 1.5

# ─────────────────────────────────────────────
# Source credibility weights
# Keys must EXACTLY match column 4 of news.csv
# ─────────────────────────────────────────────
WSOURCE = {
    'Press Information Bureau':        1.00,
    'Department of Press White House': 0.85,
    'Primary Post News Ltd':           1.00,
    'Democratic TV':                   0.63,
    'Fox News':                        0.20,
}

# ─────────────────────────────────────────────
# Mu drift function (identical to simulation)
# ─────────────────────────────────────────────
def mu(frame: int) -> float:
    return (0.0003 *
            (0.6 * math.sin(KMU * frame / 2) + math.sin(2 * KMU * frame) / 1.6)
            / 1.35)

# ─────────────────────────────────────────────
# Assets
# newspos maps to column in news.csv that carries this asset's impact:
#   newspos 2 → col 5  → Dr. Reddy's
#   newspos 3 → col 6  → Pfizer
#   newspos 4 → col 7  → Coromandel
#   newspos 5 → col 8  → Nestle
#   newspos 6 → col 9  → HDFC
#   newspos 7 → col 10 → SBI
# ─────────────────────────────────────────────
ASSETS = [
    {"name": "Dr. Reddy's", "true_price":  50.0, "sigma": 0.002,  "newspos": 2, "decay_const": 0.01},
    {"name": "Pfizer",      "true_price": 200.0, "sigma": 0.001,  "newspos": 3, "decay_const": 0.01},
    {"name": "Coromandel",  "true_price":  40.0, "sigma": 0.001,  "newspos": 4, "decay_const": 0.01},
    {"name": "Nestle",      "true_price": 150.0, "sigma": 0.001,  "newspos": 5, "decay_const": 0.01},
    {"name": "HDFC",        "true_price": 400.0, "sigma": 0.001,  "newspos": 6, "decay_const": 0.01},
    {"name": "SBI",         "true_price": 250.0, "sigma": 0.0011, "newspos": 7, "decay_const": 0.01},
]
