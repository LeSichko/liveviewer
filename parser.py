from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from models import MatchRow
from utils import sg, parse_rfc3339


def _row_sort_dt(r: MatchRow) -> datetime:
    dt = parse_rfc3339(getattr(r, "end_time", "") or "") or parse_rfc3339(getattr(r, "start_time", "") or "")
    return dt or datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_schedule(schedule: Dict[str, Any]) -> Tuple[List[MatchRow], List[MatchRow], Optional[str]]:
    """
    Возвращает (active, finished, older_token).
    older_token — pageToken для загрузки более старых матчей, None если страниц больше нет.
    """
    events = sg(schedule, "data.schedule.events", []) or []
    active: List[MatchRow] = []
    finished: List[MatchRow] = []

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=20)

    for ev in events:
        if ev.get("type") != "match":
            continue

        state = ev.get("state") or ""
        match = ev.get("match") or {}
        teams = match.get("teams") or []

        t1 = sg(teams[0], "code", sg(teams[0], "name", "Team1")) if len(teams) > 0 else "Team1"
        t2 = sg(teams[1], "code", sg(teams[1], "name", "Team2")) if len(teams) > 1 else "Team2"
        s1 = sg(teams[0], "result.gameWins", None) if len(teams) > 0 else None
        s2 = sg(teams[1], "result.gameWins", None) if len(teams) > 1 else None

        start_ts = ev.get("startTime") or ""
        start_dt = parse_rfc3339(start_ts)

        row = MatchRow(
            league=sg(ev, "league.name", "Unknown"),
            state=state,
            start_time=start_ts,
            end_time=ev.get("endTime") or "",
            block_name=ev.get("blockName") or "",
            team1=t1,
            team2=t2,
            event_id=str(ev.get("id") or ""),
            match_id=str(match.get("id") or ""),
            score1=s1,
            score2=s2,
        )

        is_completed = (state == "completed")
        is_old = (start_dt is not None and start_dt < cutoff)

        if is_completed or is_old:
            finished.append(row)
        else:
            active.append(row)

    finished.sort(key=_row_sort_dt, reverse=True)

    older_token = sg(schedule, "data.schedule.pages.older", None)
    return active, finished, older_token


def pick_game_id(event_details: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    games = sg(event_details, "data.event.match.games", []) or []
    ids: List[str] = []
    inprog: Optional[str] = None
    last_completed: Optional[str] = None

    for g in games:
        if not isinstance(g, dict):
            continue
        gid = g.get("id")
        if gid is None:
            continue
        gid = str(gid)
        ids.append(gid)

        st = (g.get("state") or "").lower()
        if st == "inprogress":
            inprog = gid
        if st == "completed":
            last_completed = gid

    if inprog:
        return inprog, ids
    if last_completed:
        return last_completed, ids
    if ids:
        return ids[-1], ids
    return None, []
