from typing import Any, Dict, Optional, Tuple

import requests

from config import PERSISTED_BASE, FEED_BASE, REQ_TIMEOUT, DEFAULT_HL
from utils import minus_seconds_rfc3339


def http_get_json(
    url: str,
    headers: Dict[str, str],
    params: Optional[Dict[str, str]] = None,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    r = requests.get(url, headers=headers, params=params, timeout=REQ_TIMEOUT)
    print("HTTP URL:", r.url)
    if r.status_code == 204:
        return 204, None
    r.raise_for_status()
    return r.status_code, r.json()


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
        url = f"{PERSISTED_BASE}/getEventDetails"
        _, data = http_get_json(url, self.headers, params={"hl": self.hl, "id": str(match_id)})
        return data or {}

    def get_window(
        self, game_id: str, starting_time: Optional[str] = None
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        url = f"{FEED_BASE}/window/{game_id}"
        params = {"startingTime": starting_time} if starting_time else None
        return http_get_json(url, self.feed_headers, params=params)

    def get_details(
        self, game_id: str, starting_time: Optional[str] = None
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        url = f"{FEED_BASE}/details/{game_id}"
        params = {"startingTime": starting_time} if starting_time else None
        return http_get_json(url, self.feed_headers, params=params)

    def anchor_time(self, offset_sec: int) -> str:
        """Возвращает строку startingTime = сейчас минус offset_sec."""
        from utils import iso_date_multiply_of_10
        anchor = iso_date_multiply_of_10()
        return minus_seconds_rfc3339(anchor, offset_sec)
