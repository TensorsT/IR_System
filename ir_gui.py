import tkinter as tk

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
    root.title("Soochow IR - Basic")
    root.geometry("980x720")

    style = ttk.Style()

    container = ttk.Frame(root, padding=18)
    container.pack(fill=BOTH, expand=True)

    header_frame = ttk.Frame(container)
    header_frame.pack(fill=X)

    header = ttk.Label(
        header_frame,
        text="Simple Vertical IR Search",
        font=("Segoe UI", 18, "bold"),
    )
    header.pack(side=LEFT)

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
        text="Examples: NLP | Zhou Guodong | paper: information extraction",
        font=("Segoe UI", 10),
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

    query_label = ttk.Label(sidebar, text="Free Query", font=("Segoe UI", 11, "bold"))
    query_label.pack(anchor="w")
    query_entry = ttk.Entry(sidebar, font=("Segoe UI", 11), width=24)
    query_entry.pack(pady=(6, 12), fill=X)

    name_label = ttk.Label(sidebar, text="Name Filter", font=("Segoe UI", 11, "bold"))
    name_label.pack(anchor="w")
    name_entry = ttk.Entry(sidebar, font=("Segoe UI", 11), width=24)
    name_entry.pack(pady=(6, 12), fill=X)

    research_label = ttk.Label(sidebar, text="Research Filter", font=("Segoe UI", 11, "bold"))
    research_label.pack(anchor="w")
    research_entry = ttk.Entry(sidebar, font=("Segoe UI", 11), width=24)
    research_entry.pack(pady=(6, 12), fill=X)

    paper_label = ttk.Label(sidebar, text="Paper Filter", font=("Segoe UI", 11, "bold"))
    paper_label.pack(anchor="w")
    paper_entry = ttk.Entry(sidebar, font=("Segoe UI", 11), width=24)
    paper_entry.pack(pady=(6, 12), fill=X)

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

    results_label = ttk.Label(main_area, text="Results", font=("Segoe UI", 12, "bold"))
    results_label.pack(anchor="w")

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
                text="No results found.",
                font=("Segoe UI", 11),
                bootstyle="secondary",
            )
            empty_label.pack(anchor="w", pady=10)
            return

        for i, result in enumerate(results, start=1):
            card = ttk.Frame(scroll_frame, padding=12, bootstyle="light")
            card.pack(fill=X, pady=6)

            title = ttk.Label(
                card,
                text=f"[{i}] {result.teacher.name} | {result.teacher.department} | {result.teacher.career}",
                font=("Segoe UI", 11, "bold"),
            )
            title.pack(anchor="w")

            if result.teacher.research_direction:
                ttk.Label(
                    card,
                    text=f"Research: {_mask_private(result.teacher.research_direction)}",
                    font=("Segoe UI", 10),
                ).pack(anchor="w", pady=(6, 0))

            if result.teacher.personal_intro:
                intro = _mask_private(result.teacher.personal_intro.replace("\n", " ").strip())
                ttk.Label(
                    card,
                    text=f"Intro: {intro[:220]}",
                    font=("Segoe UI", 10),
                    wraplength=760,
                    justify="left",
                ).pack(anchor="w", pady=(6, 0))

            if result.teacher.papers_text:
                papers = _mask_private(result.teacher.papers_text.replace("\n", " ").strip())
                ttk.Label(
                    card,
                    text=f"Papers: {papers[:220]}",
                    font=("Segoe UI", 10),
                    wraplength=760,
                    justify="left",
                ).pack(anchor="w", pady=(6, 0))

            if result.snippet:
                ttk.Label(
                    card,
                    text=f"Snippet: {_mask_private(result.snippet)}",
                    font=("Segoe UI", 10),
                    wraplength=760,
                    justify="left",
                ).pack(anchor="w", pady=(6, 0))

            if result.teacher.url:
                ttk.Label(
                    card,
                    text=f"URL: {result.teacher.url}",
                    font=("Segoe UI", 10),
                ).pack(anchor="w", pady=(6, 0))

    def on_search():
        free_query = query_entry.get().strip()
        name_filter = name_entry.get().strip()
        research_filter = research_entry.get().strip()
        paper_filter = paper_entry.get().strip()

        if free_query:
            results = search(free_query, docs, teachers, inverted, doc_norms)
        elif name_filter:
            results = search(name_filter, docs, teachers, inverted, doc_norms)
        elif research_filter or paper_filter:
            combined = " ".join([research_filter, paper_filter]).strip()
            results = search(combined, docs, teachers, inverted, doc_norms)
        else:
            results = []

        results = _filter_results(results, name_filter, research_filter, paper_filter)
        _render_results(results)

    def on_clear():
        query_entry.delete(0, tk.END)
        name_entry.delete(0, tk.END)
        research_entry.delete(0, tk.END)
        paper_entry.delete(0, tk.END)
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
