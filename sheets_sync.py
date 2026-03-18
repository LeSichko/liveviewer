"""
sheets_sync.py — синхронизация kda_stats с Google Sheets.

Зависимость: pip install gspread

Настройка:
  1. В config.py заполнить SHEETS_URL и SHEETS_CREDENTIALS
  2. Открыть таблицу → Настройки доступа → добавить client_email из SHEETS_CREDENTIALS (редактор)
  3. Вручную создать первую строку в таблице с названиями колонок
"""

from datetime import date as date_type
from typing import List, Optional
from pathlib import Path

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

from kda_tracker import KdaRow, FIELDNAMES
from config import SHEETS_CREDENTIALS, SHEETS_URL


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_SHEETS_EPOCH = date_type(1899, 12, 30)


def _date_to_serial(date_str: str):
    try:
        d = date_type.fromisoformat(date_str)
        return (d - _SHEETS_EPOCH).days
    except Exception:
        return date_str


def is_available() -> bool:
    return GSPREAD_AVAILABLE


def _open_sheet():
    creds = Credentials.from_service_account_info(SHEETS_CREDENTIALS, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_url(SHEETS_URL).sheet1


def sync_rows(new_rows: List[KdaRow]) -> tuple[int, int]:
    """
    Синхронизирует new_rows с Google Sheets.
    - Читает все существующие строки
    - Удаляет строки с теми же game_id что в new_rows
    - Дописывает new_rows
    Возвращает (добавлено, итого строк в таблице).
    """
    sheet = _open_sheet()
    existing = sheet.get_all_values()

    if not existing:
        return 0, 0

    header = existing[0]
    try:
        gid_col = header.index("game_id")
    except ValueError:
        gid_col = FIELDNAMES.index("game_id")

    new_gids = {r.game_id for r in new_rows}

    filtered = [existing[0]]
    for row in existing[1:]:
        row_gid = row[gid_col] if gid_col < len(row) else ""
        if row_gid not in new_gids:
            filtered.append(row)

    rows_to_write = [_row_to_list(r) for r in new_rows]
    final = filtered + rows_to_write

    sheet.clear()
    if final:
        sheet.update(final, "A1", value_input_option="USER_ENTERED")

    total = len(final) - 1
    return len(new_rows), total


def _row_to_list(r: KdaRow) -> list:
    result = []
    for f in FIELDNAMES:
        val = getattr(r, f, "")
        if f == "kills":
            try:
                result.append(int(val))
            except (ValueError, TypeError):
                result.append(0)
        elif f == "date":
            result.append(_date_to_serial(str(val)) if val else "")
        else:
            result.append(str(val))
    return result


def check_setup() -> Optional[str]:
    if not GSPREAD_AVAILABLE:
        return "Библиотека gspread не установлена.\nВыполни: pip install gspread"
    if not SHEETS_URL:
        return "SHEETS_URL не заполнен в config.py"
    try:
        sheet = _open_sheet()
        _ = sheet.title
    except Exception as e:
        return f"Ошибка подключения к таблице:\n{e}"
    return None