import ast
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

try:
    from fuzzywuzzy import fuzz

    _FUZZY_AVAILABLE = True
except Exception:
    _FUZZY_AVAILABLE = False


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "crawled_data")
CORPUS_DIR = os.path.join(DATA_DIR, "corpus")
TEACHERS_JSON = os.path.join(DATA_DIR, "teachers.json")


@dataclass
class DocRecord:
    doc_id: str
    path: str
    text: str


@dataclass
class TeacherRecord:
    name: str
    department: str
    career: str
    url: str
    research_direction: str
    personal_intro: str
    papers_text: str
    profile_keywords: List[str] = field(default_factory=list)
    papers_struct: List[dict] = field(default_factory=list)


@dataclass
class SearchResult:
    score: float
    doc: DocRecord
    teacher: TeacherRecord
    snippet: str


@dataclass
class PaperItem:
    title: str
    venue: str = ""
    year: str = ""
    ccf_rank: str = ""


@dataclass
class DisplayResult:
    rank: int
    name: str
    department: str
    career: str
    research: str
    intro: str
    papers: str
    snippet: str
    url: str
    score: float
    keywords: List[str]
    paper_items: List[PaperItem] = field(default_factory=list)
    research_tags: List[str] = field(default_factory=list)
    profile_keywords: List[str] = field(default_factory=list)


def _stable_sort_key(result: SearchResult) -> Tuple[float, str, str, str, str]:
    teacher = result.teacher
    return (
        -result.score,
        teacher.name or "",
        teacher.department or "",
        teacher.url or "",
        result.doc.doc_id or "",
    )


def _dedupe_and_rank(results: List[SearchResult], top_k: int) -> List[SearchResult]:
    if not results:
        return []

    merged: Dict[Tuple[str, str], SearchResult] = {}
    for item in results:
        teacher = item.teacher
        key = (
            (teacher.name or "").replace(" ", ""),
            (teacher.department or "").strip(),
        )
        existing = merged.get(key)
        if existing is None or item.score > existing.score:
            merged[key] = item
            continue

        if item.score == existing.score:
            # Keep deterministic output when scores tie.
            if _stable_sort_key(item) < _stable_sort_key(existing):
                merged[key] = item

    ranked = list(merged.values())
    ranked.sort(key=_stable_sort_key)
    return ranked[:top_k]


def load_teachers(path: str) -> List[TeacherRecord]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    teachers: List[TeacherRecord] = []
    for item in raw:
        kw_raw = item.get("keywords") or []
        profile_keywords: List[str] = []
        if isinstance(kw_raw, list):
            for kw in kw_raw:
                s = re.sub(r"\s+", " ", str(kw)).strip()
                if s and s not in profile_keywords:
                    profile_keywords.append(s)

        papers_struct: List[dict] = []
        struct_raw = item.get("papers_struct") or []
        if isinstance(struct_raw, list):
            for row in struct_raw:
                if isinstance(row, dict) and row.get("title"):
                    papers_struct.append(row)

        teachers.append(
            TeacherRecord(
                name=(item.get("name") or "").strip(),
                department=(item.get("department") or "").strip(),
                career=(item.get("career") or "").strip(),
                url=(item.get("cn_url") or item.get("url") or "").strip(),
                research_direction=(item.get("research_direction") or "").strip(),
                personal_intro=(item.get("personal_intro") or "").strip(),
                papers_text=(item.get("papers_text") or "").strip(),
                profile_keywords=profile_keywords,
                papers_struct=papers_struct,
            )
        )
    return teachers


# Navigation / section-header lines that appear on every crawled page and are
# pure noise for snippets and the index.
_NAV_LINES = frozenset(
    {
        "教师个人主页",
        "English",
        "返回首页",
        "欢迎登录",
        "导航",
        "个人资料",
        "个人概况",
        "研究领域",
        "研究方向",
        "开授课程",
        "科研项目",
        "论文",
        "科研成果",
        "荣誉及奖励",
        "招生信息",
        "相关教师",
        "最新更新",
        "教育经历",
        "工作经历",
        "社会职务",
        "个人简介",
        "访问",
        "科学研究",
        "基本信息",
        "联系方式",
        "教学",
        "课程",
        "科研团队",
    }
)


