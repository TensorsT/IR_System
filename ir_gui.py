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
    query_matches_text,
    search,
)

# ═══════════════════════════════════════════════════════════════════════════════
# 设计系统 - Design Tokens
# ═══════════════════════════════════════════════════════════════════════════════

# 字体系统
FONT_DISPLAY = ("Microsoft YaHei UI", 24, "bold")
FONT_TITLE   = ("Microsoft YaHei UI", 18, "bold")
FONT_HEADING = ("Microsoft YaHei UI", 13, "bold")
FONT_SUBHEAD = ("Microsoft YaHei UI", 11, "bold")
FONT_BODY    = ("Microsoft YaHei UI", 10)
FONT_SMALL   = ("Microsoft YaHei UI", 9)
FONT_CAPTION = ("Microsoft YaHei UI", 9)
FONT_MONO    = ("Consolas", 9, "bold")
FONT_BADGE   = ("Microsoft YaHei UI", 8, "bold")

# 颜色系统
COLOR_MUTED      = "#6b7280"
COLOR_DIVIDER    = "#e5e7eb"
COLOR_ACCENT     = "#4f46e5"      # 靛蓝主色
COLOR_ACCENT_HOVER = "#4338ca"
COLOR_SUCCESS    = "#10b981"
COLOR_WARNING    = "#f59e0b"
COLOR_DANGER     = "#ef4444"
COLOR_INFO       = "#3b82f6"
COLOR_BG_SECOND  = "#f8fafc"      # 次要背景
COLOR_CARD_BG    = "#ffffff"
COLOR_TEXT_PRIMARY = "#1e293b"
COLOR_TEXT_SECONDARY = "#64748b"

# 布局常量
SIDEBAR_W        = 240
CARD_RADIUS      = 8
SECTION_GAP      = 16
ITEM_GAP         = 10


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════

def _matches_text(value: str, needle: str) -> bool:
    if not needle:
        return True
    if not value:
        return False
    if query_matches_text(value, needle):
        return True
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
            matched = (
                _matches_text_fuzzy(haystack, research_filter, fuzzy_threshold)
                if use_fuzzy
                else _matches_text(haystack, research_filter)
            )
            if not matched:
                continue
        if paper_filter:
            haystack = " ".join([teacher.papers_text, doc_text])
            matched = (
                _matches_text_fuzzy(haystack, paper_filter, fuzzy_threshold)
                if use_fuzzy
                else _matches_text(haystack, paper_filter)
            )
            if not matched:
                continue
        filtered.append(item)
    return filtered


# ═══════════════════════════════════════════════════════════════════════════════
# 小组件工厂 - 增强版
# ═══════════════════════════════════════════════════════════════════════════════

def _section_label(parent, text: str, icon: str = ""):
    """侧边栏区块标题：带图标的现代标签"""
    frame = ttk.Frame(parent)
    frame.pack(anchor="w", pady=(SECTION_GAP, 6), fill=X)

    if icon:
        ttk.Label(
            frame,
            text=icon,
            font=("Microsoft YaHei UI", 10),
            foreground=COLOR_ACCENT,
        ).pack(side=LEFT, padx=(0, 4))

    ttk.Label(
        frame,
        text=text.upper(),
        font=("Microsoft YaHei UI", 8, "bold"),
        foreground="#9ca3af",
    ).pack(side=LEFT)


def _divider(parent, pady=(8, 8)):
    """现代分割线"""
    sep = ttk.Frame(parent, height=1)
    sep.pack(fill=X, pady=pady)
    ttk.Separator(sep, orient="horizontal").pack(fill=X)


def _labeled_entry(parent, label: str, icon: str = "🔍", width: int = 22):
    """返回 entry，带图标标签"""
    _section_label(parent, label, icon)
    entry = ttk.Entry(parent, font=FONT_BODY, width=width)
    entry.pack(fill=X, pady=(0, 6))
    return entry


def _styled_button(parent, text, command, bootstyle="primary", icon="", **kwargs):
    """创建统一风格的按钮"""
    btn_text = f"{icon}  {text}" if icon else text
    btn = ttk.Button(
        parent,
        text=btn_text,
        command=command,
        bootstyle=bootstyle,
        **kwargs
    )
    return btn


