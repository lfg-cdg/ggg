"""All tunable settings of the model in one place."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # how many recent games form the "current form" of a team
    last_n_games: int = 10
    # weight of home/away split vs. overall form (0 = ignore split, 1 = only split)
    home_away_weight: float = 0.5
    # share of a missing player's points that the team really loses (the rest is scored
    # by replacements). Backtest 2025-26: 0.1-0.2 gives the best MAE, 0.45 makes it worse.
    injury_loss_factor: float = 0.2
    # probability that a player with this status misses the game
    # (ESPN mostly uses "Day-To-Day" where other sites say "Questionable")
    injury_miss_prob: tuple = (("Out", 1.0), ("Doubtful", 0.75), ("Questionable", 0.4), ("Day-To-Day", 0.3))
    # points removed from a team's expectation when it played yesterday
    back_to_back_penalty: float = 1.5
    # minimum |prediction - line| to recommend a bet
    edge_threshold: float = 5.0
    # where API responses are cached
    cache_dir: str = "cache"
    # cache lifetime for data that changes (scoreboard, injuries), seconds
    volatile_ttl: int = 30 * 60


SETTINGS = Settings()
