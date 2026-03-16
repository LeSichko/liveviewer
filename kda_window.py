import tkinter as tk
from typing import Optional, Any, Dict
from pathlib import Path


class KdaWindow:
    def __init__(self, parent: tk.Tk, ui_font, on_mousewheel_cb, ico_path: Path = None):
        self.window: Optional[tk.Toplevel] = None
        self.text: Optional[tk.Text] = None
        self._parent = parent
        self._ui_font = ui_font
        self._on_mousewheel = on_mousewheel_cb
        self._ico_path = ico_path
    def is_open(self) -> bool:
        return bool(self.window and self.window.winfo_exists())

    def open_or_focus(self):
        if self.is_open():
            self.window.lift()
            return
        self.window = tk.Toplevel(self._parent)
        self.window.title("KDA")
        self.window.geometry("500x420")
        if self._ico_path and self._ico_path.exists():
            self.window.iconbitmap(str(self._ico_path))
        self.text = tk.Text(self.window, wrap="none")
        self.text.pack(fill="both", expand=True)
        self.text.bind("<MouseWheel>", self._on_mousewheel)

    def render(
        self,
        details_data: Optional[Dict[str, Any]],
        window_data: Optional[Dict[str, Any]],
        blue_name: str,
        red_name: str,
        flip: bool,
        ui_font,
    ):
        if not self.is_open() or not self.text or not self.text.winfo_exists():
            return
        if not isinstance(details_data, dict):
            return

        frames = details_data.get("frames") or []
        if not frames:
            return

        last = frames[-1]
        participants = last.get("participants") or []
        if not participants:
            return

        # имена из window metadata
        name_map: dict[int, str] = {}
        gm = (window_data or {}).get("gameMetadata") or {}
        for side_key in ("blueTeamMetadata", "redTeamMetadata"):
            team_meta = gm.get(side_key) or {}
            for p in team_meta.get("participantMetadata") or []:
                pid = p.get("participantId")
                name = p.get("summonerName")
                if pid is not None:
                    name_map[int(pid)] = name

        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, f"{blue_name} vs {red_name}\n\n")

        blue_lines = []
        red_lines = []

        for p in sorted(participants, key=lambda x: x.get("participantId", 0)):
            pid = p.get("participantId")
            kills   = p.get("kills", 0)
            deaths  = p.get("deaths", 0)
            assists = p.get("assists", 0)
            name = name_map.get(pid, f"PID_{pid}")
            line = f"{name:18} {kills}/{deaths}/{assists}"
            if pid <= 5:
                blue_lines.append(line)
            else:
                red_lines.append(line)

        if flip:
            self.text.insert(tk.END, f"=== {red_name} (RED) K/D/A ===\n")
            for l in red_lines:
                self.text.insert(tk.END, l + "\n")
            self.text.insert(tk.END, "\n")
            self.text.insert(tk.END, f"=== {blue_name} (BLUE) K/D/A ===\n")
            for l in blue_lines:
                self.text.insert(tk.END, l + "\n")
        else:
            self.text.insert(tk.END, f"=== {blue_name} (BLUE) K/D/A ===\n")
            for l in blue_lines:
                self.text.insert(tk.END, l + "\n")
            self.text.insert(tk.END, "\n")
            self.text.insert(tk.END, f"=== {red_name} (RED) K/D/A ===\n")
            for l in red_lines:
                self.text.insert(tk.END, l + "\n")

        self.text.config(font=ui_font)
        self.text.config(state="disabled")
