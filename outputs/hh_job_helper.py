"""Минимальный Windows GUI для поиска вакансий на публичном API hh.ru.

Запуск: py hh_job_helper.py (Python 3.10+, стандартная библиотека).
Публичный поиск не требует токена. Telegram и автоотклики пока не активны.
"""
import json
import threading
import urllib.parse
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import ctypes
from ctypes import wintypes
import secrets
import hashlib
import base64
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

APP_DIR = Path.home() / "AppData" / "Local" / "HHJobHelper"
SETTINGS = APP_DIR / "settings.json"
UA = "HHJobHelper/0.1 (personal job search prototype)"


def fetch_vacancies(params):
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request("https://api.hh.ru/vacancies?" + query,
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def find_area_id(name):
    """Resolve a Russian city name to the area ID used by the HH search API."""
    req = urllib.request.Request("https://api.hh.ru/areas",
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        tree = json.loads(response.read().decode("utf-8"))
    wanted = name.strip().casefold()
    def walk(nodes):
        for node in nodes:
            if node.get("name", "").casefold() == wanted:
                return node.get("id")
            found = walk(node.get("areas", []))
            if found:
                return found
        return None
    return walk(tree)


def telegram_call(token, method, payload=None):
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=body,
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Ошибка Telegram API"))
    return result.get("result")


def hh_request(path, token):
    req = urllib.request.Request("https://api.hh.ru" + path,
                                 headers={"User-Agent": UA, "Accept": "application/json",
                                          "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            details = json.loads(raw)
            reason = details.get("description") or details.get("errors") or details.get("oauth_error") or raw
        except ValueError:
            reason = raw
        raise RuntimeError(f"HTTP {exc.code}: {reason}") from None


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
        self.after(2000, self._schedule_search)

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
        ttk.Button(form, text="Загрузить моё резюме из hh.ru", command=self.load_resume).pack(fill="x", pady=(6, 0))
        self.resume_status = tk.StringVar(value="Резюме hh.ru не загружено")
        ttk.Label(form, textvariable=self.resume_status, foreground="#536273", wraplength=235).pack(anchor="w", pady=(5, 0))
        ttk.Label(form, text="Для связи с ботом сначала отправьте ему /start.",
                  foreground="#718092", wraplength=220).pack(anchor="w", pady=(16, 0))
        connect = ttk.LabelFrame(form, text="Подключения (локально)", padding=10)
        connect.pack(fill="x", pady=(16, 0))
        ttk.Label(connect, text="Telegram bot token").pack(anchor="w")
        self.tg_token = ttk.Entry(connect, show="•", width=25); self._enable_paste(self.tg_token); self.tg_token.pack(fill="x", pady=(3, 8))
        ttk.Label(connect, text="hh.ru OAuth Client ID").pack(anchor="w")
        self.hh_client = ttk.Entry(connect, width=25); self._enable_paste(self.hh_client); self.hh_client.pack(fill="x", pady=(3, 8))
        ttk.Label(connect, text="hh.ru OAuth Client Secret").pack(anchor="w")
        self.hh_secret = ttk.Entry(connect, show="•", width=25); self._enable_paste(self.hh_secret); self.hh_secret.pack(fill="x", pady=(3, 8))
        ttk.Button(connect, text="Сохранить подключения", command=self.save_connections).pack(fill="x", pady=(0, 4))
        ttk.Button(connect, text="Подключить / проверить Telegram", command=self.connect_telegram).pack(fill="x")
        ttk.Button(connect, text="Войти через hh.ru", command=self.connect_hh).pack(fill="x", pady=(4, 0))
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
        # ttk.Entry already implements Ctrl+V. Do not synthesize <<Paste>> here:
        # allowing both this handler and Tk's class binding duplicated pasted text.

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
        self.chat_id = self._read_settings().get("telegram_chat_id")
        self.resume_status.set(self._read_settings().get("resume_name", "Резюме hh.ru не загружено"))

    def _read_settings(self):
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def connect_hh(self):
        client_id = self.hh_client.get().strip()
        client_secret = self.hh_secret.get().strip()
        if not client_id or not client_secret:
            messagebox.showwarning("hh.ru", "Заполните Client ID и Client Secret, затем сохраните подключения.")
            return
        self.save_connections()
        redirect_uri = "http://localhost:8765/oauth/callback"
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(24)
        app = self
        class Callback(BaseHTTPRequestHandler):
            def do_GET(self):
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if query.get("state", [None])[0] != state:
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write("<meta charset='utf-8'><h2>Проверка OAuth не пройдена</h2><p>Параметр state не совпал. Закройте приложение и начните вход заново из его окна.</p>".encode("utf-8"))
                    return
                code = query.get("code", [None])[0]
                if not code:
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write("<meta charset='utf-8'><h2>hh.ru не вернул код авторизации</h2><p>Вернитесь в приложение и начните вход заново.</p>".encode("utf-8"))
                    return
                try:
                    payload = urllib.parse.urlencode({"grant_type":"authorization_code", "client_id":client_id,
                        "client_secret":client_secret, "code":code, "redirect_uri":redirect_uri,
                        "code_verifier":verifier}).encode()
                    req = urllib.request.Request("https://api.hh.ru/token", data=payload,
                        headers={"Content-Type":"application/x-www-form-urlencoded", "User-Agent":UA})
                    with urllib.request.urlopen(req, timeout=20) as response:
                        tokens = json.loads(response.read().decode("utf-8"))
                    who = hh_request("/me", tokens["access_token"])
                    if who.get("is_applicant") is False and who.get("auth_type") != "applicant":
                        raise ValueError("Выполнен вход не как соискатель hh.ru.")
                    credential_write("HHJobHelper/hh_access_token", tokens["access_token"])
                    credential_write("HHJobHelper/hh_refresh_token", tokens.get("refresh_token", ""))
                    app.hh_identity = who
                    self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
                    self.wfile.write("<h2>hh.ru подключён. Можно закрыть это окно.</h2>".encode())
                    app.after(0, lambda: app.note.set("hh.ru подключён: авторизация соискателя подтверждена."))
                except Exception as exc:
                    message = str(exc)
                    self.send_response(500); self.end_headers(); self.wfile.write(message.encode("utf-8", "replace"))
                    app.after(0, lambda: app.note.set("Ошибка авторизации hh.ru: " + message))
            def log_message(self, *_args):
                return
        try:
            server = HTTPServer(("localhost", 8765), Callback)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            params = urllib.parse.urlencode({"response_type":"code", "client_id":client_id, "state":state,
                "redirect_uri":redirect_uri, "code_challenge":challenge, "code_challenge_method":"S256"})
            webbrowser.open("https://hh.ru/oauth/authorize?" + params)
            self.note.set("Завершите вход и разрешите доступ в открывшемся окне hh.ru.")
        except Exception as exc:
            messagebox.showerror("hh.ru", f"Не удалось запустить OAuth callback: {exc}")

    def load_resume(self):
        token = credential_read("HHJobHelper/hh_access_token")
        if not token:
            messagebox.showwarning("hh.ru", "Сначала выполните вход через hh.ru.")
            return
        self.resume_status.set("Загружаю список резюме…")
        threading.Thread(target=self._load_resume_worker, args=(token,), daemon=True).start()

    def _load_resume_worker(self, token):
        try:
            result = hh_request("/resumes/mine", token)
            resumes = result.get("items", []) if isinstance(result, dict) else result
            if not resumes:
                raise ValueError("hh.ru не вернул доступных резюме.")
            resume = resumes[0]
            resume_id = str(resume.get("id", ""))
            name = resume.get("title") or resume.get("name") or "Резюме hh.ru"
            if not resume_id:
                raise ValueError("В ответе hh.ru не найден ID резюме.")
            settings = self._read_settings()
            settings["resume_id"] = resume_id
            settings["resume_name"] = name
            SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
            self.after(0, lambda: self.resume_status.set(f"Выбрано: {name}"))
            self.after(0, lambda: self.note.set("Резюме загружено. Его можно использовать для дальнейшего сопоставления вакансий."))
        except Exception as exc:
            message = str(exc)
            self.after(0, lambda: self.resume_status.set("Не удалось загрузить резюме"))
            self.after(0, lambda: self.note.set("Ошибка загрузки резюме: " + message))

    def connect_telegram(self):
        token = self.tg_token.get().strip()
        if not token:
            messagebox.showwarning("Telegram", "Вставьте токен бота и сохраните подключения.")
            return
        self.save_connections()
        self.note.set("Проверяю Telegram. Убедитесь, что уже отправили боту /start.")
        threading.Thread(target=self._connect_telegram_worker, args=(token,), daemon=True).start()

    def _connect_telegram_worker(self, token):
        try:
            bot = telegram_call(token, "getMe")
            updates = telegram_call(token, "getUpdates", {"limit": 100, "allowed_updates": ["message"]})
            chats = [u.get("message", {}).get("chat", {}) for u in updates
                     if u.get("message", {}).get("text", "").startswith("/start")]
            if not chats:
                raise ValueError("Не нашёл /start от пользователя. Откройте бота в Telegram и отправьте /start, затем повторите подключение.")
            chat_id = chats[-1].get("id")
            telegram_call(token, "sendMessage", {"chat_id": chat_id, "text": "Telegram подключён к HH Job Helper. Буду присылать новые вакансии."})
            settings = self._read_settings(); settings["telegram_chat_id"] = chat_id
            SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
            self.chat_id = chat_id
            self.after(0, lambda: self.note.set(f"Telegram подключён: @{bot.get('username', 'бот')}. Тестовое сообщение отправлено."))
        except Exception as exc:
            message = str(exc)
            self.after(0, lambda: self.note.set(f"Не удалось подключить Telegram: {message}"))

    def save(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        data = self._read_settings()
        data.update({"text": self.role.get(), "area": self.area.get(), "salary": self.salary.get(),
                     "interval": self.interval.get(), "remote": self.remote.get(), "hybrid": self.hybrid.get()})
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
            city = self.area.get().strip() or "Москва"
            area_id = find_area_id(city)
            if not area_id:
                raise ValueError(f"Не нашёл город «{city}» в справочнике hh.ru")
            params = {"text": self.role.get().strip(), "area": area_id, "per_page": "50", "page": "0",
                      "order_by": "publication_time", "host": "hh.ru"}
            salary = self.salary.get().strip()
            if salary.isdigit(): params["salary"] = salary; params["currency"] = "RUR"
            if self.remote.get() and not self.hybrid.get(): params["schedule"] = "remote"
            data = fetch_vacancies(params)
            self.after(0, lambda: self._show(data.get("items", [])))
        except Exception as exc:
            self.after(0, lambda: self._error(str(exc)))

    def _show(self, items):
        previous = set(self._read_settings().get("seen_vacancies", []))
        current_ids = {str(item.get("id")) for item in items if item.get("id")}
        new_items = [item for item in items if str(item.get("id")) not in previous]
        if previous and new_items and self.chat_id:
            threading.Thread(target=self._notify_new, args=(new_items,), daemon=True).start()
        settings = self._read_settings()
        settings["seen_vacancies"] = list((previous | current_ids))[-3000:]
        SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
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

    def _notify_new(self, items):
        try:
            token = credential_read("HHJobHelper/telegram_bot_token")
            if not token or not self.chat_id:
                return
            for offset in range(0, len(items), 8):
                batch = items[offset:offset + 8]
                lines = ["Новые вакансии на hh.ru:"]
                for item in batch:
                    url = item.get("alternate_url", "")
                    lines.append(f"• {item.get('name', 'Вакансия')} — {(item.get('employer') or {}).get('name', 'Компания')}\n{url}")
                telegram_call(token, "sendMessage", {"chat_id": self.chat_id, "text": "\n\n".join(lines), "disable_web_page_preview": True})
        except Exception as exc:
            message = str(exc)
            self.after(0, lambda: self.note.set(f"Не удалось отправить уведомление Telegram: {message}"))

    def _error(self, message):
        self.state.set("Не удалось выполнить поиск")
        messagebox.showerror("Ошибка API hh.ru", message)

    def _schedule_search(self):
        try:
            minutes = max(1, int(self.interval.get()))
        except ValueError:
            minutes = 60
        self.after(minutes * 60 * 1000, self._scheduled_search)

    def _scheduled_search(self):
        self.search()
        self._schedule_search()

    def open_job(self, _event):
        import webbrowser
        row = self.table.selection()
        if row:
            url = self.table.item(row[0], "tags")
            if url: webbrowser.open(url[0])


if __name__ == "__main__":
    App().mainloop()
