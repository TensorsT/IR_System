import argparse
import csv
from datetime import datetime
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from ir_system import (
    CORPUS_DIR,
    TEACHERS_JSON,
    build_index,
    load_corpus,
    load_teachers,
    search,
)


@dataclass
class EvalCase:
    query: str
    expected_name: str
    note: str = ""


DEFAULT_CASES: List[EvalCase] = [
    EvalCase("周国栋", "周国栋", "姓名精确查询"),
    EvalCase("李培峰", "李培峰", "姓名精确查询"),
    EvalCase("陈文亮", "陈文亮", "姓名精确查询"),
    EvalCase("刘安", "刘安", "姓名精确查询"),
    EvalCase("张莉", "张莉", "姓名精确查询"),
    EvalCase("黄河", "黄河", "姓名精确查询"),
    EvalCase("孔芳", "孔芳", "姓名精确查询"),
    EvalCase("刘宁", "刘宁", "姓名精确查询"),
    EvalCase("蔡轶", "蔡轶", "姓名精确查询"),
    EvalCase("古海波", "古海波", "姓名精确查询"),
    EvalCase("徐玉红", "徐玉红", "姓名精确查询"),
    EvalCase("王志国", "王志国", "姓名精确查询"),
    EvalCase("邓滔", "邓滔", "姓名精确查询"),
    EvalCase("张天辉", "张天辉", "姓名精确查询"),
    EvalCase("孙林", "孙林", "姓名精确查询"),
    EvalCase("赵雷", "赵雷", "姓名精确查询"),
    EvalCase("王绍丹", "王绍丹", "姓名精确查询"),
    EvalCase("自然语言处理方向", "周国栋", "研究方向查询"),
    EvalCase("自然语言理解", "周国栋", "研究方向短语查询"),
    EvalCase("机器翻译", "周国栋", "短语放宽召回"),
    EvalCase("论文:信息抽取", "周国栋", "论文关键词查询"),
]


def load_resources():
    teachers = load_teachers(TEACHERS_JSON)
    docs = load_corpus(CORPUS_DIR)
    inverted, doc_norms = build_index(docs)
    return teachers, docs, inverted, doc_norms


