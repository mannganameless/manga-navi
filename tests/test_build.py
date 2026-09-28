"""オフラインで動く動作確認（実サイトにはアクセスしない）。 python tests/test_build.py"""
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build
build.time.sleep = lambda *_: None

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=build.JST)


def fake_fetch(url):
    if url == "https://shonenjumpplus.com/rss":
        return (FX / "jump_rss.xml").read_text()
    if url == "https://comic-gardo.com/rss":
        return (FX / "gardo_rss.xml").read_text()
    if url == "https://comic-gardo.com/":
        return (FX / "gardo_top.html").read_text()
    if url == "https://comic-days.com/rss":
        return (FX / "jump_rss.xml").read_text().replace("Sep 2026", "Aug 2026")  # 古いデータ
    raise RuntimeError("接続エラー（テスト）")


items, health = build.collect(NOW, fetch=fake_fetch)
by = {i["url"]: i for i in items}

j7 = by["https://shonenjumpplus.com/episode/9253191256653653849"]
j1 = by["https://shonenjumpplus.com/episode/9253191256653653762"]
assert j7["free"] is False and j1["free"] is True
assert j7["ep"] == "第7話" and j7["series"] == "封神演義外伝〜仙界導書〜"
assert j1["img"].startswith("https://cdn-ak-img.shonenjumpplus.com/")

paid = by["https://comic-gardo.com/episode/12207421984251695811"]
free = by["https://comic-gardo.com/episode/12207421984125549352"]
assert paid["free"] is False and free["free"] is True
assert free["ep"] == "第66話「義息子（むすこ）」(1)"
assert free["date"].startswith("2026-09-27T12:00"), free["date"]
sat = by["https://comic-gardo.com/episode/12207421984065287215"]
assert sat["date"].startswith("2026-09-26T12:00"), sat["date"]

st = {h["name"]: h["status"] for h in health}
assert st["少年ジャンプ＋"] == "ok"
assert st["コミックガルド（無料公開分）"] == "ok"
assert st["コミックアース・スター"] == "error"   # 取得失敗 → 通知対象
assert st["くらげバンチ"] == "error"
assert st["コミックDAYS"] == "stale"            # 7日以上新着なし → 通知対象

build.render(items, health, NOW)
html = (build.ROOT / "site" / "index.html").read_text()
assert "__PAYLOAD__" not in html and "封神演義外伝" in html and "くらげバンチ" in html
print("すべてのテストに合格しました")
for h in health:
    print(h["status"], h["name"], h["count"], h["message"])
