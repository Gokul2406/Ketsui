"""
config.py — Edit this to configure your event.
"""
import math

# ─────────────────────────────────────────────
# Admin & Auth
# ─────────────────────────────────────────────
ADMIN_KEY     = "admin-super-secret-key-change-this"
STARTING_CASH = 40000.0

TEAMS = {
    "team_alpha":   {"name": "Team Alpha",   "api_key": "key-alpha-1234"},
    "team_beta":    {"name": "Team Beta",    "api_key": "key-beta-5678"},
    "team_gamma":   {"name": "Team Gamma",   "api_key": "key-gamma-9012"},
    "team_delta":   {"name": "Team Delta",   "api_key": "key-delta-3456"},
    "team_epsilon": {"name": "Team Epsilon", "api_key": "key-epsilon-7890"},
}

# ─────────────────────────────────────────────
# Simulation constants (match simulation code exactly)
# ─────────────────────────────────────────────
TOTAL_FRAMES           = 4784
DT                     = 0.375    # minutes per frame
K_DECAY                = 0.01     # used in take_beta_give_wideal
DELTA_T_NEWS           = 202      # frames between events (beta calibration)
KMU                    = 0.00158  # mu frequency constant
NEWS_FRAME_SPACING     = 208      # actual frame offset per news row (208*i)
NEWS_LAG_FRAMES        = 6        # lag before J_map kicks in per event

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
    return (0.001 *
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
    {"name": "Dr. Reddy's", "true_price": 50.0,  "sigma": 0.008, "newspos": 2, "decay_const": 0.01},
    {"name": "Pfizer",      "true_price": 200.0, "sigma": 0.012, "newspos": 3, "decay_const": 0.01},
    {"name": "Coromandel",  "true_price": 40.0,  "sigma": 0.016, "newspos": 4, "decay_const": 0.01},
    {"name": "Nestle",      "true_price": 150.0, "sigma": 0.008, "newspos": 5, "decay_const": 0.01},
    {"name": "HDFC",        "true_price": 400.0, "sigma": 0.008, "newspos": 6, "decay_const": 0.01},
    {"name": "SBI",         "true_price": 250.0, "sigma": 0.008, "newspos": 7, "decay_const": 0.01},
]
