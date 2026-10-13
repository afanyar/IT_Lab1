"""Десктоп-клієнт (Tkinter). Запуск: python -m tabledb.gui

Уся робота з даними йде через сервер (client_api); валідація — на сервері,
GUI лише показує помилки й залишає діалог відкритим, щоб виправити ввід.
"""
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .client_api import ApiClient, ApiError

HINTS = {
    "integer": "ціле число",
    "real": "дійсне число",
    "char": "1 символ",
    "string": "текст",
    "time": "ГГ:ХХ[:СС]",
    "timeInvl": "ГГ:ХХ[:СС]-ГГ:ХХ[:СС]",
}


class TableDialog(tk.Toplevel):
    """Створення таблиці: назва + динамічний список полів."""

    def __init__(self, parent, types, on_submit):
        super().__init__(parent)
        self.title("Нова таблиця")
        self.transient(parent)
        self.types, self.on_submit, self.field_rows = types, on_submit, []
        self.name_var = tk.StringVar()

        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="Назва таблиці:").pack(side="left")
        ttk.Entry(top, textvariable=self.name_var).pack(side="left", padx=4)
        self.fields_frame = ttk.Frame(self)
        self.fields_frame.pack(fill="x", padx=8)
        ttk.Button(self, text="+ Поле", command=self.add_field).pack(anchor="w", padx=8, pady=4)
        buttons = ttk.Frame(self)
        buttons.pack(pady=6)
        ttk.Button(buttons, text="Створити", command=self.ok).pack(side="left", padx=4)
        ttk.Button(buttons, text="Скасувати", command=self.destroy).pack(side="left")
        self.add_field()
        self.grab_set()

    def add_field(self):
        frame = ttk.Frame(self.fields_frame)
        frame.pack(fill="x", pady=1)
        name = tk.StringVar()
        ftype = tk.StringVar(value=self.types[0])
        ttk.Entry(frame, textvariable=name, width=20).pack(side="left")
        ttk.Combobox(frame, textvariable=ftype, values=self.types, state="readonly",
                     width=10).pack(side="left", padx=4)
        item = (frame, name, ftype)
        ttk.Button(frame, text="✕", width=3,
                   command=lambda: self.remove_field(item)).pack(side="left")
        self.field_rows.append(item)

    def remove_field(self, item):
        item[0].destroy()
        self.field_rows.remove(item)

    def ok(self):
        fields = [{"name": n.get().strip(), "type": t.get()} for _, n, t in self.field_rows]
        if self.on_submit(self.name_var.get().strip(), fields):
            self.destroy()


