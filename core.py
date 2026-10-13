"""Ядро системи: типи полів, таблиці, база даних, сортування."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable


class DBError(Exception):
    """Базова помилка системи."""


class ValidationError(DBError):
    """Некоректні дані (назва, тип, значення поля тощо)."""


class NotFoundError(DBError):
    """Таблицю або рядок не знайдено."""


# ---------------------------------------------------------------- типи полів
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def _time_to_seconds(text: Any) -> int:
    m = _TIME_RE.match(str(text).strip())
    if not m:
        raise ValidationError(f"Невірний формат часу {text!r}: очікується ГГ:ХХ або ГГ:ХХ:СС")
    h, mi, s = int(m[1]), int(m[2]), int(m[3] or 0)
    if h > 23 or mi > 59 or s > 59:
        raise ValidationError(f"Час {text!r} поза допустимим діапазоном 00:00:00–23:59:59")
    return h * 3600 + mi * 60 + s


def _seconds_to_time(sec: int) -> str:
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _parse_integer(raw: Any) -> int:
    if isinstance(raw, bool) or isinstance(raw, float):
        raise ValidationError(f"Невірне ціле число: {raw!r}")
    try:
        return int(str(raw).strip())
    except ValueError:
        raise ValidationError(f"Невірне ціле число: {raw!r}") from None


def _parse_real(raw: Any) -> float:
    if isinstance(raw, bool):
        raise ValidationError(f"Невірне дійсне число: {raw!r}")
    try:
        value = float(str(raw).strip().replace(",", "."))
    except ValueError:
        raise ValidationError(f"Невірне дійсне число: {raw!r}") from None
    if not math.isfinite(value):
        raise ValidationError(f"Невірне дійсне число: {raw!r}")
    return value


def _parse_char(raw: Any) -> str:
    if not isinstance(raw, str) or len(raw) != 1:
        raise ValidationError(f"Поле типу char має містити рівно 1 символ, отримано {raw!r}")
    return raw


def _parse_string(raw: Any) -> str:
    if raw is None:
        raise ValidationError("Значення рядка не може бути null")
    return str(raw)


def _parse_time(raw: Any) -> str:
    return _seconds_to_time(_time_to_seconds(raw))


def _parse_time_invl(raw: Any) -> str:
    parts = str(raw).split("-")
    if len(parts) != 2:
        raise ValidationError(
            f"Невірний інтервал {raw!r}: очікується ГГ:ХХ[:СС]-ГГ:ХХ[:СС]")
    start, end = _time_to_seconds(parts[0]), _time_to_seconds(parts[1])
    if start > end:
        raise ValidationError(f"Початок інтервалу {raw!r} пізніше за його кінець")
    return f"{_seconds_to_time(start)}-{_seconds_to_time(end)}"


def _key_time_invl(stored: str) -> tuple[int, int]:
    start, end = stored.split("-")
    return _time_to_seconds(start), _time_to_seconds(end)


@dataclass(frozen=True)
class FieldType:
    parse: Callable[[Any], Any]   # сире значення -> канонічне (зберігається)
    sort_key: Callable[[Any], Any]  # канонічне значення -> ключ сортування


FIELD_TYPES: dict[str, FieldType] = {
    "integer": FieldType(_parse_integer, lambda v: v),
    "real": FieldType(_parse_real, lambda v: v),
    "char": FieldType(_parse_char, lambda v: v),
    "string": FieldType(_parse_string, lambda v: v),
    "time": FieldType(_parse_time, _time_to_seconds),
    "timeInvl": FieldType(_parse_time_invl, _key_time_invl),
}
TYPE_NAMES = list(FIELD_TYPES)


# ------------------------------------------------------------------- таблиця
def _check_name(kind: str, name: Any) -> str:
    if not isinstance(name, str) or not name.isidentifier():
        raise ValidationError(
            f"{kind} {name!r} некоректна: літери, цифри та '_', не може починатися з цифри")
    return name


class Table:
    def __init__(self, name: str, fields: list[dict[str, str]]):
        self.name = _check_name("Назва таблиці", name)
        if not isinstance(fields, list) or not fields:
            raise ValidationError("Таблиця повинна мати хоча б одне поле")
        self.fields: list[dict[str, str]] = []
        seen: set[str] = set()
        for f in fields:
            if not isinstance(f, dict):
                raise ValidationError("Опис поля має бути об'єктом {name, type}")
            fname = _check_name("Назва поля", f.get("name"))
            ftype = f.get("type")
            if ftype not in FIELD_TYPES:
                raise ValidationError(
                    f"Невідомий тип {ftype!r} поля {fname!r}; доступні: {', '.join(TYPE_NAMES)}")
            if fname in seen:
                raise ValidationError(f"Поле {fname!r} повторюється")
            seen.add(fname)
            self.fields.append({"name": fname, "type": ftype})
        self.rows: dict[int, dict[str, Any]] = {}
        self._next_id = 1

    # --- допоміжне
    def _type_of(self, field_name: str) -> FieldType:
        for f in self.fields:
            if f["name"] == field_name:
                return FIELD_TYPES[f["type"]]
        raise ValidationError(f"Поля {field_name!r} немає в таблиці {self.name!r}")

    def _validate(self, values: dict[str, Any], partial: bool = False) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise ValidationError("Значення рядка мають бути об'єктом {поле: значення}")
        names = {f["name"] for f in self.fields}
        unknown = set(values) - names
        if unknown:
            raise ValidationError(f"Невідомі поля: {', '.join(sorted(unknown))}")
        if not partial:
            missing = [n for n in (f["name"] for f in self.fields) if n not in values]
            if missing:
                raise ValidationError(f"Не задано значення полів: {', '.join(missing)}")
        result = {}
        for name, raw in values.items():
            try:
                result[name] = self._type_of(name).parse(raw)
            except ValidationError as e:
                raise ValidationError(f"Поле {name!r}: {e}") from None
        return result

    def get_row(self, row_id: int) -> dict[str, Any]:
        if row_id not in self.rows:
            raise NotFoundError(f"Рядок {row_id} не знайдено в таблиці {self.name!r}")
        return self.rows[row_id]

    # --- рядки
    def add_row(self, values: dict[str, Any]) -> int:
        clean = self._validate(values)
        row_id = self._next_id
        self._next_id += 1
        self.rows[row_id] = {f["name"]: clean[f["name"]] for f in self.fields}
        return row_id

    def update_row(self, row_id: int, values: dict[str, Any]) -> None:
        row = self.get_row(row_id)
        row.update(self._validate(values, partial=True))

    def delete_row(self, row_id: int) -> None:
        self.get_row(row_id)
        del self.rows[row_id]

    # --- сортування за одним полем
    def sorted_rows(self, sort_by: str | None = None,
                    descending: bool = False) -> list[tuple[int, dict[str, Any]]]:
        items = list(self.rows.items())
        if sort_by is None:
            return items
        key = self._type_of(sort_by).sort_key
        # sorted() стабільне: рівні значення зберігають порядок додавання
        return sorted(items, key=lambda kv: key(kv[1][sort_by]), reverse=descending)

    # --- (де)серіалізація
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fields": [dict(f) for f in self.fields],
            "next_id": self._next_id,
            "rows": [{"id": i, "values": dict(v)} for i, v in self.rows.items()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Table":
        try:
            table = cls(data["name"], data["fields"])
            for item in data["rows"]:
                row_id = int(item["id"])
                table.rows[row_id] = table._validate(item["values"])
            table._next_id = max(int(data["next_id"]), max(table.rows, default=0) + 1)
        except (KeyError, TypeError, ValueError) as e:
            raise ValidationError(f"Пошкоджений опис таблиці: {e!r}") from None
        return table


# ---------------------------------------------------------------------- база
class Database:
    def __init__(self, name: str):
        self.name = _check_name("Назва бази", name)
        self.tables: dict[str, Table] = {}

    def create_table(self, name: str, fields: list[dict[str, str]]) -> Table:
        if name in self.tables:
            raise ValidationError(f"Таблиця {name!r} вже існує")
        table = Table(name, fields)
        self.tables[name] = table
        return table

    def drop_table(self, name: str) -> None:
        self.get_table(name)
        del self.tables[name]

    def get_table(self, name: str) -> Table:
        if name not in self.tables:
            raise NotFoundError(f"Таблицю {name!r} не знайдено")
        return self.tables[name]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tables": [t.to_dict() for t in self.tables.values()]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Database":
        try:
            db = cls(data["name"])
            for t in data["tables"]:
                table = Table.from_dict(t)
                if table.name in db.tables:
                    raise ValidationError(f"Таблиця {table.name!r} повторюється у файлі")
                db.tables[table.name] = table
        except (KeyError, TypeError) as e:
            raise ValidationError(f"Пошкоджений файл бази: {e!r}") from None
        return db