def _clean_corpus_text(raw: str) -> str:
    """Drop crawler header, page template/navigation, label-only and noise lines.

    Each corpus file starts with a `key: value` header terminated by a `---`
    separator, followed by the page body. The body still contains the site's
    navigation menu, visit counters and empty `label：` rows, which leak into
    snippets (e.g. "返回首页 欢迎登录 导航"). We strip those so snippets and the
    index only keep substantive content.
    """
    if not raw:
        return ""
    parts = re.split(r"\n-{3,}\n", raw, maxsplit=1)
    body = parts[1] if len(parts) > 1 else raw

    kept: List[str] = []
    prev = None
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s in _NAV_LINES:
            continue
        if s.startswith(("姓名:", "页面标题:")):
            continue
        # Visit counters / stray numeric lines.
        if re.fullmatch(r"\d{1,6}", s):
            continue
        # Label-only rows like "联系电话：" / "学位：" / "研究方向：" (no value).
        if len(s) <= 12 and s.endswith(("：", ":")):
            continue
        # Collapse consecutive duplicate lines (some pages repeat sections).
        if s == prev:
            continue
        kept.append(s)
        prev = s
    return "\n".join(kept)


def load_corpus(corpus_dir: str) -> List[DocRecord]:
    docs: List[DocRecord] = []
    for filename in os.listdir(corpus_dir):
        if not filename.endswith(".txt"):
            continue
        path = os.path.join(corpus_dir, filename)
        with open(path, "r", encoding="utf-8") as f:
            text = _clean_corpus_text(f.read())
        doc_id = os.path.splitext(filename)[0]
        docs.append(DocRecord(doc_id=doc_id, path=path, text=text))
    return docs


def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    tokens.extend(re.findall(r"[a-zA-Z0-9]+", text.lower()))

    cjk_blocks = re.findall(r"[\u4e00-\u9fff]+", text)
    for block in cjk_blocks:
        if not block:
            continue
        tokens.extend(list(block))
        if len(block) > 1:
            tokens.extend(block[i : i + 2] for i in range(len(block) - 1))
    return tokens


def _build_teacher_lookup(teachers: Iterable[TeacherRecord]) -> Dict[str, List[TeacherRecord]]:
    lookup: Dict[str, List[TeacherRecord]] = defaultdict(list)
    for teacher in teachers:
        key = teacher.name.replace(" ", "")
        if key:
            lookup[key].append(teacher)
    return lookup


def _mask_private(text: str) -> str:
    if not text:
        return text
    # Email: ASCII local part + domain with a real TLD. This avoids masking
    # things like "周 国栋@Google Scholar" (no dotted TLD) as a fake email.
    text = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "***@***",
        text,
    )
    # Phone: 11-digit mobiles or separated landlines only, so grant codes like
    # "#61331011" and year ranges like "2014.01-2018.12" are left intact.
    text = re.sub(
        r"(?<![\d#-])(?:1[3-9]\d{9}|0\d{2,3}[-\s]?\d{7,8}|\d{3,4}[-\s]\d{7,8})(?!\d)",
        "***",
        text,
    )
    return text


def _extract_snippet(text: str, query_tokens: List[str], limit: int = 120) -> str:
    if not text:
        return ""
    for token in query_tokens:
        if not token:
            continue
        idx = text.find(token)
        if idx != -1:
            start = max(0, idx - 40)
            end = min(len(text), idx + 40)
            snippet = text[start:end].replace("\n", " ").strip()
            return snippet[:limit]
    snippet = text[:limit].replace("\n", " ").strip()
    return snippet


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", "", text).casefold()


_BOILERPLATE_MARKERS = (
    "基本信息",
    "社会职务",
    "联系方式",
    "科学研究",
    "科研团队",
    "研究项目",
    "主要项目",
    "国家级科研项目",
    "最近更新",
    "论文发表",
    "代表性论文",
    "近五年",
    "课题组",
    "教学",
    "主持",
    "招生信息",
    "荣誉及奖励",
    "开授课程",
    "版权所有",
    "技术支持",
    "Copyright",
    "职称：",
    "-----",
)

_SECTION_LABELS = ("研究领域:", "研究方向:", "个人简介:", "简介:", "论文:", "论文/成果:")

_FOOTER_MARKERS = (
    "版权所有",
    "技术支持",
    "Copyright",
    "招生信息",
    "荣誉及奖励",
    "开授课程",
)


def _trim_footer(text: str) -> str:
    if not text:
        return ""
    cut = len(text)
    for marker in _FOOTER_MARKERS:
        idx = text.find(marker)
        if 0 < idx < cut:
            cut = idx
    return text[:cut]


def _clean_field(text: str, max_len: int = 180, cut_boilerplate: bool = False) -> str:
    """Collapse whitespace, optionally cut crawler boilerplate, and truncate."""
    if not text:
        return ""
    if cut_boilerplate:
        cut = len(text)
        for marker in _BOILERPLATE_MARKERS:
            idx = text.find(marker)
            if 0 < idx < cut:
                cut = idx
        text = text[:cut]
    text = re.sub(r"\s+", " ", text).strip(" 、；;,，.-")
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


def _strip_section_label(text: str) -> str:
    for label in _SECTION_LABELS:
        if text.startswith(label):
            return text[len(label) :].strip()
    return text


