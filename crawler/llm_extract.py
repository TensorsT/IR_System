# -*- coding: utf-8 -*-
"""
基于大模型的教师信息结构化抽取（规则保底 + LLM 增强）。

动机：
  各教师主页栏目命名差异极大（研究领域/研究方向/Research/会议论文/期刊论文…），
  纯关键词匹配必然漏抓。本脚本把爬虫存下的“全文摘录(full_text)”整体交给大模型，
  统一抽取 研究方向 / 简介 / 代表论文 / 关键词，不依赖固定栏目名。

设计要点：
  - 输入文本已在爬虫阶段脱敏(full_text)，再次发送前仍做一次兜底脱敏；
  - 输出严格 JSON，带容错解析；
  - 限速 / 重试 / 退避；缓存(已抽取则跳过，--force 重抽)；
  - checkpoint：每条成功后写回 teachers.json，Ctrl+C 可安全中断续跑；
  - 没有 API key 或调用失败时，自动回退保留规则版结果，绝不清空已有数据；
  - 抽取后回写 teachers.json 并重建语料/索引，IR 系统即可直接使用。

依赖：
  pip install openai

环境变量（DeepSeek，OpenAI 兼容接口）：
  DEEPSEEK_API_KEY  必填，调用所需密钥（兼容旧名 LLM_API_KEY）
  LLM_BASE_URL      选填，默认 https://api.deepseek.com
  LLM_MODEL         选填，默认 deepseek-v4-pro

用法示例：
  $env:DEEPSEEK_API_KEY="sk-xxx"
  python crawler/llm_extract.py --limit 5      # 先小批量试抽
  python crawler/llm_extract.py                # 全量抽取并回写
  python crawler/llm_extract.py --force        # 忽略缓存重抽
  python crawler/llm_extract.py --dry-run      # 只打印不回写
  python crawler/llm_extract.py --no-thinking  # 关闭思考模式（更快更省）
  python crawler/llm_extract.py                  # 默认开启 checkpoint，中断后可续跑
  python crawler/llm_extract.py --no-checkpoint  # 仅在全部完成后写回
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e9_crawler_v2 as cr  # 复用脱敏 / 产出物 / 路径

OUT_DIR = cr.OUT_DIR
TEACHERS_JSON = cr.TEACHERS_JSON
META_JSON = cr.META_JSON
LLM_LOG = OUT_DIR / "llm_extract_log.txt"
CHECKPOINT_JSON = OUT_DIR / "llm_extract_checkpoint.json"

DEFAULT_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-pro")

REQUEST_GAP = 1.5         # 每次调用间隔（秒）
MAX_RETRY = 4
TIMEOUT = 120
MAX_INPUT_CHARS = 12000   # 送入模型的文本上限，控制 token 成本

SYSTEM_PROMPT = (
    "你是严谨的信息抽取助手。只能依据用户提供的网页文本进行抽取，"
    "不得编造或补全未出现的信息。所有输出必须是中文（论文标题保留原文）。"
)

USER_PROMPT_TMPL = """从下面这位高校教师主页的纯文本中，抽取结构化信息，并严格按 JSON 返回。

要求：
- research_direction: 3-8个研究方向短语，用顿号「、」连接；括号内缩写可拆成独立短语（如「语义分析(SRL、AMR)」→「语义分析、SRL、AMR」）；去掉编号/职称/学院/网址；<=120字；没有则空字符串。
- personal_intro: 个人简介，<=200字的概述；没有则空字符串。
- keywords: 3-10个能代表其研究的关键词（字符串数组）。
- papers: 代表性论文数组（最多12条），每项为对象，字段：
    - title: 论文标题（保留英文原文，去掉编号前缀）
    - venue: 发表会议/期刊简称（如 SIGIR、ACL、IEEE TKDE、软件学报）
    - year: 发表年份（4位数字字符串，未知则空字符串）
    - ccf_rank: 中国计算机学会推荐分类等级，仅填 "A"、"B"、"C" 或 ""（无法判断则空）
  若原文仅有「专利」「软件著作」「待更新」等栏目名而无具体论文标题，papers 必须返回空数组 []。
  不要输出网址、免责声明、专利/软件著作标签等非论文条目。
只输出 JSON，不要解释，不要使用 markdown 代码块。

教师姓名: {name}
学院: {dept}

