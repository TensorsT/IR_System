# -*- coding: utf-8 -*-
"""批量修复 teachers.json（无需调用 LLM API）。

修复内容：
  - 论文栏占位（如「软件著作\\n专利」）→ 从 sections/full_text 提取真实论文或清空
  - 研究方向缺失/混乱 → 从 sections/简介/full_text 提取并格式化
  - 生成 papers_struct 供 GUI 逐条展示
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e9_crawler_v2 as cr
from llm_extract import (
    TEACHERS_JSON,
    META_JSON,
    _clean_paper_title,
    _clean_research_direction,
    _is_placeholder_papers,
    _is_paper_label,
    _looks_like_paper_title,
    _papers_from_full_text,
    _rebuild_corpus,
    _strip_leading_paper_labels,
    log,
)

_RESEARCH_MARKERS = (
    "研究方向包括：", "研究方向包括", "研究方向为", "研究方向：", "研究方向:",
    "主要研究方向", "主要研究领域", "研究领域：", "研究领域:", "研究领域",
    "主要研究兴趣", "研究兴趣是", "研究兴趣：", "研究兴趣:", "研究兴趣",
    "目前主要从事", "主要从事", "致力于", "专注于",
    "Research Area", "Research Interests", "Research Interest",
)

_PAPER_TITLE_PATTERNS = (
    r"([A-Z][A-Za-z0-9 ,\-'\"&:+/]{12,220}?)\s*[\[（(]\s*[CJ]\s*[\]）)]",
    r"([A-Z][A-Za-z0-9 ,\-'\"&:+/]{12,220}?)\s*\.\s*(?:IEEE|ACM)\s+[A-Za-z][A-Za-z ]+",
    r"([A-Z][A-Za-z0-9 ,\-'\"&:+/]{12,220}?)\.\s*(?:In\s+)?(?:Proc\.?\s+of\s+)?(?:the\s+)?(?:IEEE|ACM|International)",
    r"\d+[\.、．]\s*(?:[A-Za-z ,.*\n]{0,140}?\.\s*)?([A-Z][A-Za-z0-9 ,\-'\"&:+/]{12,220}?)\s*\.\s*(?:IEEE|ACM|MobiSys|UbiComp|INFOCOM|Proceedings|Journal|Trans)",
)

_PLACEHOLDER_PAPERS = frozenset({
    "软件著作\n专利", "专利\n软件著作", "专利", "软件著作", "软件著作\n专利\n",
})

_RD_GARBAGE_RE = re.compile(
    r"^(Publications|Research|To|研究兴趣|研究兴趣、To)$|"
    r"习题课|周一\d|的同学邮件|Welcome to|Homepage|"
    r"无机功能材料的可控合成及光电器件应用方面的研究工作",
    re.I,
)
_PAPER_GARBAGE_RE = re.compile(
    r"(委员|学会|委员会|奖学金|奖项|Award|Prize|Homepage|Welcome|"
    r"科研项目|基金项目|指导学生|培养方向|招生要求|旧版|科研成果)",
    re.I,
)
_ABBR_LIST_RE = re.compile(
    r"^[A-Z][A-Z.、, /]{4,80}$",
)


def _research_from_numbered_list(text: str) -> str:
    items: list[str] = []
    for m in re.finditer(r"(?:^|\n)\s*\d+[\.、．]\s*([^\n]{2,40})", text):
        item = m.group(1).strip()
        if re.search(r"http|dblp|\.html", item, re.I):
            continue
        items.append(item)
    if len(items) >= 2:
        return _clean_research_direction("、".join(items))
    return ""


def _valid_research(rd: str) -> bool:
    if not rd or len(rd) < 4 or _RD_GARBAGE_RE.search(rd):
        return False
    if len(rd) > 80 and "、" not in rd and "，" not in rd:
        return False
    return True


def _valid_paper_title(title: str, source: str = "") -> bool:
    if not _looks_like_paper_title(title):
        return False
    if _PAPER_GARBAGE_RE.search(title):
        return False
    if _ABBR_LIST_RE.match(title.replace(" ", "")) and title.count(" ") < 2:
        return False
    if re.search(r"^[A-Z]{2,}([、,][A-Z]{2,}){2,}$", title.replace(" ", "")):
        return False
    if re.search(
        r"^(本科|博士|硕士|主要研究|研究方向|软件工程，|语言多模态|\d{4}-\d{4}|CCF-)|"
        r"二等奖|三等奖|优秀奖|发表论文\d|年度.*奖|"
        r"编辑\(|Chair|TPC|编委|副主编|Publicity Chair|Distinguished",
        title,
        re.I,
    ):
        return False
    if re.fullmatch(r"[A-Za-z ,.\n]+", title) and title.count(",") >= 3 and len(title) < 80:
        return False
    if title.count(" ") < 2 and len(title) < 30:
        return False
    if source and title in source:
        ctx = source[source.find(title): source.find(title) + len(title) + 80]
        if re.search(r"\[[CJ]\]|IEEE|ACM|Proceedings|Transactions", ctx, re.I):
            return True
    if len(title) >= 25 and title.count(" ") >= 3:
        return True
    return bool(re.search(r"\[[CJ]\]|IEEE|ACM|Proceedings|SIG|ICDE|ACL|KDD", title, re.I))


def _research_from_full_text(rec: dict) -> str:
    secs = rec.get("sections") or {}
    if secs.get("research"):
        rd = _clean_research_direction(secs["research"])
        if rd:
            return rd

    text = rec.get("full_text") or rec.get("corpus_text") or ""
    if not text:
        return ""

    for marker in _RESEARCH_MARKERS:
        idx = text.find(marker)
        if idx < 0:
            continue
        tail = text[idx + len(marker):]
        tail = re.sub(r"^[为是：:\s，,]+", "", tail)
        seg = re.split(r"[。\n；;！!]", tail, 1)[0]
        rd = _clean_research_direction(seg)
        if _valid_research(rd):
            return rd

    for marker in ("研究方向", "研究领域", "Research Area", "Research"):
        idx = text.find(marker)
        if idx < 0:
            continue
        chunk = text[idx: idx + 900]
        rd = _research_from_numbered_list(chunk)
        if _valid_research(rd):
            return rd

    intro = secs.get("intro") or rec.get("personal_intro") or ""
    if intro:
        rd = _clean_research_direction(cr._research_from_intro(intro))
        if _valid_research(rd):
            return rd

    m = re.search(r"深度学习[^。]{0,40}应用[：:]\s*", text)
    if m:
        tail = text[m.end(): m.end() + 600]
        items = re.findall(r"[0-9]+[）)]\s*([^（\n\d]{4,45})", tail)
        if len(items) >= 2:
            rd = _clean_research_direction("、".join(items))
            if _valid_research(rd):
                return rd

    rd = _clean_research_direction(cr._research_from_intro(text[:4000]))
    return rd if _valid_research(rd) else ""


def _extract_papers_by_cj_markers(text: str, limit: int = 12) -> list[str]:
    papers: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9 ,\-'\"&:+/]{14,220}?)\s*[\[（(]\s*[CJ]\s*[\]）)]", text):
        title = _clean_paper_title(m.group(1))
        # 取 [C] 前最后一个完整英文标题（去掉作者前缀）
        if "." in title:
            parts = [p.strip() for p in title.split(".") if len(p.strip()) > 12]
            if parts:
                title = parts[-1]
        if _valid_paper_title(title, text):
            key = title.casefold()
            if key not in seen:
                seen.add(key)
                papers.append(title)
        if len(papers) >= limit:
            break
    return papers


def _extract_papers_from_block(text: str, limit: int = 12) -> list[str]:
    if not text:
        return []
    text = text.replace("软件著作\n专利", "").replace("专利\n软件著作", "")
    text = _strip_leading_paper_labels(text).strip()
    if not text or _is_paper_label(text):
        return []

    cj_hits = _extract_papers_by_cj_markers(text, limit)
    if len(cj_hits) >= 2:
        return cj_hits

    papers: list[str] = []
    seen: set[str] = set()

    line_hits: list[str] = []
    for line in text.splitlines():
        title = _clean_paper_title(line)
        if _valid_paper_title(title, text):
            key = title.casefold()
            if key not in seen:
                seen.add(key)
                line_hits.append(title)
    if len(line_hits) >= 2:
        return line_hits[:limit]

    flat = re.sub(r"\s+", " ", text)
    for pat in _PAPER_TITLE_PATTERNS:
        for m in re.finditer(pat, flat):
            title = _clean_paper_title(m.group(1))
            if title.count(".") >= 2:
                tail = title.split(".")[-1].strip()
                if len(tail) > 15:
                    title = tail
            key = title.casefold()
            if _valid_paper_title(title, text) and key not in seen:
                seen.add(key)
                papers.append(title)
        if len(papers) >= 2:
            return papers[:limit]

    if line_hits:
        return line_hits[:limit]
    return papers[:limit]


def _infer_paper_meta(title: str, source: str) -> dict:
    flat = re.sub(r"\s+", " ", source)
    idx = flat.find(title[: min(30, len(title))])
    chunk = flat[idx: idx + 400] if idx >= 0 else flat[:400]
    venue = ""
    year = ""
    rank = ""
    vm = re.search(
        r"(IEEE/ACM [A-Za-z ]+|IEEE Transactions [A-Za-z ]+|ACM [A-Z][A-Za-z+ ]+|"
        r"Proceedings of [A-Za-z ]+|SIG[A-Z]{2,}|INFOCOM|ICDE|ACL|KDD|NeurIPS|CVPR|WWW|MobiSys|UbiComp)",
        chunk,
        re.I,
    )
    if vm:
        venue = vm.group(1).strip()[:60]
    ym = re.search(r"(20\d{2}|19\d{2})", chunk)
    if ym:
        year = ym.group(1)
    rm = re.search(r"CCF[-\s]?([ABC])", chunk, re.I)
    if rm:
        rank = rm.group(1).upper()
    return {"title": title, "venue": venue, "year": year, "ccf_rank": rank}


def _build_papers_struct(titles: list[str], source: str) -> list[dict]:
    return [_infer_paper_meta(t, source) for t in titles[:12]]


def _paper_source_score(text: str) -> int:
    if not text or text.strip() in _PLACEHOLDER_PAPERS:
        return 0
    score = 0
    if re.search(r"\[[CJ]\]", text):
        score += 50
    if re.search(r"IEEE|ACM|Proceedings|Transactions", text, re.I):
        score += 30
    if re.search(r"[A-Z][a-z]{4,}.{10,}\.\s*(?:IEEE|ACM)", text):
        score += 20
    found = _extract_papers_from_block(text)
    score += len(found) * 5
    return score


def _collect_paper_sources(rec: dict) -> list[tuple[int, str]]:
    secs = rec.get("sections") or {}
    candidates: list[tuple[int, str]] = []
    for key, text in [
        ("papers_text_rule", rec.get("papers_text_rule") or ""),
        ("papers", secs.get("papers") or ""),
        ("publications", secs.get("publications") or ""),
        ("papers_text", rec.get("papers_text") or ""),
        ("full_text", rec.get("full_text") or ""),
    ]:
        if not text:
            continue
        candidates.append((_paper_source_score(text), text))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates


def _fix_papers(rec: dict) -> bool:
    changed = False
    secs = rec.get("sections") or {}
    sec_papers = (secs.get("papers") or "").strip()

    papers: list[str] = []
    best_source = ""
    for score, src in _collect_paper_sources(rec):
        if score <= 0:
            continue
        found = _extract_papers_from_block(src)
        if len(found) > len(papers):
            papers = found
            best_source = src

    if not papers:
        papers = _papers_from_full_text(rec)

    current = (rec.get("papers_text") or "").strip()
    is_bad = (
        not current
        or current in _PLACEHOLDER_PAPERS
        or sec_papers in _PLACEHOLDER_PAPERS
        or _is_placeholder_papers(current)
    )

    quality = [t for t in papers if _valid_paper_title(t, best_source)]
    papers = quality or papers

    if papers and not any(_valid_paper_title(t, best_source) for t in papers):
        papers = []

    def _norm_cmp(s: str) -> str:
        return s.replace("，", "、").replace(",", "、").replace(" ", "").strip()

    rd = (rec.get("research_direction") or "").strip()
    if papers and rd:
        nrd = _norm_cmp(rd)
        if any(_norm_cmp(t) == nrd or (len(t) >= 6 and (_norm_cmp(t) in nrd or nrd in _norm_cmp(t))) for t in papers):
            papers = []

    if papers:
        strong = [
            t for t in papers
            if re.search(r"[A-Za-z]{4,}", t) and t.count(" ") >= 2 and len(t) >= 18
        ]
        if not strong:
            papers = []
        else:
            papers = strong[:12]

    if papers:
        new_text = "\n".join(papers)[:5000]
        new_struct = _build_papers_struct(papers, best_source)
        if new_text != current:
            rec["papers_text"] = new_text
            rec["papers_struct"] = new_struct
            changed = True
        if sec_papers != new_text:
            secs["papers"] = new_text
            rec["sections"] = secs
            changed = True
    elif (is_bad or not papers) and current:
        rec["papers_text"] = ""
        rec.pop("papers_struct", None)
        secs["papers"] = ""
        rec["sections"] = secs
        changed = True

    return changed


def _fix_research(rec: dict) -> bool:
    changed = False
    raw = (rec.get("research_direction") or "").strip()
    clean = _clean_research_direction(raw) if raw else ""

    if clean and not _valid_research(clean):
        clean = ""

    if raw and clean and clean != raw:
        rec["research_direction"] = clean
        changed = True
    elif raw and not clean:
        rec["research_direction"] = ""
        changed = True
        raw = ""

    if not rec.get("research_direction"):
        extracted = _research_from_full_text(rec)
        if _valid_research(extracted):
            rec["research_direction"] = extracted
            changed = True
            secs = rec.get("sections") or {}
            if not secs.get("research"):
                secs["research"] = extracted
                rec["sections"] = secs

    return changed


def batch_fix_record(rec: dict) -> bool:
    rd_changed = _fix_research(rec)
    paper_changed = _fix_papers(rec)
    if rd_changed or paper_changed:
        _rebuild_corpus(rec)
        return True
    return False


def _sync_sections(rec: dict) -> None:
    secs = rec.get("sections") or {}
    sp = (secs.get("papers") or "").strip()
    pt = (rec.get("papers_text") or "").strip()
    if sp in _PLACEHOLDER_PAPERS or _is_placeholder_papers(sp):
        secs["papers"] = pt
        rec["sections"] = secs


def main() -> None:
    records = json.loads(TEACHERS_JSON.read_text(encoding="utf-8"))
    fixed = rd_n = paper_n = 0
    for rec in records:
        had_rd = bool((rec.get("research_direction") or "").strip())
        had_paper = bool((rec.get("papers_text") or "").strip()) and not _is_placeholder_papers(
            rec.get("papers_text") or ""
        )
        if batch_fix_record(rec):
            fixed += 1
            if not had_rd and rec.get("research_direction"):
                rd_n += 1
                log(f"  研究方向 {rec.get('name')}: {rec['research_direction'][:50]}")
            if (not had_paper) and rec.get("papers_text") and not _is_placeholder_papers(rec["papers_text"]):
                paper_n += 1
                log(f"  论文 {rec.get('name')}: {len(rec['papers_text'].splitlines())} 条")
        _sync_sections(rec)

    log(f"批量修复完成：共更新 {fixed} 人，新补研究方向 {rd_n} 人，新补论文 {paper_n} 人")
    TEACHERS_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        colleges = json.loads(META_JSON.read_text(encoding="utf-8")).get("colleges", [])
    except (OSError, json.JSONDecodeError):
        colleges = []
    cr.write_ir_artifacts(records, colleges)
    cr.write_quality_report(records)
    log(f"已回写 {TEACHERS_JSON} 并重建语料/索引。")


if __name__ == "__main__":
    main()