def _query_terms(query: str) -> List[str]:
    cleaned = (query or "").strip()
    for prefix in ["姓名:", "name:", "论文:", "paper:", "研究方向:", "research:"]:
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix) :].strip()
            break
    terms = [t for t in re.split(r"\s+", cleaned) if t]
    terms.extend(_relax_terms(cleaned))
    out: List[str] = []
    seen = set()
    for term in terms:
        if term and term not in seen:
            out.append(term)
            seen.add(term)
    return out


def _match_keywords(query: str, teacher: TeacherRecord) -> List[str]:
    haystack = _normalize_text(
        " ".join(
            [
                teacher.name,
                teacher.department,
                teacher.research_direction,
                teacher.papers_text,
                teacher.personal_intro,
            ]
        )
    )
    matched = []
    for term in _query_terms(query):
        if _normalize_text(term) in haystack and term not in matched:
            matched.append(term)
    # Keep only the longest non-overlapping matches so relax sub-grams
    # like "周国"/"栋" do not clutter the display alongside "周国栋".
    matched.sort(key=lambda x: -len(x))
    kept: List[str] = []
    for term in matched:
        if not any(term in longer for longer in kept):
            kept.append(term)
    return kept[:5]


def _clean_snippet(snippet: str) -> str:
    text = _strip_section_label(re.sub(r"\s+", " ", snippet or "").strip())
    cut = len(text)
    for marker in _BOILERPLATE_MARKERS:
        idx = text.find(marker)
        if 0 < idx < cut:
            cut = idx
    text = text[:cut].strip(" -—、；;,，.·|")
    # Drop fragments that carry no real information (e.g. "务。", "：").
    if len(re.sub(r"[\s\W]+", "", text)) < 4:
        return ""
    return text


_INCOMPLETE_VALUES = frozenset({"研究方向", "研究领域", "研究兴趣", "简介", "个人简介"})


def _looks_incomplete(value: str) -> bool:
    """Detect dangling labels / lead-in headings with no real content.

    Crawling sometimes captures only a heading such as "研究方向：" or
    "我近期的研究兴趣包括：" while the actual list was rendered elsewhere. Such a
    value should be hidden instead of shown as a useless field.
    """
    stripped = (value or "").strip()
    if not stripped:
        return True
    if stripped.endswith(("：", ":")):
        return True
    core = stripped.rstrip("：: 。.").strip()
    if core in _INCOMPLETE_VALUES:
        return True
    return len(core) < 2


_CCF_A_HINTS = (
    "sigir",
    "acl",
    "kdd",
    "icde",
    "infocom",
    "cvpr",
    "iccv",
    "eccv",
    "neurips",
    "nips",
    "icml",
    "aaai",
    "ijcai",
    "usenix atc",
    "sosp",
    "osdi",
    "nsdi",
    "ccs",
    "usenix security",
    "oakland",
    "sp ",
    "ieee tdsc",
    "ieee tifs",
    "tkde",
    "tocs",
    "tpds",
    "tods",
    "软件学报",
    "计算机学报",
)
_CCF_B_HINTS = (
    "www",
    "emnlp",
    "coling",
    "cikm",
    "icdm",
    "wsdm",
    "recsys",
    "mm ",
    "icassp",
    "icse",
    "fse",
    "ase",
    "icnp",
    "imc",
    "sigmod",
    "vldb",
    "pods",
    "iclr",
    "naacl",
    "tkde",
    "tmm",
    "tcsvt",
)
_CCF_C_HINTS = (
    "icann",
    "iconip",
    "pakdd",
    "dasfaa",
    "trustcom",
    "icpads",
    "hpca",
    "micro",
    "isca",
)


def _infer_ccf_rank(venue: str, title: str = "") -> str:
    hay = f"{venue} {title}".lower()
    for hint in _CCF_A_HINTS:
        if hint in hay:
            return "A"
    for hint in _CCF_B_HINTS:
        if hint in hay:
            return "B"
    for hint in _CCF_C_HINTS:
        if hint in hay:
            return "C"
    return ""


_RD_DISPLAY_META_RE = re.compile(
    r"(教授|副教授|讲师|助教|https?://|dblp\.)",
    re.I,
)
_RD_DISPLAY_SKIP_RE = re.compile(
    r"(国家级|省部级|科研项目|主持人|合作者|NSFC|国家自然科学基金|"
    r"重大研究计划|培育项目|课题|获批|立项|特聘|人才引进)",
    re.I,
)
_RD_TAG_NOISE_RE = re.compile(
    r"^(苏州大学|苏大|东南大学|山东大学|浙江大学|北京大学|清华大学|"
    r".*大学|.*学院|硕士研究生?|博士研究生?|.*硕士学位|.*博士学位|"
    r"讲师|副教授|教授|助教|硕导|博导|硕士|博士|个人信息|成果奖励|教学招生|"
    r"荣誉奖励|科研成果)$",
    re.I,
)


