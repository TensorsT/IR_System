# -*- coding: utf-8 -*-
"""
作业9：苏州大学教师个人主页爬虫（完整版，温和限速）

数据流：
  学院列表 API -> 多通道教师列表 API（hot + new）-> 个人主页 HTML -> 结构化 JSON + 语料 txt

输出（便于后续 IR）：
  crawled_data/meta.json           学院元数据
  crawled_data/teacher_index.json  教师名录（轻量）
  crawled_data/teachers.json       完整记录
  crawled_data/docno.txt           doc_id <-> 语料文件
  crawled_data/corpus/*.txt        每教师一篇文档（建索引用）
  crawled_data/raw_html/*.html     原始页面备份
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://web.suda.edu.cn"
QUERY_URL = BASE + "/_wp3services/generalQuery?queryObj=teacherHome"
LIST_PAGE = BASE + "/xylb/list.htm"

DEFAULT_COLLEGES = [
    "计算机科学与技术学院（软件学院）",
    "数学科学学院",
    "物理科学与技术学院",
    "电子信息学院",
    "外国语学院",
]

# 爬取参数：0 表示不限制人数，扫完全部 API 页直至官网人数对齐
MAX_PER_COLLEGE = 0
API_PAGE_ROWS = 100
API_PAUSE_EVERY = 6
API_PAUSE_SEC = 8.0
PAGE_PAUSE_EVERY = 10
PAGE_PAUSE_SEC = 10.0
REQUEST_GAP = (3.0, 5.5)

# 重要教师主页（API 易漏采时补种）
SEED_TEACHERS = [
    {
        "name": "周国栋",
        "career": "教授",
        "department": "计算机科学与技术学院（软件学院）",
        "cn_url": "https://web.suda.edu.cn/gdzhou/",
        "college_id": 15,
        "site_id": 15,
    },
]

OUT_DIR = Path(__file__).resolve().parent / "crawled_data"
RAW_HTML_DIR = OUT_DIR / "raw_html"
CORPUS_DIR = OUT_DIR / "corpus"
META_JSON = OUT_DIR / "meta.json"
INDEX_JSON = OUT_DIR / "teacher_index.json"
TEACHERS_JSON = OUT_DIR / "teachers.json"
DOCNO_TXT = OUT_DIR / "docno.txt"
CRAWL_LOG = OUT_DIR / "crawl_log.txt"
STATE_JSON = OUT_DIR / "crawl_state.json"

PHONE_RE = re.compile(
    r"(1[3-9]\d{9})|(\d{3,4}[-\s]?\d{7,8})"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass
class TeacherBrief:
    name: str
    career: str
    department: str
    cn_url: str
    college_id: int
    site_id: int


def make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": LIST_PAGE,
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
    )
    return s


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with CRAWL_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def polite_sleep() -> None:
    time.sleep(random.uniform(*REQUEST_GAP))


def mask_privacy(text: str) -> str:
    text = PHONE_RE.sub("***", text)
    text = EMAIL_RE.sub("***@***", text)
    return text


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/"):
        url = BASE + url
    p = urlparse(url)
    path = (p.path or "/").rstrip("/") or "/"
    return f"{p.scheme or 'https'}://{p.netloc}{path}".lower()


def is_allowed_url(url: str) -> bool:
    p = urlparse(url)
    if p.netloc and "suda.edu.cn" not in p.netloc:
        return False
    path = (p.path or "").lower()
    if path.endswith((".htm", ".html")):
        return True
    if "web.suda.edu.cn" in (p.netloc or "") and re.match(
        r"^/[a-z0-9][a-z0-9_./-]*/?$", path, re.I
    ):
        return True
    return False


def fetch_colleges(session: requests.Session) -> list[dict]:
    fields = [
        "collegeId",
        "collegeName",
        "count",
        "fullDepartName",
        "departCategoryId",
    ]
    return_infos = json.dumps(
        [{"field": f, "name": f} for f in fields], ensure_ascii=False
    )
    data = {
        "siteId": "2",
        "pageIndex": 1,
        "rows": 300,
        "returnInfos": return_infos,
        "articleType": 0,
        "isShowDepart": 1,
        "isDepartUrl": 0,
        "departmentSearch": 1,
        "parentDepartId": 0,
    }
    r = session.post(QUERY_URL, data=data, timeout=45)
    r.raise_for_status()
    return r.json().get("data", [])


def pick_colleges(all_cols: list[dict], names: list[str]) -> list[dict]:
    name_set = set(names)
    picked = [c for c in all_cols if (c.get("collegeName") or "") in name_set]
    if len(picked) != len(name_set):
        missing = name_set - {c.get("collegeName") for c in picked}
        raise RuntimeError(f"未匹配到学院: {missing}")
    return picked


def _api_teacher_page(
    session: requests.Session,
    page_index: int,
    *,
    order_field: str,
    new_search: bool = False,
) -> dict | None:
    return_fields = [
        {"field": "title", "name": "title"},
        {"field": "career", "name": "career"},
        {"field": "department", "name": "department"},
        {"field": "cnUrl", "name": "cnUrl"},
        {"field": "siteId", "name": "siteId"},
    ]
    conditions = json.dumps(
        [
            {"field": "language", "value": "1", "judge": "="},
            {"field": "published", "value": "1", "judge": "="},
        ],
        ensure_ascii=False,
    )
    orders = json.dumps([{"field": order_field, "type": "desc"}], ensure_ascii=False)
    data = {
        "siteId": "2",
        "pageIndex": page_index,
        "rows": API_PAGE_ROWS,
        "conditions": conditions,
        "orders": orders,
        "returnInfos": json.dumps(return_fields, ensure_ascii=False),
        "articleType": 1,
        "level": 0,
    }
    if new_search:
        data["newSearch"] = 1
    r = session.post(QUERY_URL, data=data, timeout=45)
    if r.status_code != 200:
        log(f"API 失败 channel={order_field} page={page_index} status={r.status_code}")
        return None
    return r.json()


def collect_teachers_for_colleges(
    session: requests.Session,
    colleges: list[dict],
    max_per_college: int,
) -> list[TeacherBrief]:
    target_ids = {int(c["collegeId"]) for c in colleges}
    id_to_name = {int(c["collegeId"]): c.get("collegeName", "") for c in colleges}
    official_counts = {
        int(c["collegeId"]): int(c.get("count") or 0) for c in colleges
    }
    unlimited = max_per_college <= 0
    buckets: dict[int, dict[str, TeacherBrief]] = {i: {} for i in target_ids}
    api_calls = 0

    def cap_for(college_id: int) -> int:
        if unlimited:
            return 999999
        return max_per_college

    def all_targets_reached() -> bool:
        return all(
            len(buckets[i]) >= official_counts.get(i, 0) for i in target_ids
        )

    def ingest(row: dict) -> bool:
        """返回是否新增一名教师。"""
        site_id = int(row.get("siteId") or 0)
        if site_id not in target_ids:
            return False
        url = normalize_url(row.get("cnUrl") or "")
        name = (row.get("title") or "").strip()
        if not name or not url:
            return False
        if len(buckets[site_id]) >= cap_for(site_id):
            return False
        if url in buckets[site_id]:
            return False
        buckets[site_id][url] = TeacherBrief(
            name=name,
            career=row.get("career") or "",
            department=row.get("department") or id_to_name.get(site_id, ""),
            cn_url=url,
            college_id=site_id,
            site_id=site_id,
        )
        return True

    def progress_line() -> str:
        parts = []
        for i in sorted(target_ids):
            off = official_counts.get(i, 0)
            got = len(buckets[i])
            parts.append(f"{id_to_name[i]}:{got}/{off}")
        return " | ".join(parts)

    # hot + new 双通道扫到最后一页，确保名录尽量完整
    channels = [("hot", "hot", False), ("new", "new", True)]
    for ch_name, order_field, new_flag in channels:
        log(f"教师列表通道: {ch_name}（全量翻页）")
        page = 1
        last_page = 1
        while page <= last_page:
            payload = _api_teacher_page(
                session, page, order_field=order_field, new_search=new_flag
            )
            api_calls += 1
            if api_calls % API_PAUSE_EVERY == 0:
                log(f"API 已请求 {api_calls} 次，休息 {API_PAUSE_SEC:.0f}s …")
                time.sleep(API_PAUSE_SEC)
            else:
                polite_sleep()

            if not payload:
                break
            rows = payload.get("data") or []
            if not rows and unlimited and all_targets_reached():
                log(f"  {ch_name} 第 {page} 页无数据且五院已满，提前结束")
                break
            if not rows:
                break
            added = sum(1 for row in rows if ingest(row))
            last_page = int(payload.get("lastPageNum") or page)
            log(
                f"  {ch_name} 第 {page}/{last_page} 页 "
                f"本页 {len(rows)} 条，新增 {added} | {progress_line()}"
            )
            if unlimited and all_targets_reached() and added == 0:
                log(f"  {ch_name} 配额已满且本页无新增，提前结束")
                break
            if page >= last_page:
                break
            page += 1

    # 补种
    for seed in SEED_TEACHERS:
        cid = int(seed["college_id"])
        if cid not in target_ids:
            continue
        url = normalize_url(seed["cn_url"])
        if url and url not in buckets[cid]:
            buckets[cid][url] = TeacherBrief(
                name=seed["name"],
                career=seed.get("career", ""),
                department=seed.get("department", id_to_name.get(cid, "")),
                cn_url=url,
                college_id=cid,
                site_id=cid,
            )
            log(f"补种教师: {seed['name']} ({id_to_name.get(cid)})")

    result: list[TeacherBrief] = []
    for cid in sorted(target_ids):
        lst = list(buckets[cid].values())
        result.extend(lst)
        official = official_counts.get(cid, 0)
        got = len(lst)
        flag = "OK" if got >= official else "不足"
        log(f"{id_to_name[cid]}: 名录 {got} / 官网 {official} [{flag}]")

    if unlimited and not all_targets_reached():
        log(
            "警告：部分学院名录仍少于官网人数，可能 API 未收录或 count 字段滞后。"
        )
    return result


def extract_sections(soup: BeautifulSoup) -> dict[str, str]:
    """按教师主页常见栏目抽取文本块。"""
    sections: dict[str, str] = {}
    text = soup.get_text("\n", strip=True)
    # 栏目锚点：研究领域、个人简介、论文 等
    markers = [
        "研究领域",
        "研究兴趣",
        "个人简介",
        "研究方向",
        "开授课程",
        "科研项目",
        "论文",
        "科研成果",
        "教育经历",
        "工作经历",
    ]
    lines = text.split("\n")
    current = "_header"
    buf: list[str] = []
    for line in lines:
        if line in markers or any(line.startswith(m) for m in markers[:4]):
            if buf:
                sections[current] = "\n".join(buf).strip()
            current = line
            buf = []
        else:
            buf.append(line)
    if buf:
        sections[current] = "\n".join(buf).strip()
    return sections


def extract_teacher_page(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    h2 = soup.find("h2")
    name = h2.get_text(strip=True).replace(" ", "") if h2 else title

    sections = extract_sections(soup)
    research = (
        sections.get("研究领域")
        or sections.get("研究兴趣")
        or sections.get("研究方向")
        or ""
    )
    intro = sections.get("个人简介") or ""
    papers_block = sections.get("论文") or ""

    text = mask_privacy(soup.get_text("\n", strip=True))
    # IR 用语料：姓名 + 学院 + 职称 + 研究方向 + 简介 + 论文（截断防膨胀）
    corpus_parts = [
        f"姓名:{name}",
        f"页面标题:{title}",
    ]
    if research:
        corpus_parts.append(f"研究领域:{research[:2000]}")
    if intro:
        corpus_parts.append(f"个人简介:{intro[:4000]}")
    if papers_block:
        corpus_parts.append(f"论文:{papers_block[:4000]}")
    corpus_text = "\n".join(corpus_parts)

    paper_titles: list[str] = []
    for a in soup.find_all("a", href=True):
        t = a.get_text(strip=True)
        if t and 4 < len(t) < 100:
            paper_titles.append(t)
    paper_titles = list(dict.fromkeys(paper_titles))[:40]

    return {
        "url": url,
        "title": title,
        "name": name,
        "research_direction": research[:500],
        "personal_intro": intro[:3000],
        "papers_text": papers_block[:5000],
        "paper_titles_sample": paper_titles,
        "sections": {k: v[:3000] for k, v in sections.items() if v},
        "corpus_text": corpus_text[:15000],
        "full_text": text[:15000],
    }


def load_existing_records() -> dict[str, dict]:
    if not TEACHERS_JSON.exists():
        return {}
    try:
        data = json.loads(TEACHERS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {normalize_url(r.get("url", "")): r for r in data if r.get("url")}


def crawl_teacher_pages(
    session: requests.Session,
    teachers: list[TeacherBrief],
    *,
    resume: bool = True,
) -> list[dict]:
    existing = load_existing_records() if resume else {}
    records: list[dict] = []
    page_count = 0
    skipped = 0

    for t in teachers:
        url = normalize_url(t.cn_url)
        if not url:
            continue
        if not is_allowed_url(url):
            log(f"跳过非目标 URL: {url}")
            continue

        if resume and url in existing:
            records.append(existing[url])
            skipped += 1
            continue

        page_count += 1
        if page_count % PAGE_PAUSE_EVERY == 0:
            log(f"已下载 {page_count} 个新主页，休息 {PAGE_PAUSE_SEC:.0f}s …")
            time.sleep(PAGE_PAUSE_SEC)
        else:
            polite_sleep()

        try:
            r = None
            for attempt in range(3):
                r = session.get(url, timeout=45)
                r.encoding = r.apparent_encoding or "utf-8"
                if r.status_code in (503, 429):
                    wait = 30 * (attempt + 1)
                    log(f"{r.status_code} 限流，等待 {wait}s 后重试: {t.name}")
                    time.sleep(wait)
                    continue
                if r.status_code == 403 and attempt < 2:
                    log(f"403，等待 20s 后重试: {t.name}")
                    time.sleep(20)
                    continue
                break
            if r is None:
                raise requests.RequestException("no response")
            r.raise_for_status()
        except requests.RequestException as e:
            log(f"下载失败 {t.name} {url}: {e}")
            continue

        slug = urlparse(url).path.strip("/").replace("/", "_") or "index"
        slug = re.sub(r"[^\w\-]", "_", slug)[:80]
        html_path = RAW_HTML_DIR / f"{slug}.html"
        RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)
        html_path.write_text(r.text, encoding="utf-8")

        detail = extract_teacher_page(r.text, url)
        detail.update(asdict(t))
        detail["html_file"] = html_path.name
        records.append(detail)
        log(f"OK [{page_count}] {t.name} ({t.department})")

    if skipped:
        log(f"断点续爬：跳过已存在 {skipped} 条，新下载 {page_count} 条")
    # 合并：保持输入顺序，以新记录覆盖
    by_url = {**existing, **{normalize_url(r["url"]): r for r in records}}
    ordered = []
    seen = set()
    for t in teachers:
        u = normalize_url(t.cn_url)
        if u in by_url and u not in seen:
            ordered.append(by_url[u])
            seen.add(u)
    for u, r in by_url.items():
        if u not in seen:
            ordered.append(r)
    return ordered


def write_ir_artifacts(
    teachers: list[dict], colleges: list[dict], max_per_college: int = 0
) -> None:
    if CORPUS_DIR.exists():
        for old in CORPUS_DIR.glob("*.txt"):
            old.unlink()
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    doc_lines = []

    for i, t in enumerate(teachers, start=1):
        name = t.get("name") or f"teacher_{i}"
        dept = t.get("department") or ""
        fname = f"{i:04d}_{name}_{dept[:8]}.txt".replace("/", "_")
        fname = re.sub(r'[<>:"|?*\\]', "_", fname)
        corpus_path = CORPUS_DIR / fname
        body = t.get("corpus_text") or t.get("full_text") or ""
        header = (
            f"doc_id: {i}\n"
            f"name: {name}\n"
            f"career: {t.get('career', '')}\n"
            f"department: {dept}\n"
            f"college_id: {t.get('college_id', '')}\n"
            f"url: {t.get('url', '')}\n"
            "---\n"
        )
        corpus_path.write_text(header + body, encoding="utf-8")
        doc_lines.append(f"{i}\t{fname}")
        index.append(
            {
                "doc_id": i,
                "name": name,
                "career": t.get("career"),
                "department": dept,
                "college_id": t.get("college_id"),
                "url": t.get("url"),
                "corpus_file": fname,
            }
        )

    DOCNO_TXT.write_text("\n".join(doc_lines) + "\n", encoding="utf-8")
    INDEX_JSON.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    META_JSON.write_text(
        json.dumps(
            {
                "source": LIST_PAGE,
                "colleges": colleges,
                "teacher_count": len(teachers),
                "max_per_college": max_per_college if max_per_college > 0 else "unlimited",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def save_state(teachers: list[TeacherBrief], colleges: list[dict]) -> None:
    STATE_JSON.write_text(
        json.dumps(
            {
                "colleges": colleges,
                "teacher_briefs": [asdict(t) for t in teachers],
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main(
    college_names: list[str] | None = None,
    max_per_college: int = MAX_PER_COLLEGE,
    resume: bool = True,
) -> None:
    college_names = college_names or DEFAULT_COLLEGES
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CRAWL_LOG.write_text("", encoding="utf-8")

    session = make_session()
    log("=" * 50)
    mode = "全量（无人数上限）" if max_per_college <= 0 else f"每学院最多 {max_per_college} 人"
    log("苏州大学教师主页爬虫 — " + mode)
    log(f"目标学院: {', '.join(college_names)}")

    all_cols = fetch_colleges(session)
    colleges = pick_colleges(all_cols, college_names)
    polite_sleep()

    teachers = collect_teachers_for_colleges(
        session, colleges, max_per_college=max_per_college
    )
    save_state(teachers, colleges)
    log(f"教师名录合计 {len(teachers)} 人，开始下载主页 …")

    records = crawl_teacher_pages(session, teachers, resume=resume)
    TEACHERS_JSON.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_ir_artifacts(records, colleges, max_per_college)

    by_college: dict[str, int] = {}
    for r in records:
        d = r.get("department") or "未知"
        by_college[d] = by_college.get(d, 0) + 1
    log("各学院实际入库人数: " + json.dumps(by_college, ensure_ascii=False))
    log(f"完成: {len(records)} 条 -> {TEACHERS_JSON}")
    log(f"语料目录: {CORPUS_DIR} ({len(list(CORPUS_DIR.glob('*.txt')))} 篇)")
    log(f"docno: {DOCNO_TXT}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="苏大教师主页爬虫")
    parser.add_argument(
        "--max-per-college",
        type=int,
        default=MAX_PER_COLLEGE,
        help="每学院人数上限，0 表示全量（默认 0）",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="不跳过已爬取主页，全部重新下载",
    )
    args = parser.parse_args()
    main(max_per_college=args.max_per_college, resume=not args.no_resume)
