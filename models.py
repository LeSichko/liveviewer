from dataclasses import dataclass
from typing import Optional


@dataclass
class MatchRow:
    league: str
    state: str          # inProgress / unstarted / completed / ...
    start_time: str
    block_name: str
    team1: str
    team2: str
    event_id: str       # schedule event id (not used for details)
    match_id: str       # scheduleEvent.match.id  (used for getEventDetails)
    score1: Optional[int] = None
    score2: Optional[int] = None
    end_time: str = ""