def _is_noise_research_tag(tag: str, teacher_name: str = "", department: str = "") -> bool:
    t = re.sub(r"\s+", " ", (tag or "").strip())
    if not t or len(t) < 2:
        return True
    name = (teacher_name or "").replace(" ", "")
    tc = t.replace(" ", "")
    if name:
        if t == teacher_name or tc == name:
            return True
        if name in tc and len(t) <= len(teacher_name) + 6:
            return True
    if _RD_TAG_NOISE_RE.match(t):
        return True
    if re.search(r"(大学|版权|技术支持|信箱|招生|Copyright)", t) and len(t) <= 16:
        return True
    if department and t in department:
        return True
    return False


def _normalize_research_display(
    text: str,
    limit: int = 140,
    teacher_name: str = "",
    department: str = "",
) -> str:
    """展示前规范化研究方向：拆编号/换行/括号，去掉明显噪音。"""
    if not text:
        return ""
    for cut in ("http://", "https://", "dblp."):
        idx = text.lower().find(cut)
        if idx > 0:
            text = text[:idx]
    text = re.sub(r"\(\s*[\r\n]+\s*", "(", text)
    text = re.sub(r"[\r\n]+\s*\)", ")", text)
    tags: List[str] = []
    seen: set[str] = set()
    for raw_line in re.split(r"[\r\n]+", text):
        line = raw_line.strip()
        if not line or _RD_DISPLAY_META_RE.search(line) or _RD_DISPLAY_SKIP_RE.search(line):
            continue
        line = re.sub(r"^[\d一二三四五六七八九十]+[\.、．:：]\s*", "", line)
        line = re.sub(r"^[（(]\d+[）)]\s*", "", line)
        line = re.sub(
            r"[（(]([^（）()]*)[）)]",
            lambda m: ("、" + m.group(1).replace(";", "、").replace(",", "、")) if m.group(1).strip() else "",
            line,
        )
        line = re.sub(r"[（()）]", "、", line)
        for part in re.split(r"[;；、,/|]+", line):
            s = re.sub(r"\s+", " ", part).strip(" ：:-.等。")
            if s.endswith("等"):
                s = s[:-1].strip(" 、；;，,.")
            if (
                len(s) < 2
                or _RD_DISPLAY_META_RE.search(s)
                or _RD_DISPLAY_SKIP_RE.search(s)
                or _is_noise_research_tag(s, teacher_name, department)
                or s in seen
            ):
                continue
            seen.add(s)
            tags.append(s)
    return "、".join(tags[:10])[:limit]


def _split_research_tags(
    text: str, limit: int = 8, teacher_name: str = "", department: str = ""
) -> List[str]:
    normalized = _normalize_research_display(
        text, limit=200, teacher_name=teacher_name, department=department
    )
    if not normalized:
        return []
    parts = re.split(r"[、；;，,/|]+", normalized)
    tags: List[str] = []
    for part in parts:
        s = re.sub(r"\s+", " ", part).strip(" ：:-")
        if len(s) < 2 or s in tags:
            continue
        tags.append(s)
    return tags[:limit]


_PAPER_LABEL_ONLY = frozenset({
    "专利", "软件著作", "著作", "专利、软件著作", "待更新", "暂无", "无",
})
_PAPER_PLACEHOLDER_HINTS = ("待更新", "请见", "标签页", "没有维护", "旧版")
_PAPER_PROFILE_RE = re.compile(
    r"(人才引进|特聘教授|博士生导师|实验室|主任|电话[:：]|苏州大学|@|特聘)",
    re.I,
)
_PAPER_LINE_NOISE_RE = re.compile(
    r"(国家自然|自然科学基金|基金项目|面上项目|重点项目|子项目|产学研|科技支撑|科技计划|"
    r"项目负责人|排名第二|招生要求|培养方向|优势条件|科研补助|青年基金|"
    r"精品.*课程|操作系统原理|Linux操作系统|工程经济与|程序设计|课程实践|"
    r"科技进步|二等奖|三等奖|优秀奖|指导学生|创新项目|"
    r"审稿人|审稿编辑|Reviewer|Area Chair|TPC member|编委|副主编|"
    r"SCI/EI收录|余篇|发明专利|授权专利|教材|学术著作|"
    r"自然科学研究项目|目前主要研究方向|主要讲授的课程|"
    r"Frontiers in Communication|Electronic Letters)",
    re.I,
)
_JOURNAL_ABBR_ONLY_RE = re.compile(
    r"^(?:IEEE\s*|ACM\s*)?[A-Z][A-Za-z.]{1,10}"
    r"(?:\s*[,，、]\s*(?:IEEE\s*|ACM\s*)?[A-Z][A-Za-z.]{1,10})+$",
)


