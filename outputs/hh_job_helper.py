"""Минимальный Windows GUI для поиска вакансий на публичном API hh.ru.

Запуск: py hh_job_helper.py (Python 3.10+, стандартная библиотека).
Публичный поиск не требует токена. Telegram и автоотклики пока не активны.
"""
import json
import threading
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import ctypes
from ctypes import wintypes

APP_DIR = Path.home() / "AppData" / "Local" / "HHJobHelper"
SETTINGS = APP_DIR / "settings.json"
UA = "HHJobHelper/0.1 (personal job search prototype)"


def fetch_vacancies(params):
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request("https://api.hh.ru/vacancies?" + query,
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def credential_write(target, secret):
    """Store a secret in Windows Credential Manager (Generic Credential)."""
    if not secret:
        return
    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]
    blob = secret.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL(0, 1, target, None, wintypes.FILETIME(0, 0), len(blob),
                      ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)), 2, 0, None, None, "HH Job Helper")
    if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(ctypes.get_last_error(), "Не удалось сохранить секрет в Windows Credential Manager")


def credential_read(target):
    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]
    ptr = ctypes.POINTER(CREDENTIAL)()
    if not ctypes.windll.advapi32.CredReadW(target, 1, 0, ctypes.byref(ptr)):
        return ""
    try:
        c = ptr.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
    finally:
        ctypes.windll.advapi32.CredFree(ptr)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Помощник поиска работы")
        self.geometry("1120x680")
        self.minsize(850, 520)
        self.configure(bg="#f4f6f8")
        self._build()
        self._load()

    def _build(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", rowheight=42, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        top = ttk.Frame(self, padding=(18, 14)); top.pack(fill="x")
        ttk.Label(top, text="Помощник поиска работы", font=("Segoe UI", 18, "bold")).pack(side="left")
        self.state = tk.StringVar(value="Публичный поиск hh.ru · автоотклики выключены")
        ttk.Label(top, textvariable=self.state).pack(side="right")
        body = ttk.Frame(self, padding=(18, 4, 18, 16)); body.pack(fill="both", expand=True)
        side = ttk.LabelFrame(body, text="Параметры поиска", padding=8); side.pack(side="left", fill="y", padx=(0, 14))
        side.configure(width=310); side.pack_propagate(False)
        scroller = tk.Canvas(side, width=280, highlightthickness=0, bg="#f4f6f8")
        scrollbar = ttk.Scrollbar(side, orient="vertical", command=scroller.yview)
        scroller.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y"); scroller.pack(side="left", fill="both", expand=True)
        form = ttk.Frame(scroller, padding=6); form_id = scroller.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _e: scroller.configure(scrollregion=scroller.bbox("all")))
        scroller.bind("<Configure>", lambda e: scroller.itemconfigure(form_id, width=e.width))
        scroller.bind_all("<MouseWheel>", lambda e: scroller.yview_scroll(int(-e.delta / 120), "units"))
        self.role = self._entry(form, "Должность / ключевые слова", "")
        self.area = self._entry(form, "Город hh.ru", "Москва")
        self.salary = self._entry(form, "Зарплата от, ₽ (необязательно)", "")
        ttk.Label(form, text="Формат работы").pack(anchor="w", pady=(9, 3))
        self.remote = tk.BooleanVar(value=True); self.hybrid = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="Удалённо", variable=self.remote).pack(anchor="w")
        ttk.Checkbutton(form, text="Гибрид", variable=self.hybrid).pack(anchor="w")
        ttk.Label(form, text="Интервал автоматического поиска").pack(anchor="w", pady=(12, 3))
        self.interval = tk.StringVar(value="60")
        ttk.Combobox(form, textvariable=self.interval, values=("30", "60", "120", "360", "1440"),
                     state="readonly", width=23).pack(fill="x")
        ttk.Label(form, text="минут", foreground="#718092").pack(anchor="w")
        ttk.Button(form, text="Искать сейчас", command=self.search).pack(fill="x", pady=(18, 6))
        ttk.Button(form, text="Сохранить настройки", command=self.save).pack(fill="x")
        ttk.Label(form, text="Telegram: настройка будет добавлена\nпосле получения токена бота.",
                  foreground="#718092", wraplength=220).pack(anchor="w", pady=(16, 0))
        connect = ttk.LabelFrame(form, text="Подключения (локально)", padding=10)
        connect.pack(fill="x", pady=(16, 0))
        ttk.Label(connect, text="Telegram bot token").pack(anchor="w")
        self.tg_token = ttk.Entry(connect, show="•", width=25); self._enable_paste(self.tg_token); self.tg_token.pack(fill="x", pady=(3, 8))
        ttk.Label(connect, text="hh.ru OAuth Client ID").pack(anchor="w")
        self.hh_client = ttk.Entry(connect, width=25); self._enable_paste(self.hh_client); self.hh_client.pack(fill="x", pady=(3, 8))
        ttk.Label(connect, text="hh.ru OAuth Client Secret").pack(anchor="w")
        self.hh_secret = ttk.Entry(connect, show="•", width=25); self._enable_paste(self.hh_secret); self.hh_secret.pack(fill="x", pady=(3, 8))
        ttk.Button(connect, text="Сохранить подключения", command=self.save_connections).pack(fill="x")
        right = ttk.Frame(body); right.pack(side="left", fill="both", expand=True)
        cards = ttk.Frame(right); cards.pack(fill="x", pady=(0, 12))
        self.count = tk.StringVar(value="0 вакансий")
        ttk.Label(cards, textvariable=self.count, font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Label(cards, text="Порог соответствия резюме будет подключён после OAuth",
                  foreground="#718092").pack(side="right")
        columns = ("job", "salary", "format", "company", "published")
        self.table = ttk.Treeview(right, columns=columns, show="headings")
        for col, title, width in (("job", "Вакансия", 240), ("salary", "Зарплата", 130),
                                  ("format", "Место / формат", 145), ("company", "Компания", 180),
                                  ("published", "Опубликована", 120)):
            self.table.heading(col, text=title); self.table.column(col, width=width, anchor="w")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<Double-1>", self.open_job)
        self.note = tk.StringVar(value="Дважды щёлкните вакансию, чтобы открыть её на hh.ru.")
        ttk.Label(right, textvariable=self.note, foreground="#718092").pack(anchor="w", pady=(8, 0))

    def _entry(self, parent, label, default):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(8, 3))
        e = ttk.Entry(parent, width=28); self._enable_paste(e); e.insert(0, default); e.pack(fill="x")
        return e

    def _enable_paste(self, widget):
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Вырезать", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="Копировать", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Вставить", command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: widget.selection_range(0, "end"))
        widget.bind("<Button-3>", lambda event: (widget.focus_set(), menu.tk_popup(event.x_root, event.y_root)))
        widget.bind("<Control-v>", lambda _event: (widget.event_generate("<<Paste>>"), "break"))
        widget.bind("<Control-V>", lambda _event: (widget.event_generate("<<Paste>>"), "break"))

    def _load(self):
        try:
            data = json.loads(SETTINGS.read_text(encoding="utf-8"))
            self.role.insert(0, data.get("text", "")); self.area.delete(0, "end"); self.area.insert(0, data.get("area", "Москва"))
            self.salary.insert(0, data.get("salary", "")); self.interval.set(str(data.get("interval", 60)))
            self.remote.set(data.get("remote", True)); self.hybrid.set(data.get("hybrid", True))
        except (OSError, ValueError):
            pass
        try:
            self.tg_token.insert(0, credential_read("HHJobHelper/telegram_bot_token"))
            self.hh_secret.insert(0, credential_read("HHJobHelper/hh_client_secret"))
        except Exception:
            pass
        try:
            creds = json.loads(SETTINGS.read_text(encoding="utf-8"))
            self.hh_client.insert(0, creds.get("hh_client_id", ""))
        except (OSError, ValueError):
            pass

    def save(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        data = {"text": self.role.get(), "area": self.area.get(), "salary": self.salary.get(),
                "interval": self.interval.get(), "remote": self.remote.get(), "hybrid": self.hybrid.get()}
        SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.note.set("Настройки сохранены на этом компьютере.")

    def save_connections(self):
        try:
            credential_write("HHJobHelper/telegram_bot_token", self.tg_token.get().strip())
            credential_write("HHJobHelper/hh_client_secret", self.hh_secret.get().strip())
            APP_DIR.mkdir(parents=True, exist_ok=True)
            try:
                data = json.loads(SETTINGS.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            data["hh_client_id"] = self.hh_client.get().strip()
            SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.note.set("Подключения сохранены. Секреты хранятся в Windows Credential Manager.")
            messagebox.showinfo("Подключения", "Данные сохранены на этом компьютере.")
        except Exception as exc:
            messagebox.showerror("Не удалось сохранить", str(exc))

    def search(self):
        self.save()
        self.state.set("Ищу вакансии на hh.ru…")
        threading.Thread(target=self._do_search, daemon=True).start()

    def _do_search(self):
        try:
            params = {"text": self.role.get().strip(), "area": "1", "per_page": "50", "page": "0",
                      "order_by": "publication_time", "host": "hh.ru"}
            salary = self.salary.get().strip()
            if salary.isdigit(): params["salary"] = salary; params["currency"] = "RUR"
            if self.remote.get(): params["schedule"] = "remote"
            data = fetch_vacancies(params)
            self.after(0, lambda: self._show(data.get("items", [])))
        except Exception as exc:
            self.after(0, lambda: self._error(str(exc)))

    def _show(self, items):
        self.table.delete(*self.table.get_children())
        for item in items:
            salary = item.get("salary") or {}
            pay = "Не указана"
            if salary:
                lo, hi, cur = salary.get("from"), salary.get("to"), salary.get("currency", "")
                pay = f"{lo or '—'}–{hi or '—'} {cur}"
            area = (item.get("area") or {}).get("name", "")
            schedule = (item.get("schedule") or {}).get("name", "")
            employer = (item.get("employer") or {}).get("name", "")
            pub = (item.get("published_at") or "")[:10]
            self.table.insert("", "end", values=(item.get("name", ""), pay, f"{area} · {schedule}", employer, pub), tags=(item.get("alternate_url", ""),))
        self.count.set(f"{len(items)} вакансий")
        self.state.set("Поиск завершён · реальная выдача hh.ru")
        self.note.set("Отклики не отправлялись. Дважды щёлкните строку для просмотра вакансии.")

    def _error(self, message):
        self.state.set("Не удалось выполнить поиск")
        messagebox.showerror("Ошибка API hh.ru", message)

    def open_job(self, _event):
        import webbrowser
        row = self.table.selection()
        if row:
            url = self.table.item(row[0], "tags")
            if url: webbrowser.open(url[0])


if __name__ == "__main__":
    App().mainloop()
