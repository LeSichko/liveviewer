import os
import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote
import requests
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, timezone, timedelta
from tkinter import font as tkfont
import time
from zoneinfo import ZoneInfo
from pathlib import Path
PERSISTED_BASE = "https://esports-api.lolesports.com/persisted/gw"
FEED_BASE = "https://feed.lolesports.com/livestats/v1"

REQ_TIMEOUT = 10
DEFAULT_HL = "en-US"


# -----------------------
# Andy-like time helper
# -----------------------

def iso_date_multiply_of_10(dt: datetime | None = None) -> str:
    # как у Энди: "сейчас", округлённое вниз к 10 секундам
    if dt is None:
        dt = datetime.now(timezone.utc)
    dt = dt.replace(microsecond=0)
    floored = dt.replace(second=(dt.second // 10) * 10)
    return floored.strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"



def sanitize_starting_time(ts: str) -> str:
    # на случай если в ts уже есть %3A или прочее
    ts = unquote(ts)
    # иногда у людей попадает "T18%3A33%3A41%3A20.000Z" -> после unquote станет "T18:33:41:20.000Z"
    # это невалидно, поэтому если после 'T' времени больше одного ":" в секции HH:MM:SS — режем до HH:MM:SS
    # ожидаем формат ...T{HH}:{MM}:{SS}.mmmZ
    try:
        head, tail = ts.split("T", 1)
        time_part = tail
        # time_part например "18:33:41:20.000Z"
        hh = time_part[0:2]
        mm = time_part[3:5]
        ss = time_part[6:8]
        return f"{head}T{hh}:{mm}:{ss}.000Z"
    except Exception:
        return ts


# -----------------------
# Minimal safe getter
# -----------------------
def sg(d: Any, path: str, default=None):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def pretty_utc(ts: Optional[str]) -> str:
    if not ts:
        return "—"
    try:
        # rfc3339 Z -> isoformat with +00:00
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts

def parse_rfc3339(ts: str) -> datetime | None:
    """
    "2026-01-25T11:00:00Z" / "...000Z" / "...+00:00" -> aware datetime in UTC
    """
    if not ts:
        return None
    s = ts.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def pretty_local(ts: str | None) -> str:
    if not ts:
        return "—"
    dt = parse_rfc3339(ts)
    if not dt:
        return ts
    local_tz = datetime.now().astimezone().tzinfo
    return dt.astimezone(local_tz).strftime("%d-%m-%Y %H:%M:%S")


def http_get_json(url: str, headers: Dict[str, str], params: Optional[Dict[str, str]] = None) -> Tuple[int, Optional[Dict[str, Any]]]:
    r = requests.get(url, headers=headers, params=params, timeout=REQ_TIMEOUT)
    print("HTTP URL:", r.url) 
    if r.status_code == 204:
        return 204, None
    r.raise_for_status()
    return r.status_code, r.json()

DRAGON_ABBR = {
    "infernal": "INF",
    "cloud":    "CLD",
    "mountain": "MTN",
    "ocean":    "OCN",
    "hextech":  "HEX",
    "chemtech": "CHM",
    "elder":    "ELD",
}

def fmt_dragons(team: dict) -> str:
    d = (team or {}).get("dragons")
    if not d:
        return "—"

    # Самый частый и нужный случай: список типов по порядку взятия
    if isinstance(d, list):
        return " ".join(DRAGON_ABBR.get(str(x).lower(), str(x)) for x in d) if d else "—"

    # Иногда бывает dict счётчиков (без порядка) — показываем как k:v
    if isinstance(d, dict):
        items = [f"{DRAGON_ABBR.get(k.lower(), k)}:{v}" for k, v in d.items()]
        return " ".join(items) if items else "—"

    return str(d)


# -----------------------
# API Client (exact endpoints Andy uses)
# -----------------------
class LolEsportsClient:
    def __init__(self, api_key: str, hl: str = DEFAULT_HL):
        self.api_key = api_key.strip()
        self.hl = hl.strip() or DEFAULT_HL

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "accept": "application/json",
            "user-agent": "lol-live-viewer/10",
        }

    @property
    def feed_headers(self) -> Dict[str, str]:
        return {
            "accept": "application/json",
            "user-agent": "lol-live-viewer/10",
        }

    def get_schedule(self) -> Dict[str, Any]:
        url = f"{PERSISTED_BASE}/getSchedule"
        _, data = http_get_json(url, self.headers, params={"hl": self.hl})
        return data or {}

    def get_event_details(self, match_id: str) -> Dict[str, Any]:
        # IMPORTANT: Andy passes scheduleEvent.match.id here
        url = f"{PERSISTED_BASE}/getEventDetails"
        _, data = http_get_json(url, self.headers, params={"hl": self.hl, "id": str(match_id)})
        return data or {}



    def _minus_seconds_rfc3339(self, ts: str, seconds: int) -> str:
        # ts like 2026-01-23T19:08:40.000Z
        base = ts.replace(".000Z", "").replace("Z", "")
        dt = datetime.fromisoformat(base).replace(tzinfo=timezone.utc)
        dt2 = dt - timedelta(seconds=seconds)
        return dt2.strftime("%Y-%m-%dT%H:%M:%S.000Z")


    def get_window(self, game_id: str, starting_time: str | None = None):
        url = f"{FEED_BASE}/window/{game_id}"
        params = {"startingTime": starting_time} if starting_time else None
        return http_get_json(url, self.feed_headers, params=params)





    def get_details(self, game_id: str, starting_time: str | None = None):
        url = f"{FEED_BASE}/details/{game_id}"
        params = {"startingTime": starting_time} if starting_time else None
        return http_get_json(url, self.feed_headers, params=params)





# -----------------------
# Rows from getSchedule (Andy-like IDs)
# -----------------------
@dataclass
class MatchRow:
    league: str
    state: str          # inProgress / unstarted / completed / ...
    start_time: str
    block_name: str
    team1: str
    team2: str
    event_id: str       # schedule event id (not used for details)
    match_id: str       # scheduleEvent.match.id  (THIS is what we use for getEventDetails)
    score1: Optional[int] = None
    score2: Optional[int] = None
    end_time: str = ""

def _row_sort_dt(r) -> datetime:
    # end_time приоритетнее, затем start_time, иначе в самый низ
    dt = parse_rfc3339(getattr(r, "end_time", "") or "") or parse_rfc3339(getattr(r, "start_time", "") or "")
    return dt or datetime(1970, 1, 1, tzinfo=timezone.utc)

def parse_schedule(schedule: Dict[str, Any]) -> Tuple[List[MatchRow], List[MatchRow]]:
    events = sg(schedule, "data.schedule.events", []) or []
    active: List[MatchRow] = []
    finished: List[MatchRow] = []

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=20)  # <= вот твой "разлет"

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

        # 1) нормальная логика по state
        is_completed = (state == "completed")

        # 2) форс-финиш по времени (старше 20 часов)
        is_old = (start_dt is not None and start_dt < cutoff)

        if is_completed or is_old:
            finished.append(row)
        else:
            active.append(row)
            finished.sort(key=_row_sort_dt, reverse=True)  # новые сверху

    return active, finished



