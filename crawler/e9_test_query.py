# -*- coding: utf-8 -*-
import json
import requests

BASE = "https://web.suda.edu.cn"


def session():
    s = requests.Session()
    s.trust_env = False
    s.headers["User-Agent"] = "Mozilla/5.0"
    return s


def general_query(s, return_fields, extra=None):
    org_return = json.dumps(
        [{"field": f, "name": f} for f in return_fields], ensure_ascii=False
    )
    data = {
        "siteId": "2",
        "pageIndex": 1,
        "rows": 300,
        "returnInfos": org_return,
        "articleType": 0,
        "isShowDepart": 1,
        "isDepartUrl": 0,
        "departmentSearch": 1,
        "parentDepartId": 0,
    }
    if extra:
        data.update(extra)
    url = BASE + "/_wp3services/generalQuery?queryObj=teacherHome"
    r = s.post(url, data=data, timeout=30)
    return r.json()


def main():
    s = session()
    fields = [
        "collegeId",
        "collegeName",
        "collegeEnName",
        "url",
        "count",
        "fullDepartName",
        "departCategoryId",
    ]
    result = general_query(s, fields)
    print("keys", result.keys())
    print("total", result.get("total"), "data len", len(result.get("data", [])))
    for row in result.get("data", []):
        name = row.get("collegeName") or row.get("fullDepartName")
        if "计算机" in (name or "") or "软件" in (name or ""):
            print(">>>", row)
    print("\nFirst 8 colleges:")
    for row in result.get("data", [])[:8]:
        print(row.get("collegeName"), row.get("collegeId"), row.get("url"), row.get("count"))


if __name__ == "__main__":
    main()
