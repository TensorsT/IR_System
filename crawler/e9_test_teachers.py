# -*- coding: utf-8 -*-
import json
import requests

BASE = "https://web.suda.edu.cn"


def session():
    s = requests.Session()
    s.trust_env = False
    s.headers["User-Agent"] = "Mozilla/5.0"
    return s


def query_teachers(s, org_id, rows=20, page_index=1):
    conditions = [
        {"field": "language", "value": "1", "judge": "="},
        {"field": "ownDepartment", "value": str(org_id), "judge": "="},
        {"field": "published", "value": "1", "judge": "="},
    ]
    return_infos = [
        {"field": "title", "name": "title"},
        {"field": "career", "name": "career"},
        {"field": "visitCount", "name": "visitCount"},
        {"field": "headerPic", "name": "headerPic"},
        {"field": "cnUrl", "name": "cnUrl"},
        {"field": "department", "name": "department"},
        {"field": "publishStatus", "name": "publishStatus"},
    ]
    data = {
        "siteId": str(org_id),
        "pageIndex": page_index,
        "rows": rows,
        "conditions": json.dumps(conditions, ensure_ascii=False),
        "orders": "[]",
        "returnInfos": json.dumps(return_infos, ensure_ascii=False),
        "articleType": "1",
        "level": "0",
        "deptTecOrder": "1_1",
    }
    url = BASE + "/_wp3services/generalQuery?queryObj=teacherHome"
    r = s.post(
        url,
        data=data,
        timeout=30,
        headers={"Referer": f"{BASE}/ssjglm/list.htm"},
    )
    print("status", r.status_code, "body[:200]", r.text[:200])
    return r.json()


def main():
    s = session()
    result = query_teachers(s, org_id=15, rows=5)
    print("total", result.get("total"), "pages", result.get("pages"))
    for t in result.get("data", [])[:5]:
        print(t)


if __name__ == "__main__":
    main()
