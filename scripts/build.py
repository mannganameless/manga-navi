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
    ("kurage", "くらげバンチ", "くらげ", "https://kuragebunch.com", "#0C8599"),
    ("bunchkai", "コミックバンチKai", "バンチKai", "https://comicbunch-kai.com", "#E03131"),
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
]
SITE_META = {k: {"name": n, "short": s, "url": u, "color": c} for k, n, s, u, c in GIGA_SITES}

SOURCES = [{"key": k, "name": n, "type": "giga_rss", "base": u} for k, n, s, u, c in GIGA_SITES]
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


# ---------- まとめ ----------
def collect(now: datetime, fetch=get):
    all_items, health = [], []
    for src in SOURCES:
        h = {"key": src["key"], "name": src["name"], "status": "ok", "count": 0, "latest": None, "message": ""}
        try:
            if src["type"] == "giga_rss":
                items = parse_giga_rss(fetch(src["base"] + "/rss"), src["key"], now)
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
    for h in health:
        print(f"[{h['status']:5}] {h['name']}: {h['count']}件 {h['message']}")
    # 全滅のときだけ失敗扱い（前回のページを残す）
    return 1 if all(h["status"] == "error" for h in health) else 0


if __name__ == "__main__":
    sys.exit(main())
