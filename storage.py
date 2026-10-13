"""Збереження бази на диск (JSON) та зчитування з диска."""
import json
import os
import tempfile

from .core import Database, DBError


def save(db: Database, path: str) -> None:
    """Атомарний запис: спочатку у тимчасовий файл, потім заміна."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(db.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def load(path: str) -> Database:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise DBError(f"Файл {os.path.basename(path)!r} не знайдено") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise DBError(f"Файл пошкоджено: {e}") from None
    return Database.from_dict(data)  # усі значення валідуються повторно
