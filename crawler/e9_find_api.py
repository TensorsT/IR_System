# -*- coding: utf-8 -*-
import re
import requests

BASE = "https://web.suda.edu.cn"


def session():
    s = requests.Session()
    s.trust_env = False
    s.headers["User-Agent"] = "Mozilla/5.0"
    return s


def main():
    s = session()
    js = s.get(
        BASE + "/_web/_platform/teacherHome/teaInfoMain/js/teaWebQuery.js", timeout=30
    ).text
    with open(r"e:\信息检索\作业9 IR综合实践项目\teaWebQuery.js", "w", encoding="utf-8") as f:
        f.write(js)

    for kw in ["depart", "Depart", "xylb", "typeId", "orgId", "college", "学院"]:
        idx = 0
        hits = []
        while True:
            i = js.lower().find(kw.lower(), idx)
            if i < 0:
                break
            hits.append(js[max(0, i - 80) : i + 120].replace("\n", " "))
            idx = i + len(kw)
        if hits:
            print(f"\n=== {kw} ({len(hits)} hits) ===")
            for h in hits[:8]:
                print(h)

    html = s.get(BASE + "/xylb/list.htm", timeout=30).text
    with open(r"e:\信息检索\作业9 IR综合实践项目\list.htm", "w", encoding="utf-8") as f:
        f.write(html)

    for pat in [
        r"departmentList[^;]{0,500}",
        r"fetch[A-Za-z]+\.do[^\"']{0,200}",
        r"typeId[^;]{0,200}",
        r"orgId[^;]{0,200}",
    ]:
        ms = re.findall(pat, html, re.I | re.S)
        if ms:
            print(f"\nHTML pattern {pat[:30]}:")
            for m in ms[:5]:
                print(m[:400])


if __name__ == "__main__":
    main()
