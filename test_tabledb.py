import os
import tempfile
import threading
import unittest

from tabledb import storage
from tabledb.client_api import ApiClient, ApiError
from tabledb.core import Database, DBError, NotFoundError, ValidationError
from tabledb.server import make_server

ALL_FIELDS = [
    {"name": "title", "type": "string"},
    {"name": "n", "type": "integer"},
    {"name": "price", "type": "real"},
    {"name": "grade", "type": "char"},
    {"name": "start", "type": "time"},
    {"name": "slot", "type": "timeInvl"},
]


def row(title="a", n=1, price=1.0, grade="A", start="10:00", slot="10:00-11:00"):
    return {"title": title, "n": n, "price": price, "grade": grade, "start": start, "slot": slot}


def make_table():
    return Database("test").create_table("t", ALL_FIELDS)


class TestTypes(unittest.TestCase):
    """Тест 1: валідація значень усіх шести типів."""

    def test_valid_values_are_normalized(self):
        t = make_table()
        rid = t.add_row(row(n="42", price="1,5", start="9:05", slot="9:00-10:30"))
        self.assertEqual(t.rows[rid]["n"], 42)
        self.assertEqual(t.rows[rid]["price"], 1.5)
        self.assertEqual(t.rows[rid]["start"], "09:05:00")
        self.assertEqual(t.rows[rid]["slot"], "09:00:00-10:30:00")

    def test_invalid_values_are_rejected(self):
        t = make_table()
        bad = [
            {"n": "abc"}, {"n": "1.5"}, {"price": "x"}, {"price": "nan"},
            {"grade": "ab"}, {"grade": ""},
            {"start": "24:00"}, {"start": "10:61"}, {"start": "ten"},
            {"slot": "10:00"}, {"slot": "11:00-10:00"}, {"slot": "10:00-25:00"},
        ]
        for patch in bad:
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                t.add_row({**row(), **patch})
        self.assertEqual(t.rows, {})  # жодного некоректного рядка не збережено


class TestTablesAndRows(unittest.TestCase):
    """Тест 2: створення/знищення таблиць, CRUD рядків."""

    def test_create_and_drop_table_validation(self):
        db = Database("db")
        db.create_table("a", [{"name": "x", "type": "integer"}])
        bad_specs = [
            ("a", [{"name": "x", "type": "integer"}]),        # дублікат таблиці
            ("b", []),                                         # немає полів
            ("b", [{"name": "x", "type": "blob"}]),            # невідомий тип
            ("b", [{"name": "x", "type": "char"}, {"name": "x", "type": "char"}]),  # дубль поля
            ("1b", [{"name": "x", "type": "char"}]),           # некоректна назва
        ]
        for name, fields in bad_specs:
            with self.subTest(name=name, fields=fields), self.assertRaises(ValidationError):
                db.create_table(name, fields)
        db.drop_table("a")
        self.assertEqual(db.tables, {})
        with self.assertRaises(NotFoundError):
            db.drop_table("a")

    def test_row_crud(self):
        t = make_table()
        r1 = t.add_row(row(title="x"))
        t.update_row(r1, {"n": "7"})
        self.assertEqual(t.rows[r1]["n"], 7)
        self.assertEqual(t.rows[r1]["title"], "x")  # інші поля не змінились
        with self.assertRaises(ValidationError):
            t.update_row(r1, {"n": "oops"})
        self.assertEqual(t.rows[r1]["n"], 7)        # невдале редагування нічого не змінило
        with self.assertRaises(ValidationError):
            t.add_row({"title": "only"})            # не всі поля
        t.delete_row(r1)
        with self.assertRaises(NotFoundError):
            t.get_row(r1)


