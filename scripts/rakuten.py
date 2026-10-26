"""楽天ブックスAPIで、作品の単行本の表紙画像とアフィリエイトリンクを探す。

・アプリID・アフィリエイトIDは GitHub の Secrets（RAKUTEN_APP_ID / RAKUTEN_AFFILIATE_ID）から読む。
・同じ作品を何度も検索しないよう、結果を rakuten_cache.json に保存し、公開サイトから前回分を引き継ぐ。
・1回の実行で新しく調べるのは最大 MAX_LOOKUPS 作品まで（APIの利用制限を守るため1秒以上あける）。
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata

import requests

API = os.environ.get("RAKUTEN_API_URL", "https://app.rakuten.co.jp/services/api/BooksBook/Search/20170404")
SITE_URL = os.environ.get("SITE_URL", "https://mannganameless.github.io/manga-navi")
MAX_LOOKUPS = 60
CACHE_DAYS = 30


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[（(【\[].*?[）)】\]]", "", s)          # 括弧の中（＠COMIC など）は無視
    return re.sub(r"[\s・、。!！?？~〜ー\-:：「」『』'\"＠@#＃]", "", s)


def search(title: str, app_id: str, aff_id: str) -> dict:
    r = requests.get(API, params={"applicationId": app_id, "affiliateId": aff_id, "format": "json",
                                  "title": title[:60], "booksGenreId": "001001", "hits": 10}, timeout=20)
    if r.status_code == 429:
        raise RuntimeError("楽天APIの利用制限に達しました")
    r.raise_for_status()
    key = norm(title)
    for it in r.json().get("Items", []):
        it = it.get("Item", it)
        if key and (key in norm(it.get("title", "")) or key in norm(it.get("seriesName", ""))):
            img = (it.get("largeImageUrl") or it.get("mediumImageUrl") or "").replace("?_ex=200x200", "?_ex=400x400")
            return {"img": img, "buy": it.get("affiliateUrl") or it.get("itemUrl", ""), "book": it.get("title", "")}
    return {}


def enrich(items: list[dict], now_ts: float) -> tuple[list[dict], dict]:
    """各話に rk_img（表紙）と buy（購入リンク）を付ける。戻り値：(items, 状態)"""
    app_id = os.environ.get("RAKUTEN_APP_ID", "").strip()
    aff_id = os.environ.get("RAKUTEN_AFFILIATE_ID", "").strip()
    state = {"enabled": bool(app_id and aff_id), "looked_up": 0, "hits": 0, "error": ""}
    try:
        cache = requests.get(SITE_URL + "/rakuten_cache.json", timeout=20).json()
    except Exception:
        cache = {}
    if state["enabled"]:
        series = list(dict.fromkeys(i["series"] for i in items if i.get("free")))
        for s in series:
            c = cache.get(s)
            if c and now_ts - c.get("t", 0) < CACHE_DAYS * 86400:
                continue
            if state["looked_up"] >= MAX_LOOKUPS:
                break
            try:
                cache[s] = {**search(s, app_id, aff_id), "t": now_ts}
            except Exception as e:
                state["error"] = f"{type(e).__name__}: {e}"
                break
            state["looked_up"] += 1
            time.sleep(1.2)
    for i in items:
        c = cache.get(i["series"]) or {}
        if c.get("img"):
            i["rk_img"], i["buy"] = c["img"], c.get("buy", "")
            state["hits"] += 1
    return items, cache
