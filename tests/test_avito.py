from avito_hotwheels.bot import Item, Seen, parse, search_url

PAGE = """
<html><body>
<div data-marker="catalog-serp">
  <div data-marker="item" data-item-id="111" id="i111">
    <img src="https://00.img.avito.st/image/1/abc.jpg">
    <a data-marker="item-title" href="/moskva/kollektsionirovanie/hot_wheels_porsche_111?context=xyz"
       title="Hot Wheels Porsche в Москве"><h3 itemprop="name">Hot Wheels Porsche 911</h3></a>
    <meta itemprop="price" content="1500">
    <div class="geo-root-abc"><span>Москва</span> <span>м. Арбатская</span></div>
    <p data-marker="item-date">2 минуты назад</p>
  </div>
  <div data-marker="item" id="i222">
    <a itemprop="url" href="/kazan/igry/hot_wheels_track_222" title="Трек Hot Wheels в Казани">Трек</a>
    <span data-marker="item-price">Бесплатно</span>
  </div>
  <div data-marker="item" data-item-id="333"><span>карточка без ссылки</span></div>
</div>
</body></html>
"""


def test_search_url_covers_all_regions_newest_first():
    assert search_url("hot wheels") == "https://www.avito.ru/all?q=hot+wheels&s=104"


def test_parse_cards():
    items = parse(PAGE)
    assert [i.id for i in items] == ["111", "222"]
    a, b = items
    assert a.title == "Hot Wheels Porsche 911"
    assert a.price == "1 500 ₽"
    assert a.place == "Москва м. Арбатская"
    assert a.date == "2 минуты назад"
    assert a.url == "https://www.avito.ru/moskva/kollektsionirovanie/hot_wheels_porsche_111"
    assert a.photo == "https://00.img.avito.st/image/1/abc.jpg"
    assert b.title == "Трек Hot Wheels в Казани"
    assert b.price == "Бесплатно"
    assert b.photo is None


def test_seen_returns_only_unknown_ids(tmp_path):
    seen = Seen(str(tmp_path / "seen.db"))
    old = Item("1", "a", "", "", "", "u", None)
    new = Item("2", "b", "", "", "", "u", None)
    assert seen.empty()
    seen.add([old])
    assert not seen.empty()
    assert seen.new([new, old]) == [new]


def test_telethon_proxy_from_url():
    from avito_hotwheels.bot import telethon_proxy
    assert telethon_proxy(None) is None
    assert telethon_proxy("socks5h://127.0.0.1:2080") == {
        "proxy_type": "socks5", "addr": "127.0.0.1", "port": 2080,
        "username": None, "password": None, "rdns": True}
    assert telethon_proxy("http://u:p@host:8080")["proxy_type"] == "http"


def test_make_telegram_uses_mtproto_only_when_enabled(monkeypatch, tmp_path):
    from avito_hotwheels import bot
    monkeypatch.chdir(tmp_path)  # Telethon creates its session file in the current directory
    monkeypatch.setenv("TG_TOKEN", "1:x")
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    assert type(bot.make_telegram("5")) is bot.Telegram
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "a" * 32)
    monkeypatch.delenv("TG_MTPROTO", raising=False)
    assert type(bot.make_telegram("5")) is bot.Telegram  # keys alone don't switch to MTProto
    monkeypatch.setenv("TG_MTPROTO", "1")
    tg = bot.make_telegram("-1001234")
    assert isinstance(tg, bot.TelegramMTProto)
    assert tg.chat_id == -1001234
    tg.client.session.close()


def test_use_ip_keeps_host_and_checks_cert_for_api_telegram_org():
    from avito_hotwheels.bot import NoSNIAdapter, Telegram
    tg = Telegram("123:abc", "1")
    tg.use_ip("149.154.167.220")
    assert tg.base == "https://149.154.167.220/bot123:abc"
    assert tg.http.headers["Host"] == "api.telegram.org"
    adapter = tg.http.get_adapter("https://149.154.167.220/bot123:abc/getMe")
    assert isinstance(adapter, NoSNIAdapter)
    assert adapter.poolmanager.connection_pool_kw["assert_hostname"] == "api.telegram.org"


def test_fetch_block_saves_page_and_resets_session(monkeypatch, tmp_path):
    import pytest
    from avito_hotwheels import bot

    class Resp:
        status_code, text = 403, "<h2>Доступ ограничен: проблема с IP</h2>"

    class Session:
        def get(self, url, timeout):
            return Resp()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, "_session", Session())
    with pytest.raises(bot.Blocked, match="HTTP 403"):
        bot.fetch("https://www.avito.ru/all?q=x", None)
    assert bot._session is None
    assert "проблема с IP" in (tmp_path / "avito_blocked.html").read_text(encoding="utf-8")
