import os
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter import font as tkfont
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from config import DEFAULT_HL, DEFAULT_API_KEY
from kda_tracker import extract_kda_rows, save_rows, CSV_PATH
import sheets_sync
from config import SHEETS_KEY_PATH, SHEETS_URL
from models import MatchRow
from utils import (
    sg, parse_rfc3339, pretty_utc, pretty_local,
    iso_date_multiply_of_10, fmt_dragons,
)
from api_client import LolEsportsClient
from parser import parse_schedule, pick_game_id
from kda_window import KdaWindow


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("LoL Live Viewer")
        self.root.geometry("1200x720")

        # --- state ---
        self.anchor_offset_sec = tk.IntVar(value=30)
        self.manual_game_id: Optional[str] = None
        self.game_choice_var = tk.StringVar(value="Auto")
        self.flip_sides_var = tk.BooleanVar(value=False)
        self.use_details_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")

        self.client: Optional[LolEsportsClient] = None
        self.active_rows: List[MatchRow] = []
        self.finished_rows: List[MatchRow] = []
        self.selected: Optional[MatchRow] = None
        self.older_token: Optional[str] = None   # pageToken для Load more

        self.event_details: Optional[Dict[str, Any]] = None
        self.all_game_ids: List[str] = []
        self.current_game_id: Optional[str] = None

        self.window_data: Optional[Dict[str, Any]] = None
        self.details_data: Optional[Dict[str, Any]] = None
        self.finished_windows: Dict[str, Dict[str, Any]] = {}
        self.finished_details: Dict[str, Dict[str, Any]] = {}

        self._last_ed_refresh = 0.0
        self._last_games_poll = 0.0
        self.games_poll_every_sec = 420.0

        self.polling = False

        # --- build ---
        self._build_ui()
        self._start_poll_loop()

        self.ui_font = tkfont.Font(family="Consolas", size=12)
        self.txt.config(font=self.ui_font)
        self.txt.bind("<MouseWheel>", self._on_mousewheel)

        ico_path = Path(__file__).with_name("icon.ico")
        self.kda = KdaWindow(self.root, self.ui_font, self._on_mousewheel, ico_path=ico_path)

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Api Key:").pack(side=tk.LEFT)
        self._api_key_entry = ttk.Entry(top, width=44)
        self._api_key_entry.pack(side=tk.LEFT, padx=4)
        self._api_key_entry.insert(0, DEFAULT_API_KEY)

        ttk.Label(top, text="Game:").pack(side=tk.LEFT, padx=(0, 2))
        self.cb_game = ttk.Combobox(
            top, textvariable=self.game_choice_var,
            width=7, state="readonly", values=["Auto"],
        )
        self.cb_game.pack(side=tk.LEFT, padx=2)
        self.cb_game.bind("<<ComboboxSelected>>", self.on_game_selected)

        ttk.Button(top, text="Show KDA", command=self.show_kda_window).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(
            top, text="Flip sides (RED on top)",
            variable=self.flip_sides_var, command=self.render,
        ).pack(side=tk.LEFT, padx=0)
        ttk.Button(top, text="Refresh schedule", command=self.refresh_schedule).pack(side=tk.LEFT, padx=2)

        ttk.Label(top, text="Anchor (-sec):").pack(side=tk.LEFT, padx=(2, 2))
        ttk.Entry(top, width=5, textvariable=self.anchor_offset_sec, justify="center").pack(side=tk.LEFT)
        ttk.Button(top, text="-10", width=4, command=lambda: self._bump_anchor(-10)).pack(side=tk.LEFT, padx=(4, 2))
        ttk.Button(top, text="+10", width=4, command=lambda: self._bump_anchor(+10)).pack(side=tk.LEFT, padx=(2, 2))

        ttk.Button(top, text="Save JSON", command=self.save_json).pack(side=tk.LEFT, padx=0)
        ttk.Button(top, text="Write KDA", command=self.write_kda).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(top, text="Write All KDA", command=self.write_all_kda).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(self.root, textvariable=self.status_var, foreground="green", padding=(3, 2)).pack(fill=tk.X)

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

        def _make_listbox(parent):
            f = ttk.Frame(parent)
            f.pack(fill=tk.BOTH, expand=True)
            lb = tk.Listbox(f, height=26)
            lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            sb = ttk.Scrollbar(f, orient=tk.VERTICAL, command=lb.yview)
            sb.pack(side=tk.RIGHT, fill=tk.Y)
            lb.config(yscrollcommand=sb.set)
            return lb

        self.lb_active = _make_listbox(tab_active)
        self.lb_active.bind("<<ListboxSelect>>", self.on_select_match)

        self.lb_finished = _make_listbox(tab_finished)
        self.lb_finished.bind("<<ListboxSelect>>", self.on_select_match)

        # кнопка Load more под списком Finished
        self.btn_load_more = ttk.Button(tab_finished, text="Load more", command=self.load_more)
        self.btn_load_more.pack(fill=tk.X, padx=4, pady=(2, 4))
        self.btn_load_more.state(["disabled"])  # выключена пока не загружено расписание

        ttk.Label(right, text="Details:").pack(anchor="w")
        self.txt = tk.Text(right, wrap="word")
        self.txt.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------ #
    # Font zoom
    # ------------------------------------------------------------------ #
    def _zoom(self, delta: int):
        size = self.ui_font.cget("size")
        self.ui_font.configure(size=max(8, min(28, size + delta)))

    def _on_mousewheel(self, event):
        if event.state & 0x0004:
            self._zoom(1 if event.delta > 0 else -1)
            return "break"

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _api_key(self) -> str:
        return self._api_key_entry.get().strip()

    def _make_client(self) -> Optional[LolEsportsClient]:
        key = self._api_key()
        if not key:
            messagebox.showerror("Missing X-Api-Key", "Paste X-Api-Key from lolesports.com network requests.")
            return None
        return LolEsportsClient(key, DEFAULT_HL)

    def _bump_anchor(self, delta: int):
        try:
            v = int(self.anchor_offset_sec.get())
        except Exception:
            v = 30
        self.anchor_offset_sec.set(max(0, v + delta))
        if self.client and self.current_game_id:
            self._run_bg(self._poll_bg)

    def _refresh_game_selector(self):
        values = ["Auto"]
        for i, gid in enumerate(self.all_game_ids or [], 1):
            values.append(f"Game {i}: {gid}")
        self.cb_game["values"] = values

        if self.manual_game_id:
            want = next((v for v in values if v.endswith(str(self.manual_game_id))), None)
            self.game_choice_var.set(want or "Auto")
            if not want:
                self.manual_game_id = None
        else:
            self.game_choice_var.set("Auto")

    def on_game_selected(self, _evt=None):
        v = (self.game_choice_var.get() or "").strip()
        if v == "Auto" or not v:
            self.manual_game_id = None
            gid, _ = pick_game_id(self.event_details or {})
            if gid:
                self.current_game_id = gid
            self.status_var.set("Game: Auto")
            return

        try:
            gid = v.split(":", 1)[1].strip()
        except Exception:
            gid = None

        if gid:
            self.manual_game_id = gid
            self.current_game_id = gid
            self.status_var.set(f"Game locked: {gid}")
            self._ui(self.render)

    # ------------------------------------------------------------------ #
    # Team name resolution
    # ------------------------------------------------------------------ #
    def _team_id_to_name(self) -> Dict[str, str]:
        ed = self.event_details or {}
        teams = sg(ed, "data.event.match.teams", []) or []
        return {
            str(t["id"]): t.get("code") or t.get("name") or str(t["id"])
            for t in teams if t.get("id") is not None
        }

    def _blue_red_names_for_game(
        self,
        game_id: Optional[str],
        window_payload: Optional[Dict] = None,
    ) -> tuple[str, str]:
        if not game_id:
            return "BLUE", "RED"

        id2name = self._team_id_to_name()

        if window_payload:
            gm = window_payload.get("gameMetadata") or {}
            bmeta = gm.get("blueTeamMetadata") or {}
            rmeta = gm.get("redTeamMetadata") or {}
            bid = bmeta.get("esportsTeamId")
            rid = rmeta.get("esportsTeamId")
            if bid is not None and rid is not None:
                return id2name.get(str(bid), "BLUE"), id2name.get(str(rid), "RED")

        ed = self.event_details or {}
        games = sg(ed, "data.event.match.games", []) or []
        for g in games:
            if str(g.get("id")) != str(game_id):
                continue
            blue_id = red_id = None
            for t in (g.get("teams") or []):
                side = (t.get("side") or "").lower()
                tid = t.get("id")
                if tid is None:
                    continue
                if side == "blue":
                    blue_id = str(tid)
                elif side == "red":
                    red_id = str(tid)
            return id2name.get(blue_id, "BLUE"), id2name.get(red_id, "RED")

        return "BLUE", "RED"

    # ------------------------------------------------------------------ #
    # Schedule
    # ------------------------------------------------------------------ #
    def refresh_schedule(self):
        self.client = self._make_client()
        if not self.client:
            return
        self.status_var.set("Loading schedule...")
        self._run_bg(self._refresh_schedule_bg)

    def _refresh_schedule_bg(self):
        try:
            sched = self.client.get_schedule()
            active, finished, older_token = parse_schedule(sched)
            self._ui(lambda: self._set_lists(active, finished, older_token, append=False))
            self._ui(lambda: self.status_var.set(
                f"Schedule loaded. Active={len(active)} Finished={len(finished)}"
            ))
        except Exception as e:
            msg = f"Schedule error: {e}"
            self._ui(lambda m=msg: self.status_var.set(m))

    # ------------------------------------------------------------------ #
    # Load more (older page)
    # ------------------------------------------------------------------ #
    def load_more(self):
        if not self.client or not self.older_token:
            return
        self.btn_load_more.state(["disabled"])
        self.status_var.set("Loading more...")
        self._run_bg(self._load_more_bg)

    def _load_more_bg(self):
        try:
            sched = self.client.get_schedule(page_token=self.older_token)
            _, finished, older_token = parse_schedule(sched)
            self._ui(lambda: self._set_lists([], finished, older_token, append=True))
            self._ui(lambda: self.status_var.set(
                f"Loaded {len(finished)} more. {'More available.' if older_token else 'No more pages.'}"
            ))
        except Exception as e:
            msg = f"Load more error: {e}"
            self._ui(lambda m=msg: self.status_var.set(m))
            self._ui(lambda: self.btn_load_more.state(["!disabled"]))

    def _set_lists(
        self,
        active: List[MatchRow],
        finished: List[MatchRow],
        older_token: Optional[str],
        append: bool = False,
    ):
        self.older_token = older_token

        # включаем/выключаем кнопку Load more
        if older_token:
            self.btn_load_more.state(["!disabled"])
            self.btn_load_more.config(text="Load more")
        else:
            self.btn_load_more.state(["disabled"])
            self.btn_load_more.config(text="Load more (no more pages)")

        if not append:
            self.active_rows = active
            self.finished_rows = finished

            self.lb_active.delete(0, tk.END)
            for r in active:
                self.lb_active.insert(
                    tk.END, f"{pretty_local(r.start_time)} | [{r.league}] {r.team1} vs {r.team2} | {r.state}"
                )
            self.lb_finished.delete(0, tk.END)
            for r in finished:
                s1 = r.score1 if r.score1 is not None else "?"
                s2 = r.score2 if r.score2 is not None else "?"
                self.lb_finished.insert(
                    tk.END,
                    f"{pretty_local(r.end_time or r.start_time)} | [{r.league}] {r.team1} {s1}-{s2} {r.team2}",
                )
        else:
            # дописываем в конец
            self.finished_rows.extend(finished)
            for r in finished:
                s1 = r.score1 if r.score1 is not None else "?"
                s2 = r.score2 if r.score2 is not None else "?"
                self.lb_finished.insert(
                    tk.END,
                    f"{pretty_local(r.end_time or r.start_time)} | [{r.league}] {r.team1} {s1}-{s2} {r.team2}",
                )

    # ------------------------------------------------------------------ #
    # Match selection
    # ------------------------------------------------------------------ #
    def on_select_match(self, evt=None):
        lb = evt.widget if evt else None
        rows = self.finished_rows if lb is self.lb_finished else self.active_rows
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

    def _load_match_bg(self):
        try:
            ed = self.client.get_event_details(self.selected.match_id)
            game_id, all_ids = pick_game_id(ed)

            self.event_details = ed
            self.all_game_ids = all_ids
            self._ui(self._refresh_game_selector)

            if self.manual_game_id and self.manual_game_id in all_ids:
                self.current_game_id = self.manual_game_id
            else:
                self.manual_game_id = None
                self.current_game_id = game_id
                self._ui(self._refresh_game_selector)

            self.window_data = None
            self.details_data = None
            self.finished_windows = {}
            self.finished_details = {}

            if not self.current_game_id:
                self._ui(lambda: self.status_var.set("No games found in eventDetails.match.games"))
                self._ui(self.render)
                return

            w_status, w = self.client.get_window(self.current_game_id)
            self.window_data = w

            d_status, d = (204, None)
            if self.use_details_var.get():
                d_status, d = self.client.get_details(self.current_game_id)
                self.details_data = d

            if self.selected.state == "completed":
                anchor = self.client.anchor_time(30)
                for gid in all_ids:
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
                f"Loaded. matchId={self.selected.match_id} "
                f"gameId={self.current_game_id} "
                f"(window {w_status}, details {d_status})"
            ))
            self._ui(self.render)

        except Exception as e:
            msg = f"Load error: {e}"
            self._ui(lambda m=msg: self.status_var.set(m))
            self._ui(lambda m=msg: messagebox.showerror("Load error", m))

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #
    def _start_poll_loop(self):
        if not self.polling:
            self.polling = True
            self.root.after(800, self._poll_tick)

    def _poll_tick(self):
        try:
            if self.client and self.selected and self.current_game_id:
                self._run_bg(self._poll_bg)
        finally:
            self.root.after(800, self._poll_tick)

    def _poll_bg(self):
        try:
            gid = self.current_game_id
            if not gid:
                return

            try:
                offset = int(self.anchor_offset_sec.get())
            except Exception:
                offset = 30
            anchor = self.client.anchor_time(offset)

            now = time.time()

            if now - self._last_ed_refresh >= 410.0:
                self._last_ed_refresh = now
                try:
                    ed = self.client.get_event_details(self.selected.match_id)
                    self.event_details = ed
                    new_gid, all_ids = pick_game_id(ed)
                    self.all_game_ids = all_ids
                    self._ui(self._refresh_game_selector)

                    if self.manual_game_id:
                        if self.manual_game_id in all_ids:
                            self.current_game_id = self.manual_game_id
                            gid = self.manual_game_id
                    elif new_gid:
                        self.current_game_id = new_gid
                        gid = new_gid
                except Exception:
                    pass

            w_status, w = self.client.get_window(gid, starting_time=anchor)
            if w_status == 200 and w and (w.get("frames") or []):
                self.window_data = w

            if self.use_details_var.get():
                d_status, d = self.client.get_details(gid, starting_time=anchor)
                if d_status == 200 and d and (d.get("frames") or []):
                    self.details_data = d

            if now - self._last_games_poll >= self.games_poll_every_sec:
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
            self._ui(lambda m=f"Poll HTTP error: {e}": self.status_var.set(m))
        except Exception as e:
            self._ui(lambda m=f"Poll error: {e}": self.status_var.set(m))

    # ------------------------------------------------------------------ #
    # Render
    # ------------------------------------------------------------------ #
    def render(self):
        self.txt.config(state="normal")
        self.txt.delete("1.0", tk.END)

        if not self.selected:
            self.txt.insert(tk.END, "Select a match.\n")
            self.txt.config(state="disabled")
            return

        r = self.selected
        score = f" {r.score1}-{r.score2}" if r.score1 is not None and r.score2 is not None else ""
        self.txt.insert(tk.END, f"[{r.league}] {r.team1}{score} {r.team2}\nstate: {r.state}\n\n")

        self.txt.insert(tk.END, "=== WINDOW (latest) ===\n")
        self._render_window_latest()

        self.txt.config(state="disabled")

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
            mm, ss = divmod(int(gt), 60)
            self.txt.insert(tk.END, f"gameTime  : {mm:02d}:{ss:02d}\n")

        blue_team = last.get("blueTeam") or {}
        red_team  = last.get("redTeam") or {}
        blue_name, red_name = self._blue_red_names_for_game(self.current_game_id, self.window_data)

        gold_diff = int(blue_team.get("totalGold", 0) or 0) - int(red_team.get("totalGold", 0) or 0)
        if self.flip_sides_var.get():
            gold_diff = -gold_diff
        lead_name = (blue_name if gold_diff > 0 else red_name if gold_diff < 0 else "=") if not self.flip_sides_var.get() \
            else (red_name if gold_diff > 0 else blue_name if gold_diff < 0 else "=")

        def _line(team, name, side):
            return (
                f"{name} ({side}): K={team.get('totalKills', 0)} "
                f"G={team.get('totalGold', 0)} T={team.get('towers', 0)} "
                f"B={team.get('barons', 0)} I={team.get('inhibitors', 0)} "
                f"D={fmt_dragons(team)}\n"
            )

        if self.flip_sides_var.get():
            self.txt.insert(tk.END, _line(red_team, red_name, "RED"))
            self.txt.insert(tk.END, _line(blue_team, blue_name, "BLUE"))
        else:
            self.txt.insert(tk.END, _line(blue_team, blue_name, "BLUE"))
            self.txt.insert(tk.END, _line(red_team, red_name, "RED"))

        self.txt.insert(tk.END, f"Gold diff : {gold_diff:+d} ({lead_name})\n")

    # ------------------------------------------------------------------ #
    # KDA window
    # ------------------------------------------------------------------ #
    def show_kda_window(self):
        self.kda.open_or_focus()
        self._render_kda()

    def _render_kda(self):
        if not self.kda.is_open():
            return
        blue_name, red_name = self._blue_red_names_for_game(self.current_game_id, self.window_data)
        self.kda.render(
            details_data=self.details_data,
            window_data=self.window_data,
            blue_name=blue_name,
            red_name=red_name,
            flip=self.flip_sides_var.get(),
            ui_font=self.ui_font,
        )

    # ------------------------------------------------------------------ #
    # Save JSON
    # ------------------------------------------------------------------ #
    def save_json(self):
        fn = datetime.now().strftime("lol_live_%Y%m%d_%H%M%S.json")
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=fn,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        def _last_frame(cache, gid):
            w = cache.get(gid)
            if w and (w.get("frames") or []):
                return w["frames"][-1]
            return None

        payload = {
            "selected": self.selected.__dict__ if self.selected else None,
            "eventDetails": self.event_details,
            "current_game_id": self.current_game_id,
            "all_game_ids": self.all_game_ids,
            "window": self.window_data,
            "details": self.details_data,
            "finished_windows_last": {gid: _last_frame(self.finished_windows, gid) for gid in self.all_game_ids},
            "finished_details_last": {gid: _last_frame(self.finished_details, gid) for gid in self.all_game_ids},
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        self.status_var.set(f"Saved: {path}")

    # ------------------------------------------------------------------ #
    # Google Sheets sync
    # ------------------------------------------------------------------ #
    def _sync_to_sheets(self, rows):
        if not SHEETS_URL:
            return
        err = sheets_sync.check_setup(SHEETS_KEY_PATH, SHEETS_URL)
        if err:
            self._ui(lambda m=err: self.status_var.set(f"Sheets: {m.splitlines()[0]}"))
            return
        try:
            added, total = sheets_sync.sync_rows(rows, SHEETS_KEY_PATH, SHEETS_URL)
            self._ui(lambda a=added, t=total: self.status_var.set(
                f"Sheets: +{a} строк, итого {t}"
            ))
        except Exception as e:
            self._ui(lambda m=str(e): self.status_var.set(f"Sheets error: {m}"))

    # ------------------------------------------------------------------ #
    # Threading helpers
    # ------------------------------------------------------------------ #
    def _run_bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _ui(self, fn):
        self.root.after(0, fn)

    # ------------------------------------------------------------------ #
    # Write KDA to CSV
    # ------------------------------------------------------------------ #
    def write_kda(self):
        if not self.selected:
            messagebox.showwarning("Write KDA", "Сначала выбери матч.")
            return
        if not self.all_game_ids:
            messagebox.showwarning("Write KDA", "Нет game_id — загрузи матч.")
            return
        self.status_var.set("Fetching details for all games...")
        self._run_bg(self._write_kda_bg)

    def _write_kda_bg(self):
        try:
            offset = int(self.anchor_offset_sec.get()) if self.anchor_offset_sec.get() else 30
            anchor = self.client.anchor_time(offset)

            for gid in self.all_game_ids:
                if gid not in self.finished_windows:
                    try:
                        st, ww = self.client.get_window(gid, starting_time=anchor)
                        if st == 200 and ww and (ww.get("frames") or []):
                            self.finished_windows[gid] = ww
                    except Exception:
                        pass
                if gid not in self.finished_details:
                    try:
                        st, dd = self.client.get_details(gid, starting_time=anchor)
                        if st == 200 and dd and (dd.get("frames") or []):
                            self.finished_details[gid] = dd
                    except Exception:
                        pass

            rows, warnings = extract_kda_rows(
                match_id=self.selected.match_id,
                all_game_ids=self.all_game_ids,
                finished_windows=self.finished_windows,
                finished_details=self.finished_details,
                team_id_to_name=self._team_id_to_name(),
                tournament=self.selected.league,
            )

            if not rows:
                msg = "Нет данных для записи.\n" + "\n".join(warnings)
                self._ui(lambda m=msg: messagebox.showwarning("Write KDA", m))
                self._ui(lambda: self.status_var.set("KDA: нет данных"))
                return

            total = save_rows(rows)
            self._sync_to_sheets(rows)

            warn_text = ("\n\nПредупреждения:\n" + "\n".join(warnings)) if warnings else ""
            msg = f"Записано {len(rows)} строк ({len(self.all_game_ids)} карт).\nВсего в файле: {total}.{warn_text}\n\n{CSV_PATH}"
            self._ui(lambda m=msg: messagebox.showinfo("Write KDA", m))
            self._ui(lambda: self.status_var.set(f"KDA записано: {len(rows)} строк → {CSV_PATH.name}"))

        except Exception as e:
            msg = f"Write KDA error: {e}"
            self._ui(lambda m=msg: self.status_var.set(m))
            self._ui(lambda m=msg: messagebox.showerror("Write KDA error", m))

    # ------------------------------------------------------------------ #
    # Write All KDA
    # ------------------------------------------------------------------ #
    def write_all_kda(self):
        if not self.client:
            messagebox.showwarning("Write All KDA", "Сначала загрузи расписание (Refresh schedule).")
            return
        completed = [r for r in self.finished_rows if r.state == "completed"]
        if not completed:
            messagebox.showwarning("Write All KDA", "Нет завершённых матчей в расписании.")
            return
        if not messagebox.askyesno(
            "Write All KDA",
            f"Будет обработано {len(completed)} матчей.\nЭто может занять некоторое время. Продолжить?"
        ):
            return
        self._run_bg(lambda: self._write_all_kda_bg(completed))

    def _write_all_kda_bg(self, completed: list):
        total_matches = len(completed)
        all_rows = []
        all_warnings = []

        try:
            offset = int(self.anchor_offset_sec.get())
        except Exception:
            offset = 30

        for i, match_row in enumerate(completed, 1):
            self._ui(lambda i=i, r=match_row: self.status_var.set(
                f"Processing {i}/{total_matches}: {r.team1} vs {r.team2} ({r.league})..."
            ))

            try:
                ed = self.client.get_event_details(match_row.match_id)
                _, all_ids = pick_game_id(ed)
                if not all_ids:
                    all_warnings.append(f"{match_row.team1} vs {match_row.team2}: нет game_id")
                    continue

                teams = (((ed.get("data") or {}).get("event") or {}).get("match") or {}).get("teams") or []
                id2name = {
                    str(t["id"]): t.get("code") or t.get("name") or str(t["id"])
                    for t in teams if t.get("id") is not None
                }

                anchor = self.client.anchor_time(offset)
                windows = {}
                details = {}

                for gid in all_ids:
                    try:
                        st, ww = self.client.get_window(gid, starting_time=anchor)
                        if st == 200 and ww and (ww.get("frames") or []):
                            windows[gid] = ww
                    except Exception:
                        pass
                    try:
                        st, dd = self.client.get_details(gid, starting_time=anchor)
                        if st == 200 and dd and (dd.get("frames") or []):
                            details[gid] = dd
                    except Exception:
                        pass

                rows, warns = extract_kda_rows(
                    match_id=match_row.match_id,
                    all_game_ids=all_ids,
                    finished_windows=windows,
                    finished_details=details,
                    team_id_to_name=id2name,
                    tournament=match_row.league,
                )
                all_rows.extend(rows)
                all_warnings.extend(warns)

            except Exception as e:
                all_warnings.append(f"{match_row.team1} vs {match_row.team2}: ошибка — {e}")

        if not all_rows:
            msg = "Нет данных для записи.\n" + "\n".join(all_warnings[:20])
            self._ui(lambda m=msg: messagebox.showwarning("Write All KDA", m))
            self._ui(lambda: self.status_var.set("Write All KDA: нет данных"))
            return

        total = save_rows(all_rows)
        self._sync_to_sheets(all_rows)
        warn_text = (f"\n\nПредупреждения ({len(all_warnings)}):\n" + "\n".join(all_warnings[:20])) if all_warnings else ""
        msg = f"Готово! Обработано матчей: {total_matches}\nЗаписано строк: {len(all_rows)}\nВсего в файле: {total}.{warn_text}\n\n{CSV_PATH}"
        self._ui(lambda m=msg: messagebox.showinfo("Write All KDA", m))
        self._ui(lambda: self.status_var.set(f"Write All KDA: {len(all_rows)} строк → {CSV_PATH.name}"))
