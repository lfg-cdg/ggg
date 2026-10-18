"""Watch an Avito search across all of Russia and send every new listing to Telegram."""
import html
import os
import random
import sqlite3
import sys
import time
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as browser_http  # copies Chrome's TLS fingerprint, Avito blocks it less
except ImportError:
    browser_http = None

AVITO = "https://www.avito.ru"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}
BLOCK_MARKERS = ("Доступ ограничен", "проблема с IP", "firewall-container", "captcha")
TG_UNREACHABLE = ("Не могу подключиться к api.telegram.org: похоже, провайдер блокирует Telegram.\n"
                  "Включите VPN или впишите в .env прокси для Telegram, например:\n"
                  "  TG_PROXY=socks5h://127.0.0.1:1080\n"
                  "(адрес и порт смотрите в настройках своего VPN-клиента)")


@dataclass
class Item:
    id: str
    title: str
    price: str
    place: str
    date: str
    url: str
    photo: str | None


class Blocked(Exception):
    pass


def load_env(path: str = ".env") -> None:
    """Read KEY=VALUE lines into os.environ (real environment variables win)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def search_url(query: str) -> str:
    # "all" = every region, s=104 = newest first
    return f"{AVITO}/all?q={quote_plus(query)}&s=104"


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def parse(page: str) -> list[Item]:
    """Listing cards from a search page, in page order (newest first)."""
    soup = BeautifulSoup(page, "html.parser")
    items = []
    for card in soup.select('[data-marker="item"]'):
        item_id = card.get("data-item-id") or card.get("id", "").lstrip("i")
        link = card.select_one('[data-marker="item-title"]') or card.select_one('a[itemprop="url"]')
        if not item_id or not link or not link.get("href"):
            continue
        title = _text(card.select_one('[itemprop="name"]')) or link.get("title") or _text(link)
        price_meta = card.select_one('meta[itemprop="price"]')
        if price_meta and price_meta.get("content", "").isdigit():
            price = f"{int(price_meta['content']):,} ₽".replace(",", " ")
        else:
            price = _text(card.select_one('[data-marker="item-price"]')) or "цена не указана"
        place = _text(card.select_one('[data-marker="item-address"]')
                      or card.select_one('[class*="geo-root"]'))
        img = card.select_one("img")
        photo = None
        if img:
            photo = img.get("src") or (img.get("srcset", "").split(" ")[0] or None)
        items.append(Item(
            id=item_id,
            title=title,
            price=price,
            place=place,
            date=_text(card.select_one('[data-marker="item-date"]')),
            url=urljoin(AVITO, urlsplit(link["href"]).path),
            photo=photo if photo and photo.startswith("http") else None,
        ))
    return items


def fetch(url: str, proxy: str | None) -> str:
    proxies = {"http": proxy, "https": proxy} if proxy else None
    if browser_http:
        resp = browser_http.get(url, headers=HEADERS, impersonate="chrome", proxies=proxies, timeout=30)
    else:
        resp = requests.get(url, headers=HEADERS, proxies=proxies, timeout=30)
    if resp.status_code in (403, 429):
        raise Blocked(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    if 'data-marker="item"' not in resp.text and any(m in resp.text for m in BLOCK_MARKERS):
        raise Blocked("captcha / IP block page")
    return resp.text


class Seen:
    """IDs of listings that were already sent (or existed before the first run)."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, ts INTEGER)")

    def empty(self) -> bool:
        return self.db.execute("SELECT 1 FROM seen LIMIT 1").fetchone() is None

    def new(self, items: list[Item]) -> list[Item]:
        return [i for i in items
                if not self.db.execute("SELECT 1 FROM seen WHERE id = ?", (i.id,)).fetchone()]

    def add(self, items: list[Item]) -> None:
        now = int(time.time())
        self.db.executemany("INSERT OR IGNORE INTO seen VALUES (?, ?)", [(i.id, now) for i in items])
        self.db.commit()


