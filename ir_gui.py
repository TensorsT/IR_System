import tkinter as tk
import time
import webbrowser

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, LEFT, X, Y

from ir_system import (
    _mask_private,
    build_index,
    load_corpus,
    load_teachers,
    search,
)


def _format_result(result, rank):
    teacher = result.teacher
    lines = [f"[{rank}] {teacher.name}  |  {teacher.department}  |  {teacher.career}"]
    if teacher.research_direction:
        lines.append(f"Research: {_mask_private(teacher.research_direction)}")
    if teacher.personal_intro:
        intro = _mask_private(teacher.personal_intro.replace("\n", " ").strip())
        lines.append(f"Intro: {intro[:200]}")
    if teacher.papers_text:
        papers = _mask_private(teacher.papers_text.replace("\n", " ").strip())
        lines.append(f"Papers: {papers[:200]}")
    if result.snippet:
        lines.append(f"Snippet: {_mask_private(result.snippet)}")
    if teacher.url:
        lines.append(f"URL: {teacher.url}")
    return "\n".join(lines)


def _matches_text(value: str, needle: str) -> bool:
    if not needle:
        return True
    if not value:
        return False
    return needle.casefold() in value.casefold()


def _sort_results(results, mode: str):
    if mode == "姓名":
        return sorted(results, key=lambda x: (x.teacher.name or "", -x.score))
    if mode == "学院":
        return sorted(
            results, key=lambda x: (x.teacher.department or "", x.teacher.name or "", -x.score)
        )
    return sorted(results, key=lambda x: x.score, reverse=True)


def _keyword_hits(query: str, teacher, doc_text: str):
    tokens = [t.strip() for t in query.replace("：", ":").split() if t.strip()]
    if not tokens:
        tokens = [query.strip()] if query.strip() else []
    haystack = " ".join(
        [
            teacher.name,
            teacher.department,
            teacher.research_direction,
            teacher.papers_text,
            doc_text,
        ]
    ).casefold()
    hits = []
    for token in tokens:
        if token.casefold() in haystack and token not in hits:
            hits.append(token)
    return hits[:4]


def _filter_results(results, name_filter, research_filter, paper_filter):
    filtered = []
    for item in results:
        teacher = item.teacher
        doc_text = item.doc.text or ""
        if name_filter and name_filter.replace(" ", "") not in teacher.name.replace(" ", ""):
            continue
        if research_filter:
            haystack = " ".join(
                [teacher.research_direction, teacher.personal_intro, doc_text]
            )
            if not _matches_text(haystack, research_filter):
                continue
        if paper_filter:
            haystack = " ".join([teacher.papers_text, doc_text])
            if not _matches_text(haystack, paper_filter):
                continue
        filtered.append(item)
    return filtered