# ═══════════════════════════════════════════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    teachers = load_teachers("crawled_data/teachers.json")
    docs = load_corpus("crawled_data/corpus")
    inverted, doc_norms, idf = build_index(docs)

    # ── 根窗口 ──
    root = ttk.Window(themename="flatly")
    root.title("苏州大学导师检索系统")
    root.geometry("1300x850")
    root.minsize(1000, 700)

    style = ttk.Style()

    # 自定义样式覆盖
    style.configure("Card.TFrame", background=COLOR_CARD_BG)
    style.configure("Sidebar.TFrame", background=COLOR_BG_SECOND)

    # ── 最外层容器 ──
    root_frame = ttk.Frame(root, padding=(20, 16, 20, 16))
    root_frame.pack(fill=BOTH, expand=True)

    # ══════════════════════════════════════════════
    # HEAD BAR - 现代标题栏
    # ══════════════════════════════════════════════
    head = ttk.Frame(root_frame)
    head.pack(fill=X, pady=(0, 12))

    # 左侧：品牌区
    head_left = ttk.Frame(head)
    head_left.pack(side=LEFT, fill=X, expand=True)

    # Logo/品牌图标
    brand_frame = ttk.Frame(head_left)
    brand_frame.pack(side=LEFT, fill=Y)

    ttk.Label(
        brand_frame,
        text="◆",
        font=("Microsoft YaHei UI", 28, "bold"),
        foreground=COLOR_ACCENT,
    ).pack(anchor="w")

    # 标题区
    title_frame = ttk.Frame(head_left, padding=(10, 0, 0, 0))
    title_frame.pack(side=LEFT, fill=Y)

    ttk.Label(
        title_frame,
        text="苏州大学导师信息检索",
        font=FONT_DISPLAY,
        foreground=COLOR_TEXT_PRIMARY,
    ).pack(anchor="w")

    ttk.Label(
        title_frame,
        text="支持姓名 / 研究方向 / 论文关键词检索，含基础与优化模式对比",
        font=FONT_BODY,
        foreground=COLOR_TEXT_SECONDARY,
    ).pack(anchor="w", pady=(2, 0))

    # 右侧：主题选择
    head_right = ttk.Frame(head)
    head_right.pack(side="right", anchor="center")

    ttk.Label(
        head_right,
        text="🎨 主题",
        font=FONT_SMALL,
        foreground=COLOR_MUTED,
    ).pack(side=LEFT, anchor="center", padx=(0, 6))

    theme_names = sorted(style.theme_names())
    theme_var = tk.StringVar(value=style.theme_use())
    theme_box = ttk.Combobox(
        head_right,
        values=theme_names,
        textvariable=theme_var,
        width=14,
        state="readonly",
        font=FONT_SMALL,
    )
    theme_box.pack(side=LEFT, anchor="center")

    # 头部下分割线
    _divider(root_frame, pady=(0, 12))

    # ══════════════════════════════════════════════
    # 主内容区（侧边栏 + 结果区）
    # ══════════════════════════════════════════════
    content = ttk.Frame(root_frame)
    content.pack(fill=BOTH, expand=True)

    content.columnconfigure(0, minsize=SIDEBAR_W, weight=0)
    content.columnconfigure(1, weight=1)
    content.rowconfigure(0, weight=1)

    # ── 侧边栏 ──────────────────────────────────────
    sidebar_outer = ttk.Frame(content, padding=(0, 0, 16, 0))
    sidebar_outer.grid(row=0, column=0, sticky="nsew")

    # 侧边栏内部：可滚动
    sidebar = ttk.Frame(sidebar_outer)
    sidebar.pack(fill=BOTH, expand=True)

    # 查询框 - 增强视觉
    _section_label(sidebar, "关键词检索", "🔍")
    query_entry = ttk.Entry(sidebar, font=("Microsoft YaHei UI", 12), width=22)
    query_entry.pack(fill=X, pady=(0, 4))
    ttk.Label(
        sidebar,
        text="按 Enter 快速搜索",
        font=("Microsoft YaHei UI", 8),
        foreground="#9ca3af",
    ).pack(anchor="w", pady=(0, 8))

    _divider(sidebar, pady=(6, 6))

    # 过滤器
    _section_label(sidebar, "精细过滤", "⚙")
    name_entry = _labeled_entry(sidebar, "姓名", "👤")
    research_entry = _labeled_entry(sidebar, "研究方向", "📚")
    paper_entry = _labeled_entry(sidebar, "论文关键词", "📄")

    _divider(sidebar, pady=(6, 6))

    # 检索模式
    _section_label(sidebar, "检索模式", "🔧")
    mode_inner = ttk.Frame(sidebar)
    mode_inner.pack(fill=X, pady=(2, 6))
    allow_relax_var = tk.BooleanVar(value=True)
    enable_fuzzy_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        mode_inner,
        text="启用放宽检索（优化）",
        variable=allow_relax_var,
        bootstyle="round-toggle",
    ).pack(anchor="w", pady=(0, 6))
    ttk.Checkbutton(
        mode_inner,
        text="启用模糊检索（优化）",
        variable=enable_fuzzy_var,
        bootstyle="round-toggle",
    ).pack(anchor="w")

    # 模糊阈值行
    fuzzy_row = ttk.Frame(sidebar)
    fuzzy_row.pack(fill=X, pady=(10, 0))
    ttk.Label(fuzzy_row, text="模糊阈值", font=FONT_SMALL, foreground=COLOR_MUTED).pack(
        side=LEFT, anchor="center"
    )
    fuzzy_threshold_var = tk.IntVar(value=70)
    tk.Spinbox(
        fuzzy_row,
        from_=0,
        to=100,
        textvariable=fuzzy_threshold_var,
        width=5,
        font=FONT_SMALL,
        relief="flat",
        bd=1,
    ).pack(side=LEFT, padx=(6, 4), anchor="center")
    ttk.Label(
        fuzzy_row,
        text="(0-100)",
        font=("Microsoft YaHei UI", 8),
        foreground="#9ca3af",
    ).pack(side=LEFT, anchor="center")

    _divider(sidebar, pady=(10, 6))

    # 结果配置
    _section_label(sidebar, "结果配置", "📊")
    topk_row = ttk.Frame(sidebar)
    topk_row.pack(fill=X, pady=(2, 6))
    ttk.Label(topk_row, text="Top-K", font=FONT_SMALL, foreground=COLOR_MUTED).pack(
        side=LEFT, anchor="center"
    )
    topk_var = tk.IntVar(value=8)
    tk.Spinbox(
        topk_row,
        from_=3,
        to=20,
        textvariable=topk_var,
        width=5,
        font=FONT_SMALL,
        relief="flat",
        bd=1,
    ).pack(side=LEFT, padx=(6, 0), anchor="center")

    sort_row = ttk.Frame(sidebar)
    sort_row.pack(fill=X, pady=(6, 4))
    ttk.Label(sort_row, text="排序", font=FONT_SMALL, foreground=COLOR_MUTED).pack(
        side=LEFT, anchor="center"
    )
    sort_var = tk.StringVar(value="相关度")
    sort_box = ttk.Combobox(
        sort_row,
        values=["相关度", "姓名", "学院"],
        textvariable=sort_var,
        state="readonly",
        width=9,
        font=FONT_SMALL,
    )
    sort_box.pack(side=LEFT, padx=(6, 0), anchor="center")

    _divider(sidebar, pady=(10, 6))

    # 快捷查询 - 现代化标签按钮
    _section_label(sidebar, "快捷查询", "⚡")
    quick_queries = [
        ("周国栋", "👤"),
        ("NLP", "🧠"),
        ("ML", "📊"),
        ("events extraction", "📄"),
        ("GNN", "🕸"),
        ("computer vision", "🖼"),
        ("recommendation system", "⭐"),
        ("wireless communication", "📡"),
        ("自然语言处理", "🇨🇳"),
    ]

    def apply_quick_query(text: str):
        query_entry.delete(0, tk.END)
        query_entry.insert(0, text)
        on_search()

    quick_frame = ttk.Frame(sidebar)
    quick_frame.pack(fill=X, pady=(2, 8))
    for q, icon in quick_queries:
        btn = ttk.Button(
            quick_frame,
            text=f"{icon} {q}",
            command=lambda text=q: apply_quick_query(text),
            bootstyle="secondary-outline",
        )
        btn.pack(fill=X, pady=(0, 4), ipady=2)

    _divider(sidebar, pady=(0, 10))

    # 操作按钮 - 增强视觉层次
    btn_frame = ttk.Frame(sidebar)
    btn_frame.pack(fill=X)

    search_btn = _styled_button(
        btn_frame,
        "搜索",
        lambda: on_search(),
        bootstyle="primary",
        icon="🔍",
    )
    search_btn.pack(fill=X, ipady=6, pady=(0, 6))

    compare_btn = _styled_button(
        btn_frame,
        "基础 vs 优化 对比",
        lambda: on_compare(),
        bootstyle="info-outline",
        icon="⚖",
    )
    compare_btn.pack(fill=X, ipady=4, pady=(0, 6))

    clear_btn = _styled_button(
        btn_frame,
        "清空",
        lambda: on_clear(),
        bootstyle="secondary-outline",
        icon="✕",
    )
    clear_btn.pack(fill=X, ipady=4)

    # ── 结果主区 ─────────────────────────────────────
    main_area = ttk.Frame(content)
    main_area.grid(row=0, column=1, sticky="nsew")
    main_area.rowconfigure(1, weight=1)
    main_area.columnconfigure(0, weight=1)

    # 结果头部（标题 + 状态栏）- 现代化
    results_head = ttk.Frame(main_area)
    results_head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
    results_head.columnconfigure(1, weight=1)

    # 结果标题带徽章
    results_title_frame = ttk.Frame(results_head)
    results_title_frame.grid(row=0, column=0, sticky="w")

    ttk.Label(
        results_title_frame,
        text="📋 检索结果",
        font=FONT_HEADING,
        foreground=COLOR_TEXT_PRIMARY,
    ).pack(side=LEFT)

    status_var = tk.StringVar(value="请在左侧输入关键词后点击「搜索」")
    status_label = ttk.Label(
        results_head,
        textvariable=status_var,
        font=FONT_CAPTION,
        foreground=COLOR_TEXT_SECONDARY,
    )
    status_label.grid(row=0, column=1, sticky="w", padx=(12, 0))

    # 结果滚动区
    results_container = ttk.Frame(main_area)
    results_container.grid(row=1, column=0, sticky="nsew")
    results_container.rowconfigure(0, weight=1)
    results_container.columnconfigure(0, weight=1)

    canvas = tk.Canvas(results_container, highlightthickness=0)
    scrollbar = ttk.Scrollbar(results_container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)

    scroll_frame = ttk.Frame(canvas)
    scroll_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

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

    # ── 卡片渲染辅助 - 增强版 ─────────────────────────

    def _clear_cards():
        for child in scroll_frame.winfo_children():
            child.destroy()

    def _add_field(parent, label, value):
        if not value:
            return
        row = ttk.Frame(parent)
        row.pack(fill=X, pady=(6, 0))
        ttk.Label(
            row,
            text=label,
            font=FONT_CAPTION,
            foreground=COLOR_TEXT_SECONDARY,
            width=8,
            anchor="e",
        ).pack(side=LEFT, anchor="n", padx=(0, 10))
        ttk.Label(
            row,
            text=value,
            font=FONT_BODY,
            wraplength=720,
            justify="left",
            foreground=COLOR_TEXT_PRIMARY,
        ).pack(side=LEFT, fill=X, expand=True)

    _CCF_BADGE_STYLE = {
        "A": "inverse-danger",
        "B": "inverse-warning",
        "C": "inverse-info"
    }

    def _add_chip_row(parent, label, chips, bootstyle="inverse-primary"):
        if not chips:
            return
        row = ttk.Frame(parent)
        row.pack(fill=X, pady=(6, 0))
        ttk.Label(
            row,
            text=label,
            font=FONT_CAPTION,
            foreground=COLOR_TEXT_SECONDARY,
            width=8,
            anchor="e",
        ).pack(side=LEFT, anchor="n", padx=(0, 10))
        chip_wrap = ttk.Frame(row)
        chip_wrap.pack(side=LEFT, fill=X, expand=True)
        for chip in chips:
            ttk.Label(
                chip_wrap,
                text=f"  {chip}  ",
                font=FONT_BADGE,
                bootstyle=bootstyle,
            ).pack(side=LEFT, padx=(0, 6), pady=3)

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
            font=FONT_CAPTION,
            foreground=COLOR_TEXT_SECONDARY,
            width=8,
            anchor="e",
        ).pack(side=LEFT, anchor="n", padx=(0, 10))
        list_frame = ttk.Frame(row)
        list_frame.pack(side=LEFT, fill=X, expand=True)

        for idx, paper in enumerate(paper_items[:10], start=1):
            item = ttk.Frame(list_frame)
            item.pack(fill=X, pady=(0, 5))

            left = ttk.Frame(item)
            left.pack(side=LEFT, anchor="n", padx=(0, 8))
            ttk.Label(
                left,
                text=f"{idx:02d}",
                font=FONT_MONO,
                foreground=COLOR_TEXT_SECONDARY,
                width=3,
            ).pack(side=LEFT)
            if paper.ccf_rank:
                ttk.Label(
                    left,
                    text=f" CCF-{paper.ccf_rank} ",
                    font=FONT_BADGE,
                    bootstyle=_CCF_BADGE_STYLE.get(paper.ccf_rank, "secondary"),
                ).pack(side=LEFT, padx=(4, 0))

            body = ttk.Frame(item)
            body.pack(side=LEFT, fill=X, expand=True)
            ttk.Label(
                body,
                text=paper.title,
                font=FONT_BODY,
                wraplength=660,
                justify="left",
                foreground=COLOR_TEXT_PRIMARY,
            ).pack(anchor="w")
            meta_parts = [x for x in [paper.venue, paper.year] if x]
            if meta_parts:
                ttk.Label(
                    body,
                    text=" · ".join(meta_parts),
                    font=FONT_CAPTION,
                    foreground=COLOR_TEXT_SECONDARY,
                ).pack(anchor="w", pady=(2, 0))

    # ── 渲染完整结果列表 - 增强版 ─────────────────────

    def _render_results(results):
        _clear_cards()

        if not results:
            # 空状态 - 现代化设计
            empty_wrap = ttk.Frame(scroll_frame)
            empty_wrap.pack(expand=True, fill=BOTH, pady=80)

            ttk.Label(
                empty_wrap,
                text="🔍",
                font=("Microsoft YaHei UI", 48),
            ).pack()

            ttk.Label(
                empty_wrap,
                text="未找到匹配结果",
                font=("Microsoft YaHei UI", 16, "bold"),
                foreground=COLOR_TEXT_SECONDARY,
            ).pack(pady=(16, 8))

            ttk.Label(
                empty_wrap,
                text="可尝试：放宽检索条件 · 增大 Top-K · 缩小模糊阈值 · 更换关键词",
                font=FONT_BODY,
                foreground=COLOR_MUTED,
            ).pack()
            return

        for i, result in enumerate(results, start=1):
            view = build_display(result, i, last_query["text"])

            # 卡片外框 - 增强阴影和边框感
            card = ttk.Frame(scroll_frame, padding=(16, 14, 16, 14))
            card.pack(fill=X, pady=(0, 10), padx=(0, 6))
            card.configure(bootstyle="light")

            # 标题行 - 增强层次
            title_row = ttk.Frame(card)
            title_row.pack(fill=X)

            # 序号徽章 - 更大更醒目
            ttk.Label(
                title_row,
                text=f"  {i}  ",
                font=("Microsoft YaHei UI", 10, "bold"),
                bootstyle="inverse-primary",
            ).pack(side=LEFT, anchor="center", padx=(0, 10))

            # 导师姓名 - 更大更粗
            ttk.Label(
                title_row,
                text=view.name,
                font=("Microsoft YaHei UI", 14, "bold"),
                foreground=COLOR_TEXT_PRIMARY,
            ).pack(side=LEFT, anchor="center")

            # 院系 · 职称
            meta = " · ".join([t for t in [view.department, view.career] if t])
            if meta:
                ttk.Label(
                    title_row,
                    text=meta,
                    font=FONT_BODY,
                    foreground=COLOR_TEXT_SECONDARY,
                ).pack(side=LEFT, anchor="center", padx=(12, 0))

            # 右侧：主页链接 + 相关度
            if view.url:
                ttk.Button(
                    title_row,
                    text="主页 ↗",
                    bootstyle="link",
                    command=lambda u=view.url: webbrowser.open(u),
                    cursor="hand2",
                ).pack(side="right", anchor="center", padx=(6, 0))

            ttk.Label(
                title_row,
                text=f"  {view.score:.2f}  ",
                font=("Microsoft YaHei UI", 10, "bold"),
                bootstyle="inverse-info",
            ).pack(side="right", anchor="center")

            # 分割线
            ttk.Separator(card, orient="horizontal").pack(fill=X, pady=(12, 8))

            # 内容字段
            _add_research_section(card, view)
            if view.profile_keywords:
                _add_chip_row(card, "关键词", view.profile_keywords, "info")
            if view.intro:
                _add_field(card, "简介", view.intro)
            if view.paper_items:
                _add_papers_section(card, view.paper_items)
            elif view.papers:
                _add_field(card, "论文", view.papers)
            if view.snippet:
                _add_field(card, "片段", view.snippet)
            if view.keywords:
                _add_chip_row(card, "命中词", view.keywords, "inverse-success")

    # ── 事件处理 ──────────────────────────────────────

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
                idf=idf,
            )
        elif name_filter:
            results = search(name_filter, docs, teachers, inverted, doc_norms, idf=idf)
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
            f"共 {len(results)} 条结果  ·  耗时 {elapsed_ms:.2f} ms  ·  {mode_text}  ·  排序: {sort_var.get()}"
        )
        _render_results(results)

    def on_clear():
        query_entry.delete(0, tk.END)
        name_entry.delete(0, tk.END)
        research_entry.delete(0, tk.END)
        paper_entry.delete(0, tk.END)
        status_var.set("查询条件已清空，请重新输入。")
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

    # ══════════════════════════════════════════════
    # 对比窗口 - 增强版
    # ══════════════════════════════════════════════

    def _make_scroll_pane(parent):
        wrapper = ttk.Frame(parent)
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)
        pane_canvas = tk.Canvas(wrapper, highlightthickness=0)
        bar = ttk.Scrollbar(wrapper, orient="vertical", command=pane_canvas.yview)
        pane_canvas.configure(yscrollcommand=bar.set)
        inner = ttk.Frame(pane_canvas)
        window = pane_canvas.create_window((0, 0), window=inner, anchor="nw")
        pane_canvas.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
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
                font=("Microsoft YaHei UI", 12),
                foreground=COLOR_TEXT_SECONDARY,
            ).pack(anchor="w", pady=12, padx=10)
            return
        for i, result in enumerate(results, start=1):
            view = build_display(result, i, query)
            is_new = view.name in highlight_names

            card = ttk.Frame(parent, padding=(14, 12, 14, 12))
            card.pack(fill=X, pady=(0, 8), padx=6)
            card.configure(bootstyle="success" if is_new else "light")

            head = ttk.Frame(card)
            head.pack(fill=X)

            ttk.Label(
                head,
                text=f"  {i}  ",
                font=("Microsoft YaHei UI", 10, "bold"),
                bootstyle="inverse-primary",
            ).pack(side=LEFT, anchor="center", padx=(0, 8))
            ttk.Label(
                head,
                text=view.name,
                font=("Microsoft YaHei UI", 12, "bold"),
                foreground=COLOR_TEXT_PRIMARY,
            ).pack(side=LEFT, anchor="center")
            ttk.Label(
                head,
                text=f"  {view.score:.2f}  ",
                font=("Microsoft YaHei UI", 10, "bold"),
                bootstyle="inverse-info",
            ).pack(side="right", anchor="center")
            if is_new:
                ttk.Label(
                    head,
                    text=" 优化新增 ",
                    font=FONT_BADGE,
                    bootstyle="inverse-success",
                ).pack(side="right", anchor="center", padx=6)

            if view.department:
                ttk.Label(
                    card,
                    text=view.department,
                    font=FONT_SMALL,
                    foreground=COLOR_TEXT_SECONDARY,
                ).pack(anchor="w", pady=(6, 0))
            if view.research:
                ttk.Label(
                    card,
                    text=view.research,
                    font=FONT_SMALL,
                    wraplength=400,
                    justify="left",
                    foreground=COLOR_TEXT_PRIMARY,
                ).pack(anchor="w", pady=(4, 0))

    def on_compare():
        query = _current_query()
        if not query:
            status_var.set("⚠ 请输入查询词后再进行对比。")
            return

        top_k = max(3, min(20, int(topk_var.get())))

        t0 = time.perf_counter()
        base = search(
            query, docs, teachers, inverted, doc_norms,
            top_k=top_k, allow_relax=False, enable_fuzzy=False,
            idf=idf,
        )
        base_ms = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        opt = search(
            query, docs, teachers, inverted, doc_norms,
            top_k=top_k,
            allow_relax=True,
            enable_fuzzy=True,
            fuzzy_threshold=max(0, min(100, int(fuzzy_threshold_var.get()))),
            idf=idf,
        )
        opt_ms = (time.perf_counter() - t1) * 1000.0

        base_names = {r.teacher.name for r in base}
        new_names = {r.teacher.name for r in opt if r.teacher.name not in base_names}

        win = ttk.Toplevel(title=f"对比检索：{query}")
        win.geometry("1100x750")
        win.minsize(800, 550)

        outer = ttk.Frame(win, padding=(20, 16, 20, 16))
        outer.pack(fill=BOTH, expand=True)
        outer.rowconfigure(2, weight=1)
        outer.columnconfigure(0, weight=1)

        # 对比标题 - 增强
        ttk.Label(
            outer,
            text=f"⚖ 对比查询：{query}",
            font=("Microsoft YaHei UI", 16, "bold"),
            foreground=COLOR_TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text=f"优化模式共新增召回 {len(new_names)} 位导师",
            font=FONT_BODY,
            foreground=COLOR_TEXT_SECONDARY,
        ).grid(row=1, column=0, sticky="w", pady=(4, 10))

        ttk.Separator(outer, orient="horizontal").grid(row=2, column=0, sticky="ew", pady=(0, 12))

        # 双列
        columns = ttk.Frame(outer)
        columns.grid(row=3, column=0, sticky="nsew")
        outer.rowconfigure(3, weight=1)
        columns.columnconfigure(0, weight=1, uniform="col")
        columns.columnconfigure(1, weight=1, uniform="col")
        columns.rowconfigure(1, weight=1)

        # 列标题 - 增强视觉
        base_title_frame = ttk.Frame(columns, padding=(10, 8, 10, 8))
        base_title_frame.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        base_title_frame.configure(bootstyle="secondary")
        ttk.Label(
            base_title_frame,
            text="📊 基础模式",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side=LEFT)
        ttk.Label(
            base_title_frame,
            text=f"{len(base)} 条  ·  {base_ms:.1f} ms",
            font=FONT_SMALL,
            foreground=COLOR_MUTED,
        ).pack(side=LEFT, padx=(10, 0))

        opt_title_frame = ttk.Frame(columns, padding=(10, 8, 10, 8))
        opt_title_frame.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        opt_title_frame.configure(bootstyle="success")
        ttk.Label(
            opt_title_frame,
            text="🚀 优化模式",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side=LEFT)
        ttk.Label(
            opt_title_frame,
            text=f"{len(opt)} 条  ·  {opt_ms:.1f} ms",
            font=FONT_SMALL,
            foreground=COLOR_MUTED,
        ).pack(side=LEFT, padx=(10, 0))

        left_wrap, left_inner = _make_scroll_pane(columns)
        left_wrap.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(8, 0))
        right_wrap, right_inner = _make_scroll_pane(columns)
        right_wrap.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(8, 0))

        _render_compact(left_inner, base, query, set())
        _render_compact(right_inner, opt, query, new_names)

    # ── 主题切换 ──────────────────────────────────────

    def on_theme_change(_event=None):
        new_theme = theme_var.get()
        style.theme_use(new_theme)
        canvas.configure(background=style.lookup("TFrame", "background"))

    theme_box.bind("<<ComboboxSelected>>", on_theme_change)
    canvas.configure(background=style.lookup("TFrame", "background"))

    # ── 键盘绑定 ──────────────────────────────────────
    query_entry.bind("<Return>", lambda e: on_search())
    name_entry.bind("<Return>", lambda e: on_search())
    research_entry.bind("<Return>", lambda e: on_search())
    paper_entry.bind("<Return>", lambda e: on_search())

    root.mainloop()


if __name__ == "__main__":
    main()