class TestSortByField(unittest.TestCase):
    """Тест 3: сортування за одним полем."""

    def setUp(self):
        self.t = make_table()

    def order(self, field, desc=False):
        return [v[field] for _, v in self.t.sorted_rows(field, desc)]

    def test_integer_is_numeric_not_lexicographic(self):
        for n in (10, 2, 33):
            self.t.add_row(row(n=n))
        self.assertEqual(self.order("n"), [2, 10, 33])
        self.assertEqual(self.order("n", desc=True), [33, 10, 2])

    def test_real_and_string_and_char(self):
        for p, s, g in [(100.0, "b", "z"), (-1.5, "a", "B"), (0.5, "c", "a")]:
            self.t.add_row(row(price=p, title=s, grade=g))
        self.assertEqual(self.order("price"), [-1.5, 0.5, 100.0])
        self.assertEqual(self.order("title"), ["a", "b", "c"])
        self.assertEqual(self.order("grade"), ["B", "a", "z"])

    def test_time_and_time_interval(self):
        for start, slot in [("23:00", "20:00-22:00"), ("07:30", "08:00-12:00"),
                            ("12:00", "08:00-09:00")]:
            self.t.add_row(row(start=start, slot=slot))
        self.assertEqual(self.order("start"), ["07:30:00", "12:00:00", "23:00:00"])
        # інтервали: спочатку за початком, потім за кінцем
        self.assertEqual(self.order("slot"),
                         ["08:00:00-09:00:00", "08:00:00-12:00:00", "20:00:00-22:00:00"])

    def test_stable_and_errors(self):
        a = self.t.add_row(row(n=1, title="first"))
        b = self.t.add_row(row(n=1, title="second"))
        self.assertEqual([i for i, _ in self.t.sorted_rows("n")], [a, b])
        self.assertEqual([i for i, _ in self.t.sorted_rows("n", True)], [a, b])
        with self.assertRaises(ValidationError):
            self.t.sorted_rows("missing")
        before = list(self.t.rows)
        self.t.sorted_rows("n")                      # сортування не змінює таблицю
        self.assertEqual(list(self.t.rows), before)


class TestStorage(unittest.TestCase):
    """Тест 4: збереження на диск і зчитування."""

    def test_roundtrip_and_corruption(self):
        db = Database("lib")
        t = db.create_table("t", ALL_FIELDS)
        t.add_row(row(title="Привіт", start="9:00"))
        r2 = t.add_row(row(title="x"))
        t.delete_row(r2)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lib.json")
            storage.save(db, path)
            loaded = storage.load(path)
            self.assertEqual(loaded.to_dict(), db.to_dict())
            self.assertEqual(loaded.get_table("t").add_row(row()), 3)  # id не повторюються
            with open(path, "w", encoding="utf-8") as f:
                f.write("{не json")
            with self.assertRaises(DBError):
                storage.load(path)
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"name":"x","tables":[{"name":"t","fields":[{"name":"a","type":"integer"}],'
                        '"next_id":2,"rows":[{"id":1,"values":{"a":"bad"}}]}]}')
            with self.assertRaises(DBError):
                storage.load(path)
            with self.assertRaises(DBError):
                storage.load(os.path.join(d, "none.json"))


class TestClientServer(unittest.TestCase):
    """Тест 5: десктоп-клієнт ↔ віддалений сервер (інтеграційний)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server("127.0.0.1", 0, self.tmp.name)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.api = ApiClient(f"http://127.0.0.1:{self.server.server_address[1]}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_full_flow(self):
        api = self.api
        self.assertIn("timeInvl", api.types())
        api.new_database("demo")
        api.create_table("events", [{"name": "title", "type": "string"},
                                    {"name": "slot", "type": "timeInvl"}])
        api.add_row("events", {"title": "B", "slot": "12:00-13:00"})
        rid = api.add_row("events", {"title": "A", "slot": "9:00-10:00"})
        data = api.get_rows("events", "slot")
        self.assertEqual([r["values"]["title"] for r in data["rows"]], ["A", "B"])
        api.update_row("events", rid, {"title": "Z"})
        data = api.get_rows("events", "title", descending=True)
        self.assertEqual([r["values"]["title"] for r in data["rows"]], ["Z", "B"])
        with self.assertRaises(ApiError):
            api.add_row("events", {"title": "bad", "slot": "13:00-12:00"})
        api.save_database("demo")
        api.new_database("other")
        self.assertEqual(api.database_info()["tables"], [])
        self.assertIn("demo", api.list_files())
        self.assertEqual(api.load_database("demo")["tables"], ["events"])
        api.delete_row("events", rid)
        api.drop_table("events")
        with self.assertRaises(ApiError):
            api.get_rows("events")
        with self.assertRaises(ApiError):
            api.save_database("bad*name")            # недопустимі символи
        api.save_database("../escape")               # шлях відсікається: файл лише в data_dir
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "escape.json")))


if __name__ == "__main__":
    unittest.main()
