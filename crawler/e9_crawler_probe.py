# -*- coding: utf-8 -*-
"""Probe SUDA teacher home site structure."""
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE = "https://web.suda.edu.cn"


def session():
    s = requests.Session()
    s.trust_env = False
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
    )
    return s


def main():
    s = session()
    r = s.get(f"{BASE}/xylb/list.htm", timeout=30)
    r.encoding = "utf-8"
    print("list.htm status", r.status_code, "len", len(r.text))

    # Look for portlet / data endpoints in HTML
    for pat in [
        r"url\s*[:=]\s*['\"]([^'\"]+)['\"]",
        r"ajax[^'\"]*['\"]([^'\"]+)['\"]",
        r"portlet[^'\"]*['\"]([^'\"]+)['\"]",
        r"/_web/[^'\"\s]+",
    ]:
        found = set(re.findall(pat, r.text, re.I))
        if found:
            print(f"\nPattern {pat[:40]}... ({len(found)})")
            for x in sorted(found)[:20]:
                print(" ", x)

    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup.find_all(["div", "ul"], attrs={"frag": True}):
        print("frag", tag.get("frag"), tag.get("portletmode"), tag.get("id"))

    # Fetch JS
    for path in [
        "/_web/_platform/teacherHome/teaInfoMain/js/teaWebQuery.js",
        "/_web/_platform/teacherHome/teaInfoMain/js/tecSearch.js",
    ]:
        jr = s.get(BASE + path, timeout=30)
        print(f"\n=== {path} ({len(jr.text)} bytes) ===")
        for m in re.finditer(r"https?://[^\s'\"]+|/[_\w][^\s'\"]*", jr.text):
            u = m.group(0)
            if any(k in u.lower() for k in ("ajax", "json", "tea", "col", "list", "query", "portlet")):
                print(" ", u[:120])
        print(jr.text[:1500])


def probe_apis():
    s = session()
    js = s.get(
        BASE + "/_web/_platform/teacherHome/teaInfoMain/js/teaWebQuery.js", timeout=30
    ).text
    apis = sorted(set(re.findall(r"fetch[A-Za-z]+\.do[^\"'\s]*", js)))
    print("\nAPI endpoints in teaWebQuery.js:")
    for a in apis:
        print(" ", a)

    # department tree
    base_api = (
        BASE
        + "/_web/_platform/teacherHome/manage/fetchDepartClassify.do"
        + "?_p=YXM9MiZ0PTI1JmQ9MTg4JnA9MiZmPTI3Jm09U04mfGJubkNvbHVtblZpcnR1YWxOYW1lPXh5bGIm"
    )
    top = s.get(base_api, timeout=30).json()
    print("\nTop categories:", [(r["displayName"], r["typeId"]) for r in top["rows"]])

    # try sub-level with typeId=915 (学院)
    for suffix in [
        "&typeId=915",
        "?typeId=915",
        "&parentId=915",
        "?parentId=915",
        "&type=915",
    ]:
        url = base_api + suffix
        try:
            data = s.get(url, timeout=30).json()
            rows = data.get("rows") or []
            if rows:
                print(f"\n{suffix} -> {len(rows)} rows, sample:", rows[:3])
        except Exception as e:
            print(suffix, "err", e)

    # search list.htm for more fetch urls
    html = s.get(f"{BASE}/xylb/list.htm", timeout=30).text
    for m in re.findall(r"fetch[A-Za-z]+\.do\?[^\"'\s]+", html):
        print("html api:", m)


if __name__ == "__main__":
    main()
    probe_apis()
