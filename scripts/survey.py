"""未対応サイトの一括診断（手動実行のときだけ動く）。

survey_targets.json に書いたサイトを1つずつ訪ね、対応のしやすさを調べて
site/survey.json に書き出す。実サイトへのアクセスはサイトあたり最大8回程度。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

ROOT = Path(__file__).resolve().parent.parent
UA = "MangaKoshinNavi-Survey/1.0"
FEED_PATHS = ["/rss", "/feed", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml"]


def get(url, **kw):
    return requests.get(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=True, **kw)


def survey_one(name, candidates):
    r = {"name": name, "tried": candidates}
    for url in candidates:
        try:
            res = get(url)
        except Exception as e:
            r.setdefault("errors", []).append(f"{url}: {type(e).__name__}")
            continue
        r["status"] = res.status_code
        r["final_url"] = res.url
        if res.status_code >= 400:
            continue
        html = res.text
        origin = f"{urlparse(res.url).scheme}://{urlparse(res.url).netloc}"
        r["origin"] = origin
        r["title"] = (re.search(r"<title[^>]*>(.*?)</title>", html, re.S) or [None, ""])[1].strip()[:80]
        r["size"] = len(html)
        r["markers"] = {
            "gigaviewer": "gigaviewer" in html,
            "next_data": "__NEXT_DATA__" in html,
            "next_flight": "self.__next_f" in html,
            "nuxt": "__NUXT" in html,
            "episode_links": len(set(re.findall(r'href="([^"]*(?:episode|chapter|story|viewer|comic/)[^"]*)"', html))),
            "update_word": len(re.findall(r"更新", html)),
            "free_word": len(re.findall(r"無料", html)),
            "app_only_hint": bool(re.search(r"アプリ(限定|のみ|で読む)", html)),
        }
        r["feed_links"] = re.findall(r'type="application/(?:rss|atom)\+xml"[^>]*href="([^"]+)"', html)[:3]
        # robots.txt
        try:
            rb = get(origin + "/robots.txt")
            txt = rb.text if rb.status_code == 200 and "<html" not in rb.text[:200].lower() else ""
            rp = RobotFileParser(); rp.parse(txt.splitlines())
            r["robots"] = {"exists": bool(txt), "allow_top": rp.can_fetch("*", res.url),
                           "disallow_lines": [l.strip() for l in txt.splitlines() if l.lower().startswith("disallow")][:8]}
        except Exception as e:
            r["robots"] = {"error": type(e).__name__}
        # フィード
        feeds = {}
        for p in FEED_PATHS:
            try:
                f = get(origin + p)
                head = f.text[:400].lower()
                if f.status_code == 200 and ("<rss" in head or "<feed" in head or "<?xml" in head):
                    feeds[p] = {"items": f.text.count("<item") + f.text.count("<entry"),
                                "giga": "gigaviewer.com" in f.text[:2000]}
            except Exception:
                pass
            time.sleep(0.3)
        r["feeds"] = feeds
        break
    return r


def run():
    path = ROOT / "survey_targets.json"
    targets = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for name, cands in targets:
        out.append(survey_one(name, cands))
        print("survey:", name, out[-1].get("status"), list(out[-1].get("feeds", {}).keys()))
        time.sleep(1)
    (ROOT / "site").mkdir(exist_ok=True)
    (ROOT / "site" / "survey.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    run()
