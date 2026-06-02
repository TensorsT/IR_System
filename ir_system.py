import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
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


@dataclass
class SearchResult:
    score: float
    doc: DocRecord
    teacher: TeacherRecord
    snippet: str


def load_teachers(path: str) -> List[TeacherRecord]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    teachers: List[TeacherRecord] = []
    for item in raw:
        teachers.append(
            TeacherRecord(
                name=(item.get("name") or "").strip(),
                department=(item.get("department") or "").strip(),
                career=(item.get("career") or "").strip(),
                url=(item.get("cn_url") or item.get("url") or "").strip(),
                research_direction=(item.get("research_direction") or "").strip(),
                personal_intro=(item.get("personal_intro") or "").strip(),
                papers_text=(item.get("papers_text") or "").strip(),
            )
        )
    return teachers


def load_corpus(corpus_dir: str) -> List[DocRecord]:
    docs: List[DocRecord] = []
    for filename in os.listdir(corpus_dir):
        if not filename.endswith(".txt"):
            continue
        path = os.path.join(corpus_dir, filename)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
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
    text = re.sub(r"[\w.+-]+@[\w.-]+", "***@***", text)
    text = re.sub(r"\d[\d\- ]{6,}\d", "***", text)
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

    if not results:
        return []

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:top_k]


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

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:top_k]


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

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:top_k]


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

    field_prefixes = ["姓名:", "name:", "论文:", "paper:", "研究方向:", "research:"]
    for prefix in field_prefixes:
        if query.lower().startswith(prefix.lower()):
            query = query[len(prefix) :].strip()
            break

    teacher_lookup = _build_teacher_lookup(teachers)
    normalized_query = query.replace(" ", "")
    if normalized_query in teacher_lookup:
        results: List[SearchResult] = []
        for teacher in teacher_lookup[normalized_query]:
            doc = next((d for d in docs if teacher.name in d.path), None)
            if not doc:
                doc = DocRecord(doc_id=teacher.name, path="", text=teacher.personal_intro)
            snippet = _extract_snippet(doc.text, [teacher.name])
            results.append(SearchResult(score=1.0, doc=doc, teacher=teacher, snippet=snippet))
        return results

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


def _format_result(result: SearchResult, rank: int) -> str:
    teacher = result.teacher
    lines = [f"[{rank}] {teacher.name}  |  {teacher.department}  |  {teacher.career}"]
    if teacher.research_direction:
        lines.append(f"研究方向: {_mask_private(teacher.research_direction)}")
    if teacher.personal_intro:
        intro = _mask_private(teacher.personal_intro.replace("\n", " ").strip())
        lines.append(f"简介: {intro[:180]}")
    if teacher.papers_text:
        papers = _mask_private(teacher.papers_text.replace("\n", " ").strip())
        lines.append(f"论文/成果: {papers[:180]}")
    if result.snippet:
        lines.append(f"片段: {_mask_private(result.snippet)}")
    if teacher.url:
        lines.append(f"主页: {teacher.url}")
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
            print(_format_result(result, i))
            print("-" * 60)
        print()


if __name__ == "__main__":
    run_cli()
