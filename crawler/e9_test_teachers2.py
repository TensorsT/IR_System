# -*- coding: utf-8 -*-
import json
import time
import requests

BASE = "https://web.suda.edu.cn"
URL = BASE + "/_wp3services/generalQuery?queryObj=teacherHome"


def session():
    s = requests.Session()
    s.trust_env = False
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
            "Referer": BASE + "/ssjglm/list.htm",
        }
    )
    return s


def post_query(s, data):
    r = s.post(URL, data=data, timeout=30)
    return r.status_code, r.text[:300], r


def build_payload(site_id, org_id, rows=10):
    conditions = [
        {"field": "language", "value": "1", "judge": "="},
        {"field": "ownDepartment", "value": str(org_id), "judge": "="},
        {"field": "published", "value": "1", "judge": "="},
    ]
    return_infos = [
        {"field": "title", "name": "title"},
        {"field": "career", "name": "career"},
        {"field": "cnUrl", "name": "cnUrl"},
        {"field": "department", "name": "department"},
    ]
    return {
        "siteId": str(site_id),
        "pageIndex": 1,
        "rows": rows,
        "conditions": json.dumps(conditions, ensure_ascii=False),
        "orders": "[]",
        "returnInfos": json.dumps(return_infos, ensure_ascii=False),
        "articleType": "1",
        "level": "0",
        "deptTecOrder": "1_1",
        "pageEvent": "dataSearchByPageIndex",
    }


def main():
    s = session()
    variants = [
        ("siteId=orgId (15)", build_payload(15, 15)),
        ("siteId=2, dept=15", build_payload(2, 15)),
        ("siteId=2, no dept", {**build_payload(2, 15), "conditions": json.dumps([{"field": "language", "value": "1", "judge": "="}])}),
    ]
    for name, data in variants:
        time.sleep(2)
        code, preview, r = post_query(s, data)
        print(f"\n=== {name} -> HTTP {code} ===")
        print(preview)
        if code == 200 and r.text.strip().startswith("{"):
            j = r.json()
            print("total", j.get("total"), "sample", j.get("data", [])[:2])


if __name__ == "__main__":
    main()