def _normalize_paper_cmp(s: str) -> str:
    return re.sub(r"\s+", "", s.replace("，", "、").replace(",", "、"))


def _is_paper_line_noise(
    title: str,
    teacher_name: str = "",
    research_direction: str = "",
) -> bool:
    t = re.sub(r"\s+", " ", (title or "")).strip()
    if not t:
        return True
    if _PAPER_LINE_NOISE_RE.search(t):
        return True
    if re.search(
        r"概论|分析与设计|测试与质量|需求工程|蓝桥杯|招生信息|优秀指导教师|发邮件时|"
        r"学生具体情况|联系方式如下|本组招生|协助学生|攻读博士|不打扰学生",
        t,
    ):
        return True
    if re.search(r"国际学术刊物|国际学术会议包括|包括ACM TKDD", t):
        return True
    if re.fullmatch(r"[\u4e00-\u9fff、，；;：:\s]{4,45}研究", t) and not re.search(r"[A-Za-z]{4,}", t):
        return True
    if re.fullmatch(r"\(?\d*\)?:?\s*\d{5,8}\s*\(\d{4}\)|\d{5,8}\s*\(\d{4}\)", t):
        return True
    if teacher_name and teacher_name in t and re.search(r"基金|项目|,20\d{2}", t):
        return True
    if research_direction:
        if _normalize_paper_cmp(t) in _normalize_paper_cmp(research_direction):
            return True
        rd_tags = {
            _normalize_paper_cmp(x)
            for x in re.split(r"[、，,;；]+", research_direction)
            if x.strip()
        }
        line_tags = [
            _normalize_paper_cmp(x)
            for x in re.split(r"[、，,;；]+", t)
            if x.strip()
        ]
        if len(line_tags) >= 2:
            overlap = sum(1 for tag in line_tags if tag in rd_tags)
            if overlap >= max(2, int(len(line_tags) * 0.75)):
                return True
    if _JOURNAL_ABBR_ONLY_RE.match(t):
        return True
    if re.search(r"的审稿人|余篇|收录论文|发表论文\d|论文\d+余", t):
        return True
    if re.search(r"(20\d{2}\.\d{2}-20\d{2}|计\d{2}计算机|,\d{4}/\d{2},)", t):
        return True
    if (
        re.search(r"^(?:Frontiers|Journal of|IEEE|ACM|Proceedings|Trans\.|Comput\.)", t, re.I)
        and ":" not in t
        and len(t) < 80
    ):
        return True
    return False


def _is_displayable_paper(
    title: str,
    teacher_name: str = "",
    research_direction: str = "",
) -> bool:
    s = re.sub(r"\s+", "", title or "").strip(" 、；;，,.-")
    if not s or s in _PAPER_LABEL_ONLY:
        return False
    if _is_paper_line_noise(title, teacher_name, research_direction):
        return False
    if _PAPER_PROFILE_RE.search(title or ""):
        return False
    if len(s) < 8 and not re.search(r"[A-Za-z]{4,}", s):
        return False
    if re.search(r":\s*[A-Z][a-z]{4,}", title or ""):
        return len(title or "") >= 30
    if re.search(r"[A-Za-z]{4,}", title or ""):
        return (title or "").count(" ") >= 2 and len(title or "") >= 20
    return not any(h in s for h in _PAPER_PLACEHOLDER_HINTS)


def _parse_paper_dict_line(line: str) -> PaperItem | None:
    """兼容 papers_text 里误存的 Python dict 字符串。"""
    s = line.strip()
    if not s.startswith("{") or "venue" not in s:
        return None
    try:
        row = ast.literal_eval(s)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(row, dict):
        return None
    title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
    venue = re.sub(r"\s+", " ", str(row.get("venue") or "")).strip()
    year = re.sub(r"\D", "", str(row.get("year") or ""))[:4]
    if not title and venue:
        title = f"{venue} ({year})" if year else venue
    if not title:
        return None
    rank = str(row.get("ccf_rank") or "").strip().upper()
    if rank not in {"A", "B", "C"}:
        rank = _infer_ccf_rank(venue, title)
    return PaperItem(title=title, venue=venue, year=year, ccf_rank=rank)


