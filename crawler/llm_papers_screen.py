# -*- coding: utf-8 -*-
"""
论文专项筛查：调用 DeepSeek 从主页文本中鉴别真实论文并结构化标注。

与 llm_extract 全量抽取不同，本脚本：
  - 提示词极短，只关心论文；
  - 排除基金/课程/奖项/专利/招生/审稿/职务等噪音；
  - 输出 title / venue / year / ccf_rank，写入 papers_struct；
  - 无具体论文标题时返回空列表并清空脏数据。

用法：
  python crawler/llm_papers_screen.py --name 赵雷
  python crawler/llm_papers_screen.py --suspicious-only   # 仅筛查疑似噪音记录
  python crawler/llm_papers_screen.py --suspicious-only --limit 5
  python crawler/llm_papers_screen.py --all --force
  python crawler/llm_papers_screen.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e9_crawler_v2 as cr
from llm_extract import (
    CHECKPOINT_JSON,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    MAX_INPUT_CHARS,
    MAX_RETRY,
    META_JSON,
    OUT_DIR,
    REQUEST_GAP,
    TEACHERS_JSON,
    TIMEOUT,
    _atomic_write_json,
    _clean_papers_struct,
    _is_paper_line_noise,
    _load_colleges,
    _rebuild_corpus,
    _source_text,
    log,
)

PAPERS_CK_JSON = OUT_DIR / "llm_papers_screen_checkpoint.json"
PAPERS_LOG = OUT_DIR / "llm_papers_screen_log.txt"
SCREEN_VERSION = 1

SYSTEM_PROMPT = "你是论文鉴别助手。只根据给定文本判断并抽取学术论文，不得编造。"

USER_PROMPT = """从教师主页文本中抽取真实学术论文条目（有具体标题），排除基金项目、课程、奖项、专利、招生、审稿职务、统计描述。
若无具体论文标题只有"发表论文N篇/ICDE等"描述，返回空数组。

严格输出 JSON：{{"papers":[{{"title":"","venue":"","year":"","ccf_rank":"A|B|C|"}}]}}
ccf_rank 为中国计算机学会推荐分类，无法判断填""。最多10条。

教师:{name}
现有候选（可能全为噪音，可忽略）:
{candidates}