网页文本:
\"\"\"
{text}
\"\"\"
"""


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with LLM_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _source_text(rec: dict) -> str:
    text = rec.get("full_text") or rec.get("corpus_text") or ""
    if not text:
        secs = rec.get("sections") or {}
        text = "\n".join(str(v) for v in secs.values())
    return cr.mask_privacy(text)[:MAX_INPUT_CHARS]


def _parse_json(content: str) -> dict | None:
    if not content:
        return None
    s = content.strip()
    # 去掉可能的 ```json ... ``` 包裹
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I | re.M).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", s, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def call_llm(client, model: str, name: str, dept: str, text: str,
             thinking: bool = True) -> dict | None:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT_TMPL.format(name=name, dept=dept, text=text)},
    ]
    kwargs: dict = {"model": model, "messages": messages, "stream": False}
    if thinking:
        # DeepSeek V4 Pro 思考模式
        kwargs["reasoning_effort"] = "high"
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

    for attempt in range(MAX_RETRY):
        try:
            response = client.chat.completions.create(**kwargs)
        except TypeError:
            # SDK 版本较旧不支持 reasoning_effort/extra_body 时，去掉后重试一次
            kwargs.pop("reasoning_effort", None)
            kwargs.pop("extra_body", None)
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as e:  # noqa: BLE001
                log(f"  调用失败({e})，放弃该条")
                return None
        except Exception as e:  # noqa: BLE001  网络/限流/服务端错误统一退避重试
            wait = 8 * (attempt + 1)
            log(f"  调用异常({type(e).__name__}: {e})，{wait}s 后重试")
            time.sleep(wait)
            continue
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError) as e:
            log(f"  响应解析失败: {e}")
            return None
        return _parse_json(content)
    return None


def _norm_list(value, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        s = re.sub(r"\s+", " ", str(item)).strip(" 、；;,，.-")
        if s and s not in out:
            out.append(s)
    return out[:limit]


# 论文条目里的噪音：纯网址、免责声明、访问提示等
_PAPER_NOISE_RE = re.compile(
    r"(没有维护|可能掺杂|请白天访问|vpn访问|个人主页|google\s*scholar|dblp|"
    r"citations|semanticscholar|orcid|researchgate|@|更多|查看|主页|链接)",
    re.I,
)

_PAPER_LABELS = frozenset({
    "专利", "软件著作", "著作", "专利软件著作", "专利、软件著作",
    "软件著作专利", "科研项目", "研究项目", "待更新", "暂无", "无",
})
_PAPER_PLACEHOLDER_HINTS = (
    "待更新", "请见", "标签页", "没有维护", "旧版", "了解更多", "点击查看",
)
_RD_META_RE = re.compile(
    r"(教授|副教授|讲师|助教|院长|副院长|博导|硕导|博士研究生|硕士研究生|"
    r"计算机科学与技术|软件学院|外国语学院|数学科学学院|物理科学与技术|"
    r"https?://|dblp\.|google\s*scholar)",
    re.I,
)
_RD_SKIP_RE = re.compile(
    r"(国家级|省部级|科研项目|主持人|合作者|NSFC|国家自然科学基金|"
    r"重大研究计划|培育项目|课题|获批|立项|特聘|博士生导师|人才引进)",
    re.I,
)
_RD_TAG_NOISE_RE = re.compile(
    r"^(苏州大学|苏大|东南大学|山东大学|浙江大学|北京大学|清华大学|"
    r".*大学|.*学院|硕士研究生?|博士研究生?|.*硕士学位|.*博士学位|"
    r"讲师|副教授|教授|助教|硕导|博导|硕士|博士|个人信息|成果奖励|教学招生|"
    r"荣誉奖励|科研成果)$",
    re.I,
)
_PAPER_SECTION_MARKERS = (
    "代表论文", "发表论文", "期刊论文", "会议论文", "论文成果", "学术论文",
    "论著", "主要论文", "Selected Publications", "Publications",
)


def _clean_paper_title(s: str) -> str:
    """去掉编号/会议标号前缀与首尾噪音，返回干净论文标题。"""
    s = re.sub(r"\s+", " ", str(s)).strip()
    # 去前导编号：1、 / 2. / （3） / [C48] / [J5] 等
    s = re.sub(r"^[\(\[（【]?\s*[A-Za-z]?\d{1,3}\s*[\)\]）】]?\s*[、.\u3001:：]?\s*", "", s)
    return s.strip(" 、；;,，.-")


def _clean_papers(items: list[str], limit: int = 20) -> list[str]:
    out: list[str] = []
    for raw in items:
        s = _clean_paper_title(raw)
        if len(s) < 6 or _is_paper_label(s):  # 太短，多为“专利/软件著作”标签
            continue
        if s.lower().startswith(("http://", "https://", "www.")):  # 纯网址
            continue
        if _PAPER_NOISE_RE.search(s):        # 免责声明/外链提示
            continue
        if s not in out:
            out.append(s)
    return out[:limit]


def _is_paper_label(text: str) -> bool:
    s = re.sub(r"\s+", "", str(text)).strip(" 、；;，,.-")
    if not s:
        return True
    if s in _PAPER_LABELS:
        return True
    if len(s) < 8 and not re.search(r"[A-Za-z]{4,}", s):
        return True
    for hint in _PAPER_PLACEHOLDER_HINTS:
        if hint in s:
            return True
    return False


_PAPER_PROFILE_RE = re.compile(
    r"(人才引进|特聘教授|博士生导师|硕导|博导|实验室|主任|电话[:：]|"
    r"计算机科学与技术学院|软件学院|苏州大学|@|特聘|欢迎|访问)",
    re.I,
)


_STRONG_PAPER_RE = re.compile(
    r"(Proceedings|Journal|Transactions|Conference|IEEE|ACM|SIG|ICDE|ACL|KDD|"
    r"TKDE|EMNLP|COLING|NeurIPS|CVPR|arxiv|doi:)",
    re.I,
)
_PAPER_LINE_NOISE_RE = re.compile(
    r"(国家自然|自然科学基金|基金项目|面上项目|重点项目|子项目|产学研|科技支撑|科技计划|"
    r"项目负责人|排名第二|招生要求|培养方向|优势条件|科研补助|青年基金|研究生称号|"
    r"精品.*课程|操作系统原理|Linux操作系统|工程经济与|程序设计|课程实践|课程设计|"
    r"科技进步|二等奖|三等奖|优秀奖|指导.*获得|指导学生|创新项目|"
    r"审稿人|审稿编辑|Reviewer|Area Chair|Program Committee|TPC member|编委|副主编|"
    r"SCI/EI收录|余篇|发明专利|授权专利|教材|学术著作|软件著作|"
    r"自然科学研究项目|目前主要研究方向|主要讲授的课程|获得教育部|获得江苏省|"
    r"主持开发|横向项目|科研鉴定|专利授权|云计算的租户|"
    r"Frontiers in Communication|Electronic Letters|Microwave and Optical)",
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
        r"概论|分析与设计|测试与质量|需求工程|蓝桥杯|招生信息|优秀指导教师|优秀硕士毕业|发邮件时|"
        r"学生具体情况|联系方式如下|本组招生|协助学生|攻读博士|攻读直博|曾荣获|不打扰学生|"
        r"阿尔托大学|武汉大学攻读|上海交通大学攻读",
        t,
    ):
        return True
    if re.search(r"国际学术刊物|国际学术会议包括|包括ACM TKDD|是多个国际", t):
        return True
    if re.fullmatch(r"[\u4e00-\u9fff、，；;：:\s]{4,45}研究", t) and not re.search(r"[A-Za-z]{4,}", t):
        return True
    if re.fullmatch(r"\(?\d*\)?:?\s*\d{5,8}\s*\(\d{4}\)|\d{5,8}\s*\(\d{4}\)|\(\d{4}\)\s*[\d\-]+J?\d*\)?", t):
        return True
    if teacher_name and teacher_name in t and re.search(r"基金|项目|,20\d{2}|-20\d{2}/", t):
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
    if re.search(r"的审稿人|等杂志审稿|特约审稿人|审稿编辑", t):
        return True
    if re.search(r"余篇|收录论文\d|发表论文\d|论文\d+余", t):
        return True
    if re.search(r"(20\d{2}\.\d{2}-20\d{2}|计\d{2}计算机|二学位|,\d{4}/\d{2},)", t):
        return True
    if re.search(r"研究,\s*国家|研究，国家|方法研究,-20", t):
        return True
    if "\uf0b7" in t or t.startswith("·"):
        return True
    # 仅期刊名/缩写行（无作者、无论文动名词）
    if (
        re.search(r"^(?:Frontiers|Journal of|IEEE|ACM|Proceedings|Trans\.|Comput\.)", t, re.I)
        and ":" not in t
        and len(t) < 80
        and t.count(" ") <= 6
    ):
        return True
    return False


def _looks_like_paper_title(
    title: str,
    teacher_name: str = "",
    research_direction: str = "",
) -> bool:
    if not title or _is_paper_label(title) or _PAPER_NOISE_RE.search(title):
        return False
    if _is_paper_line_noise(title, teacher_name, research_direction):
        return False
    if _PAPER_PROFILE_RE.search(title):
        return False
    if re.search(r"\[at\]|@|\.edu|电话|地址|办公室|信箱", title, re.I):
        return False
    if re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][a-z]+){0,3}", title.strip()):
        return False
    if re.search(r"(教授|学院|实验室|主任|访问|人次|引用数|Google)", title):
        return False
    if re.search(r":\s*[A-Z][a-z]{4,}", title):
        return len(title) >= 30
    if _STRONG_PAPER_RE.search(title):
        return True
    if re.search(r"[A-Za-z]{4,}", title):
        return len(title) >= 20 and title.count(" ") >= 2
    return len(title) >= 12


def _is_placeholder_papers(text: str) -> bool:
    """判断论文栏是否仅为栏目名/占位，而非真实论文列表。"""
    raw = (text or "").strip()
    if not raw:
        return True
    if _is_paper_label(raw):
        return True
    if _PAPER_PROFILE_RE.search(raw) and not re.search(
        r"(Proceedings|Conference|Journal|IEEE|ACM|SIG|ICDE|ACL|KDD|TKDE|arxiv)",
        raw,
        re.I,
    ):
        return True
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", raw) if ln.strip()]
    real = [t for t in (_clean_paper_title(ln) for ln in lines) if _looks_like_paper_title(t)]  # noqa: caller passes ctx if needed
    if len(real) >= 2:
        return False
    if len(real) == 1 and _STRONG_PAPER_RE.search(real[0]):
        return False
    return True


def _is_noise_research_tag(tag: str, teacher_name: str = "", department: str = "") -> bool:
    """过滤误写入研究方向的姓名、学校、学位等标签。"""
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


def _clean_research_direction(
    text: str,
    limit: int = 120,
    teacher_name: str = "",
    department: str = "",
) -> str:
    """规范化研究方向：拆编号/换行/括号，去掉职称学院网址等噪音。"""
    if not text:
        return ""
    for cut in ("http://", "https://", "dblp.", "google scholar"):
        idx = text.lower().find(cut)
        if idx > 0:
            text = text[:idx]
    text = re.sub(r"\(\s*[\r\n]+\s*", "(", text)
    text = re.sub(r"[\r\n]+\s*\)", ")", text)

    tags: list[str] = []
    seen: set[str] = set()
    for raw_line in re.split(r"[\r\n]+", text):
        line = raw_line.strip()
        if not line or _RD_META_RE.search(line) or _RD_SKIP_RE.search(line):
            continue
        line = re.sub(r"^[\d一二三四五六七八九十]+[\.、．:：]\s*", "", line)
        line = re.sub(r"^[（(]\d+[）)]\s*", "", line)
        # 括号内容拆成独立短语，去掉空括号
        line = re.sub(
            r"[（(]([^（）()]*)[）)]",
            lambda m: ("、" + m.group(1).replace(";", "、").replace(",", "、")) if m.group(1).strip() else "",
            line,
        )
        line = re.sub(r"[（()）]", "、", line)
        line = line.strip(" 、；;，,./")
        for part in re.split(r"[;；、,/|]+", line):
            part = re.sub(r"\s+", " ", part).strip(" ：:-.等。")
            if (
                len(part) < 2
                or _RD_META_RE.search(part)
                or _RD_SKIP_RE.search(part)
                or _is_noise_research_tag(part, teacher_name, department)
            ):
                continue
            if part.endswith("等"):
                part = part[:-1].strip(" 、；;，,.")
            if len(part) < 2 or part in seen:
                continue
            seen.add(part)
            tags.append(part)
    return "、".join(tags[:10])[:limit]


def _papers_from_full_text(rec: dict, limit: int = 12) -> list[str]:
    """论文栏为占位时，从 full_text 的论文区段尝试捞回真实标题。"""
    text = rec.get("full_text") or rec.get("corpus_text") or ""
    if not text:
        return []
    for marker in _PAPER_SECTION_MARKERS:
        idx = text.find(marker)
        if idx < 0:
            continue
        chunk = text[idx + len(marker): idx + len(marker) + 6000]
        out: list[str] = []
        for line in chunk.splitlines():
            title = _clean_paper_title(line)
            if not _looks_like_paper_title(title):
                continue
            if title not in out:
                out.append(title)
            if len(out) >= limit:
                break
        if out:
            return out
    return []


def _papers_look_like_profile_block(text: str) -> bool:
    head = text[:500]
    hits = sum(
        1 for m in ("人才引进", "特聘教授", "博士生导师", "实验室", "主任\n", "主任\r")
        if m in head
    )
    return hits >= 2


def _strip_leading_paper_labels(text: str) -> str:
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]
    while lines and _is_paper_label(_clean_paper_title(lines[0])):
        lines.pop(0)
    return "\n".join(lines)


def _sanitize_papers_field(rec: dict) -> bool:
    """清理占位论文栏，必要时从 full_text 回填。"""
    papers = (rec.get("papers_text") or "").strip()
    changed = False

    if papers:
        stripped = _strip_leading_paper_labels(papers)
        if stripped != papers:
            papers = stripped
            rec["papers_text"] = papers
            changed = True

    if papers and _papers_look_like_profile_block(papers):
        papers = ""
        rec["papers_text"] = ""
        changed = True

    if papers and not _is_placeholder_papers(papers):
        return changed

    recovered = _papers_from_full_text(rec)
    if recovered:
        rec["papers_text"] = "\n".join(recovered)[:5000]
        rec.pop("papers_struct", None)
        return True
    if papers:
        rec["papers_text"] = ""
        rec.pop("papers_struct", None)
        return True
    return changed


def sanitize_papers_record(rec: dict) -> bool:
    """清洗论文栏：去掉基金/课程/奖项/审稿人等非论文条目。"""
    name = rec.get("name", "")
    rd = rec.get("research_direction", "")
    changed = False

    new_struct: list[dict] = []
    for row in rec.get("papers_struct") or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        venue = str(row.get("venue") or "").strip()
        display = title or venue
        if not display or _is_paper_line_noise(display, name, rd):
            changed = True
            continue
        if title and not _looks_like_paper_title(title, name, rd):
            if venue and not _is_paper_line_noise(venue, name, rd):
                row = {**row, "title": venue}
            else:
                changed = True
                continue
        new_struct.append(row)

    new_lines: list[str] = []
    for ln in (rec.get("papers_text") or "").splitlines():
        t = _clean_paper_title(ln)
        if not t or _is_paper_line_noise(t, name, rd) or not _looks_like_paper_title(t, name, rd):
            if t:
                changed = True
            continue
        if t not in new_lines:
            new_lines.append(t)

    if new_struct:
        rec["papers_struct"] = new_struct[:12]
        text = "\n".join(p["title"] for p in new_struct if p.get("title"))
        if text != (rec.get("papers_text") or ""):
            rec["papers_text"] = text[:5000]
            changed = True
    elif new_lines:
        new_text = "\n".join(new_lines[:12])[:5000]
        if new_text != (rec.get("papers_text") or ""):
            rec["papers_text"] = new_text
            changed = True
        if rec.get("papers_struct"):
            rec.pop("papers_struct", None)
            changed = True
    else:
        old = (rec.get("papers_text") or "").strip()
        if old or rec.get("papers_struct"):
            recovered = _papers_from_full_text(rec)
            if recovered:
                clean = [
                    t for t in recovered
                    if _looks_like_paper_title(t, name, rd)
                ]
                if clean:
                    rec["papers_text"] = "\n".join(clean[:12])[:5000]
                    rec.pop("papers_struct", None)
                    changed = True
                else:
                    rec["papers_text"] = ""
                    rec.pop("papers_struct", None)
                    changed = True
            else:
                rec["papers_text"] = ""
                rec.pop("papers_struct", None)
                changed = True

    return changed


def sanitize_record(rec: dict) -> bool:
    """无需 API 的字段清洗：研究方向格式化 + 论文占位清理。"""
    changed = False
    name = rec.get("name", "")
    dept = rec.get("department", "")
    raw_rd = (rec.get("research_direction") or "").strip()
    clean_rd = _clean_research_direction(raw_rd, teacher_name=name, department=dept)
    if clean_rd and clean_rd != raw_rd:
        rec["research_direction"] = clean_rd
        changed = True
    elif raw_rd and not clean_rd:
        rec["research_direction"] = ""
        changed = True

    if not rec.get("research_direction") and rec.get("personal_intro"):
        rd = cr._research_from_intro(rec["personal_intro"])
        if rd:
            clean_rd = _clean_research_direction(rd, teacher_name=name, department=dept)
            if clean_rd:
                rec["research_direction"] = clean_rd
                changed = True

    if _sanitize_papers_field(rec):
        changed = True
    if sanitize_papers_record(rec):
        changed = True

    if changed:
        _rebuild_corpus(rec)
    return changed


def _coerce_paper_dict(raw) -> dict | None:
    """把 LLM 返回的论文项统一成 dict（兼容字符串形式的 dict）。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") and "venue" in s:
            try:
                obj = ast.literal_eval(s)
                if isinstance(obj, dict):
                    return obj
            except (SyntaxError, ValueError):
                pass
        if len(s) >= 6 and not _is_paper_label(s):
            return {"title": s, "venue": "", "year": "", "ccf_rank": ""}
    return None


