"""
kda_tracker.py — чтение и запись статистики киллов в CSV.

Структура CSV:
    date, tournament, game_num, team, player, role, champion, kills, match_id, game_id

    - строка с player="" — суммарные киллы команды
    - строки с player=<n> — киллы каждого игрока
"""

import csv
from pathlib import Path
from typing import Any, Dict, List, Tuple

CSV_PATH = Path(__file__).parent / "kda_stats.csv"

FIELDNAMES = ["date", "tournament", "game_num", "team", "player", "role", "champion", "kills", "match_id", "game_id"]

ROLES = ["TOP", "JGL", "MID", "BOT", "SUP"]  # порядок по participantId внутри команды


class KdaRow:
    __slots__ = ("match_id", "game_id", "game_num", "team", "player", "role", "champion", "kills", "tournament", "date")

    def __init__(self, match_id, game_id, game_num, team, player, role, champion, kills, tournament="", date=""):
        self.match_id   = match_id
        self.game_id    = game_id
        self.game_num   = game_num
        self.team       = team
        self.player     = player
        self.role       = role
        self.champion   = champion
        self.kills      = kills
        self.tournament = tournament
        self.date       = date

    def to_dict(self):
        return {f: getattr(self, f) for f in FIELDNAMES}


def load_rows() -> List[KdaRow]:
    if not CSV_PATH.exists():
        return []
    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append(KdaRow(
                    match_id   = r["match_id"],
                    game_id    = r["game_id"],
                    game_num   = int(r["game_num"]),
                    team       = r["team"],
                    player     = r["player"],
                    role       = r.get("role", ""),
                    champion   = r.get("champion", ""),
                    kills      = int(r["kills"]),
                    tournament = r.get("tournament", ""),
                    date       = r.get("date", ""),
                ))
            except Exception:
                continue
    return rows


def save_rows(new_rows: List[KdaRow]) -> int:
    existing = load_rows()
    new_gids = {r.game_id for r in new_rows}
    filtered = [r for r in existing if r.game_id not in new_gids]
    combined = filtered + new_rows

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in combined:
            w.writerow(r.to_dict())

    return len(combined)


def extract_kda_rows(
    match_id:         str,
    all_game_ids:     List[str],
    finished_windows: Dict[str, Any],
    finished_details: Dict[str, Any],
    team_id_to_name:  Dict[str, str],
    tournament:       str = "",
) -> Tuple[List[KdaRow], List[str]]:
    rows:     List[KdaRow] = []
    warnings: List[str]   = []

    for game_num, game_id in enumerate(all_game_ids, 1):
        window  = finished_windows.get(game_id)
        details = finished_details.get(game_id)

        if not details or not (details.get("frames") or []):
            warnings.append(f"Game {game_num} ({game_id}): нет details")
            continue
        if not window or not (window.get("frames") or []):
            warnings.append(f"Game {game_num} ({game_id}): нет window")
            continue

        gm        = window.get("gameMetadata") or {}
        name_map:  Dict[int, str] = {}
        side_map:  Dict[int, str] = {}
        champ_map: Dict[int, str] = {}

        # Собираем участников по сторонам, сохраняем порядок для ролей
        blue_pids: List[int] = []
        red_pids:  List[int] = []

        for side_key in ("blueTeamMetadata", "redTeamMetadata"):
            team_meta = gm.get(side_key) or {}
            tid = str(team_meta.get("esportsTeamId") or "")
            side_participants = sorted(
                team_meta.get("participantMetadata") or [],
                key=lambda p: p.get("participantId", 0)
            )
            for p in side_participants:
                pid = p.get("participantId")
                if pid is None:
                    continue
                name_map[int(pid)]  = p.get("summonerName") or f"PID_{pid}"
                side_map[int(pid)]  = tid
                champ_map[int(pid)] = str(p.get("championId") or "")
                if side_key == "blueTeamMetadata":
                    blue_pids.append(int(pid))
                else:
                    red_pids.append(int(pid))

        # role_map: participantId -> роль по порядку в команде
        role_map: Dict[int, str] = {}
        for i, pid in enumerate(blue_pids):
            role_map[pid] = ROLES[i] if i < len(ROLES) else ""
        for i, pid in enumerate(red_pids):
            role_map[pid] = ROLES[i] if i < len(ROLES) else ""

        last         = details["frames"][-1]
        participants = last.get("participants") or []

        # дата из timestamp фрейма
        raw_ts = last.get("rfc460Timestamp") or ""
        if raw_ts.endswith("Z"):
            raw_ts = raw_ts[:-1] + "+00:00"
        try:
            from datetime import datetime, timezone
            game_date = datetime.fromisoformat(raw_ts).astimezone(timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            game_date = ""

        team_kills:  Dict[str, int] = {}
        player_rows: List[KdaRow]   = []

        for p in sorted(participants, key=lambda x: x.get("participantId", 0)):
            pid       = int(p.get("participantId", 0))
            kills     = int(p.get("kills", 0) or 0)
            tid       = side_map.get(pid, "unknown")
            name      = name_map.get(pid, f"PID_{pid}")
            team_name = team_id_to_name.get(tid, tid)
            champ     = champ_map.get(pid, "")
            role      = role_map.get(pid, "")

            team_kills[team_name] = team_kills.get(team_name, 0) + kills
            player_rows.append(KdaRow(
                match_id, game_id, game_num, team_name, name, role, champ, kills,
                tournament=tournament, date=game_date
            ))

        for team_name, total_kills in team_kills.items():
            rows.append(KdaRow(
                match_id, game_id, game_num, team_name, "", "", "", total_kills,
                tournament=tournament, date=game_date
            ))
            rows.extend(r for r in player_rows if r.team == team_name)

    return rows, warnings