def main():
    teachers = load_teachers("crawled_data/teachers.json")
    docs = load_corpus("crawled_data/corpus")
    inverted, doc_norms = build_index(docs)

    root = ttk.Window(themename="flatly")
    root.title("苏州大学导师检索系统")
    root.geometry("1120x760")

    style = ttk.Style()

    container = ttk.Frame(root, padding=18)
    container.pack(fill=BOTH, expand=True)

    header_frame = ttk.Frame(container)
    header_frame.pack(fill=X)

    header = ttk.Label(
        header_frame,
        text="苏州大学导师信息检索",
        font=("Microsoft YaHei UI", 20, "bold"),
    )
    header.pack(side=LEFT)

    subtitle = ttk.Label(
        header_frame,
        text="支持姓名 / 研究方向 / 论文关键词，含基础与优化模式对比",
        font=("Microsoft YaHei UI", 10),
        foreground="#6b7280",
    )
    subtitle.pack(side=LEFT, padx=12)

    theme_names = sorted(style.theme_names())
    theme_var = tk.StringVar(value=style.theme_use())
    theme_box = ttk.Combobox(
        header_frame,
        values=theme_names,
        textvariable=theme_var,
        width=18,
        state="readonly",
    )
    theme_box.pack(side=LEFT, padx=12)

    hint = ttk.Label(
        container,
        text="示例：自然语言处理方向 | 周国栋 | 论文: 信息抽取",
        font=("Microsoft YaHei UI", 10),
        foreground="#6b7280",
    )
    hint.pack(pady=(6, 12))

    content = ttk.Frame(container)
    content.pack(fill=BOTH, expand=True)
    content.columnconfigure(1, weight=1)
    content.rowconfigure(0, weight=1)

    sidebar = ttk.Frame(content, padding=(0, 0, 12, 0))
    sidebar.grid(row=0, column=0, sticky="ns")

    main_area = ttk.Frame(content)
    main_area.grid(row=0, column=1, sticky="nsew")

    query_label = ttk.Label(sidebar, text="自由查询", font=("Microsoft YaHei UI", 11, "bold"))
    query_label.pack(anchor="w")
    query_entry = ttk.Entry(sidebar, font=("Microsoft YaHei UI", 11), width=24)
    query_entry.pack(pady=(6, 12), fill=X)

    name_label = ttk.Label(sidebar, text="姓名过滤", font=("Microsoft YaHei UI", 11, "bold"))
    name_label.pack(anchor="w")
    name_entry = ttk.Entry(sidebar, font=("Microsoft YaHei UI", 11), width=24)
    name_entry.pack(pady=(6, 12), fill=X)

    research_label = ttk.Label(sidebar, text="研究方向过滤", font=("Microsoft YaHei UI", 11, "bold"))
    research_label.pack(anchor="w")
    research_entry = ttk.Entry(sidebar, font=("Microsoft YaHei UI", 11), width=24)
    research_entry.pack(pady=(6, 12), fill=X)

    paper_label = ttk.Label(sidebar, text="论文关键词过滤", font=("Microsoft YaHei UI", 11, "bold"))
    paper_label.pack(anchor="w")
    paper_entry = ttk.Entry(sidebar, font=("Microsoft YaHei UI", 11), width=24)
    paper_entry.pack(pady=(6, 12), fill=X)

    mode_frame = ttk.Labelframe(sidebar, text="检索模式", padding=10)
    mode_frame.pack(fill=X, pady=(4, 8))
    allow_relax_var = tk.BooleanVar(value=True)
    enable_fuzzy_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        mode_frame,
        text="启用放宽检索（优化）",
        variable=allow_relax_var,
        bootstyle="round-toggle",
    ).pack(anchor="w")
    ttk.Checkbutton(
        mode_frame,
        text="启用模糊检索（优化）",
        variable=enable_fuzzy_var,
        bootstyle="round-toggle",
    ).pack(anchor="w", pady=(6, 0))

    config_frame = ttk.Labelframe(sidebar, text="结果配置", padding=10)
    config_frame.pack(fill=X, pady=(0, 8))
    ttk.Label(config_frame, text="Top-K", font=("Microsoft YaHei UI", 10, "bold")).pack(
        anchor="w"
    )
    topk_var = tk.IntVar(value=8)
    topk_spin = tk.Spinbox(
        config_frame,
        from_=3,
        to=20,
        textvariable=topk_var,
        width=6,
        font=("Microsoft YaHei UI", 10),
    )
    topk_spin.pack(anchor="w", pady=(4, 8))

    ttk.Label(config_frame, text="排序方式", font=("Microsoft YaHei UI", 10, "bold")).pack(
        anchor="w"
    )
    sort_var = tk.StringVar(value="相关度")
    sort_box = ttk.Combobox(
        config_frame,
        values=["相关度", "姓名", "学院"],
        textvariable=sort_var,
        state="readonly",
    )
    sort_box.pack(fill=X, pady=(4, 0))

    quick_frame = ttk.Labelframe(sidebar, text="快捷查询", padding=10)
    quick_frame.pack(fill=X, pady=(0, 8))

    quick_queries = [
        "周国栋",
        "自然语言处理方向",
        "机器翻译",
        "论文: 信息抽取",
        "图像处理",
    ]

    def apply_quick_query(text: str):
        query_entry.delete(0, tk.END)
        query_entry.insert(0, text)
        on_search()

    for q in quick_queries:
        ttk.Button(
            quick_frame,
            text=q,
            command=lambda text=q: apply_quick_query(text),
            bootstyle="secondary-outline",
        ).pack(fill=X, pady=2)

    button_frame = ttk.Frame(sidebar)
    button_frame.pack(pady=8, fill=X)

    search_btn = ttk.Button(button_frame, text="Search", command=lambda: on_search())
    search_btn.pack(fill=X)

    clear_btn = ttk.Button(
        button_frame,
        text="Clear",
        command=lambda: on_clear(),
        bootstyle="secondary",
    )
    clear_btn.pack(pady=6, fill=X)

    results_header = ttk.Frame(main_area)
    results_header.pack(fill=X)
    results_label = ttk.Label(
        results_header, text="检索结果", font=("Microsoft YaHei UI", 12, "bold")
    )
    results_label.pack(anchor="w")
    status_var = tk.StringVar(value="等待查询...")
    status_label = ttk.Label(
        results_header,
        textvariable=status_var,
        font=("Microsoft YaHei UI", 10),
        foreground="#6b7280",
    )
    status_label.pack(anchor="w", pady=(2, 0))

    results_container = ttk.Frame(main_area)
    results_container.pack(fill=BOTH, expand=True, pady=(6, 0))

    canvas = tk.Canvas(results_container, highlightthickness=0)
    scrollbar = ttk.Scrollbar(results_container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)

    scroll_frame = ttk.Frame(canvas)
    scroll_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

    canvas.pack(side=LEFT, fill=BOTH, expand=True)
    scrollbar.pack(side=LEFT, fill=Y)

    def _update_canvas_width(event):
        canvas.itemconfig(scroll_window, width=event.width)

    def _update_scrollregion(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    scroll_frame.bind("<Configure>", _update_scrollregion)
    canvas.bind("<Configure>", _update_canvas_width)

    def _clear_cards():
        for child in scroll_frame.winfo_children():
            child.destroy()

    def _render_results(results):
        _clear_cards()
        if not results:
            empty_label = ttk.Label(
                scroll_frame,
                text="未命中结果，请尝试放宽条件或切换优化模式。",
                font=("Microsoft YaHei UI", 11),
                bootstyle="secondary",
            )
            empty_label.pack(anchor="w", pady=10)
            return

        for i, result in enumerate(results, start=1):
            card = ttk.Frame(scroll_frame, padding=12, bootstyle="light")
            card.pack(fill=X, pady=6)

            title_row = ttk.Frame(card)
            title_row.pack(fill=X)
            title = ttk.Label(
                title_row,
                text=f"[{i}] {result.teacher.name} | {result.teacher.department} | {result.teacher.career}",
                font=("Microsoft YaHei UI", 11, "bold"),
            )
            title.pack(side=LEFT)
            if result.teacher.url:
                ttk.Button(
                    title_row,
                    text="打开主页",
                    bootstyle="link",
                    command=lambda u=result.teacher.url: webbrowser.open(u),
                ).pack(side=LEFT, padx=8)

            if result.teacher.research_direction:
                ttk.Label(
                    card,
                    text=f"研究方向: {_mask_private(result.teacher.research_direction)}",
                    font=("Microsoft YaHei UI", 10),
                ).pack(anchor="w", pady=(6, 0))

            if result.teacher.personal_intro:
                intro = _mask_private(result.teacher.personal_intro.replace("\n", " ").strip())
                ttk.Label(
                    card,
                    text=f"简介: {intro[:220]}",
                    font=("Microsoft YaHei UI", 10),
                    wraplength=760,
                    justify="left",
                ).pack(anchor="w", pady=(6, 0))

            if result.teacher.papers_text:
                papers = _mask_private(result.teacher.papers_text.replace("\n", " ").strip())
                ttk.Label(
                    card,
                    text=f"论文: {papers[:220]}",
                    font=("Microsoft YaHei UI", 10),
                    wraplength=760,
                    justify="left",
                ).pack(anchor="w", pady=(6, 0))

            if result.snippet:
                ttk.Label(
                    card,
                    text=f"命中片段: {_mask_private(result.snippet)}",
                    font=("Microsoft YaHei UI", 10),
                    wraplength=760,
                    justify="left",
                ).pack(anchor="w", pady=(6, 0))

            hits = _keyword_hits(query_entry.get().strip(), result.teacher, result.doc.text or "")
            if hits:
                ttk.Label(
                    card,
                    text=f"命中关键词: {' / '.join(hits)}",
                    font=("Microsoft YaHei UI", 10),
                    foreground="#0f766e",
                ).pack(anchor="w", pady=(6, 0))

    def on_search():
        free_query = query_entry.get().strip()
        name_filter = name_entry.get().strip()
        research_filter = research_entry.get().strip()
        paper_filter = paper_entry.get().strip()

        search_text = free_query
        if not search_text and (research_filter or paper_filter):
            search_text = " ".join([research_filter, paper_filter]).strip()
        if not search_text and name_filter:
            search_text = name_filter

        t0 = time.perf_counter()
        if search_text:
            results = search(
                search_text,
                docs,
                teachers,
                inverted,
                doc_norms,
                top_k=max(3, min(20, int(topk_var.get()))),
                allow_relax=allow_relax_var.get(),
                enable_fuzzy=enable_fuzzy_var.get(),
            )
        elif name_filter:
            results = search(name_filter, docs, teachers, inverted, doc_norms)
        else:
            results = []

        results = _filter_results(results, name_filter, research_filter, paper_filter)
        results = _sort_results(results, sort_var.get())
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        mode_text = "优化模式" if allow_relax_var.get() else "基础模式"
        status_var.set(
            f"共 {len(results)} 条结果 | 耗时 {elapsed_ms:.2f} ms | {mode_text} | 排序: {sort_var.get()}"
        )
        _render_results(results)

    def on_clear():
        query_entry.delete(0, tk.END)
        name_entry.delete(0, tk.END)
        research_entry.delete(0, tk.END)
        paper_entry.delete(0, tk.END)
        status_var.set("已清空查询条件。")
        _clear_cards()

    def on_theme_change(_event=None):
        new_theme = theme_var.get()
        style.theme_use(new_theme)
        canvas.configure(background=style.lookup("TFrame", "background"))

    theme_box.bind("<<ComboboxSelected>>", on_theme_change)
    canvas.configure(background=style.lookup("TFrame", "background"))

    query_entry.bind("<Return>", lambda event: on_search())
    name_entry.bind("<Return>", lambda event: on_search())
    research_entry.bind("<Return>", lambda event: on_search())
    paper_entry.bind("<Return>", lambda event: on_search())

    root.mainloop()


if __name__ == "__main__":
    main()