def _paper_display_title(title: str, venue: str, year: str) -> str:
    title = _clean_paper_title(title)
    venue = re.sub(r"\s+", " ", venue).strip()
    if len(title) >= 6:
        return title
    if venue:
        return f"{venue} ({year})" if year else venue
    return ""


def _clean_papers_struct(items, limit: int = 12) -> list[dict]:
    """规范化 LLM 返回的论文对象列表，供 GUI 逐条展示。"""
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        row = _coerce_paper_dict(raw)
        if not row:
            continue
        venue = re.sub(r"\s+", " ", str(row.get("venue") or "")).strip()[:80]
        year_m = re.search(r"(20\d{2}|19\d{2})", str(row.get("year") or ""))
        year = year_m.group(1) if year_m else ""
        title = _paper_display_title(str(row.get("title") or ""), venue, year)
        if len(title) < 4 or _is_paper_label(title) or _PAPER_NOISE_RE.search(title):
            continue
        if _is_paper_line_noise(title, "", ""):
            continue
        key = f"{title}|{venue}|{year}".casefold()
        if key in seen:
            continue
        seen.add(key)
        rank = str(row.get("ccf_rank") or "").strip().upper()
        if rank not in {"A", "B", "C"}:
            rank = ""
        out.append({"title": title, "venue": venue, "year": year, "ccf_rank": rank})
    return out[:limit]


