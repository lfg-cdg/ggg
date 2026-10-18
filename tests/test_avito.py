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
