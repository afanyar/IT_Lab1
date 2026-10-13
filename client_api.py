"""HTTP-клієнт до сервера. GUI працює лише через цей модуль."""
import json
import urllib.error
import urllib.request
from urllib.parse import quote


class ApiError(Exception):
    """Помилка від сервера (з повідомленням) або відсутність зв'язку."""


class ApiClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read().decode("utf-8"))["error"]
            except Exception:
                message = f"HTTP {e.code}"
            raise ApiError(message) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(f"Немає зв'язку з сервером {self.base_url}: {e}") from None
        return json.loads(raw) if raw else {}

    @staticmethod
    def _t(name: str) -> str:
        return "/api/tables/" + quote(name, safe="")

    # база
    def types(self): return self._request("GET", "/api/types")["types"]
    def database_info(self): return self._request("GET", "/api/database")
    def new_database(self, name): return self._request("POST", "/api/database", {"name": name})
    def list_files(self): return self._request("GET", "/api/files")["files"]
    def save_database(self, filename): return self._request("POST", "/api/database/save", {"filename": filename})
    def load_database(self, filename): return self._request("POST", "/api/database/load", {"filename": filename})

    # таблиці
    def create_table(self, name, fields): return self._request("POST", "/api/tables", {"name": name, "fields": fields})
    def drop_table(self, name): return self._request("DELETE", self._t(name))

    # рядки
    def get_rows(self, table, sort_by=None, descending=False):
        query = ""
        if sort_by:
            query = f"?sort_by={quote(sort_by, safe='')}&desc={int(descending)}"
        return self._request("GET", self._t(table) + "/rows" + query)

    def add_row(self, table, values): return self._request("POST", self._t(table) + "/rows", {"values": values})["id"]
    def update_row(self, table, row_id, values): return self._request("PUT", f"{self._t(table)}/rows/{row_id}", {"values": values})
    def delete_row(self, table, row_id): return self._request("DELETE", f"{self._t(table)}/rows/{row_id}")
