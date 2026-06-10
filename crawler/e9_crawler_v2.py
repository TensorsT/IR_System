# -*- coding: utf-8 -*-
"""
作业9：苏州大学教师个人主页爬虫 V2（高质量数据版，温和反爬）

相对 V1 的关键改进（针对已发现的数据质量问题）：
  1. 结构化抽取：按页面模块 `div.post.mbox`（标题 `.tt .tit` + 正文 `div.con`）
     精准取栏目内容，不再对整页扁平文本按关键词硬切，避免“研究方向只取到引导
     词”“片段混入导航菜单(返回首页/欢迎登录/导航)”等问题。
  2. 强力清洗：去导航行 / 空标签行(如 "联系电话：") / 访问计数 / 重复段落，
     折叠空白，裁剪页脚。
  3. 字段校验：研究方向/简介/论文若只剩残缺标签或引导词(如 "研究方向：")，
     判定为缺失并置空，绝不把无意义标签当正文存储。
  4. 健壮编码：按 响应头 charset -> meta charset -> utf-8 -> gb18030 顺序解码，
     修复保存乱码(如 "技术学�?")。
  5. 质量报告：输出 quality_report.json，统计各字段完整率并列出缺失/过短字段，
     方便核对“高质量”目标。

反爬策略（温和、可配置）：
  - 随机请求间隔，分批长休息；
  - 429/503 指数退避并尊重 Retry-After；403 退避重试；
  - 仅抓 suda.edu.cn 静态主页；连接复用；忽略系统代理。

数据流：
  学院列表 API -> 多通道教师列表 API(hot + new) -> 个人主页 HTML -> 结构化 JSON + 语料 txt

输出（与 IR 系统对接，写入同一 crawled_data 目录）：
  meta.json / teacher_index.json / teachers.json / docno.txt
  corpus/*.txt / raw_html/*.html / quality_report.json

注意：本脚本不会自动运行，请按文末说明手动执行，并先用 --limit 小批量试跑。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://web.suda.edu.cn"
QUERY_URL = BASE + "/_wp3services/generalQuery?queryObj=teacherHome"
LIST_PAGE = BASE + "/xylb/list.htm"

PARSER_VERSION = 2

DEFAULT_COLLEGES = [
    "计算机科学与技术学院（软件学院）",
    "数学科学学院",
    "物理科学与技术学院",
    "电子信息学院",
    "外国语学院",
]

# ---- 爬取 / 反爬参数（偏保守，可按需调整）----
MAX_PER_COLLEGE = 0            # 0 = 不限人数，扫到与官网人数对齐
API_PAGE_ROWS = 100
API_PAUSE_EVERY = 6            # 每多少次列表 API 调用长休息
API_PAUSE_SEC = 10.0
PAGE_PAUSE_EVERY = 10          # 每下载多少个主页长休息
PAGE_PAUSE_SEC = 12.0
REQUEST_GAP = (4.0, 7.0)       # 每次请求之间的随机间隔（秒）
MAX_RETRY = 4                  # 单页最大重试次数
TIMEOUT = 45

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# 重要教师（API 偶发漏采时补种）
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

OUT_DIR = Path(__file__).resolve().parent.parent / "crawled_data"
RAW_HTML_DIR = OUT_DIR / "raw_html"
CORPUS_DIR = OUT_DIR / "corpus"
META_JSON = OUT_DIR / "meta.json"
INDEX_JSON = OUT_DIR / "teacher_index.json"
TEACHERS_JSON = OUT_DIR / "teachers.json"
DOCNO_TXT = OUT_DIR / "docno.txt"
CRAWL_LOG = OUT_DIR / "crawl_log.txt"
STATE_JSON = OUT_DIR / "crawl_state.json"
QUALITY_JSON = OUT_DIR / "quality_report.json"

PHONE_RE = re.compile(r"(1[3-9]\d{9})|(0\d{2,3}[-\s]?\d{7,8})|(\d{3,4}[-\s]\d{7,8})")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# 站点模板 / 导航 / 栏目标题等噪音行
NAV_LINES = frozenset(
    {
        "教师个人主页", "English", "返回首页", "欢迎登录", "导航",
        "个人资料", "个人概况", "研究领域", "研究方向", "研究兴趣",
        "开授课程", "论文", "科研项目", "科研成果", "荣誉及奖励",
        "招生信息", "相关教师", "最新更新", "教育经历", "工作经历",
        "社会职务", "个人简介", "访问", "科学研究", "基本信息",
        "联系方式", "教学", "课程", "科研团队", "首页",
        "主页", "科研", "项目", "荣誉", "成果", "语言切换",
    }
)

# 残缺值（仅标签/引导词，应判定为缺失）
INCOMPLETE_VALUES = frozenset(
    {"研究方向", "研究领域", "研究兴趣", "简介", "个人简介", "论文", "科研成果"}
)

# 栏目标题 -> 规范字段（含中文别名与英文标题）
SECTION_MAP = {
    "research": [
        "研究领域", "研究方向", "研究兴趣", "主要研究方向", "研究概况",
        "research interests", "research interest", "research areas", "research area",
        "research",
    ],
    "intro": [
        "个人简介", "个人简历", "简介", "简历", "个人概况", "个人介绍",
        "biography", "bio", "about me", "about", "profile",
    ],
    "papers": [
        "论文", "科研成果", "代表性论文", "论文成果", "发表论文", "学术成果",
        "会议论文", "期刊论文", "学术论文", "代表性成果", "代表论文", "主要论文",
        "著作", "专利",
        "publications", "publication", "selected publications", "papers", "paper",
    ],
    "projects": ["科研项目", "主持项目", "承担项目", "科研课题", "projects", "grants"],
    "courses": ["开授课程", "教学", "课程", "teaching", "courses"],
    "edu": ["教育经历", "教育背景", "education"],
    "work": ["工作经历", "工作背景", "experience"],
    "honor": ["荣誉及奖励", "获奖", "荣誉奖励", "honors", "awards"],
}
SKIP_TITLES = frozenset({"相关教师", "个人资料", "访问", "导航"})

# 子页面链接关键词（自建多页站点：科研/论文/简介 常拆到独立页面）
SUBPAGE_HINTS = {
    "research": ["research", "研究", "科研"],
    "papers": ["publication", "papers", "paper", "论文", "成果"],
    "intro": ["biography", "about", "简介", "个人", "主页", "home"],
}

# 扁平分节时用于“截断当前小节”的非目标标题（避免研究领域里混入兼职/招生等）
STOP_HEADINGS = frozenset(
    {
        "社会兼职", "社会职务", "研究生", "报考咨询", "招生信息", "最新消息",
        "最新动态", "最新", "相关链接", "校训", "相关教师", "个人资料",
        "联系方式", "项目", "报考",
        "what's new", "news", "contact", "links", "teaching", "honors",
        "awards", "projects", "students", "service",
    }
)


@dataclass
class TeacherBrief:
    name: str
    career: str
    department: str
    cn_url: str
    college_id: int
    site_id: int


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # 忽略系统代理，避免被本地代理干扰
    s.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": LIST_PAGE,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
    if not text:
        return text
    text = EMAIL_RE.sub("***@***", text)
    text = PHONE_RE.sub("***", text)
    return text


def decode_response(r: requests.Response) -> str:
    """按 头部 charset -> meta charset -> utf-8 -> gb18030 顺序稳健解码。"""
    content = r.content
    candidates: list[str] = []

    ctype = r.headers.get("Content-Type", "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        candidates.append(m.group(1))

    head = content[:3072].decode("ascii", "ignore")
    m = re.search(r"charset=[\"']?([\w-]+)", head, re.I)
    if m:
        candidates.append(m.group(1))

    candidates += ["utf-8", "gb18030"]

    for enc in candidates:
        if not enc:
            continue
        try:
            decoded = content.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
        # 选第一个没有(或极少)替换符的解码结果
        if decoded.count("\ufffd") <= 2:
            return decoded
    return content.decode("utf-8", "ignore")


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
    """只允许 suda.edu.cn 下的静态主页（避免越界爬取）。"""
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


# --------------------------------------------------------------------------- #
# 列表 API
# --------------------------------------------------------------------------- #
def fetch_colleges(session: requests.Session) -> list[dict]:
    fields = ["collegeId", "collegeName", "count", "fullDepartName", "departCategoryId"]
    return_infos = json.dumps([{"field": f, "name": f} for f in fields], ensure_ascii=False)
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
    r = session.post(QUERY_URL, data=data, timeout=TIMEOUT)
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
    r = session.post(QUERY_URL, data=data, timeout=TIMEOUT)
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
    official_counts = {int(c["collegeId"]): int(c.get("count") or 0) for c in colleges}
    unlimited = max_per_college <= 0
    buckets: dict[int, dict[str, TeacherBrief]] = {i: {} for i in target_ids}
    api_calls = 0

    def cap_for(_college_id: int) -> int:
        return 999999 if unlimited else max_per_college

    def all_targets_reached() -> bool:
        return all(len(buckets[i]) >= official_counts.get(i, 0) for i in target_ids)

    def ingest(row: dict) -> bool:
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
        return " | ".join(
            f"{id_to_name[i]}:{len(buckets[i])}/{official_counts.get(i, 0)}"
            for i in sorted(target_ids)
        )

    channels = [("hot", "hot", False), ("new", "new", True)]
    for ch_name, order_field, new_flag in channels:
        log(f"教师列表通道: {ch_name}（全量翻页）")
        page = 1
        last_page = 1
        while page <= last_page:
            payload = _api_teacher_page(session, page, order_field=order_field, new_search=new_flag)
            api_calls += 1
            if api_calls % API_PAUSE_EVERY == 0:
                log(f"API 已请求 {api_calls} 次，休息 {API_PAUSE_SEC:.0f}s …")
                time.sleep(API_PAUSE_SEC)
            else:
                polite_sleep()

            if not payload:
                break
            rows = payload.get("data") or []
            if not rows:
                break
            added = sum(1 for row in rows if ingest(row))
            last_page = int(payload.get("lastPageNum") or page)
            log(
                f"  {ch_name} 第 {page}/{last_page} 页 本页 {len(rows)} 条，"
                f"新增 {added} | {progress_line()}"
            )
            if unlimited and all_targets_reached() and added == 0:
                log(f"  {ch_name} 配额已满且本页无新增，提前结束")
                break
            if page >= last_page:
                break
            page += 1

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
    return result


# --------------------------------------------------------------------------- #
# 高质量结构化抽取
# --------------------------------------------------------------------------- #
def _clean_lines(text: str, drop_labels: frozenset[str] = frozenset()) -> list[str]:
    out: list[str] = []
    prev = None
    for raw_line in text.split("\n"):
        s = re.sub(r"[\u00a0\u3000\t ]+", " ", raw_line).strip()
        if not s:
            continue
        if s in NAV_LINES or s in drop_labels:
            continue
        if re.fullmatch(r"\d{1,6}", s):  # 访问计数等
            continue
        if len(s) <= 12 and s.endswith(("：", ":")):  # 空标签行
            continue
        if s == prev:  # 折叠连续重复
            continue
        out.append(s)
        prev = s
    return out


def _clean_block(con_text: str, title: str) -> str:
    drop = frozenset({title, f"{title}：", f"{title}:"})
    lines = _clean_lines(con_text, drop)
    return "\n".join(lines).strip()


def _looks_incomplete(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return True
    if v.endswith(("：", ":")):
        return True
    core = v.rstrip("：: 。.").strip()
    if core in INCOMPLETE_VALUES:
        return True
    return len(core) < 2


_RESEARCH_LEADS = [
    "主要研究方向", "研究方向", "主要研究领域", "研究领域",
    "主要研究兴趣", "研究兴趣", "主要兴趣与研究领域",
    "主要从事", "长期从事", "目前从事", "致力于", "专注于",
]


# 出现这些词说明截出来的多半是教学/联系/行政信息，不是研究方向
_RESEARCH_NOISE_RE = re.compile(r"(教学|课程|邮件|联系|报考|招生|同学|至今|主页|欢迎|发表)")


def _research_from_intro(intro: str) -> str:
    """当研究方向缺失时，从个人简介中抽取研究方向子句（规则保底，无需 LLM）。"""
    if not intro:
        return ""
    text = re.sub(r"[\u00a0\u3000\t ]+", " ", intro)
    for lead in _RESEARCH_LEADS:
        idx = text.find(lead)
        if idx < 0:
            continue
        tail = text[idx + len(lead):]
        tail = re.sub(r"^[为是：:，,的有]*(包括|涉及|集中在|主要为|主要是|聚焦于)?[为是：:，,的有\s]*", "", tail)
        seg = re.split(r"[。\n；;！!]", tail, 1)[0].strip(" 、,，.;:：-")
        seg = re.sub(r"等(方面)?(的)?(研究|工作)?$", "", seg).strip(" 、,，.;:：-")
        if not (2 <= len(seg) <= 80) or _looks_incomplete(seg):
            continue
        if _RESEARCH_NOISE_RE.search(seg):
            continue
        # 需含中文，或足够长的英文短语，过滤 "(To" 这类残片
        if not re.search(r"[\u4e00-\u9fff]", seg) and len(re.findall(r"[A-Za-z]", seg)) < 4:
            continue
        return seg
    return ""


def _canonical_field(title: str) -> str | None:
    t = (title or "").strip().rstrip("：:").strip().lower()
    if not t:
        return None
    for canon, names in SECTION_MAP.items():
        for n in names:
            nl = n.lower()
            if t == nl or t.startswith(nl):
                return canon
    return None


def _is_stop_heading(line: str) -> bool:
    t = line.strip().rstrip("：:").strip().lower()
    return t in STOP_HEADINGS


def extract_sections_structured(soup: BeautifulSoup) -> dict[str, str]:
    """从 div.post.mbox 模块按 标题+正文(con) 精准抽取，返回 规范字段 -> 文本。"""
    boxes = (
        soup.select("div.post.mbox")
        or soup.select("div.post")
        or soup.select("div.mbox")
    )
    sections: dict[str, str] = {}
    for box in boxes:
        tt = box.select_one(".tt .tit") or box.select_one(".tt") or box.find(["h2", "h3", "h4"])
        title = tt.get_text(" ", strip=True) if tt else ""
        if not title:
            disp = box.select_one(".fws_displayTitle")
            title = disp.get_text(" ", strip=True) if disp else ""
        title = title.strip()
        if not title or title in SKIP_TITLES:
            continue
        canon = _canonical_field(title)
        if not canon or canon in sections:
            continue
        con = box.select_one("div.con") or box
        body = _clean_block(con.get_text("\n", strip=True), title)
        if body and not _looks_incomplete(body):
            sections[canon] = body
    return sections


def extract_sections_flat(soup: BeautifulSoup) -> dict[str, str]:
    """兜底：自建/英文页面按标题行切分（含英文标题与停止标题，已加清洗）。"""
    text = soup.get_text("\n", strip=True)
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    buf: list[str] = []

    def flush():
        if current and buf:
            blocks.setdefault(current, []).extend(buf)

    for raw in text.split("\n"):
        s = raw.strip()
        # 仅把较短的行当作潜在标题，避免把正文句子误判为分节点
        canon = _canonical_field(s) if 0 < len(s) <= 24 else None
        if canon:
            flush()
            current = canon
            buf = []
            continue
        if len(s) <= 24 and _is_stop_heading(s):
            flush()
            current = None
            buf = []
            continue
        if current:
            buf.append(s)
    flush()

    sections: dict[str, str] = {}
    for canon, raw_lines in blocks.items():
        body = "\n".join(_clean_lines("\n".join(raw_lines))).strip()
        if body and not _looks_incomplete(body):
            sections.setdefault(canon, body)
    return sections


def find_subpage_links(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
    """在自建多页站点中定位 科研/论文/简介 等子页面链接。"""
    out: dict[str, str] = {}
    base_netloc = urlparse(base_url).netloc
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        low = (text + " " + href).lower()
        absu = normalize_url(urljoin(base_url, href))
        if not absu or not is_allowed_url(absu):
            continue
        if urlparse(absu).netloc != base_netloc:
            continue
        if normalize_url(base_url) == absu:
            continue
        for canon, hints in SUBPAGE_HINTS.items():
            if canon in out:
                continue
            if any(h in low for h in hints):
                out[canon] = absu
    return out


def extract_main_text(soup: BeautifulSoup) -> str:
    """整页正文（去导航/计数/空标签/重复行），用于全文摘录与子页兜底。"""
    return "\n".join(_clean_lines(soup.get_text("\n", strip=True)))


def parse_page_sections(html: str) -> tuple[BeautifulSoup, str, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    sections = extract_sections_structured(soup)
    # 主字段缺失时用扁平/英文标题兜底补齐
    if not (sections.get("research") and sections.get("intro") and sections.get("papers")):
        for k, v in extract_sections_flat(soup).items():
            sections.setdefault(k, v)
    return soup, title, sections


def finalize_record(
    sections: dict[str, str],
    brief: TeacherBrief,
    url: str,
    title: str,
    full_text: str = "",
) -> dict:
    name = (brief.name or "").strip() or title
    research = mask_privacy(sections.get("research", ""))[:600]
    intro = mask_privacy(sections.get("intro", ""))[:3000]
    papers = mask_privacy(sections.get("papers", ""))[:5000]

    research = "" if _looks_incomplete(research) else research
    intro = "" if _looks_incomplete(intro) else intro
    papers = "" if _looks_incomplete(papers) else papers

    # 研究方向缺失时，从简介里规则保底抽取（有些老师把方向写在简介中）
    if not research and intro:
        research = _research_from_intro(intro)

    corpus_parts = [f"姓名:{name}", f"学院:{brief.department}", f"职称:{brief.career}"]
    if research:
        corpus_parts.append(f"研究方向:{research}")
    if intro:
        corpus_parts.append(f"个人简介:{intro}")
    if papers:
        corpus_parts.append(f"论文:{papers}")
    corpus_text = "\n".join(corpus_parts)[:15000]

    return {
        "url": url,
        "title": title,
        "name": name,
        "research_direction": research,
        "personal_intro": intro,
        "papers_text": papers,
        "sections": {k: v[:3000] for k, v in sections.items() if v},
        "corpus_text": corpus_text,
        # 全文摘录（已脱敏），供大模型抽取阶段使用，不丢信息
        "full_text": mask_privacy(full_text)[:20000],
        "parser_version": PARSER_VERSION,
    }


def extract_teacher_page(html: str, url: str, brief: TeacherBrief) -> dict:
    """单页抽取（离线/测试用，不跟进子页面）。"""
    soup, title, sections = parse_page_sections(html)
    return finalize_record(sections, brief, url, title, extract_main_text(soup))


# --------------------------------------------------------------------------- #
# 下载（含反爬退避）
# --------------------------------------------------------------------------- #
def fetch_html(session: requests.Session, url: str, name: str) -> str | None:
    for attempt in range(MAX_RETRY):
        try:
            r = session.get(url, timeout=TIMEOUT)
        except requests.RequestException as e:
            wait = 10 * (attempt + 1)
            log(f"请求异常 {name} {url}: {e}，{wait}s 后重试")
            time.sleep(wait)
            continue

        if r.status_code in (429, 503):
            retry_after = r.headers.get("Retry-After")
            wait = int(retry_after) if (retry_after or "").isdigit() else 30 * (2 ** attempt)
            log(f"{r.status_code} 限流，等待 {wait}s 后重试: {name}")
            time.sleep(wait)
            continue
        if r.status_code == 403:
            wait = 20 * (attempt + 1)
            log(f"403，等待 {wait}s 后重试: {name}")
            time.sleep(wait)
            continue
        if r.status_code != 200:
            log(f"状态码 {r.status_code}，放弃: {name} {url}")
            return None
        return decode_response(r)
    log(f"重试耗尽，放弃: {name} {url}")
    return None


def _save_raw(url: str, html: str) -> str:
    slug = urlparse(url).path.strip("/").replace("/", "_") or "index"
    slug = re.sub(r"[^\w\-]", "_", slug)[:80]
    RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_HTML_DIR / f"{slug}.html").write_text(html, encoding="utf-8")
    return f"{slug}.html"


def crawl_one_teacher(session: requests.Session, t: TeacherBrief) -> dict | None:
    """下载主页；若主字段缺失则跟进自建站子页面(科研/论文/简介)补齐。"""
    url = normalize_url(t.cn_url)
    html = fetch_html(session, url, t.name)
    if html is None:
        return None
    html_file = _save_raw(url, html)

    soup, title, sections = parse_page_sections(html)
    full_chunks = [extract_main_text(soup)]

    # 自建多页站点：尽量跟进 科研/论文/简介 等子页面，做到“全量摘录不放过”
    sublinks = find_subpage_links(soup, url)
    fetched: set[str] = {normalize_url(url)}
    for canon, suburl in sublinks.items():
        nsub = normalize_url(suburl)
        if nsub in fetched:
            continue
        # 该字段已在主页拿到，且不是核心子页，则跳过以减少请求
        if sections.get(canon) and canon not in ("research", "papers"):
            continue
        polite_sleep()
        sub_html = fetch_html(session, suburl, f"{t.name}:{canon}")
        fetched.add(nsub)
        if not sub_html:
            continue
        _save_raw(suburl, sub_html)
        sub_soup, _ti, sub_sections = parse_page_sections(sub_html)
        full_chunks.append(extract_main_text(sub_soup))
        if sub_sections.get(canon):
            sections.setdefault(canon, sub_sections[canon])
        else:
            for k, v in sub_sections.items():
                sections.setdefault(k, v)
            # 整页即该主题（如 papers.html / research.html）：标题不规范时
            # 用整页正文兜底赋给该字段，避免“会议论文/期刊论文”这类命名漏抓。
            if not sections.get(canon):
                main = extract_main_text(sub_soup)
                if main and len(main) > 30:
                    sections[canon] = main

    full_text = "\n".join(c for c in full_chunks if c)
    detail = finalize_record(sections, t, url, title, full_text)
    detail.update(asdict(t))
    detail["html_file"] = html_file
    detail["parser_version"] = PARSER_VERSION
    return detail


def load_existing_records() -> dict[str, dict]:
    """仅复用 V2 解析过的记录，旧版记录会被强制重爬以提升质量。"""
    if not TEACHERS_JSON.exists():
        return {}
    try:
        data = json.loads(TEACHERS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {
        normalize_url(r.get("url", "")): r
        for r in data
        if r.get("url") and r.get("parser_version") == PARSER_VERSION
    }


def crawl_teacher_pages(
    session: requests.Session,
    teachers: list[TeacherBrief],
    *,
    resume: bool = True,
    limit: int = 0,
) -> list[dict]:
    existing = load_existing_records() if resume else {}
    records: list[dict] = []
    page_count = 0
    skipped = 0

    todo = teachers[:limit] if limit and limit > 0 else teachers
    for t in todo:
        url = normalize_url(t.cn_url)
        if not url or not is_allowed_url(url):
            if url:
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

        detail = crawl_one_teacher(session, t)
        if detail is None:
            continue
        records.append(detail)

        miss = [
            k
            for k, present in [
                ("研究", bool(detail["research_direction"])),
                ("简介", bool(detail["personal_intro"])),
                ("论文", bool(detail["papers_text"])),
            ]
            if not present
        ]
        tag = "完整" if not miss else "缺:" + "/".join(miss)
        log(f"OK [{page_count}] {t.name} ({t.department}) [{tag}]")

    if skipped:
        log(f"断点续爬：复用 V2 记录 {skipped} 条，新下载 {page_count} 条")

    by_url = {**existing, **{normalize_url(r["url"]): r for r in records}}
    ordered: list[dict] = []
    seen: set[str] = set()
    for t in teachers:
        u = normalize_url(t.cn_url)
        if u in by_url and u not in seen:
            ordered.append(by_url[u])
            seen.add(u)
    for u, r in by_url.items():
        if u not in seen:
            ordered.append(r)
            seen.add(u)
    return ordered


# --------------------------------------------------------------------------- #
# 产出 IR 物料 + 质量报告
# --------------------------------------------------------------------------- #
def write_ir_artifacts(teachers: list[dict], colleges: list[dict], max_per_college: int = 0) -> None:
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
        body = t.get("corpus_text") or ""
        header = (
            f"doc_id: {i}\n"
            f"name: {name}\n"
            f"career: {t.get('career', '')}\n"
            f"department: {dept}\n"
            f"college_id: {t.get('college_id', '')}\n"
            f"url: {t.get('url', '')}\n"
            "---\n"
        )
        (CORPUS_DIR / fname).write_text(header + body, encoding="utf-8")
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
    INDEX_JSON.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    META_JSON.write_text(
        json.dumps(
            {
                "source": LIST_PAGE,
                "colleges": colleges,
                "teacher_count": len(teachers),
                "parser_version": PARSER_VERSION,
                "max_per_college": max_per_college if max_per_college > 0 else "unlimited",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def write_quality_report(teachers: list[dict]) -> None:
    total = len(teachers) or 1

    def has(field: str, t: dict) -> bool:
        return bool((t.get(field) or "").strip())

    n_research = sum(1 for t in teachers if has("research_direction", t))
    n_intro = sum(1 for t in teachers if has("personal_intro", t))
    n_papers = sum(1 for t in teachers if has("papers_text", t))

    missing = [
        {
            "name": t.get("name"),
            "department": t.get("department"),
            "url": t.get("url"),
            "missing": [
                lbl
                for lbl, f in [("研究方向", "research_direction"), ("简介", "personal_intro"), ("论文", "papers_text")]
                if not has(f, t)
            ],
        }
        for t in teachers
        if not (has("research_direction", t) and has("personal_intro", t) and has("papers_text", t))
    ]

    report = {
        "teacher_count": len(teachers),
        "coverage": {
            "research_direction": f"{n_research}/{total} ({n_research / total:.1%})",
            "personal_intro": f"{n_intro}/{total} ({n_intro / total:.1%})",
            "papers_text": f"{n_papers}/{total} ({n_papers / total:.1%})",
        },
        "incomplete_count": len(missing),
        "incomplete_samples": missing[:60],
    }
    QUALITY_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(
        "字段完整率 -> 研究方向 {r}/{n}, 简介 {i}/{n}, 论文 {p}/{n}; 不完整 {m} 人".format(
            r=n_research, i=n_intro, p=n_papers, n=total, m=len(missing)
        )
    )


def save_state(teachers: list[TeacherBrief], colleges: list[dict]) -> None:
    STATE_JSON.write_text(
        json.dumps(
            {
                "colleges": colleges,
                "teacher_briefs": [asdict(t) for t in teachers],
                "parser_version": PARSER_VERSION,
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
    limit: int = 0,
) -> None:
    college_names = college_names or DEFAULT_COLLEGES
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CRAWL_LOG.write_text("", encoding="utf-8")

    session = make_session()
    log("=" * 50)
    mode = "全量（无人数上限）" if max_per_college <= 0 else f"每学院最多 {max_per_college} 人"
    log(f"苏州大学教师主页爬虫 V2（高质量版）— {mode}")
    if limit:
        log(f"试跑模式：仅处理前 {limit} 位教师")
    log(f"目标学院: {', '.join(college_names)}")

    all_cols = fetch_colleges(session)
    colleges = pick_colleges(all_cols, college_names)
    polite_sleep()

    teachers = collect_teachers_for_colleges(session, colleges, max_per_college=max_per_college)
    save_state(teachers, colleges)
    log(f"教师名录合计 {len(teachers)} 人，开始下载主页 …")

    records = crawl_teacher_pages(session, teachers, resume=resume, limit=limit)
    TEACHERS_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    write_ir_artifacts(records, colleges, max_per_college)
    write_quality_report(records)

    by_college: dict[str, int] = {}
    for r in records:
        d = r.get("department") or "未知"
        by_college[d] = by_college.get(d, 0) + 1
    log("各学院实际入库人数: " + json.dumps(by_college, ensure_ascii=False))
    log(f"完成: {len(records)} 条 -> {TEACHERS_JSON}")
    log(f"语料目录: {CORPUS_DIR} ({len(list(CORPUS_DIR.glob('*.txt')))} 篇)")
    log(f"质量报告: {QUALITY_JSON}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="苏大教师主页爬虫 V2（高质量版）")
    parser.add_argument("--max-per-college", type=int, default=MAX_PER_COLLEGE, help="每学院人数上限，0=全量")
    parser.add_argument("--no-resume", action="store_true", help="不复用已有记录，全部重新下载")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 位教师（建议先小批量试跑，如 --limit 10）")
    args = parser.parse_args()
    main(max_per_college=args.max_per_college, resume=not args.no_resume, limit=args.limit)