def pick_game_id(event_details: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    """
    Andy logic: use current "game index" effectively. For us:
    - if any game state == inProgress -> pick that game id
    - else pick last game that is completed
    - else pick last game id
    """
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


# -----------------------
# UI
# -----------------------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("LoL Live Viewer")
        self.root.geometry("1200x720")
        self.anchor_offset_sec = tk.IntVar(value=30)   # по умолчанию -30 секунд
        self.manual_game_id: str | None = None     # если None -> Auto
        self.game_choice_var = tk.StringVar(value="Auto")

        self.client: Optional[LolEsportsClient] = None
        self._last_ed_refresh = 0.0
        self._last_games_poll = 0.0
        self.games_poll_every_sec = 12.0  # как часто обновлять кэш по всем картам

        self.active_rows: List[MatchRow] = []
        self.finished_rows: List[MatchRow] = []
        self.selected: Optional[MatchRow] = None
        self.kda_window = None
        self.kda_text = None
        #self.kda_font_size = 12
        self.event_details: Optional[Dict[str, Any]] = None
        self.all_game_ids: List[str] = []
        self.current_game_id: Optional[str] = None

        self.window_data: Optional[Dict[str, Any]] = None
        self.details_data: Optional[Dict[str, Any]] = None
        self.flip_sides_var = tk.BooleanVar(value=False)

        # finished per-game caches (one-shot)
        self.finished_windows: Dict[str, Dict[str, Any]] = {}
        self.finished_details: Dict[str, Dict[str, Any]] = {}

        self.polling = False

        self.api_key_var = tk.StringVar(value=os.environ.get("LOLESPORTS_API_KEY", ""))
        self.hl_var = tk.StringVar(value=DEFAULT_HL)
        self.use_details_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self._start_poll_loop()
        self.ui_font = tkfont.Font(family="Consolas", size=12)
        self.txt.config(font=self.ui_font)
        #self.details_text.config(font=self.ui_font)
        self.txt.bind("<MouseWheel>", self._on_mousewheel)
        #self.details_text.bind("<MouseWheel>", self._on_mousewheel)

    def _zoom(self, delta: int):
            size = self.ui_font.cget("size")
            self.ui_font.configure(size=max(8, min(28, size + delta)))

    def _on_mousewheel(self, event):
            # Windows: event.delta = 120/-120
            if event.state & 0x0004:  # Ctrl pressed
                self._zoom(1 if event.delta > 0 else -1)
                return "break"

    def _bump_anchor(self, delta: int):
        try:
            v = int(self.anchor_offset_sec.get())
        except Exception:
            v = 30
        v = max(0, v + delta)   # не даём уйти в минус
        self.anchor_offset_sec.set(v)

        # Чтобы эффект был сразу — пульнём данные
        if self.client is not None and self.current_game_id:
            self._run_bg(self._poll_bg)


    def _build_ui(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Api Key:").pack(side=tk.LEFT)

        self.api_key_var = ttk.Entry(top, width=44)
        self.api_key_var.pack(side=tk.LEFT, padx=4)

        self.api_key_var.insert(0, "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z")
   

        ttk.Label(top, text="Game:").pack(side=tk.LEFT, padx=(0, 2))

        self.cb_game = ttk.Combobox(
            top,
            textvariable=self.game_choice_var,
            width=7,
            state="readonly",
            values=["Auto"],
        )
        self.cb_game.pack(side=tk.LEFT, padx=2)

        self.cb_game.bind("<<ComboboxSelected>>", self.on_game_selected)
        ttk.Button(top, text="Show KDA", command=self.show_kda_window).pack(side=tk.LEFT, padx=4)
       

        ttk.Checkbutton(
            top,
            text="Flip sides (RED on top)",
            variable=self.flip_sides_var,
            command=self.render
        ).pack(side=tk.LEFT, padx=0)
        ttk.Button(top, text="Refresh schedule", command=self.refresh_schedule).pack(side=tk.LEFT, padx=2)
        ttk.Label(top, text="Anchor (-sec):").pack(side=tk.LEFT, padx=(2, 2))

        self.anchor_entry = ttk.Entry(top, width=5, textvariable=self.anchor_offset_sec, justify="center")
        self.anchor_entry.pack(side=tk.LEFT)

        ttk.Button(top, text="-10", width=4, command=lambda: self._bump_anchor(-10)).pack(side=tk.LEFT, padx=(4, 2))
        ttk.Button(top, text="+10", width=4, command=lambda: self._bump_anchor(+10)).pack(side=tk.LEFT, padx=(2, 2))

        #ttk.Checkbutton(top, text="Use details", variable=self.use_details_var).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Save JSON", command=self.save_json).pack(side=tk.LEFT, padx=0)

        ttk.Label(self.root, textvariable=self.status_var, foreground="gray", padding=(3, 2)).pack(fill=tk.X)
        #ttk.Label(top, text="hl:").pack(side=tk.LEFT)
        #ttk.Entry(top, textvariable=self.hl_var, width=7).pack(side=tk.LEFT, padx=2)
        main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(main, padding=6)
        right = ttk.Frame(main, padding=6)
        main.add(left, weight=1)
        main.add(right, weight=3)

        nb = ttk.Notebook(left)
        nb.pack(fill=tk.BOTH, expand=True)

        tab_active = ttk.Frame(nb)
        tab_finished = ttk.Frame(nb)
        nb.add(tab_active, text="Live + Upcoming")
        nb.add(tab_finished, text="Finished")


        # active list
        f1 = ttk.Frame(tab_active)
        f1.pack(fill=tk.BOTH, expand=True)
        self.lb_active = tk.Listbox(f1, height=26)
        self.lb_active.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb1 = ttk.Scrollbar(f1, orient=tk.VERTICAL, command=self.lb_active.yview)
        sb1.pack(side=tk.RIGHT, fill=tk.Y)
        self.lb_active.config(yscrollcommand=sb1.set)
        self.lb_active.bind("<<ListboxSelect>>", self.on_select_match)


        # finished list
        f2 = ttk.Frame(tab_finished)
        f2.pack(fill=tk.BOTH, expand=True)
        self.lb_finished = tk.Listbox(f2, height=26)
        self.lb_finished.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb2 = ttk.Scrollbar(f2, orient=tk.VERTICAL, command=self.lb_finished.yview)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self.lb_finished.config(yscrollcommand=sb2.set)
        self.lb_finished.bind("<<ListboxSelect>>", self.on_select_match)

        # right details
        ttk.Label(right, text="Details:").pack(anchor="w")
        self.txt = tk.Text(right, wrap="word")
        self.txt.pack(fill=tk.BOTH, expand=True)

    def _make_client(self) -> Optional[LolEsportsClient]:
        key = self.api_key_var.get().strip()
        if not key:
            messagebox.showerror("Missing X-Api-Key", "Paste X-Api-Key from lolesports.com network requests.")
            return None
        hl = self.hl_var.get().strip() or DEFAULT_HL
        return LolEsportsClient(key, hl)

    def _refresh_game_selector(self):
        """
        Заполняет combobox из self.all_game_ids.
        Формат: Auto, Game 1: <gid>, Game 2: <gid> ...
        """
        values = ["Auto"]
        for i, gid in enumerate(self.all_game_ids or [], 1):
            values.append(f"Game {i}: {gid}")

        self.cb_game["values"] = values

        # если мы в ручном режиме — держим выбранный gid
        if self.manual_game_id:
            want = None
            for v in values:
                if v.endswith(str(self.manual_game_id)):
                    want = v
                    break
            self.game_choice_var.set(want or "Auto")
            if want is None:
                self.manual_game_id = None
        else:
            # Auto
            self.game_choice_var.set("Auto")


    def on_game_selected(self, _evt=None):
        v = (self.game_choice_var.get() or "").strip()
        if v == "Auto" or not v:
            self.manual_game_id = None
            # вернёмся к авто-выбору на основе текущих eventDetails
            try:
                gid, _all = pick_game_id(self.event_details or {})
                if gid:
                    self.current_game_id = gid
            except Exception:
                pass
            self.status_var.set("Game: Auto")
            return

        # ожидаем "Game N: <gid>"
        try:
            gid = v.split(":", 1)[1].strip()
        except Exception:
            gid = None

        if gid:
            self.manual_game_id = gid
            self.current_game_id = gid
            self.status_var.set(f"Game locked: {gid}")
            # можно сразу перерендерить, даже до следующего poll
            self._ui(self.render)



    def _team_id_to_name(self) -> dict[str, str]:
        """
        teamId -> code/name из eventDetails.match.teams
        """
        ed = getattr(self, "event_details", None) or {}
        teams = sg(ed, "data.event.match.teams", []) or []
        m: dict[str, str] = {}
        for t in teams:
            tid = t.get("id")
            if tid is None:
                continue
            name = t.get("code") or t.get("name") or str(tid)
            m[str(tid)] = name
        return m


    def _blue_red_names_for_game(self, game_id: str | None, window_payload: dict | None = None) -> tuple[str, str]:
        """
        (blueName, redName) для конкретной карты.

        Приоритет для отображения:
        1) window_payload.gameMetadata.{blue,red}TeamMetadata.esportsTeamId  (самое надёжное)
        2) eventDetails.match.games[].teams[].side + id (fallback)
        """
        if not game_id:
            return ("BLUE", "RED")

        id2name = self._team_id_to_name()

        # 1) primary: window.gameMetadata (это чинит LPL-аномалии)
        if window_payload:
            gm = window_payload.get("gameMetadata") or {}
            bmeta = gm.get("blueTeamMetadata") or {}
            rmeta = gm.get("redTeamMetadata") or {}
            bid = bmeta.get("esportsTeamId")
            rid = rmeta.get("esportsTeamId")
            
            if bid is not None and rid is not None:
                blue_name = id2name.get(str(bid)) or "BLUE"
                red_name  = id2name.get(str(rid)) or "RED"
                return (blue_name, red_name)

        # 2) fallback: eventDetails.match.games[].teams[].side
        ed = getattr(self, "event_details", None) or {}
        games = sg(ed, "data.event.match.games", []) or []

        gid = str(game_id)
        for g in games:
            if str(g.get("id")) != gid:
                continue

            blue_id = None
            red_id = None
            for t in (g.get("teams") or []):
                side = (t.get("side") or "").lower()
                tid = t.get("id")
                if tid is None:
                    continue
                if side == "blue":
                    blue_id = str(tid)
                elif side == "red":
                    red_id = str(tid)

            blue_name = id2name.get(blue_id) or "BLUE"
            red_name  = id2name.get(red_id)  or "RED"
            return (blue_name, red_name)

        return ("BLUE", "RED")




    def _team_id_to_code_map(self) -> dict[str, str]:
        # Берём актуальные eventDetails из любого из твоих полей
        ed = getattr(self, "event_details", None) or getattr(self, "current_event_details", None) or {}
        match = (((ed.get("data") or {}).get("event") or {}).get("match") or {})
        teams = match.get("teams") or []
        m: dict[str, str] = {}
        for t in teams:
            tid = t.get("id")
            if tid is None:
                continue
            name = t.get("code") or t.get("name") or str(tid)
            m[str(tid)] = name
        return m




    # -----------------------
    # Schedule
    # -----------------------
    def refresh_schedule(self):
        self.client = self._make_client()
        if not self.client:
            return
        self.status_var.set("Loading schedule...")
        self._run_bg(self._refresh_schedule_bg)

    def _refresh_schedule_bg(self):
        try:
            assert self.client is not None
            sched = self.client.get_schedule()
            active, finished = parse_schedule(sched)
            self._ui(lambda: self._set_lists(active, finished))
            self._ui(lambda: self.status_var.set(f"Schedule loaded. Active={len(active)} Finished={len(finished)}"))
        except Exception as e:
            msg = f"Schedule error: {e}"
            self._ui(lambda m=msg: self.status_var.set(m))

    def _set_lists(self, active: List[MatchRow], finished: List[MatchRow]):
        self.active_rows = active
        self.finished_rows = finished

        self.lb_active.delete(0, tk.END)
        for r in active:
            local = pretty_local(r.start_time)
            self.lb_active.insert(tk.END, f"{local} | [{r.league}] {r.team1} vs {r.team2} | {r.state}")

        self.lb_finished.delete(0, tk.END)
        for r in finished:
            s1 = r.score1 if r.score1 is not None else "?"
            s2 = r.score2 if r.score2 is not None else "?"
            local = pretty_local(r.end_time or r.start_time)
            self.lb_finished.insert(tk.END, f"{local} | [{r.league}] {r.team1} {s1}-{s2} {r.team2}")

    # -----------------------
    # Select match
    # -----------------------

    def on_select_match(self, evt=None):
        lb = evt.widget if evt else None

        if lb is self.lb_finished:
            rows = self.finished_rows
        else:
            rows = self.active_rows  # или active_rows, см. ниже

        sel = lb.curselection() if lb else ()
        if not sel:
            return
        idx = sel[0]
        if idx < 0 or idx >= len(rows):
            return

        self.selected = rows[idx]
        self.client = self._make_client()
        if not self.client:
            return

        self.status_var.set("Loading match...")
        self._run_bg(self._load_match_bg)

    def _participant_name_map(self, game_id: str) -> dict[int, str]:
        """
        Возвращает:
            participantId -> summonerName
        """
        ed = getattr(self, "event_details", None) or {}
        games = sg(ed, "data.event.match.games", []) or []

        gid = str(game_id)

        for g in games:
            if str(g.get("id")) != gid:
                continue

            meta = g.get("gameMetadata") or {}
            result = {}

            for side_key in ("blueTeamMetadata", "redTeamMetadata"):
                team_meta = meta.get(side_key) or {}
                for p in (team_meta.get("participantMetadata") or []):
                    pid = p.get("participantId")
                    name = p.get("summonerName")
                    if pid is not None:
                        result[int(pid)] = name or "?"

            return result

        return {}

    def show_kda_window(self):
        if self.kda_window and tk.Toplevel.winfo_exists(self.kda_window):
            self._render_kda()
            self.kda_window.lift()
            return

        self.kda_window = tk.Toplevel(self.root)
        self.kda_window.title("KDA")
        self.kda_window.geometry("500x420")

        self.kda_text = tk.Text(self.kda_window, wrap="none")
        self.kda_text.pack(fill="both", expand=True)

        # Ctrl + колесо
        self.kda_text.bind("<MouseWheel>", self._on_mousewheel)

        self._render_kda()

    def _render_kda(self):
        if not self.kda_window or not self.kda_window.winfo_exists():
            return

        # данные ещё не загрузились
        if not isinstance(self.details_data, dict):
            return

        if not self.kda_window:
            return

        if not tk.Toplevel.winfo_exists(self.kda_window):
            return

        if not self.kda_text:
            return

        if not self.kda_text.winfo_exists():
            return

        frames = self.details_data.get("frames") or []
        if not frames:
            return

        last = frames[-1]
        participants = last.get("participants") or []
        if not participants:
            return

        # берём имена из window_data
        window = self.window_data or {}
        gm = window.get("gameMetadata") or {}

        name_map = {}
        for side_key in ("blueTeamMetadata", "redTeamMetadata"):
            team_meta = gm.get(side_key) or {}
            for p in team_meta.get("participantMetadata") or []:
                pid = p.get("participantId")
                name = p.get("summonerName")
                if pid is not None:
                    name_map[int(pid)] = name

        blue_name, red_name = self._blue_red_names_for_game(self.current_game_id, self.window_data)

        self.kda_text.config(state="normal")
        self.kda_text.delete("1.0", tk.END)

        self.kda_text.insert(tk.END, f"{blue_name} vs {red_name}\n\n")

        blue_lines = []
        red_lines = []

        for p in sorted(participants, key=lambda x: x.get("participantId", 0)):
            pid = p.get("participantId")
            kills = p.get("kills", 0)
            deaths = p.get("deaths", 0)
            assists = p.get("assists", 0)

            name = name_map.get(pid, f"PID_{pid}")
            line = f"{name:18} {kills}/{deaths}/{assists}"

            if pid <= 5:
                blue_lines.append(line)
            else:
                red_lines.append(line)

        if self.flip_sides_var.get():
            self.kda_text.insert(tk.END, f"=== {red_name} (RED) K/D/A ===\n")
            for l in red_lines:
                self.kda_text.insert(tk.END, l + "\n")
            self.kda_text.insert(tk.END, "\n")
            self.kda_text.insert(tk.END, f"=== {blue_name} (BLUE) K/D/A ===\n")
            for l in blue_lines:
                self.kda_text.insert(tk.END, l + "\n")
        else:
            self.kda_text.insert(tk.END, f"=== {blue_name} (BLUE) K/D/A ===\n")
            for l in blue_lines:
                self.kda_text.insert(tk.END, l + "\n")

            self.kda_text.insert(tk.END, "\n")

            self.kda_text.insert(tk.END, f"=== {red_name} (RED) K/D/A ===\n")
            for l in red_lines:
                self.kda_text.insert(tk.END, l + "\n")

        self.kda_text.config(font=self.ui_font)

        self.kda_text.config(state="disabled")

    def _load_match_bg(self):
        try:
            assert self.client is not None
            assert self.selected is not None

            # ANDY: getEventDetails(id = scheduleEvent.match.id)
            ed = self.client.get_event_details(self.selected.match_id)
            game_id, all_ids = pick_game_id(ed)

            self.event_details = ed

            self.all_game_ids = all_ids

            # обновим селектор карт в UI
            self._ui(self._refresh_game_selector)

            # если юзер НЕ зафиксировал карту — работаем как раньше
            if not self.manual_game_id:
                self.current_game_id = game_id
            else:
                # ручной режим: оставляем выбранный gid, но если его нет в списке — упадём в Auto
                if self.manual_game_id in (self.all_game_ids or []):
                    self.current_game_id = self.manual_game_id
                else:
                    self.manual_game_id = None
                    self.current_game_id = game_id
                    self._ui(self._refresh_game_selector)


            self.current_game_id = game_id

            self.window_data = None
            self.details_data = None
            self.finished_windows = {}
            self.finished_details = {}

            if not game_id:
                self._ui(lambda: self.status_var.set("No games found in eventDetails.match.games"))
                self._ui(self.render)
                return

            # ANDY: first window call without startingTime
            w_status, w = self.client.get_window(game_id, starting_time=None)
            d_status, d = (204, None)
            if self.use_details_var.get():
                # ANDY: details also works with startingTime "now", but first call can be empty; keep it simple
                d_status, d = self.client.get_details(game_id, starting_time=None)

            self.window_data = w
            self.details_data = d

            # If finished match: fetch per-game one-shot with startingTime=nowRounded (Andy-like)
            if self.selected.state == "completed":
                anchor = iso_date_multiply_of_10()  # "2026-01-23T18:33:50.000Z" (с :)
                anchor = self.client._minus_seconds_rfc3339(anchor, 30)

                for gid in self.all_game_ids:
                    try:
                        st, ww = self.client.get_window(gid, starting_time=anchor)
                        if st == 200 and ww and (ww.get("frames") or []):
                            self.finished_windows[gid] = ww
                    except Exception:
                        pass
                    if self.use_details_var.get():
                        try:
                            st, dd = self.client.get_details(gid, starting_time=anchor)
                            if st == 200 and dd and (dd.get("frames") or []):
                                self.finished_details[gid] = dd
                        except Exception:
                            pass

            self._ui(lambda: self.status_var.set(
                f"Loaded. matchId={self.selected.match_id} gameId={game_id} (window {w_status}, details {d_status})"
            ))
            self._ui(self.render)

        except Exception as e:
            msg = f"Load error: {e}"
            self._ui(lambda m=msg: self.status_var.set(m))
            self._ui(lambda m=msg: messagebox.showerror("Load error", m))

    # -----------------------
    # Polling (Andy-like)
    # -----------------------
    def _start_poll_loop(self):
        if not self.polling:
            self.polling = True
            self.root.after(800, self._poll_tick)

    def _poll_tick(self):
        try:
            # poll only when we have an active (non-completed) match loaded
            if self.client and self.selected and self.current_game_id:
                self._run_bg(self._poll_bg)

        finally:
            self.root.after(800, self._poll_tick)

    def _poll_bg(self):
        try:
            assert self.client is not None
            gid = self.current_game_id
            if not gid:
                return

            anchor = iso_date_multiply_of_10()
            try:
                offset = int(self.anchor_offset_sec.get())
            except Exception:
                offset = 30
            anchor = self.client._minus_seconds_rfc3339(anchor, offset)

            # ----------------------------
            # 0) Refresh eventDetails раз в 120 секунд (как было)
            # ----------------------------
# раз в 120 сек обновим eventDetails и all_game_ids (и current_game_id ТОЛЬКО если Auto)
            now = time.time()
            if not hasattr(self, "_last_ed_refresh"):
                self._last_ed_refresh = 0.0

            if now - self._last_ed_refresh >= 410.0:
                self._last_ed_refresh = now
                try:
                    ed = self.client.get_event_details(self.selected.match_id)
                    self.event_details = ed
                    new_gid, all_ids = pick_game_id(ed)
                    self.all_game_ids = all_ids

                    # обновим селектор в UI
                    self._ui(self._refresh_game_selector)

                    if self.manual_game_id:
                        # ручной режим: держим выбранный gid (если он ещё существует)
                        if self.manual_game_id in (self.all_game_ids or []):
                            self.current_game_id = self.manual_game_id
                            gid = self.manual_game_id
                    else:
                        # Auto режим
                        if new_gid:
                            self.current_game_id = new_gid
                            gid = new_gid
                except Exception:
                    pass


            # ----------------------------
            # 1) latest (текущая карта)
            # ----------------------------
            w_status, w = self.client.get_window(gid, starting_time=anchor)
            if w_status == 200 and w and (w.get("frames") or []):
                self.window_data = w

            if self.use_details_var.get():
                d_status, d = self.client.get_details(gid, starting_time=anchor)
                if d_status == 200 and d and (d.get("frames") or []):
                    self.details_data = d

            # ----------------------------
            # 2) per-game cache (раз в 120 секунд, как ты хочешь)
            # ----------------------------
            if not hasattr(self, "_last_games_poll"):
                self._last_games_poll = 0.0

            games_every = getattr(self, "games_poll_every_sec", 420.0)
            if now - self._last_games_poll >= games_every:
                self._last_games_poll = now

                for g2 in (self.all_game_ids or []):
                    try:
                        st, ww = self.client.get_window(g2, starting_time=anchor)
                        if st == 200 and ww and (ww.get("frames") or []):
                            self.finished_windows[g2] = ww
                    except Exception:
                        pass

                    if self.use_details_var.get():
                        try:
                            st, dd = self.client.get_details(g2, starting_time=anchor)
                            if st == 200 and dd and (dd.get("frames") or []):
                                self.finished_details[g2] = dd
                        except Exception:
                            pass

            self._ui(self.render)
            self._ui(self._render_kda)
        except requests.HTTPError as e:
            msg = f"Poll HTTP error: {e}"
            self._ui(lambda m=msg: self.status_var.set(m))
        except Exception as e:
            msg = f"Poll error: {e}"
            self._ui(lambda m=msg: self.status_var.set(m))


    # -----------------------
    # Render
    # -----------------------
    def render(self):
        self.txt.config(state="normal")
        self.txt.delete("1.0", tk.END)

        if not self.selected:
            self.txt.insert(tk.END, "Select a match.\n")
            self.txt.config(state="disabled")
            return

        r = self.selected
        score = ""
        if r.score1 is not None and r.score2 is not None:
            score = f" {r.score1}-{r.score2}"

        self.txt.insert(tk.END, f"[{r.league}] {r.team1}{score} {r.team2}\n")
        self.txt.insert(tk.END, f"state: {r.state}\n")
        #self.txt.insert(tk.END, f"matchId   : {r.match_id}\n")
        #self.txt.insert(tk.END, f"eventId   : {r.event_id}\n")
        #self.txt.insert(tk.END, f"startTime : {pretty_utc(r.start_time)}\n\n")

        #self.txt.insert(tk.END, f"current gameId : {self.current_game_id or '—'}\n")
        #if self.all_game_ids:
            #self.txt.insert(tk.END, f"all gameIds    : {self.all_game_ids}\n")
        self.txt.insert(tk.END, "\n")

        # latest window snapshot
        self.txt.insert(tk.END, "=== WINDOW (latest) ===\n")
        self._render_window_latest()

        #if self.use_details_var.get():
            #self.txt.insert(tk.END, "\n=== DETAILS (latest) ===\n")
            #self._render_details_latest()

        if self.all_game_ids:
            self.txt.insert(tk.END, "\n=== PER GAME (window) ===\n")
            self._render_finished_window_per_game()
            #if self.use_details_var.get():
                #self.txt.insert(tk.END, "\n=== PER GAME (details) ===\n")
                #self._render_finished_details_per_game()


        self.txt.config(state="disabled")

    def _winner_name_from_window_last(self, last_frame: dict, blue_name: str, red_name: str) -> str:
        blue = last_frame.get("blueTeam") or {}
        red  = last_frame.get("redTeam") or {}

        # самый частый вариант в feed
        if blue.get("isWinner") is True:
            return blue_name
        if red.get("isWinner") is True:
            return red_name

        # запасной вариант (иногда поле иначе называется)
        if blue.get("winner") is True:
            return blue_name
        if red.get("winner") is True:
            return red_name

        return "—"


    def _render_window_latest(self):
        w = self.window_data or {}
        frames = w.get("frames") or []
        if not frames:
            self.txt.insert(tk.END, "No window frames.\n")
            return

        last = frames[-1]
        self.txt.insert(tk.END, f"timestamp : {pretty_utc(last.get('rfc460Timestamp'))}\n")
        self.txt.insert(tk.END, f"gameState : {last.get('gameState')}\n")

        gt = last.get("gameTime")
        if isinstance(gt, (int, float)):
            mm = int(gt) // 60
            ss = int(gt) % 60
            self.txt.insert(tk.END, f"gameTime  : {mm:02d}:{ss:02d}\n")

        blue_team = last.get("blueTeam") or {}
        red_team  = last.get("redTeam") or {}
        blue_name, red_name = self._blue_red_names_for_game(self.current_game_id, self.window_data)

        gold_blue = int(blue_team.get('totalGold',0) or 0)
        gold_red = int(red_team.get('totalGold',0) or 0)
        diff = gold_blue-gold_red 
        lead_name = blue_name if diff>0 else red_name if diff < 0 else "0"
        if self.flip_sides_var.get():
            diff = -diff
            lead_name = red_name if diff>0 else blue_name if diff < 0 else "0"
        #lead_name = blue_name if diff>0 else red_name if diff < 0 else "0"

        bd = fmt_dragons(blue_team)
        rd = fmt_dragons(red_team)
        line_blue = (
            
            f"{blue_name} (BLUE): K={blue_team.get('totalKills',0)} "
            f"G={blue_team.get('totalGold',0)} T={blue_team.get('towers',0)} "
            f"B={blue_team.get('barons',0)} I={blue_team.get('inhibitors',0)} D={bd}\n"
        )
        line_red = (
            
            f"{red_name} (RED) : K={red_team.get('totalKills',0)} "
            f"G={red_team.get('totalGold',0)} T={red_team.get('towers',0)} "
            f"B={red_team.get('barons',0)} I={red_team.get('inhibitors',0)} D={rd}\n"
        )
        if self.flip_sides_var.get():
            self.txt.insert(tk.END, line_red)
            self.txt.insert(tk.END, line_blue)
        else:
            self.txt.insert(tk.END, line_blue)
            self.txt.insert(tk.END, line_red)
        self.txt.insert(tk.END, f"Gold diff: {diff:+d} ({lead_name})\n")
        #self.txt.insert(tk.END, f"gameTime  : {mm:02d}:{ss:02d}\n")


    def _render_details_latest(self):
        d = self.details_data or {}
        frames = d.get("frames") or []
        if not frames:
            self.txt.insert(tk.END, "No details frames.\n")
            return
        last = frames[-1]
        self.txt.insert(tk.END, f"timestamp : {pretty_utc(last.get('rfc460Timestamp'))}\n")
        parts = last.get("participants") or []
        self.txt.insert(tk.END, f"participants : {len(parts)}\n")
        # show 5 sample lines
        shown = 0
        for p in parts:
            sn = p.get("summonerName")
            champ = p.get("championId")
            role = p.get("role")
            if sn or champ or role:
                self.txt.insert(tk.END, f" - {sn} | champ={champ} | role={role}\n")
                shown += 1
            if shown >= 5:
                break

    def _render_finished_window_per_game(self):
        if not self.all_game_ids:
            self.txt.insert(tk.END, "No game ids.\n")
            return

        for i, gid in enumerate(self.all_game_ids, 1):
            w = self.finished_windows.get(gid)
            if not w or not (w.get("frames") or []):
                self.txt.insert(tk.END, f"Game {i}: {gid} (no window)\n")
                continue

            last = w["frames"][-1]
            blue = last.get("blueTeam") or {}
            red  = last.get("redTeam") or {}

            blue_name, red_name = self._blue_red_names_for_game(gid, w)

            gold_blue = int(blue.get('totalGold',0) or 0)
            gold_red = int(red.get('totalGold',0) or 0)
            diff = gold_blue-gold_red 
            lead_name = blue_name if diff>0 else red_name if diff < 0 else "0"
            bd = fmt_dragons(blue)
            rd = fmt_dragons(red)
            self.txt.insert(tk.END, f"Game {i}: {gid} | {pretty_utc(last.get('rfc460Timestamp'))}\n")
            self.txt.insert(tk.END, f"  {blue_name} (BLUE): K={blue.get('totalKills',0)} G={blue.get('totalGold',0)} T={blue.get('towers',0)} B={blue.get('barons',0)} I={blue.get('inhibitors',0)} D={bd}\n")
            self.txt.insert(tk.END, f"  {red_name} (RED) : K={red.get('totalKills',0)} G={red.get('totalGold',0)} T={red.get('towers',0)} B={red.get('barons',0)} I={red.get('inhibitors',0)} D={rd}\n")
            self.txt.insert(tk.END, f"Gold diff: {diff:+d} ({lead_name})\n")



    def _render_finished_details_per_game(self):
        if not self.all_game_ids:
            self.txt.insert(tk.END, "No game ids.\n")
            return

        for i, gid in enumerate(self.all_game_ids, 1):
            d = self.finished_details.get(gid)
            if not d or not (d.get("frames") or []):
                self.txt.insert(tk.END, f"Game {i}: {gid} (no details)\n")
                continue

            last = d["frames"][-1]
            parts = last.get("participants") or []

            blue_name, red_name = self._blue_red_names_for_game(gid)

            self.txt.insert(
                tk.END,
                f"Game {i}: {gid} | {blue_name} vs {red_name} | participants={len(parts)} | {pretty_utc(last.get('rfc460Timestamp'))}\n"
            )




    # -----------------------
    # Save JSON
    # -----------------------
    def save_json(self):
        fn = datetime.now().strftime("lol_live_%Y%m%d_%H%M%S.json")
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=fn,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return

        payload = {
            "selected": self.selected.__dict__ if self.selected else None,
            "eventDetails": self.event_details,
            "current_game_id": self.current_game_id,
            "all_game_ids": self.all_game_ids,
            "window": self.window_data,
            "details": self.details_data,
            "finished_windows_last": {gid: (w.get("frames") or [])[-1] if w and (w.get("frames") or []) else None
                                     for gid, w in self.finished_windows.items()},
            "finished_details_last": {gid: (d.get("frames") or [])[-1] if d and (d.get("frames") or []) else None
                                      for gid, d in self.finished_details.items()},
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        self.status_var.set(f"Saved: {path}")

    # -----------------------
    # threading helpers
    # -----------------------
    def _run_bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _ui(self, fn):
        self.root.after(0, fn)


def main():
    root = tk.Tk()


    ico_path = Path(__file__).with_name("icon.ico")
    if ico_path.exists():
         root.iconbitmap(str(ico_path))
    App(root)
    root.mainloop()
if __name__ == "__main__":
    main()