def _build_paper_items(teacher: TeacherRecord, papers_fallback: str) -> List[PaperItem]:
    items: List[PaperItem] = []
    if teacher.papers_struct:
        for row in teacher.papers_struct[:12]:
            title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
            venue = re.sub(r"\s+", " ", str(row.get("venue") or "")).strip()
            year = re.sub(r"\D", "", str(row.get("year") or ""))[:4]
            if not title and venue:
                title = f"{venue} ({year})" if year else venue
            if not title:
                continue
            if _is_paper_line_noise(title, teacher.name, teacher.research_direction):
                continue
            venue = re.sub(r"\s+", " ", str(row.get("venue") or "")).strip()
            year = re.sub(r"\D", "", str(row.get("year") or ""))[:4]
            rank = str(row.get("ccf_rank") or "").strip().upper()
            if rank not in {"A", "B", "C"}:
                rank = _infer_ccf_rank(venue, title)
            items.append(PaperItem(title=title, venue=venue, year=year, ccf_rank=rank))
        if items:
            return items

    for line in re.split(r"[\r\n]+", papers_fallback or ""):
        parsed = _parse_paper_dict_line(line)
        if parsed:
            items.append(parsed)
            continue
        title = re.sub(r"\s+", " ", line).strip()
        if not _is_displayable_paper(title, teacher.name, teacher.research_direction):
            continue
        rank = _infer_ccf_rank("", title)
        items.append(PaperItem(title=title, ccf_rank=rank))
    return items[:12]


def build_display(result: SearchResult, rank: int, query: str = "") -> DisplayResult:
    """Build a clean, de-duplicated view model shared by CLI and GUI."""
    teacher = result.teacher
    research_raw = (
        "" if _looks_incomplete(teacher.research_direction) else teacher.research_direction
    )
    research = _mask_private(
        _normalize_research_display(
            _clean_field(research_raw, max_len=500, cut_boilerplate=True),
            limit=140,
            teacher_name=teacher.name,
            department=teacher.department,
        )
    )
    intro_raw = "" if _looks_incomplete(teacher.personal_intro) else teacher.personal_intro
    papers_raw = "" if _looks_incomplete(teacher.papers_text) else teacher.papers_text
    intro = _mask_private(_clean_field(_trim_footer(intro_raw), max_len=200))
    papers = _mask_private(_clean_field(_trim_footer(papers_raw), max_len=200))

    snippet = _mask_private(_clean_snippet(result.snippet))

    visible = _normalize_text(" ".join([research, intro, papers]))
    snippet_norm = _normalize_text(snippet)
    if snippet_norm and visible and snippet_norm in visible:
        snippet = ""

    research_tags = _split_research_tags(
        research, teacher_name=teacher.name, department=teacher.department
    )
    papers_source = _mask_private(_trim_footer(teacher.papers_text or papers_raw))
    paper_items = _build_paper_items(teacher, papers_source)

    return DisplayResult(
        rank=rank,
        name=teacher.name,
        department=teacher.department,
        career=teacher.career,
        research=research,
        intro=intro,
        papers=papers,
        snippet=snippet,
        url=teacher.url,
        score=result.score,
        keywords=_match_keywords(query, teacher),
        paper_items=paper_items,
        research_tags=research_tags,
        profile_keywords=list(teacher.profile_keywords),
    )


def _relax_terms(query: str) -> List[str]:
    terms: List[str] = []
    terms.extend(re.findall(r"[a-zA-Z0-9]+", query.lower()))

    cjk_blocks = re.findall(r"[\u4e00-\u9fff]+", query)
    for block in cjk_blocks:
        if not block:
            continue
        if len(block) <= 2:
            terms.append(block)
            continue
        terms.extend(block[i : i + 2] for i in range(0, len(block), 2))

    deduped: List[str] = []
    seen = set()
    for term in terms:
        if term and term not in seen:
            deduped.append(term)
            seen.add(term)
    return deduped


def build_index(docs: List[DocRecord]) -> Tuple[Dict[str, Dict[str, int]], Dict[str, float]]:
    inverted: Dict[str, Dict[str, int]] = defaultdict(dict)
    doc_freq: Dict[str, int] = defaultdict(int)

    for doc in docs:
        tf = Counter(_tokenize(doc.text))
        for term, freq in tf.items():
            inverted[term][doc.doc_id] = freq
        for term in tf.keys():
            doc_freq[term] += 1

    num_docs = max(len(docs), 1)
    idf: Dict[str, float] = {}
    for term, df in doc_freq.items():
        idf[term] = math.log(1 + num_docs / (1 + df))

    doc_norms: Dict[str, float] = defaultdict(float)
    for term, postings in inverted.items():
        term_idf = idf.get(term, 0.0)
        for doc_id, freq in postings.items():
            weight = (1 + math.log(freq)) * term_idf
            doc_norms[doc_id] += weight * weight

    for doc_id, value in doc_norms.items():
        doc_norms[doc_id] = math.sqrt(value) if value > 0 else 1.0

    return inverted, doc_norms


