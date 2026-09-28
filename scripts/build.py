"""マンガ更新ナビ：各公式サイトから更新情報を集めて site/index.html を作る。

GitHub Actions から1日3回実行される。手元で試すときは:
    pip install -r requirements.txt
    python scripts/build.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
UA = "MangaKoshinNavi/1.0 (+https://github.com/)"  # 公開後、自分のサイトURLに書き換える
GIGA_NS = "{https://gigaviewer.com}"
STALE_DAYS = 7  # これ以上新着がなければ「要確認」

# 対応サイトの一覧。GigaViewer（はてな）系のサイトは1行足すだけで追加できる
# (キー, 表示名, 短い名前, URL, 色)
GIGA_SITES = [
    ("jump", "少年ジャンプ＋", "ジャンプ＋", "https://shonenjumpplus.com", "#E8590C"),
    ("tonari", "となりのヤングジャンプ", "となりのYJ", "https://www.tonarinoyj.jp", "#D9480F"),
    ("webry", "サンデーうぇぶり", "うぇぶり", "https://www.sunday-webry.com", "#F08C00"),
    ("days", "コミックDAYS", "DAYS", "https://comic-days.com", "#1C7ED6"),
    ("gardo", "コミックガルド", "ガルド", "https://comic-gardo.com", "#C2255C"),
    ("earthstar", "コミックアース・スター", "アース・スター", "https://comic-earthstar.com", "#5C940D"),
    ("kurage", "くらげバンチ", "くらげ", "https://kuragebunch.com", "#0C8599"),  # コミックバンチKaiの作品もここに含まれる
    ("zenon", "ゼノン編集部", "ゼノン", "https://comic-zenon.com", "#495057"),
    ("magcomi", "マグコミ", "マグコミ", "https://magcomi.com", "#7048E8"),
    ("action", "webアクション", "アクション", "https://comic-action.com", "#F76707"),
    ("trail", "コミックトレイル", "トレイル", "https://comic-trail.com", "#2F9E44"),
    ("border", "コミックボーダー", "ボーダー", "https://comicborder.com", "#364FC7"),
    ("feel", "FEEL web", "FEEL", "https://feelweb.jp", "#D6336C"),
    ("ogyaaa", "COMIC OGYAAA!!", "OGYAAA", "https://comic-ogyaaa.com", "#AE3EC9"),
    ("seasons", "Seasons", "Seasons", "https://comic-seasons.com", "#66A80F"),
    ("ichijin", "一迅プラス", "一迅プラス", "https://ichicomi.com", "#1971C2"),
    ("yours", "COMIC Y-OURS", "Y-OURS", "https://comic-y-ours.com", "#E64980"),
    ("mtsquare", "まんがタイムSquare", "タイムSquare", "https://mangatime-square.com", "#F59F00"),
    ("ourfeel", "OUR FEEL", "OUR FEEL", "https://ourfeel.jp", "#9C36B5"),
]
# GigaViewer以外のサイト（サイトごとに専用の読み取りを用意）
OTHER_SITES = [
    ("kadocomi", "カドコミ", "カドコミ", "https://comic-walker.com", "#F76707"),
    ("gangan", "ガンガンONLINE", "ガンガン", "https://www.ganganonline.com", "#E03131"),
    ("magapoke", "マガポケ", "マガポケ", "https://pocket.shonenmagazine.com", "#1864AB"),
]
SITE_META = {k: {"name": n, "short": s, "url": u, "color": c} for k, n, s, u, c in GIGA_SITES + OTHER_SITES}

SOURCES = [{"key": k, "name": n, "type": "giga_rss", "base": u} for k, n, s, u, c in GIGA_SITES]
# 「あとから無料になった話」がRSSに出にくいサイト。トップページに載った作品の
# 作品別フィード（/atom/series/ID）を読み、最近無料になった話を拾う
ATOM_SITES = ["kurage", "zenon", "magcomi", "action", "earthstar", "ichijin", "days"]
MAX_SERIES = 25      # 1サイトあたりに調べる作品数の上限
FREE_LOOKBACK_DAYS = 7
for k in ATOM_SITES:
    SOURCES.append({"key": k, "name": SITE_META[k]["name"] + "（無料化分）", "type": "giga_atom", "base": SITE_META[k]["url"]})

SOURCES.append({"key": "kadocomi", "name": "カドコミ", "type": "kadocomi", "base": "https://comic-walker.com"})
SOURCES.append({"key": "magapoke", "name": "マガポケ", "type": "magapoke", "base": "https://pocket.shonenmagazine.com"})
SOURCES.append({"key": "gangan", "name": "ガンガンONLINE", "type": "gangan", "base": "https://www.ganganonline.com"})

# コミックガルドは無料公開分がRSSに出ないため、トップページからも取得する
SOURCES.insert(5, {"key": "gardo", "name": "コミックガルド（無料公開分）", "type": "gardo_top", "base": "https://comic-gardo.com"})

# アクセス解析（Cloudflare Web Analytics）のトークン。リポジトリ直下の analytics_token.txt に書く
TOKEN_FILE = ROOT / "analytics_token.txt"


def get(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


# ---------- GigaViewer 系サイトの公式RSS ----------
def parse_giga_rss(xml_text: str, site: str, now: datetime) -> list[dict]:
    """RSSを読み、無料/有料を判定して返す。

    giga:freeTermStartDate がある話 = 無料、ない話 = 有料（先読み・ポイント）。
    """
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        series = (it.findtext("description") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = parsedate_to_datetime(it.findtext("pubDate"))
        free_el = it.find(f"{GIGA_NS}freeTermStartDate")
        free_start = parsedate_to_datetime(free_el.text) if free_el is not None and free_el.text else None
        free = free_start is not None and free_start <= now
        enc = it.find("enclosure")
        img = enc.get("url") if enc is not None else ""
        # ジャンプ＋は「[第12話]作品名」形式なので話数を切り出す
        ep = title
        m = re.match(r"^\[(.+?)\](.*)$", title)
        if m:
            ep = m.group(1)
            series = series or m.group(2)
        date = free_start if free and free_start and free_start > pub else pub
        items.append(dict(site=site, series=series or title, ep=ep,
                          author=(it.findtext("author") or "").strip(), url=link,
                          date=date.astimezone(JST).isoformat(), free=free, img=img))
    return items


# ---------- コミックガルド：トップページの曜日別欄（毎日12時の無料公開分） ----------
WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def last_weekday_noon(weekday: int, now: datetime) -> datetime:
    """now 以前で、指定曜日の12:00(JST)のうち最も新しい日時。"""
    n = now.astimezone(JST)
    d = n.replace(hour=12, minute=0, second=0, microsecond=0)
    d -= timedelta(days=(d.weekday() - weekday) % 7)
    if d > n:
        d -= timedelta(days=7)
    return d


def parse_gardo_top(html: str, now: datetime) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for box in soup.select('div[class*="weekly_update_container"]'):
        head = box.find("h3")
        label = head.get_text(strip=True).upper() if head else ""
        wd = next((i for i, w in enumerate(WEEKDAYS) if label.startswith(w)), None)
        if wd is None:
            continue
        date = last_weekday_noon(wd, now)
        for a in box.select('a[class*="updated_link"]'):
            ep_el = a.select_one('[class*="episode_title"]')
            img_el = a.find("img")
            items.append(dict(site="gardo", series=a.get("data-series-name", "").strip(),
                              ep=ep_el.get_text(strip=True) if ep_el else "",
                              author="", url=a.get("href", ""), date=date.isoformat(),
                              free=True, img=img_el.get("src", "") if img_el else ""))
    return items


# ---------- カドコミ・ガンガンONLINE（ページ内のデータを読む） ----------
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def next_data(html: str) -> dict:
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("ページ内のデータが見つかりません（サイトの構造が変わった可能性）")
    return json.loads(m.group(1))


def parse_kadocomi_new(html: str) -> list[dict]:
    """カドコミの「新着作品」ページ（/new）。直近数日分の更新が載っている。"""
    nd = next_data(html)
    items = []
    for q in nd["props"]["pageProps"]["dehydratedState"]["queries"]:
        key = q.get("queryKey") or []
        if not (key and isinstance(key[0], list) and key[0][0] == "/api/series/new"):
            continue
        for s in q["state"]["data"]["result"]:
            ep = s.get("episode") or {}
            if not ep.get("code"):
                continue
            date = datetime.fromisoformat(s["latestUpdateDate"].replace("Z", "+00:00"))
            items.append(dict(site="kadocomi", series=s.get("title", ""), ep=ep.get("title", ""),
                              author="/".join(a["name"] for a in s.get("authors", [])),
                              url=f"https://comic-walker.com/detail/{s['code']}/episodes/{ep['code']}",
                              date=date.astimezone(JST).isoformat(), free=True, img=s.get("thumbnail", "")))
    return items


def parse_gangan_top(html: str) -> list[dict]:
    """ガンガンONLINEのトップページ「今日の更新作品」。"""
    nd = next_data(html)
    base = "https://www.ganganonline.com"
    items = []
    for sec in nd["props"]["pageProps"]["data"]["sections"]:
        ts = sec.get("titleSection")
        if not ts or "更新" not in (ts.get("header") or ""):
            continue
        for x in ts.get("titles", []):
            m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", x.get("updated", ""))
            if not m or not x.get("chapterId"):
                continue
            date = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=JST)
            img = x.get("imageUrl", "")
            items.append(dict(site="gangan", series=x.get("header", ""), ep="最新話", author="",
                              url=f"{base}/title/{x['titleId']}/chapter/{x['chapterId']}",
                              date=date.isoformat(), free=True, img=(base + img) if img.startswith("/") else img))
    return items


def parse_magapoke_top(html: str, now: datetime) -> list[dict]:
    """マガポケのトップページ「MM/DD○曜日の更新作品」（無料話の更新）。"""
    soup = BeautifulSoup(html, "html.parser")
    base = "https://pocket.shonenmagazine.com"
    items = []
    for sec in soup.select("section.p-index-update"):
        head = sec.find(["h2", "h3"])
        m = re.search(r"(\d{1,2})/(\d{1,2})", head.get_text() if head else "")
        if not m:
            continue
        n = now.astimezone(JST)
        year = n.year - 1 if int(m[1]) > n.month + 1 else n.year  # 年をまたぐとき
        date = datetime(year, int(m[1]), int(m[2]), tzinfo=JST)
        for a in sec.select('a[href*="/episode/"]'):
            title = a.select_one("h3")
            img = a.find("img")
            items.append(dict(site="magapoke", series=title.get_text(strip=True) if title else "",
                              ep="最新話", author="", url=base + a["href"] if a["href"].startswith("/") else a["href"],
                              date=date.isoformat(), free=True, img=img.get("src", "") if img else ""))
    return items


# ---------- 作品別フィード（無料化した話を拾う） ----------
ATOM_NS = "{http://www.w3.org/2005/Atom}"
SERIES_ID_RE = re.compile(r"series-[a-z-]*thumbnail[a-z-]*(?:/|%2F)(\d{10,})-")


def series_ids_from_top(html: str) -> list[str]:
    """トップページの「最新の更新」欄に載っている作品のIDを集める。

    サイトごとにデザインが違うため、よくある形を順に探し、見つからなければ
    ページ全体から（上に載っている順に）作品IDを拾う。
    """
    soup = BeautifulSoup(html, "html.parser")
    ids: list[str] = []
    selectors = [
        'div.latest-update li[data-test-id]',          # くらげバンチなど（旧デザイン）
        'li[class*="UpdateSeriesItem"]',               # webアクションなど（新デザイン）
        'div[class*="weekly_update_container"] li',    # コミックガルド
        'div[class*="LatestUpdate"] li',
    ]
    for sel in selectors:
        for el in soup.select(sel):
            if el.get("data-test-id", "").isdigit():
                ids.append(el["data-test-id"])
            ids += SERIES_ID_RE.findall(str(el))
    if not ids:  # どの形にも当てはまらないときの予備
        ids = SERIES_ID_RE.findall(html)
    return list(dict.fromkeys(ids))[:MAX_SERIES]


def parse_series_atom(xml_text: str, site: str, now: datetime) -> list[dict]:
    """作品別フィードから、最近（7日以内）無料になった話だけを返す。"""
    root = ET.fromstring(xml_text)
    since = now - timedelta(days=FREE_LOOKBACK_DAYS)
    items = []
    for e in root.iter(f"{ATOM_NS}entry"):
        fs = e.findtext(f"{GIGA_NS}freeTermStartDate")
        if not fs:
            continue
        start = datetime.fromisoformat(fs.replace("Z", "+00:00"))
        if not (since <= start <= now):
            continue
        link, img = "", ""
        for l in e.findall(f"{ATOM_NS}link"):
            if l.get("rel") == "enclosure":
                img = l.get("href", "")
            elif not link:
                link = l.get("href", "")
        items.append(dict(site=site, series=(e.findtext(f"{ATOM_NS}content") or "").strip(),
                          ep=(e.findtext(f"{ATOM_NS}title") or "").strip(),
                          author=(e.findtext(f"{ATOM_NS}author/{ATOM_NS}name") or "").strip(),
                          url=link, date=start.astimezone(JST).isoformat(), free=True, img=img))
    return items


def collect_atom(base: str, site: str, now: datetime, fetch) -> tuple[list[dict], int]:
    ids = series_ids_from_top(fetch(base + "/"))
    items = []
    for sid in ids:
        try:
            items += parse_series_atom(fetch(f"{base}/atom/series/{sid}"), site, now)
        except Exception:
            traceback.print_exc()
        time.sleep(0.5)
    return items, len(ids)


# ---------- まとめ ----------
def collect(now: datetime, fetch=get):
    all_items, health = [], []
    for src in SOURCES:
        h = {"key": src["key"], "name": src["name"], "status": "ok", "count": 0, "latest": None, "message": ""}
        try:
            if src["type"] == "giga_rss":
                items = parse_giga_rss(fetch(src["base"] + "/rss"), src["key"], now)
            elif src["type"] == "giga_atom":
                items, n_series = collect_atom(src["base"], src["key"], now, fetch)
                h["count"] = len(items)
                if n_series == 0:
                    h.update(status="error", message="トップページから作品を見つけられませんでした。サイトの構造が変わった可能性があります。")
                else:
                    h["message"] = f"{n_series}作品を確認"
                    if items:
                        h["latest"] = max(i["date"] for i in items)
                all_items += items
                health.append(h)
                time.sleep(1)
                continue
            elif src["type"] == "kadocomi":
                items = parse_kadocomi_new(fetch(src["base"] + "/new"))
            elif src["type"] == "magapoke":
                items = parse_magapoke_top(fetch(src["base"] + "/"), now)
            elif src["type"] == "gangan":
                items = parse_gangan_top(fetch(src["base"] + "/"))
            else:
                items = parse_gardo_top(fetch(src["base"] + "/"), now)
            h["count"] = len(items)
            if not items:
                h.update(status="error", message="取得件数が0件でした。サイトの構造が変わった可能性があります。")
            else:
                latest = max(datetime.fromisoformat(i["date"]) for i in items)
                h["latest"] = latest.isoformat()
                days = (now - latest).days
                if days >= STALE_DAYS:
                    h.update(status="stale", message=f"{days}日間新着がありません。")
            all_items += items
        except Exception as e:  # 1サイトの失敗で全体を止めない
            h.update(status="error", message=f"{type(e).__name__}: {e}")
            traceback.print_exc()
        health.append(h)
        time.sleep(1)  # 相手のサーバーに負担をかけないよう間隔をあける

    # 同じURLが複数の取得元にあれば「無料」を優先
    merged: dict[str, dict] = {}
    for it in all_items:
        cur = merged.get(it["url"])
        if cur is None or (it["free"] and not cur["free"]):
            if cur and not it["img"]:
                it["img"] = cur["img"]
            if cur and not it["author"]:
                it["author"] = cur["author"]
            merged[it["url"]] = it
    items = sorted(merged.values(), key=lambda i: i["date"], reverse=True)
    return items, health


def notify(health: list[dict]) -> None:
    bad = [h for h in health if h["status"] != "ok"]
    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not bad or not hook:
        return
    lines = [f"・{h['name']}：{h['message']}" for h in bad]
    try:
        requests.post(hook, json={"content": "【マンガ更新ナビ】取得に問題があります\n" + "\n".join(lines)}, timeout=15)
    except Exception:
        traceback.print_exc()


def render(items, health, now: datetime) -> None:
    tpl = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    token = TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else ""
    token = re.sub(r"[^0-9A-Za-z]", "", token)  # 念のため英数字だけにする
    payload = json.dumps({"items": items, "health": health, "sites": SITE_META, "analytics": token,
                          "generated": now.isoformat()}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")  # scriptタグの途中終了を防ぐ
    out = ROOT / "site"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(tpl.replace("__PAYLOAD__", payload), encoding="utf-8")
    (out / "data.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    now = datetime.now(JST)
    items, health = collect(now)
    render(items, health, now)
    notify(health)
    # 未対応サイトの診断：survey_targets.json があり、手動実行（Run workflow）のときだけ
    if (ROOT / "survey_targets.json").exists() and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import survey
            survey.run()
        except Exception:
            traceback.print_exc()
    for h in health:
        print(f"[{h['status']:5}] {h['name']}: {h['count']}件 {h['message']}")
    # 全滅のときだけ失敗扱い（前回のページを残す）
    return 1 if all(h["status"] == "error" for h in health) else 0


if __name__ == "__main__":
    sys.exit(main())
