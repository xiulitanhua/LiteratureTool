"""
文献综合处理工具 v3.3 —— 一站式 DOI 获取 + PDF 下载
浅色科研数据表格中心界面
"""

import os, sys, re, json, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime
import pandas as pd

from doi_fetcher import find_doi, extract_year, normalize_text, DEFAULT_THRESHOLD
from pdf_downloader import download_all, build_filename, clean_filename
from updater import check_for_update
from report import generate_report

# 当前版本号（格式: yyyyMMdd-HHmm，与 GitHub version.json 对比）
CURRENT_VERSION = "20260622-0000"

# ═══════════════ 主题色板 (浅色科研软件风格) ═══════════════

C = {
    "bg_dark":    "#f6f8fb",   # 页面背景
    "bg_sidebar": "#ffffff",   # 导航/工具背景
    "bg_card":    "#ffffff",   # 面板
    "bg_input":   "#f8fafc",   # 输入框
    "bg_hover":   "#edf2f7",   # 悬停
    "accent":     "#2563eb",   # 科研蓝
    "accent2":    "#0f766e",   # 青绿色辅助
    "success":    "#15803d",   # 绿色
    "danger":     "#dc2626",   # 红色
    "warning":    "#b45309",   # 黄色
    "text":       "#172033",   # 主文字
    "text_dim":   "#64748b",   # 次文字
    "border":     "#d9e2ec",   # 边框
    "muted":      "#eef3f8",
    "white":      "#ffffff",
}

FONT = "Microsoft YaHei UI"
MONO = "Consolas"

# ═══════════════ 列检测 ═══════════════

def normalize_header(text):
    return normalize_text(text).replace(" ", "")

def detect_columns(df):
    headers = list(df.columns)
    result = {"title_col": None, "year_col": None, "author_col": None,
              "journal_col": None, "doi_col": None, "headers": headers}
    km = {
        "title_col":   ["title", "题名", "标题", "篇名", "论文题目", "文献题名"],
        "year_col":    ["year", "年份", "发表年", "出版年", "发表日期", "出版日期", "日期"],
        "author_col":  ["author", "authors", "作者"],
        "journal_col": ["journal", "source", "publication", "期刊", "刊名", "来源"],
        "doi_col":     ["doi"],
    }
    for key, kws in km.items():
        for kw in kws:
            nk = normalize_header(kw)
            for i, h in enumerate(headers):
                if nk in normalize_header(str(h)):
                    result[key] = i
                    break
            if result[key] is not None:
                break
    return result

# ═══════════════ 主应用 ═══════════════

