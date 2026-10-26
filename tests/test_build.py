"""オフラインで動く動作確認（実サイトにはアクセスしない）。 python tests/test_build.py"""
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build
build.time.sleep = lambda *_: None
build.PUBLISHER_IMAGES = True  # 読み取り部分の確認のため画像も残す

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
    if url == "https://kuragebunch.com/":
        return (FX / "kurage_top.html").read_text()
    if url == "https://kuragebunch.com/atom/series/12207421983749476406":
        return (FX / "kurage_atom.xml").read_text()
    if url == "https://www.comicnettai.com/":
        return (FX / "nettai_top.html").read_text()
    if url == "https://to-corona-ex.com/":
        return (FX / "coronaex_top.html").read_text()
    if url == "https://youngchampion.jp/":
        return (FX / "comici_top.html").read_text()
    if url == "https://pachikuri.jp/rss.xml":
        return (FX / "generic_rss.xml").read_text()
    if url == "https://pocket.shonenmagazine.com/":
        return (FX / "magapoke_top.html").read_text()
    if url == "https://comic-walker.com/new":
        return (FX / "kadocomi_new.html").read_text()
    if url == "https://www.ganganonline.com/":
        return (FX / "gangan_top.html").read_text()
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

# 作品別フィード：7日以内に無料になった話だけ拾う
kf = by.get("https://kuragebunch.com/episode/12207421984031582767")
assert kf and kf["free"] and kf["date"].startswith("2026-09-22T12:00"), kf
assert kf["ep"] == "第6話 グル 後半" and kf["series"] == "介護とハイエナ" and kf["img"]
assert "https://kuragebunch.com/episode/12207421984152846114" not in by  # 有料
assert "https://kuragebunch.com/episode/1" not in by                     # 古い無料化
hk = next(h for h in health if h["name"] == "くらげバンチ（無料化分）")
assert hk["status"] == "ok" and hk["count"] == 1 and "2作品" in hk["message"], hk
hz = next(h for h in health if h["name"] == "ゼノン編集部（無料化分）")
assert hz["status"] == "error"

# カドコミ・ガンガンONLINE
k = by["https://comic-walker.com/detail/KC_020669_S/episodes/KC_0206690000200011_E"]
assert k["free"] and k["ep"] == "第1話前編" and k["date"].startswith("2026-09-28T11:00") and k["author"] == "ナツマサキ"
g = by["https://www.ganganonline.com/title/2391/chapter/129246"]
assert g["free"] and g["date"].startswith("2026-09-28T00:00") and g["img"].startswith("https://www.ganganonline.com/secure/")
assert next(h for h in health if h["name"] == "ガンガンONLINE")["status"] == "ok"

mp = by["https://pocket.shonenmagazine.com/title/03342/episode/443844"]
assert mp["free"] and mp["series"] == "弱くて可愛い君が好き" and mp["date"].startswith("2026-09-28T00:00") and mp["img"]

# comici系は一時停止中なので、読み取り部分だけ直接確認する
yc = {i["url"]: i for i in build.parse_comici_top((FX / "comici_top.html").read_text(), "yanchan", "https://youngchampion.jp", NOW)}["https://youngchampion.jp/series/e384a1f128162"]
assert yc["free"] and yc["series"] == "教えて・旦那サマ" and yc["author"] == "草薙竜樹" and yc["date"].startswith("2026-09-28T00:00")
pk = by["https://pachikuri.jp/comic/5"]
assert pk["img"] == "https://pachikuri.jp/img/5.jpg" and "https://pachikuri.jp/comic/1" not in by

ce = by["https://to-corona-ex.com/episodes/274976650170893"]
assert ce["free"] and ce["ep"] == "第14話" and ce["date"].startswith("2026-09-28T11:00")

nt = by["https://www.comicnettai.com/book/773"]
assert nt["date"].startswith("2026-09-25T00:00") and nt["author"] == "稲葉そーへー/本橋隆司" and nt["img"]

# 日刊月チャン・栞
gc = build.parse_gecchan_top('<div class="contentInner"><figure><img src="/comics/john/image"><div class="detailBox"><h3>ジョン</h3><div class="author">盆ノ木至</div><div class="update"><span class="date">9.25</span></div></div></figure><div class="btnBox"><a href="/comics/john/725">最新話を読む</a></div></div>', NOW)
assert gc and gc[0]["url"] == "https://nikkangecchan.jp/comics/john/725" and gc[0]["date"].startswith("2026-09-25")
sh = build.parse_shiori_top('<article class="card"><a href="https://shiori-on.com/product/runrun"><time datetime="2026-09-26">更新日</time><img src="https://x/a.png" alt="ルンルン"></a></article><article class="card"><a href="https://shiori-on.com/product/old"><time datetime="2025-01-01">x</time></a></article>', NOW)
assert len(sh) == 1 and sh[0]["series"] == "ルンルン"
# 手動登録
build.MANUAL_FILE = FX / "manual_entries.csv"
(FX / "manual_entries.csv").write_text("# コメント\n日付,サイト名,作品名,話数,URL,無料,画像URL\n2026-09-27 12:00,ファイアCROSS,テスト作品,第3話,https://firecross.jp/x,1,\n2025-01-01 00:00,ファイアCROSS,古い,第1話,https://firecross.jp/old,1,\n", encoding="utf-8")
mi = build.load_manual(NOW)
assert len(mi) == 1 and mi[0]["site"] == "firecross" and mi[0]["free"]

# 新デザイン・予備の読み取り
new_design = '<ul><li class="UpdateSeriesItem_item_wrapper__w6pxS"><img src="https://x/https%3A%2F%2Fcdn-img.comic-action.com%2Fpublic%2Fseries-thumbnail%2F4855956445099488439-1ffe%3F1"></li></ul>'
assert build.series_ids_from_top(new_design) == ["4855956445099488439"]
fallback = '<div><img src="https://cdn-img.example.com/public/series-thumbnail/1234567890123-abc"></div>'
assert build.series_ids_from_top(fallback) == ["1234567890123"]

build.render(items, health, NOW)
html = (build.ROOT / "site" / "index.html").read_text()
assert "__PAYLOAD__" not in html and "封神演義外伝" in html and "くらげバンチ" in html
# 出版社画像をオフにすると画像URLが消えること
build.PUBLISHER_IMAGES = False
items2, _ = build.collect(NOW, fetch=fake_fetch)
assert all(i["img"] == "" for i in items2)
print("すべてのテストに合格しました")
for h in health:
    print(h["status"], h["name"], h["count"], h["message"])