def repair_papers_record(rec: dict) -> bool:
    """修复 papers_text 被写成 Python dict 字符串的记录。"""
    pt = (rec.get("papers_text") or "").strip()
    if not pt or "'title':" not in pt and '"title":' not in pt:
        return False
    struct = _clean_papers_struct(pt.splitlines(), 12)
    if not struct:
        return False
    rec["papers_struct"] = struct
    rec["papers_text"] = "\n".join(p["title"] for p in struct)[:5000]
    _rebuild_corpus(rec)
    return True


def apply_extraction(rec: dict, data: dict) -> bool:
    """把 LLM 结果写入记录；空字段保留规则版，不清空。返回是否有更新。"""
    rec.setdefault("research_direction_rule", rec.get("research_direction", ""))
    rec.setdefault("personal_intro_rule", rec.get("personal_intro", ""))
    rec.setdefault("papers_text_rule", rec.get("papers_text", ""))

    research = _clean_research_direction(
        str(data.get("research_direction") or ""),
        teacher_name=rec.get("name", ""),
        department=rec.get("department", ""),
    )
    intro = re.sub(r"\s+", " ", str(data.get("personal_intro") or "")).strip()
    keywords = _norm_list(data.get("keywords"), 10)

    papers_raw = data.get("papers")
    papers_struct = _clean_papers_struct(papers_raw, 12)
    if papers_struct:
        papers_list = [p["title"] for p in papers_struct if p.get("title")]
    elif isinstance(papers_raw, list):
        papers_list = [p["title"] for p in _clean_papers_struct(papers_raw, 20) if p.get("title")]
    else:
        papers_list = _clean_papers(_norm_list(papers_raw, 40), 20)

    changed = False
    if research:
        rec["research_direction"] = research[:200]
        changed = True
    if intro:
        rec["personal_intro"] = intro[:600]
        changed = True
    if papers_list:
        rec["papers_text"] = "\n".join(papers_list)[:5000]
        rec["papers_struct"] = papers_struct
        changed = True
        sanitize_papers_record(rec)
    elif _is_placeholder_papers(rec.get("papers_text", "")):
        if _sanitize_papers_field(rec):
            changed = True
    if keywords:
        rec["keywords"] = keywords
        changed = True

    # 研究方向仍为空时，规则保底：从简介里抽取（有些老师写在简介中）
    if not rec.get("research_direction") and rec.get("personal_intro"):
        rd = _clean_research_direction(
            cr._research_from_intro(rec["personal_intro"]),
            teacher_name=rec.get("name", ""),
            department=rec.get("department", ""),
        )
        if rd:
            rec["research_direction"] = rd
            changed = True

    sanitize_record(rec)
    _rebuild_corpus(rec)
    rec["llm_extracted"] = True
    return changed