def _phrase_search(
    query: str,
    docs: List[DocRecord],
    teachers: List[TeacherRecord],
    top_k: int,
) -> List[SearchResult]:
    if not query:
        return []

    needle = _normalize_text(query)
    if not needle:
        return []

    query_tokens = _tokenize(query)
    results: List[SearchResult] = []
    for doc in docs:
        haystack = _normalize_text(doc.text)
        if needle and needle in haystack:
            count = haystack.count(needle)
            score = 1.0 + math.log(1 + count)
            teacher = next((t for t in teachers if t.name and t.name in doc.path), None)
            if not teacher:
                continue
            snippet = _extract_snippet(doc.text, query_tokens or [query])
            results.append(SearchResult(score=score, doc=doc, teacher=teacher, snippet=snippet))

    for teacher in teachers:
        haystack = _normalize_text(
            " ".join(
                [
                    teacher.name,
                    teacher.department,
                    teacher.career,
                    teacher.research_direction,
                    teacher.personal_intro,
                    teacher.papers_text,
                ]
            )
        )
        if needle and needle in haystack:
            doc = next((d for d in docs if teacher.name and teacher.name in d.path), None)
            if not doc:
                doc = DocRecord(doc_id=teacher.name, path="", text=teacher.personal_intro)
            snippet = _extract_snippet(doc.text, query_tokens or [query])
            results.append(SearchResult(score=1.0, doc=doc, teacher=teacher, snippet=snippet))

    return _dedupe_and_rank(results, top_k)


def _token_search(
    query_tokens: List[str],
    docs: List[DocRecord],
    teachers: List[TeacherRecord],
    inverted: Dict[str, Dict[str, int]],
    doc_norms: Dict[str, float],
    top_k: int,
    require_all: bool = False,
) -> List[SearchResult]:
    if not query_tokens:
        return []

    doc_ids: Iterable[str]
    if require_all:
        postings_lists = [inverted.get(term) for term in query_tokens if term]
        if not postings_lists or any(postings is None for postings in postings_lists):
            return []
        doc_ids = set(postings_lists[0].keys())
        for postings in postings_lists[1:]:
            doc_ids = set(doc_ids).intersection(postings.keys())
        if not doc_ids:
            return []
    else:
        doc_ids = []

    scores: Dict[str, float] = defaultdict(float)
    for term in query_tokens:
        postings = inverted.get(term)
        if not postings:
            continue
        for doc_id, tf in postings.items():
            if require_all and doc_id not in doc_ids:
                continue
            weight = 1 + math.log(tf)
            scores[doc_id] += weight

    results: List[SearchResult] = []
    doc_map = {doc.doc_id: doc for doc in docs}
    for doc_id, score in scores.items():
        norm = doc_norms.get(doc_id, 1.0)
        final_score = score / norm
        doc = doc_map.get(doc_id)
        if not doc:
            continue
        teacher = next((t for t in teachers if t.name and t.name in doc.path), None)
        if not teacher:
            continue
        snippet = _extract_snippet(doc.text, query_tokens)
        results.append(SearchResult(score=final_score, doc=doc, teacher=teacher, snippet=snippet))

    return _dedupe_and_rank(results, top_k)


def _fuzzy_search(
    query: str,
    docs: List[DocRecord],
    teachers: List[TeacherRecord],
    top_k: int,
) -> List[SearchResult]:
    if not _FUZZY_AVAILABLE or not query:
        return []

    query_tokens = _tokenize(query)
    results: List[SearchResult] = []
    for teacher in teachers:
        haystack = " ".join(
            [
                teacher.name,
                teacher.department,
                teacher.career,
                teacher.research_direction,
                teacher.personal_intro,
                teacher.papers_text,
            ]
        )
        if not haystack.strip():
            continue
        score = fuzz.partial_ratio(query, haystack)
        if score < 70:
            continue
        doc = next((d for d in docs if teacher.name and teacher.name in d.path), None)
        if not doc:
            doc = DocRecord(doc_id=teacher.name, path="", text=teacher.personal_intro)
        snippet = _extract_snippet(doc.text, query_tokens or [query])
        results.append(SearchResult(score=float(score), doc=doc, teacher=teacher, snippet=snippet))

    return _dedupe_and_rank(results, top_k)


def _field_search(
    query: str,
    field: str,
    docs: List[DocRecord],
    teachers: List[TeacherRecord],
    top_k: int,
) -> List[SearchResult]:
    """Search restricted to a single teacher field (papers / research)."""
    if not query:
        return []

    terms = _relax_terms(query)
    needle = _normalize_text(query)
    results: List[SearchResult] = []
    for teacher in teachers:
        field_text = (
            teacher.papers_text if field == "papers" else teacher.research_direction
        )
        haystack = _normalize_text(field_text)
        if not haystack:
            continue
        score = 0.0
        if needle and needle in haystack:
            score += 3.0
        for term in terms:
            norm = _normalize_text(term)
            if norm and norm in haystack:
                score += 1.0
        if score <= 0:
            continue
        doc = next((d for d in docs if teacher.name and teacher.name in d.path), None)
        if not doc:
            doc = DocRecord(doc_id=teacher.name, path="", text=field_text)
        snippet = _extract_snippet(field_text, [query] + terms)
        results.append(SearchResult(score=score, doc=doc, teacher=teacher, snippet=snippet))

    return _dedupe_and_rank(results, top_k)