class Telegram:
    def __init__(self, token: str, chat_id: str = "", proxy: str | None = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.http = requests.Session()
        if proxy:  # Telegram is often blocked in Russia while Avito must be reached directly
            self.http.proxies = {"http": proxy, "https": proxy}

    def check(self) -> None:
        """Exit with a clear message if Telegram can't be reached or the token is wrong."""
        try:
            me = self._call("getMe")
        except requests.RequestException:
            sys.exit(TG_UNREACHABLE)
        if not me.get("ok"):
            sys.exit(f"Telegram не принял TG_TOKEN: {me.get('description')}")

    def _call(self, method: str, **params) -> dict:
        for _ in range(3):
            resp = self.http.post(f"{self.base}/{method}", data=params, timeout=30)
            data = resp.json()
            if resp.status_code == 429:
                time.sleep(data.get("parameters", {}).get("retry_after", 5))
                continue
            return data
        return data

    def text(self, message: str) -> None:
        self._call("sendMessage", chat_id=self.chat_id, text=message, parse_mode="HTML",
                   disable_web_page_preview="true")

    def item(self, it: Item) -> None:
        caption = (f"<b>{html.escape(it.title)}</b>\n{html.escape(it.price)}\n"
                   f"{html.escape(it.place)}\n{html.escape(it.date)}\n{it.url}")
        if it.photo and self._call("sendPhoto", chat_id=self.chat_id, photo=it.photo,
                                   caption=caption, parse_mode="HTML").get("ok"):
            return
        self._call("sendMessage", chat_id=self.chat_id, text=caption, parse_mode="HTML")


def print_chat_ids(tg: Telegram) -> None:
    tg.check()
    updates = tg._call("getUpdates")
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("username") or u["message"]["chat"].get("title")
             for u in updates.get("result", []) if "message" in u}
    if not chats:
        print("Нет сообщений. Напишите боту что-нибудь в Telegram и запустите команду ещё раз.")
    for chat_id, name in chats.items():
        print(f"TG_CHAT_ID={chat_id}   ({name})")


def run() -> None:
    token, chat_id = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        sys.exit("Заполните TG_TOKEN и TG_CHAT_ID в файле .env (см. avito_hotwheels/README.md)")
    query = os.environ.get("AVITO_QUERY", "hot wheels")
    interval = int(os.environ.get("INTERVAL", "60"))
    proxy = os.environ.get("PROXY") or None
    url = search_url(query)
    tg = Telegram(token, chat_id, os.environ.get("TG_PROXY") or None)
    tg.check()
    seen = Seen(os.environ.get("DB_PATH", "avito_seen.db"))
    pause, blocked = interval, False

    print(f"Слежу за {url} каждые ~{interval} с. Остановить: Ctrl+C")
    while True:
        try:
            page = fetch(url, proxy)
            items = parse(page)
            if not items:
                with open("avito_last.html", "w", encoding="utf-8") as f:
                    f.write(page)
                print("На странице 0 объявлений: возможно, Авито поменял вёрстку (страница в avito_last.html)")
            elif seen.empty():
                seen.add(items)
                tg.text(f"Бот запущен: «{html.escape(query)}», вся Россия. "
                        f"Объявлений на странице: {len(items)}. Буду присылать только новые.")
                print(f"Первый запуск, запомнено объявлений: {len(items)}")
            else:
                fresh = seen.new(items)
                for it in reversed(fresh):  # oldest first, so the newest ends up at the bottom of the chat
                    tg.item(it)
                    seen.add([it])
                    time.sleep(1)
                print(f"{time.strftime('%H:%M:%S')} новых: {len(fresh)}")
            if blocked:
                tg.text("Авито снова отвечает, продолжаю.")
            pause, blocked = interval, False
        except Blocked as e:
            pause = min(max(pause * 2, 300), 1800)
            print(f"{time.strftime('%H:%M:%S')} Авито блокирует ({e}), жду {pause // 60} мин")
            if not blocked:
                tg.text(f"Авито ограничил доступ ({html.escape(str(e))}). Буду пробовать реже.")
            blocked = True
        except Exception as e:  # network hiccups etc.: log and try again next round
            print(f"{time.strftime('%H:%M:%S')} ошибка: {e!r}")
        time.sleep(pause + random.uniform(-0.15, 0.15) * pause)


def main() -> None:
    load_env()
    if sys.argv[1:] == ["chatid"]:
        token = os.environ.get("TG_TOKEN") or sys.exit("Сначала впишите TG_TOKEN в .env")
        print_chat_ids(Telegram(token, proxy=os.environ.get("TG_PROXY") or None))
    else:
        try:
            run()
        except KeyboardInterrupt:
            pass
