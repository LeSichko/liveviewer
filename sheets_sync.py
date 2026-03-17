"""
sheets_sync.py — синхронизация kda_stats с Google Sheets.

Зависимость: pip install gspread

Настройка:
  1. Google Cloud Console → включить Google Sheets API + Google Drive API
  2. Создать Service Account → скачать JSON ключ → положить рядом с main.py
  3. Открыть таблицу → Настройки доступа → добавить email сервис аккаунта (редактор)
  4. В config.py заполнить SHEETS_KEY_PATH и SHEETS_URL
  5. Вручную создать первую строку в таблице с названиями колонок
"""

from typing import List, Optional
from pathlib import Path

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

from kda_tracker import KdaRow, FIELDNAMES


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def is_available() -> bool:
    return GSPREAD_AVAILABLE


def _open_sheet(key_path: str, url: str):
    creds = Credentials.from_service_account_file(key_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_url(url).sheet1


def sync_rows(new_rows: List[KdaRow], key_path: str, url: str) -> tuple[int, int]:
    """
    Синхронизирует new_rows с Google Sheets.
    - Читает все существующие строки
    - Удаляет строки с теми же game_id что в new_rows
    - Дописывает new_rows
    Возвращает (добавлено, итого строк в таблице).
    """
    sheet = _open_sheet(key_path, url)
    existing = sheet.get_all_values()

    if not existing:
        return 0, 0

    # Определяем индекс колонки game_id по заголовку
    header = existing[0]
    try:
        gid_col = header.index("game_id")
    except ValueError:
        gid_col = FIELDNAMES.index("game_id")

    new_gids = {r.game_id for r in new_rows}

    # Оставляем заголовок + строки без совпадений по game_id
    filtered = [existing[0]]
    for row in existing[1:]:
        row_gid = row[gid_col] if gid_col < len(row) else ""
        if row_gid not in new_gids:
            filtered.append(row)

    # Собираем итоговый массив и пишем одним запросом
    rows_to_write = [_row_to_list(r) for r in new_rows]
    final = filtered + rows_to_write

    sheet.clear()
    if final:
        sheet.update(final, "A1")

    total = len(final) - 1  # минус заголовок
    return len(new_rows), total


def _row_to_list(r: KdaRow) -> list:
    return [str(getattr(r, f, "")) for f in FIELDNAMES]


def check_setup(key_path: str, url: str) -> Optional[str]:
    if not GSPREAD_AVAILABLE:
        return "Библиотека gspread не установлена.\nВыполни: pip install gspread"
    if not key_path or not Path(key_path).exists():
        return f"Файл ключа не найден: {key_path}"
    if not url:
        return "SHEETS_URL не заполнен в config.py"
    try:
        sheet = _open_sheet(key_path, url)
        _ = sheet.title
    except Exception as e:
        return f"Ошибка подключения к таблице:\n{e}"
    return None