文本:
{text}
"""


def papers_log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with PAPERS_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _parse_papers_json(content: str) -> list[dict] | None:
    if not content:
        return None
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I | re.M)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}|\[.*\]", s, flags=re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        papers = data.get("papers")
        if isinstance(papers, list):
            return papers
    return None


def call_papers_llm(client, model: str, name: str, text: str, candidates: str) -> list[dict] | None:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_PROMPT.format(
                name=name,
                candidates=candidates[:800] or "(无)",
                text=text,
            ),
        },
    ]
    kwargs: dict = {"model": model, "messages": messages, "stream": False}
    for attempt in range(MAX_RETRY):
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            wait = 6 * (attempt + 1)
            papers_log(f"  API异常({type(e).__name__})，{wait}s后重试")
            time.sleep(wait)
            continue
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError):
            return None
        parsed = _parse_papers_json(content)
        if parsed is not None:
            return parsed
        papers_log("  JSON解析失败，重试")
        time.sleep(3)
    return None


_VENUE_ONLY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 .\-]{1,14}$")


def is_suspicious_papers(rec: dict) -> bool:
    """papers_text 含明显噪音（期刊缩写、统计句、短碎片等）。"""
    pt = (rec.get("papers_text") or "").strip()
    if not pt:
        return False
    lines = [ln.strip() for ln in pt.splitlines() if ln.strip()]
    if not lines:
        return False
    name = rec.get("name", "")
    rd = rec.get("research_direction", "")
    noise = sum(1 for ln in lines if _is_paper_line_noise(ln, name, rd))
    if noise >= max(1, len(lines) // 2):
        return True
    if len(lines) <= 4 and max(len(ln) for ln in lines) < 45:
        return True
    if all(len(ln) < 20 for ln in lines):
        return True
    if all(_VENUE_ONLY_RE.match(ln) for ln in lines):
        return True
    return False


def apply_papers_screen(rec: dict, papers_raw: list[dict]) -> None:
    struct = _clean_papers_struct(papers_raw, 12)
    name = rec.get("name", "")
    rd = rec.get("research_direction", "")
    struct = [
        p for p in struct
        if not _is_paper_line_noise(p.get("title", ""), name, rd)
    ]
    if struct:
        rec["papers_struct"] = struct
        rec["papers_text"] = "\n".join(p["title"] for p in struct)[:5000]
    else:
        rec["papers_text"] = ""
        rec.pop("papers_struct", None)
    rec["papers_screened"] = True
    rec["papers_screen_version"] = SCREEN_VERSION
    _rebuild_corpus(rec)


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek 论文专项筛查")
    parser.add_argument("--name", default="", help="仅处理指定教师")
    parser.add_argument("--suspicious-only", action="store_true", help="仅筛查疑似噪音论文记录")
    parser.add_argument("--all", action="store_true", help="处理全部未筛查记录（默认）")
    parser.add_argument("--force", action="store_true", help="忽略 papers_screened 标记重筛")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--gap", type=float, default=0.8, help="请求间隔秒")
    args = parser.parse_args()

    if not TEACHERS_JSON.exists():
        papers_log(f"未找到 {TEACHERS_JSON}")
        sys.exit(1)

    records = json.loads(TEACHERS_JSON.read_text(encoding="utf-8"))
    colleges = _load_colleges()

    if args.name:
        todo = [r for r in records if r.get("name") == args.name]
        if not todo:
            papers_log(f"未找到教师: {args.name}")
            sys.exit(1)
    elif args.suspicious_only:
        todo = [r for r in records if is_suspicious_papers(r)]
    elif args.all:
        todo = list(records)
    else:
        papers_log("请指定 --name / --suspicious-only / --all")
        sys.exit(1)

    if not args.force:
        todo = [r for r in todo if not r.get("papers_screened") or args.name]

    if args.limit > 0:
        todo = todo[: args.limit]

    papers_log(
        f"待筛查 {len(todo)} 人，model={args.model} gap={args.gap}s "
        f"dry_run={args.dry_run} force={args.force}"
    )

    api_key = "sk-5fbc5d1e91b24e758ddf00f88150bcbc"
    if not api_key:
        papers_log("未设置 API key")
        sys.exit(1)

    try:
        from openai import OpenAI
    except ImportError:
        papers_log("pip install openai")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=args.base_url, timeout=TIMEOUT)
    ok = fail = 0
    ck_meta = {"ok": 0, "fail": 0, "last_name": ""}

    def _flush(name: str) -> None:
        if args.dry_run:
            return
        ck_meta["last_name"] = name
        _atomic_write_json(TEACHERS_JSON, records)
        _atomic_write_json(PAPERS_CK_JSON, ck_meta)

    def _on_interrupt(signum, frame) -> None:  # noqa: ARG001
        papers_log("中断，保存 checkpoint…")
        if ck_meta.get("last_name"):
            _flush(ck_meta["last_name"])
        sys.exit(130)

    if not args.dry_run:
        signal.signal(signal.SIGINT, _on_interrupt)

    for i, rec in enumerate(todo, start=1):
        name = rec.get("name", "")
        text = _source_text(rec)
        if not text.strip():
            papers_log(f"[{i}/{len(todo)}] {name} 无文本，清空论文栏")
            if not args.dry_run:
                rec["papers_text"] = ""
                rec.pop("papers_struct", None)
                rec["papers_screened"] = True
                _rebuild_corpus(rec)
            continue

        candidates = (rec.get("papers_text") or "").strip()
        papers_raw = call_papers_llm(client, args.model, name, text, candidates)
        if papers_raw is None:
            fail += 1
            papers_log(f"[{i}/{len(todo)}] {name} 筛查失败，保留原数据")
            time.sleep(args.gap)
            continue

        if args.dry_run:
            struct = _clean_papers_struct(papers_raw, 12)
            papers_log(
                f"[{i}/{len(todo)}] {name} dry-run -> {len(struct)} 条 "
                f"{[p.get('title','')[:40] for p in struct[:2]]}"
            )
        else:
            apply_papers_screen(rec, papers_raw)
            ok += 1
            n = len(rec.get("papers_struct") or [])
            papers_log(f"[{i}/{len(todo)}] {name} OK -> {n} 条论文")
            _flush(name)

        time.sleep(args.gap)

    papers_log(f"完成：成功 {ok}，失败 {fail}")
    if args.dry_run:
        return

    _atomic_write_json(TEACHERS_JSON, records)
    cr.write_ir_artifacts(records, colleges)
    cr.write_quality_report(records)
    if PAPERS_CK_JSON.exists():
        PAPERS_CK_JSON.unlink(missing_ok=True)
    papers_log("已回写 teachers.json 并重建语料/索引")


if __name__ == "__main__":
    main()