def _rebuild_corpus(rec: dict) -> None:
    """按统一结构重建干净语料（供 LLM 与规则两条路径共用）。"""
    parts = [
        f"姓名:{rec.get('name', '')}",
        f"学院:{rec.get('department', '')}",
        f"职称:{rec.get('career', '')}",
    ]
    if rec.get("research_direction"):
        parts.append(f"研究方向:{rec['research_direction']}")
    if rec.get("keywords"):
        parts.append("关键词:" + "、".join(rec["keywords"]))
    if rec.get("personal_intro"):
        parts.append(f"个人简介:{rec['personal_intro']}")
    if rec.get("papers_text"):
        parts.append(f"论文:{rec['papers_text']}")
    rec["corpus_text"] = "\n".join(parts)[:15000]


def _load_colleges() -> list:
    try:
        return json.loads(META_JSON.read_text(encoding="utf-8")).get("colleges", [])
    except (OSError, json.JSONDecodeError):
        return []


def _atomic_write_json(path: Path, data) -> None:
    """先写临时文件再替换，避免中断时损坏 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_checkpoint() -> dict:
    if not CHECKPOINT_JSON.exists():
        return {}
    try:
        return json.loads(CHECKPOINT_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_checkpoint(
    records: list,
    colleges: list,
    meta: dict,
    *,
    rebuild_artifacts: bool = False,
) -> None:
    _atomic_write_json(TEACHERS_JSON, records)
    meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write_json(CHECKPOINT_JSON, meta)
    if rebuild_artifacts:
        cr.write_ir_artifacts(records, colleges)
        cr.write_quality_report(records)


def rules_backfill(rec: dict) -> bool:
    """无需 API：研究方向为空时从简介抽取，返回是否更新。"""
    if rec.get("research_direction") or not rec.get("personal_intro"):
        return False
    rd = cr._research_from_intro(rec["personal_intro"])
    if not rd:
        return False
    rec["research_direction"] = rd
    rec["research_from_intro"] = True
    _rebuild_corpus(rec)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="大模型结构化抽取（规则保底 + LLM 增强）")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 位（试跑）")
    parser.add_argument("--force", action="store_true", help="忽略缓存，重抽已抽取过的记录")
    parser.add_argument("--dry-run", action="store_true", help="只调用打印，不回写文件")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"模型名（默认 {DEFAULT_MODEL}）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"API 基址（默认 {DEFAULT_BASE_URL}）")
    parser.add_argument("--no-thinking", action="store_true", help="关闭思考模式（更快更省）")
    parser.add_argument(
        "--rules-only",
        action="store_true",
        help="不调用大模型，仅用规则从简介回填研究方向（无需 API key）",
    )
    parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="关闭逐条 checkpoint（默认每条成功后写回 teachers.json，可断点续跑）",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=1,
        help="每成功 N 条写一次 teachers.json（默认 1）",
    )
    parser.add_argument(
        "--rebuild-every",
        type=int,
        default=20,
        help="每累计 N 条成功抽取后重建语料/索引（默认 20，0 表示仅在结束时重建）",
    )
    args = parser.parse_args()

    if not TEACHERS_JSON.exists():
        log(f"未找到 {TEACHERS_JSON}，请先运行爬虫。")
        sys.exit(1)
    records = json.loads(TEACHERS_JSON.read_text(encoding="utf-8"))

    # ---- 规则模式：无需 API，仅从简介回填研究方向 ----
    if args.rules_only:
        filled = cleaned = 0
        targets = records[: args.limit] if args.limit > 0 else records
        for rec in targets:
            if rules_backfill(rec):
                filled += 1
                log(f"  回填 {rec.get('name')}: {rec['research_direction'][:40]}")
            if sanitize_record(rec):
                cleaned += 1
        log(f"规则处理完成：简介回填研究方向 {filled} 人，格式清洗 {cleaned} 人")
        if args.dry_run:
            log("dry-run 模式：未写回文件。")
            return
        TEACHERS_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            colleges = json.loads(META_JSON.read_text(encoding="utf-8")).get("colleges", [])
        except (OSError, json.JSONDecodeError):
            colleges = []
        cr.write_ir_artifacts(records, colleges)
        cr.write_quality_report(records)
        log(f"已回写 {TEACHERS_JSON} 并重建语料/索引。")
        return

    # ---- LLM 模式 ----
    api_key = "sk-5fbc5d1e91b24e758ddf00f88150bcbc"
    if not api_key:
        log("未设置 DEEPSEEK_API_KEY，已退出（可改用 --rules-only 无需 API）。")
        sys.exit(1)

    try:
        from openai import OpenAI
    except ImportError:
        log("未安装 openai，请先执行: pip install openai")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=args.base_url, timeout=TIMEOUT)
    thinking = not args.no_thinking

    done_n = sum(1 for r in records if r.get("llm_extracted"))
    todo = [r for r in records if args.force or not r.get("llm_extracted")]
    if args.limit > 0:
        todo = todo[: args.limit]

    checkpoint_on = not args.no_checkpoint and not args.dry_run
    ck_every = max(1, int(args.checkpoint_every))
    rebuild_every = max(0, int(args.rebuild_every))
    colleges = _load_colleges()
    prev_ck = _load_checkpoint() if checkpoint_on else {}

    if args.force and checkpoint_on and CHECKPOINT_JSON.exists():
        CHECKPOINT_JSON.unlink(missing_ok=True)
        prev_ck = {}

    log(
        f"待抽取 {len(todo)} / 共 {len(records)} 条（已完成 {done_n}），模型={args.model} "
        f"thinking={thinking} gap={REQUEST_GAP}s rebuild_every={rebuild_every} "
        f"checkpoint={'on' if checkpoint_on else 'off'} dry_run={args.dry_run}"
    )
    if prev_ck:
        log(
            f"  上次 checkpoint: {prev_ck.get('last_name')} "
            f"({prev_ck.get('ok', 0)} 成功 / {prev_ck.get('fail', 0)} 失败) @ {prev_ck.get('updated_at')}"
        )

    ok = fail = 0
    pending_ck = 0
    ck_meta = {
        "model": args.model,
        "thinking": thinking,
        "ok": prev_ck.get("ok", 0),
        "fail": prev_ck.get("fail", 0),
        "last_name": prev_ck.get("last_name", ""),
        "last_index": prev_ck.get("last_index", 0),
    }

    def _flush_checkpoint(name: str, index: int, *, rebuild: bool = False) -> None:
        nonlocal pending_ck
        ck_meta.update({"last_name": name, "last_index": index, "ok": ok, "fail": fail})
        _save_checkpoint(records, colleges, ck_meta, rebuild_artifacts=rebuild)
        pending_ck = 0
        log(f"  checkpoint {name}: 已写回 teachers.json" + (" + 语料索引" if rebuild else ""))

    def _on_interrupt(signum, frame) -> None:  # noqa: ARG001
        if checkpoint_on and pending_ck > 0 and ck_meta.get("last_name"):
            log("收到中断信号，正在保存 checkpoint…")
            try:
                _flush_checkpoint(ck_meta["last_name"], ck_meta.get("last_index", 0), rebuild=False)
            except OSError as e:
                log(f"  checkpoint 保存失败: {e}")
        log("已中断，重新运行同一命令即可从 llm_extracted 标记续跑。")
        sys.exit(130)

    if checkpoint_on:
        signal.signal(signal.SIGINT, _on_interrupt)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _on_interrupt)

    for i, rec in enumerate(todo, start=1):
        name = rec.get("name", "")
        dept = rec.get("department", "")
        text = _source_text(rec)
        if not text.strip():
            log(f"[{i}/{len(todo)}] {name} 无可用文本，跳过")
            continue

        data = call_llm(client, args.model, name, dept, text, thinking=thinking)
        if not data:
            fail += 1
            ck_meta["fail"] = fail
            rules_backfill(rec)  # LLM 失败也尝试规则回填
            log(f"[{i}/{len(todo)}] {name} 抽取失败，保留规则版")
        else:
            apply_extraction(rec, data)
            ok += 1
            ck_meta["ok"] = ok
            preview = (rec.get("research_direction") or "")[:40]
            n_papers = len((rec.get("papers_text") or "").splitlines())
            log(f"[{i}/{len(todo)}] {name} OK 研究方向={preview!r} 论文{n_papers}条")

            if checkpoint_on and ok % ck_every == 0:
                do_rebuild = rebuild_every > 0 and ok % rebuild_every == 0
                _flush_checkpoint(name, i, rebuild=do_rebuild)
            elif checkpoint_on:
                pending_ck += 1
                ck_meta.update({"last_name": name, "last_index": i})

        time.sleep(REQUEST_GAP)

    log(f"完成：成功 {ok}，失败 {fail}")

    if args.dry_run:
        log("dry-run 模式：未写回文件。")
        return

    final_rebuild = True
    if checkpoint_on:
        last = ck_meta.get("last_name") or (todo[-1].get("name") if todo else "")
        if last:
            _flush_checkpoint(last, ck_meta.get("last_index", len(todo)), rebuild=final_rebuild)
        else:
            _save_checkpoint(records, colleges, ck_meta, rebuild_artifacts=final_rebuild)
    else:
        _save_checkpoint(records, colleges, ck_meta, rebuild_artifacts=final_rebuild)

    if checkpoint_on and ok > 0 and not args.force:
        CHECKPOINT_JSON.unlink(missing_ok=True)
        log("全部完成，已清除 checkpoint 状态文件。")
    log(f"已回写 {TEACHERS_JSON} 并重建语料/索引。")


if __name__ == "__main__":
    main()