def evaluate_mode(
    mode_name: str,
    allow_relax: bool,
    enable_fuzzy: bool,
    top_k: int,
    cases: List[EvalCase],
    teachers,
    docs,
    inverted,
    doc_norms,
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for case in cases:
        t0 = time.perf_counter()
        results = search(
            case.query,
            docs,
            teachers,
            inverted,
            doc_norms,
            top_k=top_k,
            allow_relax=allow_relax,
            enable_fuzzy=enable_fuzzy,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        names = [r.teacher.name for r in results]
        top1 = names[0] if names else ""
        expected_rank = 0
        for idx, name in enumerate(names[:top_k], start=1):
            if name == case.expected_name:
                expected_rank = idx
                break

        row = {
            "mode": mode_name,
            "query": case.query,
            "expected": case.expected_name,
            "top1": top1,
            "topk_names": "|".join(names[:top_k]),
            "hit@1": "1" if top1 == case.expected_name else "0",
            "hit@k": "1" if expected_rank > 0 else "0",
            "expected_rank": str(expected_rank),
            "result_count": str(len(results)),
            "latency_ms": f"{elapsed_ms:.2f}",
            "note": case.note,
        }
        rows.append(row)
    return rows


def write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def summarize_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    grouped: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["mode"], []).append(row)

    summary_rows: List[Dict[str, str]] = []
    for mode, mode_rows in grouped.items():
        total = len(mode_rows)
        hit1 = sum(int(r["hit@1"]) for r in mode_rows)
        hitk = sum(int(r["hit@k"]) for r in mode_rows)
        avg_latency = sum(float(r["latency_ms"]) for r in mode_rows) / total
        summary_rows.append(
            {
                "mode": mode,
                "cases": str(total),
                "hit@1_count": str(hit1),
                "hit@1_rate": f"{hit1/total:.4f}",
                "hit@k_count": str(hitk),
                "hit@k_rate": f"{hitk/total:.4f}",
                "avg_latency_ms": f"{avg_latency:.2f}",
            }
        )
    summary_rows.sort(key=lambda x: x["mode"])
    return summary_rows


def print_summary(summary_rows: List[Dict[str, str]]) -> None:
    if not summary_rows:
        print("No evaluation rows.")
        return
    print("=== Evaluation Summary ===")
    for row in summary_rows:
        cases = int(row["cases"])
        hit1 = int(row["hit@1_count"])
        hitk = int(row["hit@k_count"])
        avg_latency = float(row["avg_latency_ms"])
        print(
            f"{row['mode']}: hit@1={hit1}/{cases} ({hit1/cases:.2%}), "
            f"hit@k={hitk}/{cases} ({hitk/cases:.2%}), "
            f"avg_latency={avg_latency:.2f}ms"
        )


def append_run_log(
    run_log_path: Path,
    detail_out: str,
    summary_out: str,
    case_count: int,
    top_k: int,
    teacher_count: int,
    doc_count: int,
    summary_rows: List[Dict[str, str]],
) -> None:
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"## {now_text}",
        "",
        "### 本次执行内容",
        "- 执行了 baseline（不放宽、不模糊）评测。",
        "- 执行了 optimized（放宽+模糊）评测。",
        f"- 评测查询数: {case_count}，top_k: {top_k}。",
        f"- 数据规模: teachers={teacher_count}, docs={doc_count}。",
        f"- 产出明细: `{detail_out}`。",
        f"- 产出汇总: `{summary_out}`。",
        "",
        "### 指标摘要",
    ]
    for row in summary_rows:
        lines.append(
            "- {mode}: hit@1={hit1}/{cases} ({hit1_rate}), hit@k={hitk}/{cases} ({hitk_rate}), avg_latency={lat}ms".format(
                mode=row["mode"],
                hit1=row["hit@1_count"],
                hit1_rate=f"{float(row['hit@1_rate']):.2%}",
                hitk=row["hit@k_count"],
                hitk_rate=f"{float(row['hit@k_rate']):.2%}",
                cases=row["cases"],
                lat=row["avg_latency_ms"],
            )
        )
    lines.extend(["", "---", ""])

    with run_log_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate baseline vs optimized IR retrieval quality."
    )
    parser.add_argument(
        "--top-k", type=int, default=5, help="Top-K results returned for each query."
    )
    parser.add_argument(
        "--out",
        default="outputs/eval_compare.csv",
        help="CSV output path for detailed comparison rows.",
    )
    parser.add_argument(
        "--summary-out",
        default="outputs/eval_summary.csv",
        help="CSV output path for per-mode summary rows.",
    )
    parser.add_argument(
        "--run-log",
        default="outputs/eval_run_log.md",
        help="Markdown log file that appends what was done each run.",
    )
    args = parser.parse_args()

    modes = [
        ("baseline", False, False),
        ("optimized", True, True),
    ]
    teachers, docs, inverted, doc_norms = load_resources()

    all_rows: List[Dict[str, str]] = []
    for mode_name, allow_relax, enable_fuzzy in modes:
        rows = evaluate_mode(
            mode_name=mode_name,
            allow_relax=allow_relax,
            enable_fuzzy=enable_fuzzy,
            top_k=args.top_k,
            cases=DEFAULT_CASES,
            teachers=teachers,
            docs=docs,
            inverted=inverted,
            doc_norms=doc_norms,
        )
        all_rows.extend(rows)

    write_csv(Path(args.out), all_rows)
    summary_rows = summarize_rows(all_rows)
    write_csv(Path(args.summary_out), summary_rows)
    append_run_log(
        run_log_path=Path(args.run_log),
        detail_out=args.out,
        summary_out=args.summary_out,
        case_count=len(DEFAULT_CASES),
        top_k=args.top_k,
        teacher_count=len(teachers),
        doc_count=len(docs),
        summary_rows=summary_rows,
    )
    print_summary(summary_rows)
    print(f"Detailed rows saved to: {args.out}")
    print(f"Summary rows saved to: {args.summary_out}")
    print(f"Run log appended to: {args.run_log}")


if __name__ == "__main__":
    main()
