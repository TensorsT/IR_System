import tkinter as tk
import time
import webbrowser

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, LEFT, X, Y

try:
    from fuzzywuzzy import fuzz

    _FUZZY_AVAILABLE = True
except Exception:
    _FUZZY_AVAILABLE = False

from ir_system import (
    build_display,
    build_index,
    load_corpus,
    load_teachers,
    search,
)


def _matches_text(value: str, needle: str) -> bool:
    if not needle:
        return True
    if not value:
        return False
    return needle.casefold() in value.casefold()


def _matches_text_fuzzy(value: str, needle: str, threshold: int = 70) -> bool:
    if _matches_text(value, needle):
        return True
    if not (_FUZZY_AVAILABLE and value and needle):
        return False
    try:
        return fuzz.partial_ratio(needle, value) >= threshold
    except Exception:
        return False


def _sort_results(results, mode: str):
    if mode == "姓名":
        return sorted(results, key=lambda x: (x.teacher.name or "", -x.score))
    if mode == "学院":
        return sorted(
            results, key=lambda x: (x.teacher.department or "", x.teacher.name or "", -x.score)
        )
    return sorted(results, key=lambda x: x.score, reverse=True)


def _filter_results(
    results,
    name_filter,
    research_filter,
    paper_filter,
    use_fuzzy: bool = True,
    fuzzy_threshold: int = 70,
):
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
            if use_fuzzy:
                matched = _matches_text_fuzzy(haystack, research_filter, fuzzy_threshold)
            else:
                matched = _matches_text(haystack, research_filter)
            if not matched:
                continue
        if paper_filter:
            haystack = " ".join([teacher.papers_text, doc_text])
            if use_fuzzy:
                matched = _matches_text_fuzzy(haystack, paper_filter, fuzzy_threshold)
            else:
                matched = _matches_text(haystack, paper_filter)
            if not matched:
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

    threshold_frame = ttk.Frame(mode_frame)
    threshold_frame.pack(fill=X, pady=(8, 0))
    ttk.Label(
        threshold_frame,
        text="模糊阈值",
        font=("Microsoft YaHei UI", 10, "bold"),
    ).pack(anchor="w")
    fuzzy_threshold_var = tk.IntVar(value=70)
    fuzzy_threshold_spin = tk.Spinbox(
        threshold_frame,
        from_=0,
        to=100,
        textvariable=fuzzy_threshold_var,
        width=6,
        font=("Microsoft YaHei UI", 10),
    )
    fuzzy_threshold_spin.pack(anchor="w", pady=(4, 0))
    ttk.Label(
        threshold_frame,
        text="0-100，越低越宽松",
        font=("Microsoft YaHei UI", 9),
        bootstyle="secondary",
    ).pack(anchor="w", pady=(2, 0))

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
    ttk.Label(
        config_frame,
        text="返回排名前 k 项（Top-K），用于计算 hit@k（越大召回越高）。",
        font=("Microsoft YaHei UI", 9),
        foreground="#6b7280",
    ).pack(anchor="w", pady=(0, 8))

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

    search_btn = ttk.Button(
        button_frame, text="搜索", command=lambda: on_search(), bootstyle="primary"
    )
    search_btn.pack(fill=X, ipady=4)

    compare_btn = ttk.Button(
        button_frame,
        text="基础 vs 优化 对比",
        command=lambda: on_compare(),
        bootstyle="info",
    )
    compare_btn.pack(fill=X)

    clear_btn = ttk.Button(
        button_frame,
        text="清空",
        command=lambda: on_clear(),
        bootstyle="secondary-outline",
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

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-event.delta / 120), "units")

    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    last_query = {"text": ""}

    def _clear_cards():
        for child in scroll_frame.winfo_children():
            child.destroy()

    def _add_field(parent, label, value):
        row = ttk.Frame(parent)
        row.pack(fill=X, pady=(6, 0))
        ttk.Label(
            row,
            text=label,
            font=("Microsoft YaHei UI", 9, "bold"),
            bootstyle="secondary",
            width=6,
        ).pack(side=LEFT, anchor="n")
        ttk.Label(
            row,
            text=value,
            font=("Microsoft YaHei UI", 10),
            wraplength=720,
            justify="left",
        ).pack(side=LEFT, fill=X, expand=True)

    _CCF_BADGE_STYLE = {"A": "inverse-danger", "B": "inverse-warning", "C": "inverse-info"}

    def _add_chip_row(parent, label, chips, bootstyle="inverse-primary"):
        if not chips:
            return
        row = ttk.Frame(parent)
        row.pack(fill=X, pady=(6, 0))
        ttk.Label(
            row,
            text=label,
            font=("Microsoft YaHei UI", 9, "bold"),
            bootstyle="secondary",
            width=6,
        ).pack(side=LEFT, anchor="n")
        chip_wrap = ttk.Frame(row)
        chip_wrap.pack(side=LEFT, fill=X, expand=True)
        for chip in chips:
            ttk.Label(
                chip_wrap,
                text=f" {chip} ",
                font=("Microsoft YaHei UI", 9, "bold"),
                bootstyle=bootstyle,
            ).pack(side=LEFT, padx=(0, 5), pady=2)

    def _add_research_section(parent, view):
        if view.research_tags:
            _add_chip_row(parent, "研究方向", view.research_tags, "inverse-primary")
        elif view.research:
            _add_field(parent, "研究方向", view.research)

    def _add_papers_section(parent, paper_items):
        if not paper_items:
            return
        row = ttk.Frame(parent)
        row.pack(fill=X, pady=(8, 0))
        ttk.Label(
            row,
            text="论文",
            font=("Microsoft YaHei UI", 9, "bold"),
            bootstyle="secondary",
            width=6,
        ).pack(side=LEFT, anchor="n")
        list_frame = ttk.Frame(row)
        list_frame.pack(side=LEFT, fill=X, expand=True)

        for idx, paper in enumerate(paper_items[:10], start=1):
            item = ttk.Frame(list_frame)
            item.pack(fill=X, pady=3)

            left = ttk.Frame(item)
            left.pack(side=LEFT, anchor="n", padx=(0, 8))
            ttk.Label(
                left,
                text=f"{idx:02d}",
                font=("Consolas", 9, "bold"),
                bootstyle="secondary",
                width=3,
            ).pack(side=LEFT)
            if paper.ccf_rank:
                ttk.Label(
                    left,
                    text=f" CCF-{paper.ccf_rank} ",
                    font=("Microsoft YaHei UI", 8, "bold"),
                    bootstyle=_CCF_BADGE_STYLE.get(paper.ccf_rank, "secondary"),
                ).pack(side=LEFT, padx=(4, 0))

            body = ttk.Frame(item)
            body.pack(side=LEFT, fill=X, expand=True)
            ttk.Label(
                body,
                text=paper.title,
                font=("Microsoft YaHei UI", 10),
                wraplength=640,
                justify="left",
            ).pack(anchor="w")
            meta_parts = [x for x in [paper.venue, paper.year] if x]
            if meta_parts:
                ttk.Label(
                    body,
                    text=" · ".join(meta_parts),
                    font=("Microsoft YaHei UI", 9),
                    bootstyle="secondary",
                ).pack(anchor="w", pady=(1, 0))

    def _render_results(results):
        _clear_cards()
        if not results:
            empty = ttk.Frame(scroll_frame, padding=24)
            empty.pack(fill=X, pady=20)
            ttk.Label(
                empty,
                text="未命中结果",
                font=("Microsoft YaHei UI", 13, "bold"),
                bootstyle="secondary",
            ).pack()
            ttk.Label(
                empty,
                text="可尝试放宽条件（增大top-k或缩小模糊阈值）、切换优化模式，或更换关键词。",
                font=("Microsoft YaHei UI", 10),
                bootstyle="secondary",
            ).pack(pady=(6, 0))
            return

        for i, result in enumerate(results, start=1):
            view = build_display(result, i, last_query["text"])

            card = ttk.Frame(scroll_frame, padding=14, bootstyle="light")
            card.pack(fill=X, pady=7, padx=2)

            title_row = ttk.Frame(card)
            title_row.pack(fill=X)
            ttk.Label(
                title_row,
                text=f" {i} ",
                font=("Microsoft YaHei UI", 10, "bold"),
                bootstyle="inverse-primary",
            ).pack(side=LEFT, padx=(0, 8))
            ttk.Label(
                title_row,
                text=view.name,
                font=("Microsoft YaHei UI", 13, "bold"),
            ).pack(side=LEFT)
            meta = " · ".join([t for t in [view.department, view.career] if t])
            if meta:
                ttk.Label(
                    title_row,
                    text=meta,
                    font=("Microsoft YaHei UI", 10),
                    bootstyle="secondary",
                ).pack(side=LEFT, padx=10)

            ttk.Label(
                title_row,
                text=f"相关度 {view.score:.2f}",
                font=("Microsoft YaHei UI", 9, "bold"),
                bootstyle="inverse-info",
            ).pack(side="right")
            if view.url:
                ttk.Button(
                    title_row,
                    text="打开主页",
                    bootstyle="link",
                    command=lambda u=view.url: webbrowser.open(u),
                ).pack(side="right", padx=6)

            ttk.Separator(card, orient="horizontal").pack(fill=X, pady=(8, 2))

            _add_research_section(card, view)
            if view.profile_keywords:
                _add_chip_row(card, "研究关键词", view.profile_keywords, "info")
            if view.intro:
                _add_field(card, "简介", view.intro)
            if view.paper_items:
                _add_papers_section(card, view.paper_items)
            elif view.papers:
                _add_field(card, "论文", view.papers)
            if view.snippet:
                _add_field(card, "片段", view.snippet)

            if view.keywords:
                _add_chip_row(card, "命中关键词", view.keywords, "inverse-success")

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

        last_query["text"] = search_text
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
                fuzzy_threshold=max(0, min(100, int(fuzzy_threshold_var.get()))),
            )
        elif name_filter:
            results = search(name_filter, docs, teachers, inverted, doc_norms)
        else:
            results = []

        results = _filter_results(
            results,
            name_filter,
            research_filter,
            paper_filter,
            use_fuzzy=enable_fuzzy_var.get(),
            fuzzy_threshold=max(0, min(100, int(fuzzy_threshold_var.get()))),
        )
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

    def _current_query():
        free_query = query_entry.get().strip()
        if free_query:
            return free_query
        parts = [
            research_entry.get().strip(),
            paper_entry.get().strip(),
            name_entry.get().strip(),
        ]
        return " ".join(p for p in parts if p).strip()

    def _make_scroll_pane(parent):
        wrapper = ttk.Frame(parent)
        pane_canvas = tk.Canvas(wrapper, highlightthickness=0)
        bar = ttk.Scrollbar(wrapper, orient="vertical", command=pane_canvas.yview)
        pane_canvas.configure(yscrollcommand=bar.set)
        inner = ttk.Frame(pane_canvas)
        window = pane_canvas.create_window((0, 0), window=inner, anchor="nw")
        pane_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        bar.pack(side=LEFT, fill=Y)
        inner.bind(
            "<Configure>",
            lambda e: pane_canvas.configure(scrollregion=pane_canvas.bbox("all")),
        )
        pane_canvas.bind(
            "<Configure>", lambda e: pane_canvas.itemconfig(window, width=e.width)
        )
        pane_canvas.configure(background=style.lookup("TFrame", "background"))
        return wrapper, inner

    def _render_compact(parent, results, query, highlight_names):
        if not results:
            ttk.Label(
                parent,
                text="无结果",
                font=("Microsoft YaHei UI", 11),
                bootstyle="secondary",
            ).pack(anchor="w", pady=10)
            return
        for i, result in enumerate(results, start=1):
            view = build_display(result, i, query)
            is_new = view.name in highlight_names
            card = ttk.Frame(
                parent, padding=10, bootstyle="success" if is_new else "light"
            )
            card.pack(fill=X, pady=5)

            head = ttk.Frame(card)
            head.pack(fill=X)
            ttk.Label(
                head,
                text=f" {i} ",
                font=("Microsoft YaHei UI", 9, "bold"),
                bootstyle="inverse-primary",
            ).pack(side=LEFT, padx=(0, 6))
            ttk.Label(
                head, text=view.name, font=("Microsoft YaHei UI", 11, "bold")
            ).pack(side=LEFT)
            ttk.Label(
                head,
                text=f"{view.score:.2f}",
                font=("Microsoft YaHei UI", 9, "bold"),
                bootstyle="inverse-info",
            ).pack(side="right")
            if is_new:
                ttk.Label(
                    head,
                    text="优化新增",
                    font=("Microsoft YaHei UI", 8, "bold"),
                    bootstyle="inverse-success",
                ).pack(side="right", padx=4)

            if view.department:
                ttk.Label(
                    card,
                    text=view.department,
                    font=("Microsoft YaHei UI", 9),
                    bootstyle="secondary",
                ).pack(anchor="w", pady=(4, 0))
            if view.research:
                ttk.Label(
                    card,
                    text=view.research,
                    font=("Microsoft YaHei UI", 9),
                    wraplength=380,
                    justify="left",
                ).pack(anchor="w", pady=(4, 0))

    def on_compare():
        query = _current_query()
        if not query:
            status_var.set("请输入查询词后再进行对比。")
            return

        top_k = max(3, min(20, int(topk_var.get())))

        t0 = time.perf_counter()
        base = search(
            query, docs, teachers, inverted, doc_norms,
            top_k=top_k, allow_relax=False, enable_fuzzy=False,
        )
        base_ms = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        opt = search(
            query, docs, teachers, inverted, doc_norms,
            top_k=top_k,
            allow_relax=True,
            enable_fuzzy=True,
            fuzzy_threshold=max(0, min(100, int(fuzzy_threshold_var.get()))),
        )
        opt_ms = (time.perf_counter() - t1) * 1000.0

        base_names = {r.teacher.name for r in base}
        new_names = {r.teacher.name for r in opt if r.teacher.name not in base_names}

        win = ttk.Toplevel(title=f"对比检索: {query}")
        win.geometry("980x680")
        outer = ttk.Frame(win, padding=14)
        outer.pack(fill=BOTH, expand=True)

        ttk.Label(
            outer,
            text=f"查询: {query}    优化新增召回: {len(new_names)} 位",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w")
        ttk.Separator(outer, orient="horizontal").pack(fill=X, pady=(8, 6))

        columns = ttk.Frame(outer)
        columns.pack(fill=BOTH, expand=True)
        columns.columnconfigure(0, weight=1, uniform="col")
        columns.columnconfigure(1, weight=1, uniform="col")
        columns.rowconfigure(1, weight=1)

        ttk.Label(
            columns,
            text=f"基础模式  ·  {len(base)} 条  ·  {base_ms:.2f} ms",
            font=("Microsoft YaHei UI", 11, "bold"),
            bootstyle="secondary",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 6))
        ttk.Label(
            columns,
            text=f"优化模式  ·  {len(opt)} 条  ·  {opt_ms:.2f} ms",
            font=("Microsoft YaHei UI", 11, "bold"),
            bootstyle="success",
        ).grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 6))

        left_wrap, left_inner = _make_scroll_pane(columns)
        left_wrap.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        right_wrap, right_inner = _make_scroll_pane(columns)
        right_wrap.grid(row=1, column=1, sticky="nsew", padx=(8, 0))

        _render_compact(left_inner, base, query, set())
        _render_compact(right_inner, opt, query, new_names)

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