def search(
    query: str,
    docs: List[DocRecord],
    teachers: List[TeacherRecord],
    inverted: Dict[str, Dict[str, int]],
    doc_norms: Dict[str, float],
    top_k: int = 8,
    allow_relax: bool = True,
    enable_fuzzy: bool = True,
) -> List[SearchResult]:
    query = (query or "").strip()
    if not query:
        return []

    field = None
    field_map = [
        ("姓名:", "name"),
        ("name:", "name"),
        ("论文:", "papers"),
        ("paper:", "papers"),
        ("研究方向:", "research"),
        ("research:", "research"),
    ]
    for prefix, fname in field_map:
        if query.lower().startswith(prefix.lower()):
            field = fname
            query = query[len(prefix) :].strip()
            break

    if not query:
        return []

    teacher_lookup = _build_teacher_lookup(teachers)
    normalized_query = query.replace(" ", "")
    if field in (None, "name") and normalized_query in teacher_lookup:
        results: List[SearchResult] = []
        for teacher in teacher_lookup[normalized_query]:
            doc = next((d for d in docs if teacher.name in d.path), None)
            if not doc:
                doc = DocRecord(doc_id=teacher.name, path="", text=teacher.personal_intro)
            snippet = _extract_snippet(doc.text, [teacher.name])
            results.append(SearchResult(score=1.0, doc=doc, teacher=teacher, snippet=snippet))
        return _dedupe_and_rank(results, top_k)

    if field in ("papers", "research"):
        scoped = _field_search(query, field, docs, teachers, top_k)
        if scoped:
            return scoped

    if allow_relax:
        exact_results = _phrase_search(query, docs, teachers, top_k)
        if exact_results:
            return exact_results

        relaxed_terms = _relax_terms(query)
        relaxed_results = _token_search(
            relaxed_terms,
            docs,
            teachers,
            inverted,
            doc_norms,
            top_k,
            require_all=False,
        )
        if relaxed_results:
            return relaxed_results

    query_tokens = _tokenize(query)
    base_results = _token_search(
        query_tokens,
        docs,
        teachers,
        inverted,
        doc_norms,
        top_k,
        require_all=False,
    )
    if base_results:
        return base_results

    if enable_fuzzy and allow_relax:
        return _fuzzy_search(query, docs, teachers, top_k)

    return []


def _format_result(result: SearchResult, rank: int, query: str = "") -> str:
    view = build_display(result, rank, query)
    career = f"  |  {view.career}" if view.career else ""
    lines = [f"[{rank}] {view.name}  |  {view.department}{career}"]
    if view.research:
        lines.append(f"研究方向: {view.research}")
    if view.intro:
        lines.append(f"简介: {view.intro}")
    if view.paper_items:
        lines.append("论文/成果:")
        for i, paper in enumerate(view.paper_items[:8], start=1):
            badge = f"[CCF-{paper.ccf_rank}] " if paper.ccf_rank else ""
            meta = " · ".join(x for x in [paper.venue, paper.year] if x)
            suffix = f" ({meta})" if meta else ""
            lines.append(f"  {i}. {badge}{paper.title}{suffix}")
    elif view.papers:
        lines.append(f"论文/成果: {view.papers}")
    if view.snippet:
        lines.append(f"片段: {view.snippet}")
    if view.keywords:
        lines.append(f"命中关键词: {' / '.join(view.keywords)}")
    if view.url:
        lines.append(f"主页: {view.url}")
    return "\n".join(lines)


def run_cli() -> None:
    teachers = load_teachers(TEACHERS_JSON)
    docs = load_corpus(CORPUS_DIR)
    inverted, doc_norms = build_index(docs)

    print("苏州大学导师检索系统 (基础版)")
    print("输入示例: 自然语言处理方向 | 周国栋 | 论文: 信息抽取")
    print("输入 quit 退出\n")

    while True:
        query = input("查询> ").strip()
        if not query:
            continue
        if query.lower() in {"quit", "exit"}:
            break

        results = search(query, docs, teachers, inverted, doc_norms)
        if not results:
            print("未找到结果。\n")
            continue

        for i, result in enumerate(results, start=1):
            print(_format_result(result, i, query))
            print("-" * 60)
        print()


if __name__ == "__main__":
    run_cli()
