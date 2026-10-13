"""REST-сервер (лише стандартна бібліотека). Запуск: python -m tabledb.server"""
import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import storage
from .core import TYPE_NAMES, Database, DBError, NotFoundError, ValidationError

_FILENAME_RE = re.compile(r"^[\w\- ]+$")
WEB_PAGE = os.path.join(os.path.dirname(__file__), "web", "index.html")


class AppState:
    """Одна активна база на сервері + блокування для багатопотоковості."""

    def __init__(self, data_dir: str):
        self.db = Database("untitled")
        self.lock = threading.RLock()
        self.data_dir = data_dir


def _db_path(state: AppState, filename) -> str:
    name = os.path.basename(str(filename or ""))
    if name.endswith(".json"):
        name = name[:-5]
    if not _FILENAME_RE.match(name):
        raise ValidationError("Некоректне ім'я файлу (літери, цифри, '_', '-', пробіл)")
    return os.path.join(state.data_dir, name + ".json")


def _row_id(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise ValidationError(f"Некоректний id рядка: {text!r}") from None


def _rows_payload(table, sort_by, descending):
    return {
        "fields": table.fields,
        "rows": [{"id": i, "values": v} for i, v in table.sorted_rows(sort_by, descending)],
    }


def dispatch(state: AppState, method: str, path: str, query: dict, body: dict):
    """Чиста функція маршрутизації: (статус, відповідь). Зручно тестувати."""
    parts = [unquote(p) for p in path.strip("/").split("/")]
    if parts[0] != "api":
        raise NotFoundError("Невідомий шлях")
    route = tuple(parts[1:])
    with state.lock:
        db = state.db
        match (method, route):
            case ("GET", ("types",)):
                return 200, {"types": TYPE_NAMES}
            case ("GET", ("database",)):
                return 200, {"name": db.name, "tables": list(db.tables)}
            case ("POST", ("database",)):
                state.db = Database(body.get("name"))
                return 201, {"name": state.db.name}
            case ("GET", ("files",)):
                os.makedirs(state.data_dir, exist_ok=True)
                files = sorted(f[:-5] for f in os.listdir(state.data_dir) if f.endswith(".json"))
                return 200, {"files": files}
            case ("POST", ("database", "save")):
                storage.save(db, _db_path(state, body.get("filename")))
                return 200, {"saved": True}
            case ("POST", ("database", "load")):
                state.db = storage.load(_db_path(state, body.get("filename")))
                return 200, {"name": state.db.name, "tables": list(state.db.tables)}
            case ("POST", ("tables",)):
                table = db.create_table(body.get("name"), body.get("fields"))
                return 201, {"name": table.name, "fields": table.fields}
            case ("GET", ("tables", name)):
                t = db.get_table(name)
                return 200, {"name": t.name, "fields": t.fields, "row_count": len(t.rows)}
            case ("DELETE", ("tables", name)):
                db.drop_table(name)
                return 200, {"deleted": True}
            case ("GET", ("tables", name, "rows")):
                desc = query.get("desc", ["0"])[0] in ("1", "true")
                return 200, _rows_payload(db.get_table(name), query.get("sort_by", [None])[0], desc)
            case ("POST", ("tables", name, "rows")):
                return 201, {"id": db.get_table(name).add_row(body.get("values"))}
            case ("PUT", ("tables", name, "rows", rid)):
                db.get_table(name).update_row(_row_id(rid), body.get("values"))
                return 200, {"updated": True}
            case ("DELETE", ("tables", name, "rows", rid)):
                db.get_table(name).delete_row(_row_id(rid))
                return 200, {"deleted": True}
    raise NotFoundError("Невідомий маршрут")


class Handler(BaseHTTPRequestHandler):
    state: AppState  # задається у make_server

    def _send(self, status: int, payload=None):
        data = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        # CORS — щоб на етапі 3 веб-клієнт міг звертатися до цього ж API
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                raise ValidationError("Тіло запиту не є коректним JSON") from None
            if not isinstance(body, dict):
                raise ValidationError("Тіло запиту має бути JSON-об'єктом")
            status, payload = dispatch(self.state, self.command, url.path,
                                       parse_qs(url.query), body)
        except NotFoundError as e:
            status, payload = 404, {"error": str(e)}
        except DBError as e:
            status, payload = 400, {"error": str(e)}
        except Exception as e:  # непередбачена помилка сервера
            status, payload = 500, {"error": f"Внутрішня помилка сервера: {e}"}
        self._send(status, payload)

    def do_GET(self):
        # веб-клієнт (етап 3) віддається тим самим сервером; решта — REST API
        if urlparse(self.path).path in ("/", "/index.html"):
            try:
                with open(WEB_PAGE, "rb") as f:
                    page = f.read()
            except OSError:
                return self._send(404, {"error": "Веб-сторінку не знайдено"})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            return self.wfile.write(page)
        self._handle()

    do_POST = do_PUT = do_DELETE = _handle

    def do_OPTIONS(self):
        self._send(204)

    def log_message(self, *args):  # тихий режим
        pass


def make_server(host: str = "127.0.0.1", port: int = 8000, data_dir: str = "data"):
    state = AppState(data_dir)
    handler = type("BoundHandler", (Handler,), {"state": state})
    return ThreadingHTTPServer((host, port), handler)


def main():
    parser = argparse.ArgumentParser(description="TableDB REST-сервер")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default="data", help="папка для збережених баз")
    args = parser.parse_args()
    server = make_server(args.host, args.port, args.data_dir)
    print(f"TableDB сервер: http://{args.host}:{args.port}  (дані: {args.data_dir})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()