class RowDialog(tk.Toplevel):
    """Додавання / редагування рядка: по одному полю вводу на кожне поле таблиці."""

    def __init__(self, parent, title, fields, initial, on_submit):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.on_submit, self.vars = on_submit, {}
        form = ttk.Frame(self)
        form.pack(padx=8, pady=6)
        for r, f in enumerate(fields):
            ttk.Label(form, text=f"{f['name']} ({f['type']})").grid(row=r, column=0, sticky="w")
            var = tk.StringVar(value=initial.get(f["name"], ""))
            ttk.Entry(form, textvariable=var, width=28).grid(row=r, column=1, padx=4, pady=2)
            ttk.Label(form, text=HINTS[f["type"]], foreground="gray").grid(row=r, column=2, sticky="w")
            self.vars[f["name"]] = var
        buttons = ttk.Frame(self)
        buttons.pack(pady=6)
        ttk.Button(buttons, text="OK", command=self.ok).pack(side="left", padx=4)
        ttk.Button(buttons, text="Скасувати", command=self.destroy).pack(side="left")
        self.grab_set()

    def ok(self):
        if self.on_submit({name: var.get() for name, var in self.vars.items()}):
            self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TableDB — клієнт")
        self.geometry("960x520")
        self.api = ApiClient()
        self.table = None          # поточна таблиця
        self.fields = []           # поля поточної таблиці
        self.rows = {}             # id -> values (кеш для редагування)
        self.sort_by, self.desc = None, False
        self._build()

    # ---------------------------------------------------------------- вигляд
    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=6, pady=4)
        ttk.Label(top, text="Сервер:").pack(side="left")
        self.url_var = tk.StringVar(value="http://127.0.0.1:8000")
        ttk.Entry(top, textvariable=self.url_var, width=26).pack(side="left", padx=4)
        ttk.Button(top, text="Підключитися", command=self.connect).pack(side="left")
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        for text, cmd in (("Нова БД", self.new_db), ("Зберегти", self.save_db),
                          ("Завантажити", self.load_db)):
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=2)
        self.db_label = ttk.Label(top, text="")
        self.db_label.pack(side="right")

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6)

        left = ttk.Frame(body)
        body.add(left, weight=1)
        ttk.Label(left, text="Таблиці").pack(anchor="w")
        self.table_list = tk.Listbox(left, exportselection=False)
        self.table_list.pack(fill="both", expand=True)
        self.table_list.bind("<<ListboxSelect>>", self.on_table_select)
        lbtn = ttk.Frame(left)
        lbtn.pack(fill="x", pady=4)
        ttk.Button(lbtn, text="Створити", command=self.create_table).pack(side="left", padx=2)
        ttk.Button(lbtn, text="Видалити", command=self.drop_table).pack(side="left", padx=2)

        right = ttk.Frame(body)
        body.add(right, weight=4)
        self.tree = ttk.Treeview(right, show="headings", selectmode="browse")
        yscroll = ttk.Scrollbar(right, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(right, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        rbtn = ttk.Frame(right)
        rbtn.grid(row=2, column=0, sticky="w", pady=4)
        ttk.Button(rbtn, text="Додати рядок", command=self.add_row).pack(side="left", padx=2)
        ttk.Button(rbtn, text="Редагувати", command=self.edit_row).pack(side="left", padx=2)
        ttk.Button(rbtn, text="Видалити рядок", command=self.delete_row).pack(side="left", padx=2)
        ttk.Label(rbtn, text="  Сортування: клік по заголовку стовпця",
                  foreground="gray").pack(side="left")

        self.status = ttk.Label(self, anchor="w", relief="sunken",
                                text="Запустіть сервер і натисніть «Підключитися»")
        self.status.pack(fill="x", side="bottom")

    def _call(self, fn, *args, **kwargs):
        """Виклик API: (True, результат) або (False, None) з показом помилки."""
        try:
            return True, fn(*args, **kwargs)
        except ApiError as e:
            messagebox.showerror("Помилка", str(e), parent=self)
            return False, None

    # ------------------------------------------------------------ синхронізація
    def connect(self):
        self.api.base_url = self.url_var.get().strip().rstrip("/")
        self.refresh()

    def refresh(self):
        ok, info = self._call(self.api.database_info)
        if not ok:
            return
        self.db_label.config(text=f"БД: {info['name']}")
        self.table_list.delete(0, "end")
        for name in info["tables"]:
            self.table_list.insert("end", name)
        if self.table not in info["tables"]:
            self.table, self.sort_by = None, None
        elif self.table:
            self.table_list.selection_set(info["tables"].index(self.table))
        self.load_rows()
        self.status.config(text=f"Підключено до {self.api.base_url}")

    def load_rows(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        if not self.table:
            self.tree["columns"] = ()
            return
        ok, data = self._call(self.api.get_rows, self.table, self.sort_by, self.desc)
        if not ok:
            return
        self.fields = data["fields"]
        names = [f["name"] for f in self.fields]
        self.tree["columns"] = names
        for f in self.fields:
            arrow = ""
            if f["name"] == self.sort_by:
                arrow = " ▼" if self.desc else " ▲"
            self.tree.heading(f["name"], text=f"{f['name']} [{f['type']}]{arrow}",
                              command=lambda n=f["name"]: self.sort_click(n))
            self.tree.column(f["name"], width=130, anchor="w")
        self.rows = {r["id"]: r["values"] for r in data["rows"]}
        for r in data["rows"]:
            self.tree.insert("", "end", iid=str(r["id"]),
                             values=[r["values"][n] for n in names])
        self.status.config(text=f"Таблиця {self.table}: рядків — {len(self.rows)}")

    # --------------------------------------------------------------------- БД
    def new_db(self):
        name = simpledialog.askstring("Нова база", "Назва бази:", parent=self)
        if name:
            ok, _ = self._call(self.api.new_database, name.strip())
            if ok:
                self.table = None
                self.refresh()

    def save_db(self):
        name = simpledialog.askstring("Зберегти", "Ім'я файлу на сервері:", parent=self)
        if name:
            ok, _ = self._call(self.api.save_database, name.strip())
            if ok:
                self.status.config(text=f"Збережено: {name.strip()}")

    def load_db(self):
        ok, files = self._call(self.api.list_files)
        if not ok:
            return
        if not files:
            messagebox.showinfo("Завантажити", "На сервері немає збережених баз", parent=self)
            return
        name = simpledialog.askstring("Завантажити",
                                      "Доступні: " + ", ".join(files) + "\nІм'я файлу:", parent=self)
        if name:
            ok, _ = self._call(self.api.load_database, name.strip())
            if ok:
                self.table = None
                self.refresh()

    # ----------------------------------------------------------------- таблиці
    def on_table_select(self, _event):
        sel = self.table_list.curselection()
        if sel:
            self.table = self.table_list.get(sel[0])
            self.sort_by, self.desc = None, False
            self.load_rows()

    def create_table(self):
        ok, types = self._call(self.api.types)
        if not ok:
            return

        def submit(name, fields):
            ok, _ = self._call(self.api.create_table, name, fields)
            if ok:
                self.table, self.sort_by = name, None
                self.refresh()
            return ok

        TableDialog(self, types, submit)

    def drop_table(self):
        if self.table and messagebox.askyesno(
                "Видалення", f"Видалити таблицю «{self.table}»?", parent=self):
            ok, _ = self._call(self.api.drop_table, self.table)
            if ok:
                self.table = None
                self.refresh()

    # ------------------------------------------------------------------ рядки
    def _selected_id(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Рядок", "Спочатку виберіть рядок", parent=self)
            return None
        return int(sel[0])

    def add_row(self):
        if not self.table:
            return

        def submit(values):
            ok, _ = self._call(self.api.add_row, self.table, values)
            if ok:
                self.load_rows()
            return ok

        RowDialog(self, "Новий рядок", self.fields, {}, submit)

    def edit_row(self):
        row_id = self._selected_id() if self.table else None
        if row_id is None:
            return

        def submit(values):
            ok, _ = self._call(self.api.update_row, self.table, row_id, values)
            if ok:
                self.load_rows()
            return ok

        initial = {k: str(v) for k, v in self.rows[row_id].items()}
        RowDialog(self, "Редагування рядка", self.fields, initial, submit)

    def delete_row(self):
        row_id = self._selected_id() if self.table else None
        if row_id is not None and messagebox.askyesno("Видалення", "Видалити рядок?", parent=self):
            ok, _ = self._call(self.api.delete_row, self.table, row_id)
            if ok:
                self.load_rows()

    # -------------------------------------------------------------- сортування
    def sort_click(self, field):
        self.desc = (not self.desc) if self.sort_by == field else False
        self.sort_by = field
        self.load_rows()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