class LiteratureApp:
    def __init__(self, root):
        self.root = root
        self.root.title("文献综合处理工具")
        self.root.geometry("1280x760")
        self.root.minsize(1080, 640)
        self.root.configure(bg=C["bg_dark"])

        self.input_path = tk.StringVar()
        self.output_dir = tk.StringVar(value=os.getcwd())
        self.threshold = tk.DoubleVar(value=0.8)
        self.concurrency = 5  # 并发线程数
        self.running = False
        self.stop_requested = False
        self.df = None
        self.detected = None

        self.stats_doi_total = tk.StringVar(value="—")
        self.stats_doi_found = tk.StringVar(value="—")
        self.stats_doi_failed = tk.StringVar(value="—")
        self.stats_pdf_done = tk.StringVar(value="—")
        self.stats_stage = tk.StringVar(value="等待开始")
        self.detail_vars = {}

        self.sidebar_btns = []
        self.current_page = tk.StringVar(value="overview")

        self._build_ui()
        self._log("文献综合处理工具 v3.3", "header")
        self._log("Excel → DOI 获取 → PDF 下载，当前界面以文献表格核对为中心。")

        # 后台检查更新
        check_for_update(self.root, CURRENT_VERSION, log_callback=self._log)

    # ═══════════════ UI 构建 ═══════════════

    def _build_ui(self):
        self._configure_styles()

        outer = tk.Frame(self.root, bg=C["bg_dark"])
        outer.pack(fill=tk.BOTH, expand=True)

        self.main_area = tk.Frame(outer, bg=C["bg_dark"])
        self.main_area.pack(fill=tk.BOTH, expand=True)

        self.page_overview = tk.Frame(self.main_area, bg=C["bg_dark"])
        self._build_overview_page()

        self.page_settings = tk.Frame(self.main_area, bg=C["bg_dark"])
        self._build_settings_page()

        self._switch_page("overview")

    def _style_sidebar_btn(self, btn, active):
        if active:
            btn.configure(bg=C["accent"], fg=C["white"],
                          activebackground=C["accent"], activeforeground=C["white"])
        else:
            btn.configure(bg=C["bg_sidebar"], fg=C["text_dim"],
                          activebackground=C["bg_hover"], activeforeground=C["text"])

    def _switch_page(self, key):
        for btn, bk in self.sidebar_btns:
            self._style_sidebar_btn(btn, bk == key)
        self.current_page.set(key)
        if key == "overview":
            self.page_settings.pack_forget()
            self.page_overview.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)
        elif key == "settings":
            self.page_overview.pack_forget()
            self.page_settings.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

    # ═══════════════ 总览页 ═══════════════

    def _build_overview_page(self):
        p = self.page_overview

        header = tk.Frame(p, bg=C["bg_dark"])
        header.pack(fill=tk.X, padx=18, pady=(16, 10))
        tk.Label(header, text="文献数据核对台", font=(FONT, 18, "bold"),
                 fg=C["text"], bg=C["bg_dark"]).pack(side=tk.LEFT)
        tk.Label(header, text="Excel 文献表 → DOI 匹配 → PDF 下载",
                 font=(FONT, 10), fg=C["text_dim"], bg=C["bg_dark"]).pack(side=tk.LEFT, padx=(14, 0), pady=(5, 0))

        nav = tk.Frame(header, bg=C["bg_dark"])
        nav.pack(side=tk.RIGHT)
        self._make_button(nav, "总览", lambda: self._switch_page("overview"), kind="ghost").pack(side=tk.LEFT, padx=(0, 6))
        self._make_button(nav, "设置", lambda: self._switch_page("settings"), kind="ghost").pack(side=tk.LEFT)

        toolbar = tk.Frame(p, bg=C["bg_card"], highlightbackground=C["border"], highlightthickness=1)
        toolbar.pack(fill=tk.X, padx=18, pady=(0, 10))
        toolbar_inner = tk.Frame(toolbar, bg=C["bg_card"])
        toolbar_inner.pack(fill=tk.X, padx=12, pady=10)

        self.file_label = tk.Label(toolbar_inner, text="未选择 Excel 文件", font=(FONT, 9),
                                   fg=C["text_dim"], bg=C["bg_card"], anchor=tk.W)
        self.file_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.btn_detect = self._make_button(toolbar_inner, "检测字段", lambda: self._detect_columns(True), kind="secondary")
        self.btn_detect.pack(side=tk.RIGHT, padx=(6, 0))
        self.btn_stop = self._make_button(toolbar_inner, "停止", self._stop, kind="danger")
        self.btn_stop.config(state=tk.DISABLED)
        self.btn_stop.pack(side=tk.RIGHT, padx=(6, 0))
        self.btn_open = self._make_button(toolbar_inner, "打开输出目录", self._open_output_dir, kind="secondary")
        self.btn_open.pack(side=tk.RIGHT, padx=(6, 0))
        self.btn_download = self._make_button(toolbar_inner, "下载 PDF", self._start_download_pdf, kind="secondary")
        self.btn_download.pack(side=tk.RIGHT, padx=(6, 0))
        self.btn_fetch_doi = self._make_button(toolbar_inner, "获取 DOI", self._start_fetch_doi, kind="secondary")
        self.btn_fetch_doi.pack(side=tk.RIGHT, padx=(6, 0))
        self.btn_all = self._make_button(toolbar_inner, "一键处理", self._start_full_pipeline, kind="primary")
        self.btn_all.pack(side=tk.RIGHT, padx=(6, 0))
        self._make_button(toolbar_inner, "选择 Excel", self._select_input, kind="primary").pack(side=tk.RIGHT, padx=(6, 0))

        path_bar = tk.Frame(p, bg=C["bg_dark"])
        path_bar.pack(fill=tk.X, padx=18, pady=(0, 10))
        path_bar.columnconfigure(1, weight=1)
        path_bar.columnconfigure(3, weight=1)
        tk.Label(path_bar, text="输入", font=(FONT, 9), fg=C["text_dim"], bg=C["bg_dark"]).grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        tk.Entry(path_bar, textvariable=self.input_path, font=(MONO, 9), bg=C["bg_input"], fg=C["text"],
                 insertbackground=C["text"], relief=tk.FLAT, highlightbackground=C["border"],
                 highlightthickness=1).grid(row=0, column=1, sticky=tk.EW, ipady=5)
        tk.Label(path_bar, text="输出", font=(FONT, 9), fg=C["text_dim"], bg=C["bg_dark"]).grid(row=0, column=2, sticky=tk.W, padx=(14, 8))
        tk.Entry(path_bar, textvariable=self.output_dir, font=(MONO, 9), bg=C["bg_input"], fg=C["text"],
                 insertbackground=C["text"], relief=tk.FLAT, highlightbackground=C["border"],
                 highlightthickness=1).grid(row=0, column=3, sticky=tk.EW, ipady=5)
        self._make_button(path_bar, "浏览", self._select_output_dir, kind="ghost").grid(row=0, column=4, padx=(8, 0))

        cards = tk.Frame(p, bg=C["bg_dark"])
        cards.pack(fill=tk.X, padx=18, pady=(0, 10))
        cards.columnconfigure((0, 1, 2, 3, 4), weight=1)
        self._stat_card(cards, "待处理文献", self.stats_doi_total, C["text"], 0)
        self._stat_card(cards, "已匹配 DOI", self.stats_doi_found, C["success"], 1)
        self._stat_card(cards, "DOI 未匹配", self.stats_doi_failed, C["danger"], 2)
        self._stat_card(cards, "PDF 已下载", self.stats_pdf_done, C["warning"], 3)
        self._stat_card(cards, "当前状态", self.stats_stage, C["accent"], 4)

        work = tk.Frame(p, bg=C["bg_dark"])
        work.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 10))
        work.columnconfigure(0, weight=1)
        work.rowconfigure(0, weight=1)

        table_panel = self._card_frame(work, "文献列表")
        table_panel.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 10))
        table_body = tk.Frame(table_panel, bg=C["bg_card"])
        table_body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        table_body.columnconfigure(0, weight=1)
        table_body.rowconfigure(0, weight=1)

        cols = ("idx", "title", "year", "author", "journal", "doi", "source", "match", "pdf")
        self.paper_table = ttk.Treeview(table_body, columns=cols, show="headings", selectmode="browse")
        headings = {
            "idx": "#", "title": "标题", "year": "年份", "author": "作者",
            "journal": "期刊", "doi": "DOI 状态", "source": "来源",
            "match": "匹配度", "pdf": "PDF 状态"
        }
        widths = {"idx": 44, "title": 330, "year": 70, "author": 120, "journal": 150,
                  "doi": 110, "source": 90, "match": 80, "pdf": 100}
        for col in cols:
            self.paper_table.heading(col, text=headings[col])
            self.paper_table.column(col, width=widths[col], minwidth=44, anchor=tk.W, stretch=(col == "title"))
        self.paper_table.tag_configure("even", background="#ffffff")
        self.paper_table.tag_configure("odd", background="#f8fafc")
        self.paper_table.tag_configure("matched", foreground=C["success"])
        self.paper_table.tag_configure("failed", foreground=C["danger"])
        self.paper_table.tag_configure("missing", foreground=C["text_dim"])
        self.paper_table.grid(row=0, column=0, sticky=tk.NSEW)
        ybar = ttk.Scrollbar(table_body, orient=tk.VERTICAL, command=self.paper_table.yview)
        ybar.grid(row=0, column=1, sticky=tk.NS)
        self.paper_table.configure(yscrollcommand=ybar.set)
        self.paper_table.bind("<<TreeviewSelect>>", self._on_table_select)

        side = tk.Frame(work, bg=C["bg_dark"], width=320)
        side.grid(row=0, column=1, sticky=tk.NS)
        side.grid_propagate(False)
        detail = self._card_frame(side, "选中文献详情")
        detail.pack(fill=tk.BOTH, expand=True)
        detail_body = tk.Frame(detail, bg=C["bg_card"])
        detail_body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))
        for key, label in [
            ("title", "原始标题"), ("doi", "DOI"), ("source", "来源"),
            ("status", "DOI 状态"), ("match", "匹配度"), ("pdf", "PDF"), ("path", "下载路径/链接")
        ]:
            tk.Label(detail_body, text=label, font=(FONT, 8), fg=C["text_dim"],
                     bg=C["bg_card"]).pack(anchor=tk.W, pady=(9, 2))
            var = tk.StringVar(value="—")
            self.detail_vars[key] = var
            tk.Label(detail_body, textvariable=var, font=(FONT, 9), fg=C["text"],
                     bg=C["bg_card"], anchor=tk.W, justify=tk.LEFT, wraplength=280).pack(anchor=tk.W, fill=tk.X)

        progress_panel = tk.Frame(p, bg=C["bg_card"], highlightbackground=C["border"], highlightthickness=1)
        progress_panel.pack(fill=tk.X, padx=18, pady=(0, 10))
        prog_inner = tk.Frame(progress_panel, bg=C["bg_card"])
        prog_inner.pack(fill=tk.X, padx=12, pady=9)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = tk.Canvas(prog_inner, height=6, bg=C["bg_input"],
                                       highlightthickness=0)
        self.progress_bar.pack(fill=tk.X)
        self._draw_progress(0)

        self.status_var = tk.StringVar(value="就绪 — 请选择 Excel 文件")
        tk.Label(prog_inner, textvariable=self.status_var, font=(FONT, 8),
                 fg=C["text_dim"], bg=C["bg_card"]).pack(anchor=tk.W, pady=(6, 0))

        lc = self._card_frame(p, "运行日志")
        lc.pack(fill=tk.X, padx=18, pady=(0, 16))
        self.log_area = scrolledtext.ScrolledText(lc, wrap=tk.WORD,
            font=(MONO, 9), bg=C["bg_input"], fg=C["text_dim"], height=5,
            insertbackground=C["text"], relief=tk.FLAT, padx=12, pady=8,
            highlightbackground=C["border"], highlightthickness=1)
        self.log_area.pack(fill=tk.X, padx=12, pady=(0, 12))
        self.log_area.tag_config("success", foreground=C["success"])
        self.log_area.tag_config("error", foreground=C["danger"])
        self.log_area.tag_config("info", foreground=C["accent"])
        self.log_area.tag_config("warn", foreground=C["warning"])
        self.log_area.tag_config("muted", foreground=C["text_dim"])
        self.log_area.tag_config("header", foreground=C["accent2"], font=(MONO, 9, "bold"))

    # ═══════════════ 设置页 ═══════════════

    def _build_settings_page(self):
        p = self.page_settings

        header = tk.Frame(p, bg=C["bg_dark"])
        header.pack(fill=tk.X, padx=18, pady=(16, 10))
        tk.Label(header, text="设置", font=(FONT, 18, "bold"),
                 fg=C["text"], bg=C["bg_dark"]).pack(side=tk.LEFT)
        self._make_button(header, "返回总览", lambda: self._switch_page("overview"), kind="primary").pack(side=tk.RIGHT)

        fc = self._card_frame(p, "⚙️ 匹配阈值")
        fc.pack(fill=tk.X, padx=18, pady=(0, 10))
        inner = tk.Frame(fc, bg=C["bg_card"])
        inner.pack(fill=tk.X, padx=16, pady=14)

        tk.Label(inner, text="DOI 标题匹配阈值", font=(FONT, 10, "bold"),
                 fg=C["text"], bg=C["bg_card"]).pack(anchor=tk.W)
        tk.Label(inner, text="只有标题匹配度 ≥ 此值的文献才会写入 DOI",
                 font=(FONT, 8), fg=C["text_dim"], bg=C["bg_card"]).pack(anchor=tk.W, pady=(2, 10))

        scale_row = tk.Frame(inner, bg=C["bg_card"])
        scale_row.pack(fill=tk.X)

        self.threshold_canvas = tk.Canvas(scale_row, height=40, bg=C["bg_card"], highlightthickness=0)
        self.threshold_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.threshold_label = tk.Label(scale_row, text="80%", font=(FONT, 16, "bold"),
                                        fg=C["accent"], bg=C["bg_card"], width=5)
        self.threshold_label.pack(side=tk.RIGHT, padx=(10, 0))

        # 滑块
        self._draw_threshold_slider()
        self.threshold_canvas.bind("<Button-1>", self._on_slider_click)
        self.threshold_canvas.bind("<B1-Motion>", self._on_slider_drag)

        # 说明
        fc2 = self._card_frame(p, "💡 使用说明")
        fc2.pack(fill=tk.X, padx=18)
        inner2 = tk.Frame(fc2, bg=C["bg_card"])
        inner2.pack(fill=tk.X, padx=16, pady=14)

        tips = [
            ("1", "选择包含文献标题的 Excel 文件"),
            ("2", "点击「一键全流程」自动获取 DOI 并下载 PDF"),
            ("3", "或分步操作：先获取 DOI，再下载 PDF"),
            ("📁", f"PDF 保存在: {self.output_dir.get()}\\Downloaded_PDFs"),
        ]
        for icon, tip in tips:
            row = tk.Frame(inner2, bg=C["bg_card"])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=icon, font=(FONT, 9), fg=C["accent"],
                     bg=C["bg_card"], width=3).pack(side=tk.LEFT)
            tk.Label(row, text=tip, font=(FONT, 9), fg=C["text_dim"],
                     bg=C["bg_card"]).pack(side=tk.LEFT)

    # ── UI 辅助方法 ──

    def _configure_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview",
                        background=C["white"],
                        fieldbackground=C["white"],
                        foreground=C["text"],
                        borderwidth=0,
                        rowheight=30,
                        font=(FONT, 9))
        style.configure("Treeview.Heading",
                        background=C["muted"],
                        foreground=C["text"],
                        relief=tk.FLAT,
                        font=(FONT, 9, "bold"))
        style.map("Treeview",
                  background=[("selected", "#dbeafe")],
                  foreground=[("selected", C["text"])])
        style.configure("Vertical.TScrollbar",
                        background=C["muted"],
                        troughcolor=C["bg_card"],
                        bordercolor=C["border"],
                        arrowcolor=C["text_dim"])

    def _make_button(self, parent, text, command, kind="secondary"):
        palettes = {
            "primary": (C["accent"], C["white"], "#1d4ed8"),
            "secondary": (C["bg_hover"], C["text"], C["muted"]),
            "ghost": (C["bg_card"], C["text_dim"], C["bg_hover"]),
            "danger": ("#fee2e2", C["danger"], "#fecaca"),
        }
        bg, fg, active = palettes.get(kind, palettes["secondary"])
        return tk.Button(parent, text=text, command=command, font=(FONT, 9),
                         bg=bg, fg=fg, activebackground=active, activeforeground=fg,
                         relief=tk.FLAT, cursor="hand2", padx=12, pady=6, borderwidth=0)

    def _dot(self, parent, color, size=7):
        c = tk.Canvas(parent, width=size, height=size, bg=C["bg_card"], highlightthickness=0)
        c.pack(side=tk.LEFT)
        c.create_oval(0, 0, size, size, fill=color, outline="")

    def _stat_card(self, parent, label, var, color, col):
        frame = tk.Frame(parent, bg=C["bg_card"], highlightbackground=C["border"],
                         highlightthickness=1, padx=14, pady=12)
        frame.grid(row=0, column=col, sticky=tk.EW, padx=(0 if col == 0 else 8, 0))
        tk.Label(frame, text=label, font=(FONT, 8), fg=C["text_dim"],
                 bg=C["bg_card"]).pack()
        tk.Label(frame, textvariable=var, font=(MONO, 15, "bold"),
                 fg=color, bg=C["bg_card"]).pack(pady=(2, 0))

    def _card_frame(self, parent, title):
        f = tk.Frame(parent, bg=C["bg_card"], highlightbackground=C["border"],
                     highlightthickness=1)
        hdr = tk.Frame(f, bg=C["bg_card"])
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text=title, font=(FONT, 9, "bold"), fg=C["text_dim"],
                 bg=C["bg_card"]).pack(anchor=tk.W, padx=14, pady=(10, 6))
        sep = tk.Frame(f, bg=C["border"], height=1)
        sep.pack(fill=tk.X)
        return f

    def _draw_progress(self, pct):
        self.progress_bar.delete("all")
        w = self.progress_bar.winfo_width()
        if w < 10: w = 600
        fill = int(w * pct / 100)
        if fill > 0:
            self.progress_bar.create_rectangle(0, 0, fill, 6, fill=C["accent"], outline="")

    def _resolve_column(self, key):
        if self.df is None:
            return None
        headers = list(self.df.columns)
        if self.detected and self.detected.get(key) is not None:
            idx = self.detected[key]
            if 0 <= idx < len(headers):
                return headers[idx]
        aliases = {
            "doi_col": ["DOI", "doi"],
            "title_col": ["TITLE", "Title", "标题", "题名", "篇名"],
            "year_col": ["YEAR", "Year", "年份", "出版年"],
            "author_col": ["AUTHOR", "Author", "Authors", "作者"],
            "journal_col": ["JOURNAL", "Journal", "Source", "期刊", "刊名"],
        }
        for name in aliases.get(key, []):
            if name in headers:
                return name
        return None

    def _doi_column_for_display(self):
        if self.df is None:
            return None
        if "DOI" in list(self.df.columns) and int(self.df["DOI"].notna().sum()) > 0:
            return "DOI"
        return self._resolve_column("doi_col") or ("DOI" if "DOI" in list(self.df.columns) else None)

    def _cell_text(self, row, col, default=""):
        if col is None:
            return default
        value = row.get(col, default)
        if pd.isna(value):
            return default
        return str(value).strip()

    def _short(self, text, n=72):
        text = str(text or "").replace("\n", " ").strip()
        return text if len(text) <= n else text[:n - 1] + "…"

    def _refresh_table(self, select_first=False):
        if not hasattr(self, "paper_table"):
            return
        for item in self.paper_table.get_children():
            self.paper_table.delete(item)
        if self.df is None:
            self._set_detail()
            return

        tcn = self._resolve_column("title_col")
        ycn = self._resolve_column("year_col")
        acn = self._resolve_column("author_col")
        jcn = self._resolve_column("journal_col")
        dcn = self._doi_column_for_display()
        source_col = "DOI来源" if "DOI来源" in list(self.df.columns) else None
        match_col = "匹配度" if "匹配度" in list(self.df.columns) else None
        pdf_col = "PDF链接" if "PDF链接" in list(self.df.columns) else None
        doi_status_col = "DOI状态" if "DOI状态" in list(self.df.columns) else None

        doi_found = 0
        pdf_done = 0
        doi_failed = 0
        for pos, (idx, row) in enumerate(self.df.iterrows(), start=1):
            doi = self._cell_text(row, dcn)
            pdf = self._cell_text(row, pdf_col)
            doi_status = self._cell_text(row, doi_status_col)
            if doi:
                doi_status = "已匹配"
            elif doi_status:
                doi_failed += 1 if "失败" in doi_status or "未匹配" in doi_status else 0
            else:
                doi_status = "待查询"
            doi_found += 1 if doi else 0
            pdf_done += 1 if pdf else 0
            values = (
                pos,
                self._short(self._cell_text(row, tcn, "未命名文献"), 82),
                self._short(self._cell_text(row, ycn), 8),
                self._short(self._cell_text(row, acn), 22),
                self._short(self._cell_text(row, jcn), 26),
                doi_status,
                self._short(self._cell_text(row, source_col, "—"), 12),
                self._short(self._cell_text(row, match_col, "—"), 8),
                "已下载" if pdf else "待下载",
            )
            state_tag = "matched" if doi else ("failed" if doi_status != "待查询" else "missing")
            tags = ["even" if pos % 2 == 0 else "odd", state_tag]
            self.paper_table.insert("", tk.END, iid=str(idx), values=values, tags=tags)

        total = len(self.df)
        self._update_stats(doi_total=max(total - doi_found - doi_failed, 0),
                           doi_found=doi_found, doi_failed=doi_failed, pdf_done=pdf_done)
        if select_first and self.paper_table.get_children():
            first = self.paper_table.get_children()[0]
            self.paper_table.selection_set(first)
            self.paper_table.focus(first)
            self._on_table_select()
        elif not self.paper_table.selection():
            self._set_detail()

    def _set_detail(self, row=None):
        values = {
            "title": "—", "doi": "—", "source": "—",
            "status": "—", "match": "—", "pdf": "—", "path": "—",
        }
        if row is not None:
            tcn = self._resolve_column("title_col")
            dcn = self._doi_column_for_display()
            source_col = "DOI来源" if self.df is not None and "DOI来源" in list(self.df.columns) else None
            match_col = "匹配度" if self.df is not None and "匹配度" in list(self.df.columns) else None
            pdf_col = "PDF链接" if self.df is not None and "PDF链接" in list(self.df.columns) else None
            doi_status_col = "DOI状态" if self.df is not None and "DOI状态" in list(self.df.columns) else None
            doi = self._cell_text(row, dcn)
            pdf = self._cell_text(row, pdf_col)
            doi_status = "已匹配" if doi else self._cell_text(row, doi_status_col, "待查询")
            values.update({
                "title": self._cell_text(row, tcn, "未命名文献"),
                "doi": doi or "待查询",
                "source": self._cell_text(row, source_col, "—"),
                "status": doi_status,
                "match": self._cell_text(row, match_col, "—"),
                "pdf": "已下载" if pdf else "待下载",
                "path": pdf or "—",
            })
        for key, value in values.items():
            if key in self.detail_vars:
                self.detail_vars[key].set(value)

    def _on_table_select(self, _event=None):
        if self.df is None or not hasattr(self, "paper_table"):
            return
        selected = self.paper_table.selection()
        if not selected:
            self._set_detail()
            return
        try:
            idx = int(selected[0])
            self._set_detail(self.df.loc[idx])
        except Exception:
            self._set_detail()

    def _draw_threshold_slider(self):
        self.threshold_canvas.delete("all")
        w = self.threshold_canvas.winfo_width()
        if w < 100: w = 400
        val = self.threshold.get()
        x = 20 + (w - 40) * (val - 0.5) / 0.5
        # 轨道
        self.threshold_canvas.create_rectangle(20, 18, w - 20, 22, fill=C["bg_input"], outline="")
        # 已选
        self.threshold_canvas.create_rectangle(20, 18, x, 22, fill=C["accent"], outline="")
        # 圆形滑块
        self.threshold_canvas.create_oval(x - 7, 12, x + 7, 28, fill=C["accent"], outline=C["accent"])
        self.threshold_label.config(text=f"{int(val * 100)}%")

    def _on_slider_click(self, e):
        self._update_slider(e.x)

    def _on_slider_drag(self, e):
        self._update_slider(e.x)

    def _update_slider(self, x):
        w = self.threshold_canvas.winfo_width()
        if w < 100: w = 400
        ratio = max(0, min(1, (x - 20) / (w - 40)))
        val = 0.5 + ratio * 0.5
        self.threshold.set(val)
        self._draw_threshold_slider()

    # ═══════════════ 回调 ═══════════════

    def _select_input(self):
        path = filedialog.askopenfilename(
            title="选择 Excel 文件",
            filetypes=[("Excel 文件", "*.xlsx *.xls"), ("所有文件", "*.*")])
        if path:
            self.input_path.set(path)
            self.file_label.config(text=os.path.basename(path))
            self._detect_columns(True)

    def _select_output_dir(self):
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_dir.set(path)
            self._log(f"输出目录: {path}", "info")

    def _open_output_dir(self):
        d = self.output_dir.get()
        if os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showinfo("提示", "输出目录不存在")

    def _stop(self):
        self.stop_requested = True
        self._log("⏹ 正在停止...", "warn")
        self._set_buttons_state(tk.DISABLED)

    def _set_buttons_state(self, state):
        for btn in [self.btn_detect, self.btn_fetch_doi, self.btn_download, self.btn_all]:
            btn.config(state=state)
        self.btn_stop.config(state=tk.NORMAL if state == tk.DISABLED else tk.DISABLED)

    def _log(self, msg, tag=None):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_area.insert(tk.END, f"[{ts}] ", "muted")
        if tag:
            self.log_area.insert(tk.END, f"{msg}\n", tag)
        else:
            self.log_area.insert(tk.END, f"{msg}\n")
        self.log_area.see(tk.END)
        self.root.update_idletasks()

    def _update_stats(self, stage=None, doi_total=None, doi_found=None, doi_failed=None, pdf_done=None):
        if stage: self.stats_stage.set(stage)
        if doi_total is not None: self.stats_doi_total.set(str(doi_total))
        if doi_found is not None: self.stats_doi_found.set(str(doi_found))
        if doi_failed is not None: self.stats_doi_failed.set(str(doi_failed))
        if pdf_done is not None: self.stats_pdf_done.set(str(pdf_done))

    # ═══════════════ 核心逻辑 ═══════════════

    def _detect_columns(self, reload=True):
        path = self.input_path.get()
        if not path or not os.path.isfile(path):
            messagebox.showwarning("提示", "请先选择一个 Excel 文件")
            return
        try:
            if reload or self.df is None:
                self.df = pd.read_excel(path)
            self.detected = detect_columns(self.df)
            total_rows = len(self.df)
            headers = list(self.df.columns)

            if self.detected["title_col"] is None:
                self._update_stats(stage="⚠ 标题列缺失")
                self._log("❌ 未检测到标题列！", "error")
            else:
                existing_doi = 0
                if self.detected["doi_col"] is not None:
                    dn = headers[self.detected["doi_col"]]
                    existing_doi = int(self.df[dn].notna().sum())
                self._update_stats(stage="✅ 就绪", doi_total=total_rows - existing_doi,
                                   doi_found=existing_doi)
                self._log(f"✅ 检测完成: {total_rows} 行, {len(headers)} 列, 已有 DOI: {existing_doi}", "info")
                self._refresh_table(select_first=True)

        except Exception as e:
            messagebox.showerror("错误", f"读取 Excel 失败:\n{e}")
            self._log(f"读取失败: {e}", "error")

    def _check_ready(self):
        if self.running:
            messagebox.showinfo("提示", "正在运行中")
            return False
        if not self.input_path.get() or not os.path.isfile(self.input_path.get()):
            messagebox.showwarning("提示", "请先选择 Excel 文件")
            return False
        if self.df is None or self.detected is None:
            self._detect_columns(True)
        if self.detected is None or self.detected.get("title_col") is None:
            messagebox.showwarning("提示", "未检测到标题列")
            return False
        return True

    def _get_output_path(self, suffix):
        base = os.path.splitext(os.path.basename(self.input_path.get()))[0]
        for s in ["_已加DOI", "_WithLinks"]:
            if base.endswith(s):
                base = base[:-len(s)]
                break
        return os.path.join(self.output_dir.get(), f"{base}{suffix}")

    # ═══════════════ DOI 获取 ═══════════════

    def _start_fetch_doi(self):
        if not self._check_ready(): return
        self._set_buttons_state(tk.DISABLED)
        self.stop_requested = False
        self.running = True
        self._update_stats(stage="🔍 获取 DOI...")
        threading.Thread(target=self._fetch_doi_thread, daemon=True).start()

    def _fetch_doi_thread(self, _pipeline_mode=False):
        try:
            threshold = self.threshold.get()
            headers = list(self.df.columns)
            tc = self.detected["title_col"]
            yc = self.detected.get("year_col")
            dc = self.detected.get("doi_col")
            tcn = headers[tc]

            for col in ["DOI", "DOI链接", "匹配度", "DOI来源", "DOI状态"]:
                if col not in headers:
                    self.df[col] = None
            headers = list(self.df.columns)

            dcn = "DOI" if dc is None else headers[dc]
            rows_p = []
            for idx, row in self.df.iterrows():
                if pd.notna(row.get(dcn)) and str(row.get(dcn)).strip():
                    continue
                title = row.get(tcn)
                if pd.isna(title) or not str(title).strip():
                    continue
                yr = extract_year(row.iloc[yc]) if yc is not None else None
                rows_p.append((idx, str(title).strip(), yr))

            total = len(rows_p)
            if total == 0:
                self._log("所有文献已有 DOI，无需获取", "info")
                self._update_stats(stage="✅ 无需获取")
                return

            workers = min(self.concurrency, total)
            self._log(f"⚡ 并发获取 DOI — {total} 条 ({workers} 线程) (阈值 {int(threshold*100)}%)", "header")
            self._update_stats(stage=f"🔍 {workers}线程获取中...", doi_total=total)

            found = 0
            failed = 0
            completed = 0
            lock = threading.Lock()
            futures_map = {}  # future -> (idx, title)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                for idx, title, year in rows_p:
                    if self.stop_requested:
                        break
                    future = executor.submit(find_doi, title, threshold, year)
                    futures_map[future] = (idx, title)

                for future in as_completed(futures_map):
                    if self.stop_requested:
                        break
                    idx, title = futures_map[future]
                    try:
                        r = future.result()
                    except Exception as e:
                        r = None

                    with lock:
                        completed += 1
                        if r and "doi" in r and r["doi"]:
                            self.df.at[idx, "DOI"] = r["doi"]
                            self.df.at[idx, "DOI链接"] = r.get("url") or f"https://doi.org/{r['doi']}"
                            self.df.at[idx, "匹配度"] = f"{int(r.get('similarity', 0) * 100)}%"
                            self.df.at[idx, "DOI来源"] = r.get("source", "Unknown")
                            self.df.at[idx, "DOI状态"] = "已匹配"
                            found += 1
                            src = r.get('source', '?')
                            self._log(f"  ✅ [{src}] {r['doi'][:40]}", "success")
                        else:
                            self.df.at[idx, "DOI状态"] = "获取失败"
                            self.df.at[idx, "匹配度"] = "—"
                            self.df.at[idx, "DOI来源"] = "—"
                            failed += 1
                            self._log(f"  ❌ {title[:50]}", "error")

                        pct = (completed / total) * 100
                        self._draw_progress(pct)
                        self.status_var.set(f"DOI: {completed}/{total}  ✅{found} ❌{failed}")
                        self._update_stats(doi_found=found, doi_failed=failed)
                        self.root.update_idletasks()

            op = self._get_output_path("_已加DOI.xlsx")
            self.df.to_excel(op, index=False)
            self._draw_progress(100)
            self.status_var.set(f"DOI 完成：成功 {found}，未匹配 {failed}")
            if failed:
                self._log(f"⚠ DOI 完成: {found}/{total} (未匹配 {failed})", "warn")
                messagebox.showwarning("DOI 获取完成",
                    f"成功匹配 {found} 条，未匹配 {failed} 条。")
            else:
                self._log(f"✅ DOI 完成: {found}/{total}", "success")
            self._log(f"📁 {op}", "info")
            self._update_stats(stage="✅ DOI 完成" if not failed else "⚠ 有未匹配",
                               doi_found=found, doi_failed=failed)
            self._detect_columns(reload=False)
            self._refresh_table()

        except Exception as e:
            self._log(f"DOI 错误: {e}", "error")
            messagebox.showerror("错误", str(e))
        finally:
            self.running = False
            if not _pipeline_mode:
                self._set_buttons_state(tk.NORMAL)

    # ═══════════════ PDF 下载 ═══════════════

    def _start_download_pdf(self):
        if not self._check_ready(): return
        dep = self._get_output_path("_已加DOI.xlsx")
        if os.path.isfile(dep):
            self._log(f"📂 加载: {os.path.basename(dep)}", "info")
            self.df = pd.read_excel(dep)
            self._detect_columns(reload=False)

        headers = list(self.df.columns)
        if "DOI" not in headers and self.detected.get("doi_col") is None:
            messagebox.showwarning("提示", "表格中未检测到 DOI 列，请先获取 DOI")
            return

        self._set_buttons_state(tk.DISABLED)
        self.stop_requested = False
        self.running = True
        self._update_stats(stage="📥 下载 PDF...")
        threading.Thread(target=self._download_pdf_thread, daemon=True).start()

    def _download_pdf_thread(self, _pipeline_mode=False):
        try:
            headers = list(self.df.columns)
            dcn = None
            if "DOI" in headers:
                dcn = "DOI"
            elif self.detected.get("doi_col") is not None:
                dcn = headers[self.detected["doi_col"]]
            else:
                for h in headers:
                    if "doi" in str(h).lower():
                        dcn = h; break
            if dcn is None:
                self._log("未找到 DOI 列", "error")
                return

            self._log(f"📋 DOI 列: 「{dcn}」, {len(self.df)} 行", "info")
            tcn = headers[self.detected["title_col"]]
            acn = headers[self.detected.get("author_col")] if self.detected.get("author_col") is not None else None
            ycn = headers[self.detected.get("year_col")] if self.detected.get("year_col") is not None else None
            jcn = headers[self.detected.get("journal_col")] if self.detected.get("journal_col") is not None else None

            rows = []
            for _, row in self.df.iterrows():
                doi = row.get(dcn)
                if pd.isna(doi) or not str(doi).strip():
                    continue
                rows.append({
                    "DOI": str(doi).strip(),
                    "title": str(row.get(tcn, "")),
                    "AUTHOR": str(row.get(acn, "")) if acn else "",
                    "YEAR": row.get(ycn, ""),
                    "JOURNAL": str(row.get(jcn, "")) if jcn else "",
                })

            total = len(rows)
            if total == 0:
                self._log("无 DOI 记录", "warn")
                return

            sd = os.path.join(self.output_dir.get(), "Downloaded_PDFs")
            self._log(f"下载 PDF — {total} 篇 → {sd}", "header")
            self._update_stats(stage="📥 下载中...", pdf_done=0)

            def cb(cur, tn, row):
                if self.stop_requested: return
                self.status_var.set(f"PDF: {cur}/{tn}")
                self._draw_progress((cur / tn) * 100)
                self.root.update_idletasks()
                st = row.get("_download_status", "")
                src = row.get("_download_source", "")
                src_tag = f" [{src}]" if src and src != "—" else ""
                ts = str(row.get("title", ""))[:40]
                if "✅" in st:
                    self._log(f"  ✅ {ts}{src_tag}", "success")
                elif "跳过" in st: pass
                else:
                    self._log(f"  ❌ {ts}", "error")

            results = download_all(rows, sd, progress_callback=cb)

            if "PDF链接" not in list(self.df.columns):
                self.df["PDF链接"] = None

            ri = 0
            for dfi, row in self.df.iterrows():
                dv = row.get(dcn)
                if pd.isna(dv) or not str(dv).strip(): continue
                if ri < len(results):
                    pl = results[ri].get("PDF链接", "")
                    if pl: self.df.at[dfi, "PDF链接"] = pl
                    ri += 1

            op = self._get_output_path("_WithLinks.xlsx")
            self.df.to_excel(op, index=False)
            sc = sum(1 for r in results if "成功" in r.get("_download_status", ""))
            self._draw_progress(100)
            self.status_var.set(f"PDF: {sc}/{total}")
            self._log(f"✅ PDF 完成: {sc}/{total}", "success")
            self._log(f"📁 {op}", "info")
            self._update_stats(stage="✅ 完成", pdf_done=sc)
            self._refresh_table()
            # 生成报告
            try:
                rp = generate_report(self.output_dir.get(),
                    os.path.basename(self.input_path.get()), self.df)
                self._log(f"📊 报告: {os.path.basename(rp)}", "success")
            except Exception: pass

        except Exception as e:
            self._log(f"PDF 错误: {e}", "error")
            messagebox.showerror("错误", str(e))
        finally:
            self.running = False
            if not _pipeline_mode:
                self._set_buttons_state(tk.NORMAL)

    # ═══════════════ 全流程 ═══════════════

    def _start_full_pipeline(self):
        if not self._check_ready(): return
        self._set_buttons_state(tk.DISABLED)
        self.stop_requested = False
        self.running = True
        threading.Thread(target=self._full_pipeline_thread, daemon=True).start()

    def _full_pipeline_thread(self):
        try:
            self._log("═" * 40, "header")
            self._log("🚀 全流程: 获取 DOI → 下载 PDF", "header")
            self._log("═" * 40, "header")
            self._log("▶ 阶段 1/2: 获取 DOI", "info")
            self._fetch_doi_thread(_pipeline_mode=True)
            if self.stop_requested: return

            de = self._get_output_path("_已加DOI.xlsx")
            self._log(f"📂 加载: {os.path.basename(de)}", "info")
            self.df = pd.read_excel(de)
            self._detect_columns(reload=False)

            self._log("▶ 阶段 2/2: 下载 PDF", "info")
            self._download_pdf_thread(_pipeline_mode=True)

            if not self.stop_requested:
                self._log("═" * 40, "header")
                self._log("🎉 全流程完成！", "success")
                self._log("═" * 40, "header")
                self.status_var.set("全流程完成 ✅")
                self._update_stats(stage="🎉 完成")
                # 生成 HTML 报告
                self._log("📊 正在生成结果报告...", "info")
                rp = generate_report(self.output_dir.get(),
                    os.path.basename(self.input_path.get()), self.df)
                self._log(f"📊 报告已生成: {os.path.basename(rp)}", "success")
        except Exception as e:
            self._log(f"全流程错误: {e}", "error")
            messagebox.showerror("错误", str(e))
        finally:
            self.running = False
            self._set_buttons_state(tk.NORMAL)


def main():
    root = tk.Tk()
    root.configure(bg=C["bg_dark"])
    app = LiteratureApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
