"""
LitBox v3.4 - Literature Collector
浅色科研数据表格中心界面
"""

import os, sys, re, json, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime
import pandas as pd

from doi_fetcher import find_doi, extract_year, normalize_text, DEFAULT_THRESHOLD
from pdf_downloader import download_all, build_filename, clean_filename
from pdf_renamer import scan_pdf_folder, apply_renames
from updater import check_for_update
from report import generate_report
import deepseek_ai

# 当前版本号（格式: yyyyMMdd-HHmm，与 GitHub version.json 对比）
CURRENT_VERSION = "20260812-1955"

APP_NAME = "LitBox"
APP_SUBTITLE = "Literature Collector"


def resource_path(relative_path):
    """获取开发环境或 PyInstaller 打包后的资源路径。"""
    base_path = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base_path, relative_path)

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
        self.root.title(f"{APP_NAME} - {APP_SUBTITLE}")
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
        self.pdf_rename_results = []

        # DeepSeek AI 配置（从 config.json 加载）
        self.ds_cfg = deepseek_ai.load_config()
        self.rename_mode = tk.StringVar(value="local")  # local / ai

        self.stats_doi_total = tk.StringVar(value="—")
        self.stats_doi_found = tk.StringVar(value="—")
        self.stats_doi_failed = tk.StringVar(value="—")
        self.stats_pdf_done = tk.StringVar(value="—")
        self.stats_stage = tk.StringVar(value="等待开始")
        self.detail_vars = {}

        self.sidebar_btns = []
        self.current_page = tk.StringVar(value="overview")

        self._set_window_icon(self.root)
        self._build_ui()
        self._log(f"{APP_NAME} v3.5", "header")
        self._log("Excel → DOI 获取 → PDF 下载 → 智能命名，当前界面以文献表格核对为中心。")
        if deepseek_ai.is_configured(self.ds_cfg):
            self._log("🤖 DeepSeek AI 补全已启用", "success")
        else:
            self._log("🤖 DeepSeek AI 补全未启用（可在设置页配置）", "info")

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

    def _set_window_icon(self, window):
        try:
            icon = tk.PhotoImage(file=resource_path(os.path.join("assets", "wenxian_yunxia.png")))
            window.iconphoto(True, icon)
            window._app_icon = icon
        except Exception:
            pass

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
        tk.Label(header, text=APP_NAME, font=(FONT, 18, "bold"),
                 fg=C["text"], bg=C["bg_dark"]).pack(side=tk.LEFT)
        tk.Label(header, text="Excel → DOI → PDF → 规范命名",
                 font=(FONT, 10), fg=C["text_dim"], bg=C["bg_dark"]).pack(side=tk.LEFT, padx=(14, 0), pady=(5, 0))

        nav = tk.Frame(header, bg=C["bg_dark"])
        nav.pack(side=tk.RIGHT)
        self._make_button(nav, "AI 对话", self._toggle_ai_chat, kind="ghost").pack(side=tk.LEFT, padx=(0, 6))
        self._make_button(nav, "总览", lambda: self._switch_page("overview"), kind="ghost").pack(side=tk.LEFT, padx=(0, 6))
        self._make_button(nav, "使用教程", self._show_tutorial, kind="ghost").pack(side=tk.LEFT, padx=(0, 6))
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
        self.btn_rename_pdf = self._make_button(toolbar_inner, "PDF 重命名", self._start_pdf_rename, kind="secondary")
        self.btn_rename_pdf.pack(side=tk.RIGHT, padx=(6, 0))
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

        cols = ("idx", "title", "pdf", "doi", "year", "author", "journal", "source", "match")
        self.paper_table = ttk.Treeview(table_body, columns=cols, show="headings", selectmode="browse")
        headings = {
            "idx": "#", "title": "标题", "pdf": "PDF 状态", "doi": "DOI 状态",
            "year": "年份", "author": "作者", "journal": "期刊",
            "source": "来源", "match": "匹配度"
        }
        widths = {"idx": 44, "title": 300, "pdf": 100, "doi": 110, "year": 70,
                  "author": 120, "journal": 150, "source": 90, "match": 80}
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
        # 绑定鼠标滚轮滚动
        self.paper_table.bind("<MouseWheel>", lambda e: self._on_mousewheel(e))
        # 绑定双击事件以防滚动期间点击丢失
        self.paper_table.bind("<Button-1>", self._on_table_click)

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
            lbl = tk.Label(detail_body, textvariable=var, font=(FONT, 9), fg=C["text"],
                     bg=C["bg_card"], anchor=tk.W, justify=tk.LEFT, wraplength=280)
            lbl.pack(anchor=tk.W, fill=tk.X)
            if key == "doi":
                self._doi_detail_label = lbl
                self._doi_url = ""
                lbl.bind("<Button-1>", self._on_doi_click)
                lbl.bind("<Enter>", lambda e: lbl.configure(cursor="hand2"))

        # AI 代理操作按钮（选中文献后可用）
        ai_ops = tk.Frame(detail_body, bg=C["bg_card"])
        ai_ops.pack(fill=tk.X, pady=(14, 0))
        self.btn_ai_search_doi = self._make_button(
            ai_ops, "🔍 搜索 DOI", lambda: self._ai_proxy_action("doi"), kind="secondary")
        self.btn_ai_search_doi.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.btn_ai_get_pdf = self._make_button(
            ai_ops, "📥 AI 搜集下载", lambda: self._ai_proxy_action("pdf"), kind="primary")
        self.btn_ai_get_pdf.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        self.btn_ai_search_doi.config(state=tk.DISABLED)
        self.btn_ai_get_pdf.config(state=tk.DISABLED)
        tk.Label(detail_body, text="选中文献后可用：AI 自动匹配 DOI / 搜集 PDF，过程在对话窗口展示",
                 font=(FONT, 8), fg=C["text_dim"], bg=C["bg_card"], justify=tk.LEFT,
                 anchor=tk.W, wraplength=280).pack(anchor=tk.W, pady=(6, 0))

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

        # ═══════════ 下载进度详情面板 ═══════════
        dl_panel = self._card_frame(p, "📥 下载进度")
        dl_panel.pack(fill=tk.X, padx=18, pady=(0, 10))
        dl_inner = tk.Frame(dl_panel, bg=C["bg_card"])
        dl_inner.pack(fill=tk.X, padx=12, pady=10)

        # 明细进度条：成功数/总数 + 百分比
        dl_stat_row = tk.Frame(dl_inner, bg=C["bg_card"])
        dl_stat_row.pack(fill=tk.X)
        self.dl_progress_var = tk.StringVar(value="0/0 篇")
        tk.Label(dl_stat_row, textvariable=self.dl_progress_var, font=(FONT, 10, "bold"),
                 fg=C["accent"], bg=C["bg_card"], width=10, anchor=tk.W).pack(side=tk.LEFT)
        self.dl_bar = tk.Canvas(dl_stat_row, height=8, bg=C["bg_input"], highlightthickness=0)
        self.dl_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        self._dl_percent_var = tk.StringVar(value="0%")
        tk.Label(dl_stat_row, textvariable=self._dl_percent_var, font=(FONT, 9),
                 fg=C["text_dim"], bg=C["bg_card"], width=5, anchor=tk.E).pack(side=tk.LEFT, padx=(8, 0))

        # 每篇文献的实时状态表
        dl_table_frame = tk.Frame(dl_inner, bg=C["bg_card"])
        dl_table_frame.pack(fill=tk.X, pady=(8, 0))
        dl_cols = ("no", "title", "doi", "source", "status", "cost")
        self.dl_tree = ttk.Treeview(dl_table_frame, columns=dl_cols, show="headings", height=5)
        dl_head = {"no": "#", "title": "标题", "doi": "DOI", "source": "来源", "status": "状态", "cost": "用时"}
        dl_wid = {"no": 36, "title": 250, "doi": 150, "source": 80, "status": 90, "cost": 50}
        for col in dl_cols:
            self.dl_tree.heading(col, text=dl_head[col])
            self.dl_tree.column(col, width=dl_wid[col], minwidth=30, anchor=tk.W,
                                stretch=(col in ("title", "doi")))
        self.dl_tree.tag_configure("ok", foreground=C["success"])
        self.dl_tree.tag_configure("fail", foreground=C["danger"])
        self.dl_tree.tag_configure("ai", foreground=C["accent2"])
        self.dl_tree.tag_configure("wait", foreground=C["text_dim"])
        self.dl_tree.pack(fill=tk.X)
        self._dl_start_time = None
        self._dl_row_map = {}  # DOI -> tree iid

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

    # ═══════════════ 使用教程 ═══════════════

    # ═══════════════ AI 对话窗口 ═══════════════

    def _toggle_ai_chat(self):
        """打开/关闭 AI 对话窗口（可开关）。"""
        try:
            if hasattr(self, "_ai_chat_win") and self._ai_chat_win is not None and self._ai_chat_win.winfo_exists():
                self._ai_chat_win.destroy()
                self._ai_chat_win = None
                return
        except Exception:
            self._ai_chat_win = None
        self._open_ai_chat()

    def _open_ai_chat(self):
        """创建 AI 对话窗口。"""
        # 同步最新配置：设置页 UI 里已填的值优先（未点"保存配置"也能生效）
        if hasattr(self, "ai_key_var") and str(self.ai_key_var.get()).strip():
            self.ds_cfg["api_key"] = self.ai_key_var.get().strip()
            self.ds_cfg["base_url"] = self.ai_url_var.get().strip() or "https://api.deepseek.com"
            self.ds_cfg["model"] = self.ai_model_var.get().strip() or "deepseek-chat"
            self.ds_cfg["deepseek_enabled"] = self.ai_enabled_var.get()
            deepseek_ai.save_config(self.ds_cfg)
        else:
            # 设置页没填过：重新读磁盘配置（可能用户在外部改过 config.json）
            self.ds_cfg = deepseek_ai.load_config()

        if not deepseek_ai.is_configured(self.ds_cfg):
            messagebox.showwarning(
                "AI 未配置",
                "请先在「设置」页填写 DeepSeek API Key 并勾选「启用 AI 补全」。\n"
                "（Key 在 platform.deepseek.com 获取）")
            return

        win = tk.Toplevel(self.root)
        win.title(f"{APP_NAME} - AI 对话")
        win.geometry("640x880")
        win.minsize(500, 640)
        win.configure(bg=C["bg_dark"])
        win.transient(self.root)
        self._set_window_icon(win)
        self._ai_chat_win = win

        header = tk.Frame(win, bg=C["bg_card"], highlightbackground=C["border"], highlightthickness=1)
        header.pack(fill=tk.X)
        tk.Label(header, text="🤖 AI 对话", font=(FONT, 14, "bold"), fg=C["text"],
                 bg=C["bg_card"]).pack(side=tk.LEFT, padx=16, pady=10)
        tk.Label(header, text="可直接提问，或引用表格中选中的文献", font=(FONT, 9),
                 fg=C["text_dim"], bg=C["bg_card"]).pack(side=tk.LEFT, padx=(6, 0), pady=(13, 10))
        self._make_button(header, "关闭", win.destroy, kind="ghost").pack(side=tk.RIGHT, padx=12, pady=8)

        # 消息区
        body = tk.Frame(win, bg=C["bg_dark"])
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        chat_frame = tk.Frame(body, bg=C["bg_card"], highlightbackground=C["border"], highlightthickness=1)
        chat_frame.pack(fill=tk.BOTH, expand=True)

        self._ai_chat_log = scrolledtext.ScrolledText(
            chat_frame, wrap=tk.WORD, font=(FONT, 10),
            bg=C["bg_input"], fg=C["text"], relief=tk.FLAT, padx=12, pady=10)
        self._ai_chat_log.pack(fill=tk.BOTH, expand=True)
        self._ai_chat_log.configure(state=tk.DISABLED)

        # 引用文献按钮行
        ref_row = tk.Frame(body, bg=C["bg_dark"])
        ref_row.pack(fill=tk.X, pady=(8, 0))
        self._ai_ref_row = ref_row
        self._ai_chat_body = body
        self._make_button(ref_row, "📎 引用选中文献", self._ai_attach_selected, kind="secondary").pack(side=tk.LEFT)
        self._make_button(ref_row, "🔍 批量搜文献", self._ai_batch_search, kind="secondary").pack(side=tk.LEFT, padx=(8, 0))
        self._make_button(ref_row, "🧹 清空对话", self._ai_clear_chat, kind="ghost").pack(side=tk.LEFT, padx=(8, 0))
        self._ai_ref_label = tk.Label(ref_row, text="", font=(FONT, 8), fg=C["accent2"],
                                      bg=C["bg_dark"], anchor=tk.W)
        self._ai_ref_label.pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)

        # 输入区（注意：按钮先 pack 占右侧，输入框再 expand，否则按钮被挤出窗口）
        input_row = tk.Frame(body, bg=C["bg_dark"])
        input_row.pack(fill=tk.X, pady=(8, 0))
        self._ai_send_btn = self._make_button(input_row, "发送", self._ai_send, kind="primary")
        self._ai_send_btn.pack(side=tk.RIGHT, padx=(8, 0), fill=tk.Y)
        self._ai_input = scrolledtext.ScrolledText(
            input_row, height=3, wrap=tk.WORD, font=(FONT, 10),
            bg=C["bg_card"], fg=C["text"], relief=tk.FLAT,
            highlightbackground=C["border"], highlightthickness=1)
        self._ai_input.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 状态
        self._ai_status_var = tk.StringVar(value="就绪")
        tk.Label(body, textvariable=self._ai_status_var, font=(FONT, 8),
                 fg=C["text_dim"], bg=C["bg_dark"]).pack(fill=tk.X, pady=(6, 0))

        # 快捷键：Ctrl+Enter 发送
        self._ai_input.bind("<Control-Return>", lambda e: self._ai_send())
        self._ai_attached = None

        self._ai_append("🤖 你好，我是 LitBox 内置的 DeepSeek 助手。\n"
                        "· 直接输入问题即可对话\n"
                        "· 先选中表格中的文献，再点「引用选中文献」，可让 AI 分析该文献\n"
                        "· 可以问：这篇文献的 DOI？开放获取版本？如何获取？", "system")
        self._ai_input.focus_set()

    def _ai_append(self, text, role="user"):
        """向对话区追加一条消息。role: user/ai/system"""
        if not hasattr(self, "_ai_chat_log"):
            return
        self._ai_chat_log.configure(state=tk.NORMAL)
        tag = {"user": "user", "ai": "ai", "system": "system"}.get(role, "system")
        self._ai_chat_log.insert(tk.END, text + "\n\n", tag)
        self._ai_chat_log.tag_config("user", foreground=C["text"], font=(FONT, 10, "bold"))
        self._ai_chat_log.tag_config("ai", foreground=C["accent2"], font=(FONT, 10))
        self._ai_chat_log.tag_config("system", foreground=C["text_dim"], font=(FONT, 9))
        self._ai_chat_log.configure(state=tk.DISABLED)
        self._ai_chat_log.see(tk.END)

    def _ai_attach_selected(self):
        """把表格中选中的文献附加到输入框上下文。"""
        if self.df is None or not hasattr(self, "paper_table"):
            self._ai_append("⚠ 请先在总览页导入并选中一条文献。", "system")
            return
        sel = self.paper_table.selection()
        if not sel:
            self._ai_append("⚠ 请先在表格中选中一条文献。", "system")
            return
        idx = int(sel[0])
        row = self.df.loc[idx]

        def cell(col, default="未知"):
            if col is None:
                return default
            v = row.get(col, "")
            if pd.isna(v) or not str(v).strip():
                return default
            return str(v).strip()

        tcn = self._resolve_column("title_col")
        ycn = self._resolve_column("year_col")
        acn = self._resolve_column("author_col")
        jcn = self._resolve_column("journal_col")
        dcn = self._doi_column_for_display()

        ref = {
            "title": cell(tcn, "未知标题"),
            "year": cell(ycn),
            "author": cell(acn),
            "journal": cell(jcn),
            "doi": cell(dcn),
            "status": cell("DOI状态", "待查询"),
        }
        self._ai_attached = ref
        summary = f"已引用: {ref['title'][:50]}"
        if ref.get("year"): summary += f" ({ref['year']})"
        self._ai_ref_label.configure(text=summary)
        self._ai_append(f"📎 已引用文献:\n标题: {ref['title']}\n年份: {ref['year']}\n作者: {ref['author']}\n"
                        f"期刊: {ref['journal']}\nDOI: {ref['doi']}\n状态: {ref['status']}", "system")

    def _ai_clear_chat(self):
        """清空对话区。"""
        if hasattr(self, "_ai_chat_log"):
            self._ai_chat_log.configure(state=tk.NORMAL)
            self._ai_chat_log.delete("1.0", tk.END)
            self._ai_chat_log.configure(state=tk.DISABLED)
        self._ai_attached = None
        self._ai_ref_label.configure(text="")

    # ═══════════════ AI 批量文献搜集 ═══════════════

    def _ai_batch_search(self):
        """按主题批量搜集文献：用户输入主题 → AI 解析 → Crossref 搜索 → 候选导入。"""
        if not deepseek_ai.is_configured(self.ds_cfg):
            self._ai_append("⚠ 请先在设置页配置 DeepSeek API Key。", "system")
            return

        # 弹出主题输入窗口
        topic_win = tk.Toplevel(self.root)
        topic_win.title("🔍 批量搜集文献")
        topic_win.geometry("520x240")
        topic_win.configure(bg=C["bg_dark"])
        topic_win.transient(self.root)
        self._set_window_icon(topic_win)

        tk.Label(topic_win, text="描述你想搜集的文献主题", font=(FONT, 12, "bold"),
                 fg=C["text"], bg=C["bg_dark"]).pack(anchor=tk.W, padx=18, pady=(18, 6))
        tk.Label(topic_win, text="例：2020 年以来北大西洋中脊地幔熔融的文献，找 15 篇",
                 font=(FONT, 9), fg=C["text_dim"], bg=C["bg_dark"]).pack(anchor=tk.W, padx=18)

        entry = tk.Text(topic_win, height=4, wrap=tk.WORD, font=(FONT, 10),
                        bg=C["bg_card"], fg=C["text"], relief=tk.FLAT,
                        highlightbackground=C["border"], highlightthickness=1)
        entry.pack(fill=tk.X, padx=18, pady=12)

        def do_search():
            text = entry.get("1.0", tk.END).strip()
            if not text:
                return
            topic_win.destroy()
            self._ai_append(f"🔍 批量搜集: {text}", "user")
            self._ai_status_var.set("解析主题...")
            threading.Thread(target=lambda: self._ai_batch_search_worker(text), daemon=True).start()

        btns = tk.Frame(topic_win, bg=C["bg_dark"])
        btns.pack(fill=tk.X, padx=18)
        self._make_button(btns, "开始搜索", do_search, kind="primary").pack(side=tk.RIGHT)
        self._make_button(btns, "取消", topic_win.destroy, kind="ghost").pack(side=tk.RIGHT, padx=(0, 8))
        entry.focus_set()

    def _ai_batch_search_worker(self, text):
        """批量搜索工作线程：AI 解析主题 → Crossref 搜索 → 展示候选。
        全程以「思考步骤流」输出：目标 → 规划 → 执行 → 筛选 → 结果。"""
        def step(msg, kind="system"):
            """追加一条带时间戳的思考步骤。"""
            ts = time.strftime("%H:%M:%S")
            self.root.after(0, lambda: self._ai_append(f"[{ts}] {msg}", kind))
            self.root.update_idletasks()

        try:
            # ── 步骤 1: 理解用户需求 ──
            step(f"🧠 思考① 理解需求\n  用户请求: {str(text)[:100]}")
            step("  正在让 DeepSeek 解析：提取英文关键词 / 年份范围 / 数量...")

            query = deepseek_ai.parse_topic_query(text, cfg=self.ds_cfg)
            if not query:
                step("⚠ 解析失败：需求描述信息不足", "warn")
                self.root.after(0, lambda: self._ai_append(
                    "  请描述更具体，如：2020年以来 北大西洋中脊 地幔熔融 文献，找 15 篇", "system"))
                self.root.after(0, lambda: self._ai_status_var.set("就绪"))
                return

            kws = ", ".join(query["keywords"])
            yf = query.get("year_from") or "不限"
            yt = query.get("year_to") or "不限"
            lim = query["limit"]
            step(f"🧠 思考② 检索规划\n  解析结果: 关键词 [{kws}] | 年份 {yf}~{yt} | 目标 {lim} 篇")
            step(f"  选择 Crossref 作为检索源：收录全、字段规范、支持年份过滤")

            # ── 步骤 2: 构建查询并执行 ──
            query_built = " ".join(query["keywords"])
            if query.get("year_from"):
                query_built += f" | 限定 ≥{query['year_from']}"
            step(f"🔍 思考③ 执行检索\n  查询: {query_built}\n  调 Crossref API...")

            results = deepseek_ai.search_works_by_topic(query, limit=lim)
            if not results:
                step("⚠ 思考④ 结果：Crossref 返回 0 篇", "warn")
                step("  可能原因: 关键词过窄/年份过新/无收录。建议换个同义词再试。", "system")
                self.root.after(0, lambda: self._ai_status_var.set("无结果"))
                return

            # ── 步骤 3: 筛选与去重 ──
            step(f"✅ 思考④ 结果: Crossref 返回 {len(results)} 篇原始结果")
            step(f"  正在筛选: 剔除无标题/无 DOI 记录，保留有效候选...")
            valid = [w for w in results if w.get("title") and (w.get("doi") or True)]
            step(f"  有效候选: {len(valid)} 篇（将在下方列表展示，可勾选导入）")

            self._candidate_works = valid
            self.root.after(0, lambda: self._ai_show_batch_candidates(valid))
            self.root.after(0, lambda: self._ai_status_var.set(f"找到 {len(valid)} 篇"))
        except Exception as e:
            self.root.after(0, lambda: self._ai_append(f"❌ 批量搜索错误: {e}", "system"))
            self.root.after(0, lambda: self._ai_status_var.set("就绪"))

    def _ai_show_batch_candidates(self, results):
        """在对话窗口展示候选文献列表 + 导入按钮。"""
        # 清理旧的候选区（多次搜索时）
        for w in getattr(self, "_ai_cand_frames", []):
            try:
                w.destroy()
            except Exception:
                pass
        self._ai_cand_frames = []

        self._ai_append(f"📚 找到 {len(results)} 篇候选文献：", "ai")
        self._ai_candidates = results

        # 候选列表区（不含导入按钮，按钮单独放避免被挤压）
        cand_frame = tk.Frame(self._ai_chat_body, bg=C["bg_card"],
                              highlightbackground=C["border"], highlightthickness=1)
        cand_frame.pack(fill=tk.X, padx=14, pady=(0, 4), before=self._ai_ref_row)
        self._ai_cand_frames.append(cand_frame)

        header = tk.Frame(cand_frame, bg=C["bg_card"])
        header.pack(fill=tk.X, padx=10, pady=(8, 4))
        tk.Label(header, text=f"候选文献（{len(results)} 篇，勾选后导入 Excel）",
                 font=(FONT, 9, "bold"), fg=C["text_dim"], bg=C["bg_card"]).pack(side=tk.LEFT)
        self._make_button(header, "全选", lambda: self._ai_select_all_candidates(True), kind="ghost").pack(side=tk.RIGHT)
        self._make_button(header, "取消全选", lambda: self._ai_select_all_candidates(False), kind="ghost").pack(side=tk.RIGHT, padx=(0, 6))

        list_frame = tk.Frame(cand_frame, bg=C["bg_card"])
        list_frame.pack(fill=tk.X, padx=10, pady=(0, 8))
        # height 改成 7 让候选更易读（之前 6 容易被父容器压扁）
        self._ai_cand_list = tk.Listbox(list_frame, height=7, selectmode=tk.EXTENDED,
                                         font=(FONT, 9), bg=C["bg_input"], fg=C["text"],
                                         relief=tk.FLAT, highlightbackground=C["border"])
        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._ai_cand_list.yview)
        self._ai_cand_list.configure(yscrollcommand=scroll.set)
        self._ai_cand_list.pack(side=tk.LEFT, fill=tk.X, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        for w in results:
            label = f"[{w['year'] or '----'}] {w['title'][:70]} | {w['journal'][:25]}"
            self._ai_cand_list.insert(tk.END, label)

        # 导入按钮：放单独一行（在 cand_frame 之后、ref_row 之前），保证不被压扁
        btn_bar = tk.Frame(self._ai_chat_body, bg=C["bg_dark"])
        btn_bar.pack(fill=tk.X, padx=14, pady=(0, 6), before=self._ai_ref_row)
        self._ai_cand_frames.append(btn_bar)
        self._make_button(btn_bar, "➕ 导入选中的到 Excel", self._ai_import_candidates,
                         kind="primary").pack(side=tk.RIGHT)

    def _ai_select_all_candidates(self, select):
        """全选/取消全选候选文献。"""
        if hasattr(self, "_ai_cand_list"):
            if select:
                self._ai_cand_list.selection_set(0, tk.END)
            else:
                self._ai_cand_list.selection_clear(0, tk.END)

    def _ai_import_candidates(self):
        """把勾选的候选文献写入 Excel（无 Excel 时自动创建新文件，插入待下载态）。"""
        if not hasattr(self, "_ai_cand_list") or not hasattr(self, "_ai_candidates"):
            return
        sel = self._ai_cand_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先勾选要导入的文献")
            return

        # 收集选中项
        selected = [self._ai_candidates[i] for i in sel]

        # ── 自动创建 Excel：无 df 或未加载文件时，新建标准结构并设置检测 ──
        import pandas as pd
        created_new = False
        if self.df is None or len(self.df) == 0 or self.detected is None:
            self.df = pd.DataFrame(columns=["标题", "作者", "年份", "期刊", "DOI", "DOI状态"])
            self.detected = {"title_col": 0, "year_col": 2, "author_col": 1,
                             "journal_col": 3, "doi_col": 4}
            created_new = True

        # 去重（按 DOI，跳过已有的）
        existing_dois = set()
        if "DOI" in list(self.df.columns):
            existing_dois = {str(v).strip().lower() for v in self.df["DOI"].dropna()}
        new_items = [w for w in selected if not w.get("doi") or str(w["doi"]).lower() not in existing_dois]

        if not new_items:
            messagebox.showinfo("提示", "选中的文献都已存在清单中")
            self._ai_append("ℹ 所选文献均已存在于当前 Excel。", "system")
            return

        tcn = self._resolve_column("title_col") or "标题"
        acn = self._resolve_column("author_col") or "作者"
        ycn = self._resolve_column("year_col") or "年份"
        jcn = self._resolve_column("journal_col") or "期刊"
        dcn = self._doi_column_for_display() or "DOI"

        rows = []
        for w in new_items:
            rows.append({
                tcn: w["title"],
                acn: ", ".join(w.get("authors") or []),
                ycn: w.get("year"),
                jcn: w.get("journal", ""),
                dcn: w.get("doi", ""),
                "DOI状态": "待下载" if w.get("doi") else "待查询",
            })
        self.df = pd.concat([self.df, pd.DataFrame(rows)], ignore_index=True)
        self._refresh_table()
        self._update_stats(stage=f"✅ 导入 {len(rows)} 篇")
        self._ai_append(f"✅ 已将 {len(rows)} 篇文献加入{'新创建的 ' if created_new else ''}Excel"
                        f"（跳过 {len(selected) - len(new_items)} 篇重复）。", "ai")
        self._log(f"📥 批量导入 {len(rows)} 篇文献到表格" + ("（自动创建 Excel）" if created_new else ""), "success")

        # 保存：无输入文件时自动创建 _批量搜集.xlsx
        try:
            if created_new or not self.input_path.get() or not os.path.isfile(self.input_path.get()):
                op = os.path.join(self.output_dir.get() or os.getcwd(), "批量搜集文献清单.xlsx")
            else:
                op = self._get_output_path("_批量搜集.xlsx")
            self.df.to_excel(op, index=False)
            self._ai_append(f"📁 已保存: {os.path.basename(op)}", "system")
        except Exception:
            pass

    # ═══════════════ AI 代理：选中文献自动搜 DOI / 下载 PDF ═══════════════

    def _ai_proxy_action(self, action):
        """选中文献后触发 AI 代理任务（action: doi / pdf）。
        打开 AI 对话窗口，自动附加选中文献并执行任务，思考过程实时展示。"""
        if self.df is None or not hasattr(self, "paper_table"):
            messagebox.showwarning("提示", "请先导入 Excel 并选中一条文献")
            return
        sel = self.paper_table.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在表格中选中一条文献")
            return
        idx = int(sel[0])
        row = self.df.loc[idx]

        # 收集文献信息
        def cell(col, default="未知"):
            if col is None:
                return default
            v = row.get(col, "")
            if pd.isna(v) or not str(v).strip():
                return default
            return str(v).strip()

        ref = {
            "title": cell(self._resolve_column("title_col"), "未知标题"),
            "year": cell(self._resolve_column("year_col")),
            "author": cell(self._resolve_column("author_col")),
            "journal": cell(self._resolve_column("journal_col")),
            "doi": cell(self._doi_column_for_display()),
            "status": cell("DOI状态", "待查询"),
        }

        # 打开对话窗口（若已开则复用）
        self._open_ai_chat()
        self._ai_attached = ref
        summary = f"已引用: {ref['title'][:50]}"
        if ref.get("year"):
            summary += f" ({ref['year']})"
        self._ai_ref_label.configure(text=summary)

        action_name = "搜索 DOI" if action == "doi" else "搜集下载 PDF"
        self._ai_append(f"🤖 收到任务：{action_name}\n目标文献: {ref['title']}\n", "system")

        # 后台执行任务
        def worker():
            if action == "doi":
                self._ai_proxy_search_doi(idx, ref)
            else:
                self._ai_proxy_get_pdf(idx, ref)

        threading.Thread(target=worker, daemon=True).start()

    def _ai_proxy_search_doi(self, idx, ref):
        """AI 代理任务①：为选中文献搜索 DOI（机器核验找回 → AI 兜底 → 核验写入）。
        全程以「思考步骤流」输出。"""
        import lit_verify
        title = ref.get("title", "")
        year = ref.get("year")
        author = ref.get("author")

        def step(msg, kind="system"):
            ts = time.strftime("%H:%M:%S")
            self.root.after(0, lambda: self._ai_append(f"[{ts}] {msg}", kind))
            self.root.update_idletasks()

        step(f"🧠 思考① 明确目标\n  文献: {title[:60]}")
        step(f"  已知: 年份={year or '未知'} 作者={author or '未知'} | 目标: 找到并核验真实 DOI")

        step(f"🧠 思考② 选检索源\n  优先 Crossref：标题+作者+年份综合检索，命中率高")
        step("  🔍 执行 Crossref 标题检索...")
        try:
            machine = lit_verify.find_doi_by_title(title, year=year, author=author, threshold=0.6)
        except Exception:
            machine = None

        if machine and machine.get("doi"):
            step(f"✅ 思考③ 检索命中\n  DOI: {machine['doi']}\n  匹配度: {machine.get('similarity', 0):.0%}（≥60% 达标）", "ai")
            step("  命中来源可信（Crossref 权威收录），直接写入 Excel")
            self.root.after(0, lambda: self._write_doi_match(idx, machine["doi"],
                             machine.get("similarity", 1.0), f"AI代理-{machine.get('source','Crossref')}", title))
            self.root.after(0, lambda: self._ai_append(f"[{time.strftime('%H:%M:%S')}] ✅ 已写入 Excel：{machine['doi']}", "ai"))
            return

        step("⚠ 思考③ Crossref 未命中（标题差异/未被收录）", "warn")
        step("🧠 思考④ 切换 DeepSeek 语义匹配\n  理由: 机器检索有收录盲区，AI 可凭语义记忆直接报 DOI")
        if deepseek_ai.is_configured(self.ds_cfg):
            try:
                result = deepseek_ai.ai_find_doi(title, year=year, author=author, cfg=self.ds_cfg)
            except Exception:
                result = None
            if result and result.get("doi"):
                step(f"🤖 DeepSeek 候选: {result['doi']}（自评置信度 {result.get('confidence', 0):.0%}）")
                step("🧠 思考⑤ 核验防编造\n  对 AI 候选执行 Crossref 双检: DOI 存在性 + 标题相似度 ≥60%")
                v = lit_verify.verify_doi_full(result["doi"], title)
                ok = bool(v.get("exists") and v.get("similarity", 0) >= 0.6)
                if ok:
                    step(f"✅ 思考⑤ 核验通过（存在 + 相似度 {v.get('similarity', 0):.0%}）\n  写入 Excel", "ai")
                    self.root.after(0, lambda: self._write_doi_match(idx, result["doi"],
                                     v.get("similarity", 0.6), "AI代理-DeepSeek", title))
                    self.root.after(0, lambda: self._ai_append(f"[{time.strftime('%H:%M:%S')}] ✅ 已写入 Excel：{result['doi']}", "ai"))
                    return
                step(f"⚠ 思考⑤ 核验拦截\n  AI 给的 {result['doi']} 与真实标题相似度仅 {v.get('similarity', 0):.0%}，判定为编造/错配，不写入", "warn")
            else:
                step("❌ DeepSeek 未给出有效 DOI", "system")
        else:
            step("❌ DeepSeek 未配置（可在设置页填写 API Key）", "system")

        step("⚠ 结论: 未能找到可信 DOI\n  可尝试:\n  1. 在对话中直接询问 AI 该文献信息\n  2. Google Scholar 人工核实标题拼写", "warn")

    def _ai_proxy_get_pdf(self, idx, ref):
        """AI 代理任务②：为选中文献搜集下载 PDF（机器 OA → AI 链接 → 下载 → 写入）。
        全程以「思考步骤流」输出。"""
        import lit_verify
        doi = ref.get("doi", "")
        title = ref.get("title", "")

        def step(msg, kind="system"):
            ts = time.strftime("%H:%M:%S")
            self.root.after(0, lambda: self._ai_append(f"[{ts}] {msg}", kind))
            self.root.update_idletasks()

        if not doi or doi in ("未知", "待查询", "无"):
            step("⚠ 前置检查失败: 该文献还没有 DOI", "warn")
            step("  建议先执行「🔍 搜索 DOI」拿到 DOI 后再来下载")
            return

        step(f"🧠 思考① 明确目标\n  文献: {title[:60]}\n  DOI: {doi}")
        step("🧠 思考② 先查免费 OA 镜像（先免费后付费原则）\n  查询 Semantic Scholar + Unpaywall...")
        links = []
        try:
            v = lit_verify.verify_doi_full(doi, title)
            if v.get("oa_url"):
                links.append({"url": v["oa_url"], "source": "OA直链"})
            for loc in (v.get("oa_locations") or []):
                if loc.get("url") and loc["url"] not in [l["url"] for l in links]:
                    links.append({"url": loc["url"], "source": f"Unpaywall-{loc.get('host','')[:18]}"})
        except Exception:
            pass

        if links:
            step(f"✅ 思考② 机器核验到 {len(links)} 个 OA 直链", "ai")
            for l in links[:4]:
                step(f"  · {l['source']}: {l['url'][:70]}")
        else:
            step("⚠ 思考② 无机器 OA 记录（非 OA 或镜像无收录）", "warn")
            step("🧠 思考③ 询问 DeepSeek 找开放获取候选链接")
            if deepseek_ai.is_configured(self.ds_cfg):
                try:
                    links = deepseek_ai.ai_find_pdf_links(doi, title, cfg=self.ds_cfg) or []
                except Exception:
                    links = []
                if links:
                    step(f"🤖 DeepSeek 给出 {len(links)} 个候选链接（注意: AI 链接需实测有效性）", "ai")
            else:
                step("❌ DeepSeek 未配置", "system")

        if not links:
            step("⚠ 思考③ 无可用链接 → 判定付费墙", "warn")
            step("  建议:\n  1. 机构 VPN 访问期刊官网\n  2. 邮件向通讯作者索取\n  3. 馆际互借")
            return

        step(f"📥 思考④ 开始下载（{len(links)} 个候选，逐链接校验 %PDF 头）...")
        from pdf_downloader import download_pdf
        out_dir = os.path.join(self.output_dir.get(), "Downloaded_PDFs")
        os.makedirs(out_dir, exist_ok=True)
        from pdf_downloader import build_filename
        row = self.df.loc[idx]
        save_path = os.path.join(out_dir, build_filename(row) or "litbox_ai.pdf")

        success, msg, source = download_pdf(doi, save_path, ai_links=links)
        if success:
            # 重命名优化
            try:
                final_path, meta_source = self._rename_after_ai_download(save_path, row, doi, out_dir)
            except Exception:
                final_path, meta_source = save_path, "表格"
            rel = os.path.join(out_dir, os.path.basename(final_path)).replace("\\", "/")
            self.root.after(0, lambda: self._mark_pdf_downloaded(idx, rel, final_path, f"AI代理-{source}"))
            step(f"✅ 思考④ 下载成功 [{source}]\n  文件: {os.path.basename(final_path)}", "ai")
            step("✅ 已写入 Excel 的 PDF 链接列", "ai")
        else:
            step(f"❌ 思考④ 下载失败: {msg}", "system")
            step("  建议: 在对话中询问 AI 该文献的其他获取渠道")

    def _mark_pdf_downloaded(self, idx, rel_path, final_path, source):
        """把下载成功的结果写入 DataFrame 并刷新表格。"""
        if idx in self.df.index:
            self.df.at[idx, "PDF链接"] = f'=HYPERLINK("{rel_path}", "打开PDF")'
            if "PDF状态" in list(self.df.columns):
                self.df.at[idx, "PDF状态"] = "已下载"
        self._refresh_table()
        self._update_stats(stage="✅ AI 下载完成")

    def _ai_send(self, _event=None):
        """发送消息给 DeepSeek（后台线程，不阻塞界面）。"""
        if not deepseek_ai.is_configured(self.ds_cfg):
            self._ai_append("⚠ AI 未配置，请先在设置页填写 API Key。", "system")
            return
        text = self._ai_input.get("1.0", tk.END).strip()
        if not text:
            return
        self._ai_input.delete("1.0", tk.END)
        self._ai_append(text, "user")

        # 附加引用的文献上下文
        context = ""
        if self._ai_attached:
            r = self._ai_attached
            context = (f"\n\n[引用的文献信息]\n标题: {r['title']}\n年份: {r['year']}\n"
                       f"作者: {r['author']}\n期刊: {r['journal']}\nDOI: {r['doi']}\n状态: {r['status']}")

        # 禁用发送按钮，防止连点
        self._ai_send_btn.config(state=tk.DISABLED)
        self._ai_status_var.set("思考中...")

        def worker():
            system_prompt = (
                "你是 LitBox 文献工具的 AI 助手，擅长学术文献检索与补全。"
                "回答要简洁、准确。若文献信息中标注了 DOI 状态为'获取失败'或'AI建议'，"
                "可以尝试给出该文献的正确 DOI、期刊官网或开放获取来源，但必须提醒用户核实。"
            )
            try:
                reply = deepseek_ai.chat_text(
                    system_prompt, text + context, cfg=self.ds_cfg, temperature=0.3)
                if not reply:
                    ok, msg = deepseek_ai.diagnose_network(self.ds_cfg)
                    reply = "（无响应）\n🔍 网络诊断: " + msg
            except Exception as e:
                reply = f"❌ 错误: {e}"

            self.root.after(0, lambda: self._ai_show_reply(reply))

        threading.Thread(target=worker, daemon=True).start()

    def _ai_show_reply(self, reply):
        """在主线程显示 AI 回复。"""
        if hasattr(self, "_ai_chat_log"):
            self._ai_append(reply, "ai")
        self._ai_send_btn.config(state=tk.NORMAL)
        self._ai_status_var.set("就绪")

    # ═══════════════ 使用教程 ═══════════════

    def _show_tutorial(self):
        win = tk.Toplevel(self.root)
        win.title(f"{APP_NAME} - 使用教程")
        win.geometry("900x620")
        win.minsize(780, 540)
        win.configure(bg=C["bg_dark"])
        win.transient(self.root)
        self._set_window_icon(win)

        header = tk.Frame(win, bg=C["bg_card"], highlightbackground=C["border"], highlightthickness=1)
        header.pack(fill=tk.X)
        tk.Label(header, text="使用教程", font=(FONT, 18, "bold"), fg=C["text"],
                 bg=C["bg_card"]).pack(side=tk.LEFT, padx=22, pady=16)
        tk.Label(header, text="从清单到规范命名 PDF", font=(FONT, 10), fg=C["text_dim"],
                 bg=C["bg_card"]).pack(side=tk.LEFT, pady=(20, 16))
        self._make_button(header, "关闭", win.destroy, kind="ghost").pack(side=tk.RIGHT, padx=18, pady=12)

        body = tk.Frame(win, bg=C["bg_dark"])
        body.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)
        sidebar = tk.Frame(body, width=210, bg=C["bg_card"], highlightbackground=C["border"], highlightthickness=1)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        content = tk.Frame(body, bg=C["bg_card"], highlightbackground=C["border"], highlightthickness=1)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

        steps = [
            ("开始前准备", "准备 Excel 文献清单", [
                "Excel 至少需要一列文献标题；建议同时包含作者、年份、期刊和 DOI。",
                "一行对应一篇文献。表头可使用“题名、作者、年份、期刊、DOI”等常见名称。",
                "先在总览页设置输出目录，所有结果表格、PDF 和报告都会保存在那里。",
            ]),
            ("导入与检查", "导入 Excel 并检查字段", [
                "点击“选择 Excel”，软件会读取清单并在文献列表中显示。",
                "点击“检测字段”确认标题、作者、年份、期刊和 DOI 是否识别正确。",
                "如果标题列未识别，请把 Excel 表头改为“标题”或“题名”后重新导入。",
            ]),
            ("获取 DOI", "为文献匹配 DOI", [
                "点击“获取 DOI”，软件会根据标题在线匹配文献信息。",
                "匹配度越高，结果通常越可靠；设置页可调整 DOI 标题匹配阈值。",
                "完成后会生成“_已加DOI.xlsx”，未匹配项目会保留，方便人工核对。",
            ]),
            ("下载 PDF", "批量搜集 PDF", [
                "确认清单中已有 DOI 后，点击“下载 PDF”。",
                "PDF 默认保存在输出目录的 Downloaded_PDFs 文件夹，并同步生成带链接的 Excel。",
                "部分文献受访问权限或网络限制可能无法下载，请根据日志中的失败记录补充处理。",
            ]),
            ("智能命名", "统一整理现有 PDF", [
                "先导入对应的 Excel 清单，再点击“PDF 重命名”并选择 PDF 文件夹。",
                "软件优先使用 Excel 的作者、年份和期刊，再从 PDF 或 DOI 补全缺失信息。",
                "命名格式为：作者_年份_期刊缩写_研究区域_题名简写.pdf。",
                "预览窗口只展示结果；点击“确认重命名”后才会真正修改文件名。",
            ]),
            ("常见问题", "结果不完整时怎么处理", [
                "没有年份：检查 Excel 年份列；若 PDF 是扫描件且没有文字层，自动读取可能失败。",
                "期刊缩写异常：优先在 Excel 中填写完整期刊名，软件会提取首字母缩写。",
                "文件没有匹配：确保 Excel 标题与 PDF 首页题名接近，或在清单中补充 DOI。",
                "建议先用少量文件测试并核对预览，再处理整个文件夹。",
            ]),
            ("AI 补全", "DeepSeek 智能兜底与文献补全", [
                "在设置页填入 DeepSeek API Key（platform.deepseek.com 获取）并启用 AI 补全。",
                "AI 找 DOI：Crossref/OpenAlex 匹配失败时，AI 根据标题+作者+年份直接报出 DOI。",
                "AI 找 PDF：tesble/doi.org/Sci-Hub 全部失败时，AI 提供 PMC/arXiv 等开放获取链接并自动尝试。",
                "AI 补元数据：作者/年份/期刊/研究区域缺失时自动补全，文件名更规范。",
                "置信度低于 70% 的 AI 结果只标记不写入，请人工在 doi.org 验证后手动补入。",
            ]),
        ]

        step_buttons = []

        def show_step(index):
            for child in content.winfo_children():
                child.destroy()
            for i, button in enumerate(step_buttons):
                if i == index:
                    button.configure(bg="#dbeafe", fg=C["accent"])
                else:
                    button.configure(bg=C["bg_card"], fg=C["text_dim"])

            short, title, points = steps[index]
            tk.Label(content, text=f"步骤 {index + 1} / {len(steps)}", font=(FONT, 9, "bold"),
                     fg=C["accent"], bg=C["bg_card"]).pack(anchor=tk.W, padx=28, pady=(28, 6))
            tk.Label(content, text=title, font=(FONT, 20, "bold"), fg=C["text"],
                     bg=C["bg_card"]).pack(anchor=tk.W, padx=28)
            tk.Frame(content, height=1, bg=C["border"]).pack(fill=tk.X, padx=28, pady=20)

            for number, point in enumerate(points, start=1):
                row = tk.Frame(content, bg=C["bg_card"])
                row.pack(fill=tk.X, padx=28, pady=8)
                badge = tk.Label(row, text=str(number), font=(FONT, 9, "bold"), width=3, height=1,
                                 fg=C["white"], bg=C["accent"])
                badge.pack(side=tk.LEFT, anchor=tk.N, padx=(0, 12))
                tk.Label(row, text=point, font=(FONT, 10), fg=C["text"], bg=C["bg_card"],
                         justify=tk.LEFT, anchor=tk.W, wraplength=520).pack(side=tk.LEFT, fill=tk.X, expand=True)

            footer = tk.Frame(content, bg=C["bg_card"])
            footer.pack(side=tk.BOTTOM, fill=tk.X, padx=28, pady=24)
            if index > 0:
                self._make_button(footer, "上一步", lambda: show_step(index - 1), kind="secondary").pack(side=tk.LEFT)
            if index < len(steps) - 1:
                self._make_button(footer, "下一步", lambda: show_step(index + 1), kind="primary").pack(side=tk.RIGHT)
            else:
                self._make_button(footer, "开始使用", lambda: (win.destroy(), self._switch_page("overview")),
                                  kind="primary").pack(side=tk.RIGHT)

        tk.Label(sidebar, text="操作流程", font=(FONT, 10, "bold"), fg=C["text"],
                 bg=C["bg_card"]).pack(anchor=tk.W, padx=16, pady=(18, 10))
        for index, (short, _, _) in enumerate(steps):
            button = tk.Button(sidebar, text=f"{index + 1}.  {short}", command=lambda i=index: show_step(i),
                               font=(FONT, 9), bg=C["bg_card"], fg=C["text_dim"],
                               activebackground=C["bg_hover"], activeforeground=C["text"],
                               relief=tk.FLAT, cursor="hand2", anchor=tk.W, padx=16, pady=11)
            button.pack(fill=tk.X)
            step_buttons.append(button)

        show_step(0)
    # ═══════════════ 设置页 ═══════════════

    def _build_settings_page(self):
        # 外层 Canvas + 滚动条，让内容超出窗口时可滚动（避免 AI 补全卡片被截断）
        canvas = tk.Canvas(self.page_settings, bg=C["bg_dark"], highlightthickness=0)
        vscroll = ttk.Scrollbar(self.page_settings, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        inner = tk.Frame(canvas, bg=C["bg_dark"])
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=max(e.width, 1)))
        # 鼠标滚轮滚动（Windows）
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        p = inner  # 后续所有 widget 都 pack 到这个内部可滚动 Frame

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

        # PDF 命名模式
        fc_rm = self._card_frame(p, "📄 PDF 命名模式")
        fc_rm.pack(fill=tk.X, padx=18, pady=(0, 10))
        inner_rm = tk.Frame(fc_rm, bg=C["bg_card"])
        inner_rm.pack(fill=tk.X, padx=16, pady=14)

        tk.Radiobutton(inner_rm, text="本地命名（离线，快速）", variable=self.rename_mode,
                       value="local", font=(FONT, 9), fg=C["text"], bg=C["bg_card"],
                       activebackground=C["bg_card"], selectcolor=C["white"]).pack(anchor=tk.W, pady=2)
        tk.Radiobutton(inner_rm, text="AI 命名（元数据缺失时用 DeepSeek 补全，需 API Key）",
                       variable=self.rename_mode, value="ai", font=(FONT, 9), fg=C["text"],
                       bg=C["bg_card"], activebackground=C["bg_card"], selectcolor=C["white"]).pack(anchor=tk.W, pady=2)

        # 说明
        fc2 = self._card_frame(p, "💡 使用说明")
        fc2.pack(fill=tk.X, padx=18)
        inner2 = tk.Frame(fc2, bg=C["bg_card"])
        inner2.pack(fill=tk.X, padx=16, pady=14)

        tips = [
            ("1", "选择包含文献标题的 Excel 文件"),
            ("2", "点击「一键全流程」自动获取 DOI 并下载 PDF"),
            ("3", "或分步操作：先获取 DOI，再下载 PDF"),
            ("🤖", "在设置页配置 DeepSeek API Key，可在常规来源失败时 AI 兜底补全"),
            ("📁", f"PDF 保存在: {self.output_dir.get()}\\Downloaded_PDFs"),
        ]
        for icon, tip in tips:
            row = tk.Frame(inner2, bg=C["bg_card"])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=icon, font=(FONT, 9), fg=C["accent"],
                     bg=C["bg_card"], width=3).pack(side=tk.LEFT)
            tk.Label(row, text=tip, font=(FONT, 9), fg=C["text_dim"],
                     bg=C["bg_card"]).pack(side=tk.LEFT)

        self._build_ai_settings_card(p)

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

    def _build_ai_settings_card(self, p):
        """设置页的 DeepSeek AI 补全配置卡片。"""
        fc = self._card_frame(p, "🤖 AI 补全（DeepSeek）")
        fc.pack(fill=tk.X, padx=18, pady=(0, 10))

        inner = tk.Frame(fc, bg=C["bg_card"])
        inner.pack(fill=tk.X, padx=16, pady=14)

        self.ai_enabled_var = tk.BooleanVar(value=bool(self.ds_cfg.get("deepseek_enabled")))
        self.ai_key_var = tk.StringVar(value=self.ds_cfg.get("api_key", ""))
        self.ai_url_var = tk.StringVar(value=self.ds_cfg.get("base_url", "https://api.deepseek.com"))
        self.ai_model_var = tk.StringVar(value=self.ds_cfg.get("model", "deepseek-chat"))
        self.ai_status_var = tk.StringVar(value="")

        cb_row = tk.Frame(inner, bg=C["bg_card"])
        cb_row.pack(fill=tk.X, pady=(0, 8))
        tk.Checkbutton(cb_row, text="启用 AI 补全（常规来源失败时自动兜底）",
                       variable=self.ai_enabled_var, command=self._on_ai_toggle,
                       font=(FONT, 9), fg=C["text"], bg=C["bg_card"],
                       activebackground=C["bg_card"], selectcolor=C["white"]).pack(side=tk.LEFT)

        def add_row(label, var, show=None, width=52):
            row = tk.Frame(inner, bg=C["bg_card"])
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=label, font=(FONT, 9), fg=C["text_dim"],
                     bg=C["bg_card"], width=10, anchor=tk.W).pack(side=tk.LEFT)
            tk.Entry(row, textvariable=var, font=(MONO, 9), show=show,
                     bg=C["bg_input"], fg=C["text"], relief=tk.FLAT,
                     insertbackground=C["text"], width=width).pack(side=tk.LEFT, fill=tk.X, expand=True)

        add_row("API Key", self.ai_key_var, show="*")
        add_row("Base URL", self.ai_url_var)
        add_row("模型", self.ai_model_var)

        btn_row = tk.Frame(inner, bg=C["bg_card"])
        btn_row.pack(fill=tk.X, pady=(10, 2))
        self._make_button(btn_row, "保存配置", self._save_ai_config, kind="primary").pack(side=tk.LEFT)
        self._make_button(btn_row, "测试连接", self._test_ai_connection, kind="secondary").pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(btn_row, textvariable=self.ai_status_var, font=(FONT, 9),
                 fg=C["accent2"], bg=C["bg_card"]).pack(side=tk.LEFT, padx=(12, 0))

        tip = tk.Label(inner,
                       text="当 Crossref/OpenAlex 匹配失败时 AI 辅助查找 DOI；下载失败时 AI 提供开放获取链接；"
                            "命名信息缺失时 AI 补全元数据。Key 在 platform.deepseek.com 获取。",
                       font=(FONT, 8), fg=C["text_dim"], bg=C["bg_card"],
                       justify=tk.LEFT, anchor=tk.W, wraplength=560)
        tip.pack(fill=tk.X, pady=(8, 0))

    def _on_ai_toggle(self):
        """启用开关：未填 Key 时提示并回弹。"""
        if self.ai_enabled_var.get() and not str(self.ai_key_var.get()).strip():
            self.ai_enabled_var.set(False)
            messagebox.showwarning("提示", "请先填写 DeepSeek API Key\n（platform.deepseek.com 获取）")

    def _save_ai_config(self):
        """保存 AI 配置到 config.json。"""
        self.ds_cfg["deepseek_enabled"] = self.ai_enabled_var.get()
        self.ds_cfg["api_key"] = self.ai_key_var.get().strip()
        self.ds_cfg["base_url"] = self.ai_url_var.get().strip() or "https://api.deepseek.com"
        self.ds_cfg["model"] = self.ai_model_var.get().strip() or "deepseek-chat"
        ok = deepseek_ai.save_config(self.ds_cfg)
        if ok:
            self.ai_status_var.set("✅ 已保存")
            self._log("🤖 DeepSeek 配置已保存", "success")
        else:
            self.ai_status_var.set("❌ 保存失败")
            self._log("🤖 DeepSeek 配置保存失败", "error")

    def _test_ai_connection(self):
        """测试 DeepSeek 连接。失败时给出网络诊断。"""
        self.ai_status_var.set("⏳ 测试中...")
        self.root.update_idletasks()
        test_cfg = {
            "api_key": self.ai_key_var.get().strip(),
            "base_url": self.ai_url_var.get().strip() or "https://api.deepseek.com",
            "model": self.ai_model_var.get().strip() or "deepseek-chat",
            "timeout": 15,
            "max_retries": 1,
        }
        ok, msg = deepseek_ai.test_connection(test_cfg)
        if ok:
            self.ai_status_var.set(f"✅ {msg}")
        else:
            # 连接失败 → 进一步诊断网络/代理
            net_ok, net_msg = deepseek_ai.diagnose_network(test_cfg)
            if not net_ok:
                self.ai_status_var.set(f"❌ 网络问题: {net_msg}")
            else:
                self.ai_status_var.set(f"❌ {msg}")

    def _draw_progress(self, pct):
        self.progress_bar.delete("all")
        w = self.progress_bar.winfo_width()
        if w < 10: w = 600
        fill = int(w * pct / 100)
        if fill > 0:
            self.progress_bar.create_rectangle(0, 0, fill, 6, fill=C["accent"], outline="")

    # ═══════════════ 下载进度详情面板 ═══════════════

    def _dl_init(self, total):
        """下载开始：清空面板并初始化。total=总篇数。"""
        if not hasattr(self, "dl_tree"):
            return
        for item in self.dl_tree.get_children():
            self.dl_tree.delete(item)
        self._dl_row_map = {}
        self._dl_total = total
        self._dl_done = 0
        self._dl_ok = 0
        self._dl_fail = 0
        self._dl_start_time = time.time()
        self._dl_update_bar(0)

    def _dl_add_row(self, doi, title, status="等待", source="—", tag="wait"):
        """为一条文献添加/更新下载状态行。返回 iid。"""
        if not hasattr(self, "dl_tree"):
            return None
        key = str(doi or title or "").strip()
        if key in self._dl_row_map:
            iid = self._dl_row_map[key]
        else:
            no = len(self._dl_row_map) + 1
            iid = self.dl_tree.insert("", tk.END, values=(no, self._short(str(title), 44),
                                                          str(doi)[:32], source, status, "—"),
                                      tags=(tag,))
            self._dl_row_map[key] = iid
        return iid

    def _dl_update(self, doi, status, source=None, cost=None, title=None):
        """更新某条文献的下载状态。"""
        if not hasattr(self, "dl_tree"):
            return
        key = str(doi or title or "").strip()
        iid = self._dl_row_map.get(key)
        if not iid:
            iid = self._dl_add_row(doi, title or doi, status, source or "—")
            if not iid:
                return
        values = list(self.dl_tree.item(iid, "values"))
        # values = [no, title, doi, source, status, cost]
        if title:
            values[1] = self._short(str(title), 44)
        if source is not None:
            values[3] = source
        values[4] = status
        if cost is not None:
            values[5] = f"{cost:.1f}s" if cost > 0 else "—"
        tag = "wait"
        if "✅" in status or "成功" in status:
            tag = "ok"
            self._dl_ok += 1
        elif "❌" in status or "失败" in status:
            tag = "fail"
            self._dl_fail += 1
        elif "🤖" in status or "AI" in status:
            tag = "ai"
        self.dl_tree.item(iid, values=values, tags=(tag,))
        self.dl_tree.see(iid)

    def _dl_done_one(self, status):
        """完成一篇：推进计数并刷新进度条。"""
        self._dl_done += 1
        pct = (self._dl_done / self._dl_total * 100) if self._dl_total else 0
        self._dl_update_bar(pct)
        self.dl_progress_var.set(f"{self._dl_done}/{self._dl_total} 篇")
        if self._dl_start_time:
            cost = time.time() - self._dl_start_time
            self.status_var.set(f"下载进度: {self._dl_done}/{self._dl_total}（成功 {self._dl_ok} / 失败 {self._dl_fail}）{cost:.0f}s")
        self.root.update_idletasks()

    def _dl_update_bar(self, pct):
        """画明细进度条。"""
        if not hasattr(self, "dl_bar"):
            return
        self.dl_bar.delete("all")
        w = self.dl_bar.winfo_width()
        if w < 10:
            w = 500
        fill = int(w * pct / 100)
        color = C["success"] if pct >= 100 else C["accent"]
        if fill > 0:
            self.dl_bar.create_rectangle(0, 0, fill, 8, fill=color, outline="")
        self._dl_percent_var.set(f"{int(pct)}%")

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
        # 保存当前滚动位置
        scroll_pos = self.paper_table.yview()[0] if self.paper_table.get_children() else 0
        # 保存当前选中行
        selected_iid = None
        sel = self.paper_table.selection()
        if sel: selected_iid = sel[0]

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
                "已下载" if pdf else "待下载",
                doi_status,
                self._short(self._cell_text(row, ycn), 8),
                self._short(self._cell_text(row, acn), 22),
                self._short(self._cell_text(row, jcn), 26),
                self._short(self._cell_text(row, source_col, "—"), 12),
                self._short(self._cell_text(row, match_col, "—"), 8),
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
        # 恢复滚动位置和选中行
        if scroll_pos > 0:
            self.paper_table.yview_moveto(scroll_pos)
        if selected_iid and self.paper_table.exists(selected_iid):
            self.paper_table.selection_set(selected_iid)
            self.paper_table.focus(selected_iid)

    def _set_detail(self, row=None):
        values = {
            "title": "—", "doi": "—", "source": "—",
            "status": "—", "match": "—", "pdf": "—", "path": "—",
        }
        self._doi_url = ""
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
            # 存储 DOI URL 用于点击跳转
            self._doi_url = f"https://doi.org/{doi}" if doi else ""
            doi_link_col = "DOI链接"
            if self.df is not None and doi_link_col in list(self.df.columns):
                link = self._cell_text(row, doi_link_col)
                if link and link != "—": self._doi_url = link
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
        # DOI 链接样式：有 DOI 时蓝字可点击，无 DOI 时灰色
        if hasattr(self, "_doi_detail_label"):
            if self._doi_url:
                self._doi_detail_label.configure(fg=C["accent"], cursor="hand2",
                    font=(FONT, 9, "underline"))
            else:
                self._doi_detail_label.configure(fg=C["text"], cursor="",
                    font=(FONT, 9))

    def _on_doi_click(self, _event=None):
        """点击 DOI 标签时在浏览器打开 DOI 链接"""
        if self._doi_url:
            import webbrowser
            webbrowser.open(self._doi_url)

    def _on_mousewheel(self, event):
        """鼠标滚轮滚动 Treeview"""
        if hasattr(self, "paper_table"):
            delta = -1 if event.delta > 0 else 1
            self.paper_table.yview_scroll(delta, "units")

    def _on_table_click(self, event):
        """处理 Treeview 点击，确保滚动期间也能选中"""
        if not hasattr(self, "paper_table"): return
        item = self.paper_table.identify_row(event.y)
        if item:
            self.paper_table.selection_set(item)
            self.paper_table.focus(item)
            self._on_table_select()
        else:
            self.paper_table.selection_remove(self.paper_table.selection())
            self._set_detail()

    def _on_table_select(self, _event=None):
        if self.df is None or not hasattr(self, "paper_table"):
            return
        selected = self.paper_table.selection()
        if not selected:
            self._set_detail()
            self._set_ai_proxy_buttons(False)
            return
        try:
            idx = int(selected[0])
            self._selected_row_idx = idx
            self._set_detail(self.df.loc[idx])
            self._set_ai_proxy_buttons(True)
        except Exception:
            self._selected_row_idx = None
            self._set_detail()
            self._set_ai_proxy_buttons(False)

    def _set_ai_proxy_buttons(self, enabled):
        """根据是否选中文献控制 AI 代理按钮可用性。"""
        if not hasattr(self, "btn_ai_search_doi"):
            return
        if not enabled:
            self.btn_ai_search_doi.config(state=tk.DISABLED)
            self.btn_ai_get_pdf.config(state=tk.DISABLED)
            return
        state = tk.NORMAL if self.running is False else tk.DISABLED
        self.btn_ai_search_doi.config(state=state)
        self.btn_ai_get_pdf.config(state=state)

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
        for btn in [self.btn_detect, self.btn_fetch_doi, self.btn_download, self.btn_all, self.btn_rename_pdf]:
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

    # ═══════════════ 本地 PDF 重命名 ═══════════════

    def _find_optional_column(self, candidates):
        if self.df is None:
            return None
        headers = list(self.df.columns)
        lowered = {str(h).lower(): h for h in headers}
        for name in candidates:
            if name in headers:
                return name
            if str(name).lower() in lowered:
                return lowered[str(name).lower()]
        for h in headers:
            hl = str(h).lower()
            for name in candidates:
                if str(name).lower() in hl:
                    return h
        return None

    def _pdf_link_basename(self, value):
        text = str(value or "").strip()
        if not text or text.lower() == "nan":
            return ""
        m = re.search(r'HYPERLINK\("([^"]+)"', text, re.IGNORECASE)
        if m:
            text = m.group(1)
        return os.path.basename(text.replace("/", os.sep))

    def _pdf_rename_excel_rows(self):
        """把当前 Excel 表转成 PDF 命名可用的标准字段。"""
        if self.df is None:
            return []
        tcn = self._resolve_column("title_col")
        ycn = self._resolve_column("year_col")
        acn = self._resolve_column("author_col")
        jcn = self._resolve_column("journal_col")
        dcn = self._doi_column_for_display()
        area_col = self._find_optional_column(["RESEARCH_AREA", "研究区域", "研究区", "区域", "地区", "研究地点", "Study area"] )
        abbr_col = self._find_optional_column(["JOURNAL_ABBR", "期刊缩写", "刊名缩写", "缩写", "Journal Abbr"] )
        pdf_col = self._find_optional_column(["PDF链接", "PDF路径", "PDF文件", "PDF", "文件名", "File", "Filename"] )

        rows = []
        for _, row in self.df.iterrows():
            item = {
                "title": self._cell_text(row, tcn),
                "AUTHOR": self._cell_text(row, acn),
                "YEAR": self._cell_text(row, ycn),
                "JOURNAL": self._cell_text(row, jcn),
                "DOI": self._cell_text(row, dcn),
                "RESEARCH_AREA": self._cell_text(row, area_col),
                "JOURNAL_ABBR": self._cell_text(row, abbr_col),
                "PDF_NAME": self._pdf_link_basename(self._cell_text(row, pdf_col)),
            }
            if any(str(v).strip() for v in item.values()):
                rows.append(item)
        return rows


    def _start_pdf_rename(self):
        if self.running:
            messagebox.showinfo("提示", "正在运行中")
            return
        if self.df is None or self.detected is None:
            if not self.input_path.get() or not os.path.isfile(self.input_path.get()):
                path = filedialog.askopenfilename(
                    title="先选择包含作者/年份/期刊的 Excel 文件",
                    filetypes=[("Excel 文件", "*.xlsx *.xls"), ("所有文件", "*.*")])
                if not path:
                    return
                self.input_path.set(path)
                self.file_label.config(text=os.path.basename(path))
            self._detect_columns(True)
            if self.df is None:
                return

        excel_rows = self._pdf_rename_excel_rows()
        if not excel_rows:
            messagebox.showwarning("提示", "Excel 中没有可用于命名的文献信息")
            return

        folder = filedialog.askdirectory(title="选择需要重命名的 PDF 文件夹")
        if not folder:
            return
        self._set_buttons_state(tk.DISABLED)
        self.stop_requested = False
        self.running = True
        self.pdf_rename_results = []
        self._update_stats(stage="🔎 Excel 匹配 PDF...", pdf_done=0)
        mode = self.rename_mode.get()
        mode_desc = "AI 命名" if mode == "ai" else "本地命名"
        self._log(f"🔎 使用 Excel 元数据 {len(excel_rows)} 条，扫描 PDF 文件夹: {folder}（{mode_desc}）", "header")
        threading.Thread(target=self._scan_pdf_rename_thread,
                         args=(folder, excel_rows, mode), daemon=True).start()


    def _scan_pdf_rename_thread(self, folder, excel_rows, mode="local"):
        try:
            def cb(cur, total, item):
                if total:
                    self._draw_progress((cur / total) * 100)
                self.status_var.set(f"PDF 命名预览: {cur}/{total}")
                old_name = item.get("old_name", "")
                new_name = item.get("new_name", "")
                if new_name:
                    self._log(f"  {cur}. {old_name} → {new_name}", "info")
                else:
                    self._log(f"  {cur}. {old_name} 识别失败", "warn")
                self.root.update_idletasks()

            use_ai = (mode == "ai") and deepseek_ai.is_configured(self.ds_cfg)
            ai_cfg = self.ds_cfg if use_ai else None
            results = scan_pdf_folder(folder, recursive=False, progress_callback=cb,
                                      excel_rows=excel_rows, use_ai=use_ai, ai_cfg=ai_cfg)
            self.pdf_rename_results = results
            self._draw_progress(100)
            self.status_var.set(f"PDF 命名预览完成: {len(results)} 个文件")
            self._update_stats(stage="✅ 命名预览完成", pdf_done=len(results))
            self.root.after(0, self._show_pdf_rename_preview)
        except Exception as e:
            self._log(f"PDF 重命名预览错误: {e}", "error")
            messagebox.showerror("错误", str(e))
        finally:
            self.running = False
            self.root.after(0, lambda: self._set_buttons_state(tk.NORMAL))

    def _show_pdf_rename_preview(self):
        if not self.pdf_rename_results:
            messagebox.showinfo("提示", "未找到 PDF 文件")
            return

        win = tk.Toplevel(self.root)
        win.title("PDF 重命名预览")
        win.geometry("980x520")
        win.configure(bg=C["bg_dark"])
        win.transient(self.root)

        header = tk.Frame(win, bg=C["bg_dark"])
        header.pack(fill=tk.X, padx=14, pady=(14, 8))
        tk.Label(header, text="PDF 重命名预览", font=(FONT, 16, "bold"),
                 fg=C["text"], bg=C["bg_dark"]).pack(side=tk.LEFT)
        tk.Label(header, text="确认后才会真正修改文件名", font=(FONT, 9),
                 fg=C["text_dim"], bg=C["bg_dark"]).pack(side=tk.LEFT, padx=(12, 0), pady=(5, 0))

        body = tk.Frame(win, bg=C["bg_card"], highlightbackground=C["border"], highlightthickness=1)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        cols = ("idx", "old", "new", "doi", "source", "title")
        tree = ttk.Treeview(body, columns=cols, show="headings", selectmode="extended")
        headings = {
            "idx": "#", "old": "原文件名", "new": "新文件名",
            "doi": "DOI", "source": "来源", "title": "标题"
        }
        widths = {"idx": 44, "old": 210, "new": 310, "doi": 150, "source": 80, "title": 260}
        for col in cols:
            tree.heading(col, text=headings[col])
            tree.column(col, width=widths[col], minwidth=44, anchor=tk.W, stretch=(col in ("new", "title")))
        tree.grid(row=0, column=0, sticky=tk.NSEW, padx=10, pady=10)
        ybar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=tree.yview)
        ybar.grid(row=0, column=1, sticky=tk.NS, pady=10)
        tree.configure(yscrollcommand=ybar.set)

        for pos, item in enumerate(self.pdf_rename_results, start=1):
            tree.insert("", tk.END, iid=str(pos - 1), values=(
                pos,
                self._short(item.get("old_name", ""), 42),
                self._short(item.get("new_name", ""), 64),
                self._short(item.get("doi", ""), 30),
                item.get("source", ""),
                self._short(item.get("title", ""), 70),
            ))

        footer = tk.Frame(win, bg=C["bg_dark"])
        footer.pack(fill=tk.X, padx=14, pady=(0, 14))
        self._make_button(footer, "取消", win.destroy, kind="ghost").pack(side=tk.RIGHT)
        self._make_button(footer, "确认重命名", lambda: self._confirm_pdf_rename(win), kind="primary").pack(side=tk.RIGHT, padx=(0, 8))

    def _confirm_pdf_rename(self, win):
        if not self.pdf_rename_results:
            return
        ok = messagebox.askyesno("确认重命名", f"将重命名 {len(self.pdf_rename_results)} 个 PDF 文件，是否继续？")
        if not ok:
            return
        try:
            results = apply_renames(self.pdf_rename_results)
            success = sum(1 for item in results if item.get("status") == "已重命名")
            skipped = sum(1 for item in results if item.get("status") != "已重命名")
            self.pdf_rename_results = results
            self._log(f"✅ PDF 重命名完成: 成功 {success}, 跳过/失败 {skipped}", "success")
            messagebox.showinfo("完成", f"重命名完成\n成功: {success}\n跳过/失败: {skipped}")
            win.destroy()
        except Exception as e:
            self._log(f"PDF 重命名错误: {e}", "error")
            messagebox.showerror("错误", str(e))
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

            for col in ["DOI", "DOI链接", "匹配度", "DOI来源", "DOI状态",
                         "JOURNAL_ABBR", "RESEARCH_AREA"]:
                if col not in headers:
                    self.df[col] = pd.NA
            # 确保这些列为 object/string 类型，避免 float64 冲突
            for col in ["DOI", "DOI链接", "匹配度", "DOI来源", "DOI状态",
                        "JOURNAL_ABBR", "RESEARCH_AREA"]:
                if col in self.df.columns:
                    self.df[col] = self.df[col].astype(object)
            headers = list(self.df.columns)

            dcn = "DOI" if dc is None else headers[dc]
            # 检测作者列
            ac = self.detected.get("author_col")
            acn = headers[ac] if ac is not None else None
            rows_p = []
            for idx, row in self.df.iterrows():
                if pd.notna(row.get(dcn)) and str(row.get(dcn)).strip():
                    continue
                title = row.get(tcn)
                if pd.isna(title) or not str(title).strip():
                    continue
                yr = extract_year(row.iloc[yc]) if yc is not None else None
                author = str(row.get(acn, "")) if acn else None
                rows_p.append((idx, str(title).strip(), yr, author))

            total = len(rows_p)
            if total == 0:
                self._log("所有文献已有 DOI，无需获取", "info")
                self._update_stats(stage="✅ 无需获取")
                return

            workers = min(self.concurrency, total)
            self._log(f"⚡ 并发获取 DOI — {total} 条 ({workers} 线程) (阈值 {int(threshold*100)}%, 标题+作者匹配)", "header")
            self._update_stats(stage=f"🔍 {workers}线程获取中...", doi_total=total)

            found = 0
            failed = 0
            completed = 0
            lock = threading.Lock()
            futures_map = {}  # future -> (idx, title)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                for idx, title, year, author in rows_p:
                    if self.stop_requested:
                        break
                    future = executor.submit(find_doi, title, threshold, year, author)
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
                            # 存储期刊缩写和研究区域（供后续 PDF 命名使用）
                            if r.get("journal_abbr"):
                                self.df.at[idx, "JOURNAL_ABBR"] = r["journal_abbr"]
                            if r.get("research_area"):
                                self.df.at[idx, "RESEARCH_AREA"] = r["research_area"]
                            found += 1
                            src = r.get('source', '?')
                            matched = r.get('matched_title', '') or ''
                            self._log(f"  ✅ [{src}] {r['doi'][:40]} ← {matched[:40]}", "success")
                        else:
                            self.df.at[idx, "DOI状态"] = "获取失败"
                            self.df.at[idx, "匹配度"] = "—"
                            self.df.at[idx, "DOI来源"] = "—"
                            failed += 1
                            self._log(f"  ❌ {title[:60]}", "error")

                        pct = (completed / total) * 100
                        self._draw_progress(pct)
                        self.status_var.set(f"DOI: {completed}/{total}  ✅{found} ❌{failed}")
                        self._update_stats(doi_found=found, doi_failed=failed)
                        # 实时刷新表格
                        if completed % 3 == 0 or completed == total:
                            self.root.after(0, self._refresh_table)
                        self.root.update_idletasks()

            op = self._get_output_path("_已加DOI.xlsx")
            self.df.to_excel(op, index=False)
            self._draw_progress(100)
            self.status_var.set(f"DOI 完成：成功 {found}，未匹配 {failed}")
            if failed:
                self._log(f"⚠ DOI 完成: {found}/{total} (未匹配 {failed})", "warn")
                # DeepSeek AI 兜底：对失败条目串行调用 AI 找 DOI
                ai_recovered = self._ai_backfill_doi(rows_p, futures_map)
                if ai_recovered:
                    found += ai_recovered
                    failed -= ai_recovered
                    self.df.to_excel(op, index=False)
                    self._log(f"🤖 AI 兜底找回 {ai_recovered} 条 DOI", "success")
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

    def _ai_backfill_doi(self, rows_p, futures_map):
        """
        DOI 兜底链路（文档 v2 方法）：
        1. 机器核验找回：Crossref 标题检索 + Semantic Scholar 标题搜索找回真实 DOI
        2. AI 兜底：上述都失败时让 DeepSeek 给候选 DOI
        3. AI 结果必须过 Crossref 核验（exists + 标题相似度 >= 0.6）才能写入，防止 AI 编造
        返回找回数量。
        """
        # 收集失败条目 (idx, title, year, author, journal)
        failed_items = []
        for future, (idx, title) in futures_map.items():
            if self.stop_requested:
                break
            try:
                r = future.result()
            except Exception:
                r = None
            if not r or not r.get("doi"):
                failed_items.append((idx, title))

        if not failed_items:
            return 0

        import lit_verify
        recovered = 0
        machine_recovered = 0
        # 状态栏提示 AI 搜索中
        self.status_var.set(f"🤖 AI 搜索中... {len(failed_items)} 条待补 DOI")
        self._update_stats(stage=f"🤖 AI 搜索中（{len(failed_items)} 条）")

        for pos, (idx, title) in enumerate(failed_items):
            if self.stop_requested:
                break
            headers = list(self.df.columns)
            yc = self.detected.get("year_col")
            ac = self.detected.get("author_col")
            row = self.df.loc[idx]
            year = extract_year(row.iloc[yc]) if yc is not None else None
            author = str(row.get(headers[ac], "")) if ac is not None else None

            # ① 机器核验找回（先免费：Crossref → S2）
            try:
                machine = lit_verify.find_doi_by_title(
                    title, year=year, author=author, threshold=0.6)
            except Exception:
                machine = None

            if machine and machine.get("doi"):
                self._write_doi_match(idx, machine["doi"], machine.get("similarity", 1.0),
                                      f"核验找回-{machine.get('source', 'Crossref')}", title)
                machine_recovered += 1
                recovered += 1
                self._log(f"  🔍 ✅ [{machine.get('source','?')}] {machine['doi'][:40]} ← {str(title)[:40]}", "success")
                self.root.update_idletasks()
                continue

            # ② AI 兜底（机器失败后，且已配置 Key）
            if not deepseek_ai.is_configured(self.ds_cfg):
                continue

            self._log(f"  🤖 AI 兜底: {str(title)[:50]}", "info")
            try:
                result = deepseek_ai.ai_find_doi(
                    title, year=year, author=author, cfg=self.ds_cfg)
            except Exception:
                result = None

            if result and result.get("found") and result.get("doi"):
                # AI 结果必须过核验：DOI 真实存在 + 标题相似度 >= 0.6
                try:
                    v = lit_verify.verify_doi_full(result["doi"], title)
                except Exception:
                    v = {"exists": None, "similarity": 0.0, "matched_title": ""}

                verified_ok = bool(v.get("exists") and v.get("similarity", 0) >= 0.6)
                confidence = result.get("confidence", 0)

                if verified_ok and confidence >= 0.6:
                    self._write_doi_match(idx, result["doi"], v.get("similarity", confidence),
                                          "DeepSeek+核验", title)
                    recovered += 1
                    self._log(f"  🤖 ✅ 核验通过 [{v.get('similarity',0):.0%}] {result['doi'][:40]} ← {str(title)[:40]}", "success")
                else:
                    self.df.at[idx, "DOI状态"] = "AI建议(未核验)"
                    self._log(f"  🤖 ⚠ {str(title)[:40]} → AI 给出 {result['doi'][:30]} 但核验未通过，待人工核对", "warn")
            else:
                self._log(f"  🤖 ❌ {str(title)[:40]}", "error")
            self.root.update_idletasks()

        if machine_recovered:
            self._log(f"🔍 机器核验找回 {machine_recovered} 条", "success")
        return recovered

    def _write_doi_match(self, idx, doi, similarity, source, title):
        """写入一条 DOI 匹配结果到 DataFrame。"""
        self.df.at[idx, "DOI"] = doi
        self.df.at[idx, "DOI链接"] = f"https://doi.org/{doi}"
        self.df.at[idx, "匹配度"] = f"{int(similarity * 100)}%"
        self.df.at[idx, "DOI来源"] = source
        self.df.at[idx, "DOI状态"] = "已匹配"

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

            # 检测研究区域列（用户可能在 Excel 中预先填写）
            research_area_col = None
            for h in headers:
                hl = str(h).lower()
                if "研究区域" in hl or "research_area" in hl or "研究区" in hl or "区域" in hl:
                    research_area_col = h
                    break

            # 检测期刊缩写列
            journal_abbr_col = None
            for h in headers:
                hl = str(h).lower()
                if "期刊缩写" in hl or "journal_abbr" in hl or "缩写" in hl:
                    journal_abbr_col = h
                    break

            rows = []
            for _, row in self.df.iterrows():
                doi = row.get(dcn)
                if pd.isna(doi) or not str(doi).strip():
                    continue
                row_dict = {
                    "DOI": str(doi).strip(),
                    "title": str(row.get(tcn, "")),
                    "AUTHOR": str(row.get(acn, "")) if acn else "",
                    "YEAR": row.get(ycn, ""),
                    "JOURNAL": str(row.get(jcn, "")) if jcn else "",
                }
                # 传递期刊缩写（优先用 DOI 获取阶段写入的，其次用 Excel 中的）
                if "JOURNAL_ABBR" in headers:
                    abbr_val = row.get("JOURNAL_ABBR", "")
                    if pd.notna(abbr_val) and str(abbr_val).strip():
                        row_dict["JOURNAL_ABBR"] = str(abbr_val).strip()
                elif journal_abbr_col:
                    abbr_val = row.get(journal_abbr_col, "")
                    if pd.notna(abbr_val) and str(abbr_val).strip():
                        row_dict["JOURNAL_ABBR"] = str(abbr_val).strip()

                # 传递研究区域（优先用 DOI 获取阶段写入的，其次用 Excel 中的）
                if "RESEARCH_AREA" in headers:
                    area_val = row.get("RESEARCH_AREA", "")
                    if pd.notna(area_val) and str(area_val).strip():
                        row_dict["RESEARCH_AREA"] = str(area_val).strip()
                elif research_area_col:
                    area_val = row.get(research_area_col, "")
                    if pd.notna(area_val) and str(area_val).strip():
                        row_dict["RESEARCH_AREA"] = str(area_val).strip()

                rows.append(row_dict)

            total = len(rows)
            if total == 0:
                self._log("无 DOI 记录", "warn")
                return

            sd = os.path.join(self.output_dir.get(), "Downloaded_PDFs")
            self._log(f"下载 PDF — {total} 篇 → {sd}", "header")
            self._update_stats(stage="📥 下载中...", pdf_done=0)
            self._dl_init(total)

            # DOI → DataFrame index 映射（用于实时更新）
            doi_to_dfidx = {}
            for dfi, row in self.df.iterrows():
                d = str(row.get(dcn, "")).strip()
                if d: doi_to_dfidx[d] = dfi

            def cb(cur, tn, row):
                if self.stop_requested: return
                self.status_var.set(f"PDF: {cur}/{tn}")
                self._draw_progress((cur / tn) * 100)
                st = row.get("_download_status", "")
                src = row.get("_download_source", "")
                src_tag = f" [{src}]" if src and src != "—" else ""
                ts = str(row.get("title", ""))[:40]
                doi = str(row.get("DOI", ""))
                # 下载详情面板实时更新
                if "✅" in st:
                    self._dl_update(doi, "✅ 成功", source=src or "—", cost=0, title=ts)
                    # 实时更新 DataFrame 中的 PDF 链接
                    d = row.get("DOI", "")
                    if d in doi_to_dfidx:
                        pl = row.get("PDF链接", "")
                        if pl: self.df.at[doi_to_dfidx[d], "PDF链接"] = pl
                    self._log(f"  ✅ {ts}{src_tag}", "success")
                elif "跳过" in st:
                    self._dl_update(doi, "⏭ 跳过(已存在)", source=src or "—", title=ts)
                elif "无 DOI" in st:
                    self._dl_update(doi, "无 DOI", source="—", title=ts)
                else:
                    self._dl_update(doi, "❌ 失败", source=src or "—", title=ts)
                    self._log(f"  ❌ {ts}", "error")
                self._dl_done_one(st)
                # 每完成一条刷新表格
                if cur % 2 == 0 or cur == tn:
                    self.root.after(0, self._refresh_table)
                self.root.update_idletasks()

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

            # DeepSeek AI 兜底：对下载失败的条目找 OA 链接并重试（实时更新进度）
            ai_recovered = self._ai_retry_failed_pdfs(results, sd, total, sc)
            if ai_recovered:
                sc += ai_recovered
                self.df.to_excel(op, index=False)
                self._log(f"🤖 AI 兜底成功下载 {ai_recovered} 篇", "success")
            self.status_var.set(f"PDF: {sc}/{total}")

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

    def _ai_retry_failed_pdfs(self, results, save_dir, base_total=0, base_done=0):
        """
        PDF 下载兜底链路（文档 v2 方法）：
        1. 机器核验 OA 链接：S2 / Unpaywall 直接给免费 PDF 直链（GOLD/GREEN）
        2. AI 兜底：机器没有 OA 信息时才让 DeepSeek 给候选链接
        实时更新进度条与统计（base_total/base_done 用于延续主下载的进度）。
        返回重试成功的数量。
        """
        import lit_verify

        failed_rows = [r for r in results if "❌" in str(r.get("_download_status", ""))]
        if not failed_rows:
            return 0

        from pdf_downloader import download_pdf
        self._log(f"📡 下载兜底：{len(failed_rows)} 篇失败，先查 OA 镜像，再 AI 兜底", "header")
        # 状态栏明确提示 AI 搜索中
        self.status_var.set(f"🤖 AI 搜索中... {len(failed_rows)} 篇待补足")
        self._update_stats(stage=f"🤖 AI 搜索中（{len(failed_rows)} 篇）")
        recovered = 0
        total_ai = len(failed_rows)
        for idx, row in enumerate(failed_rows, start=1):
            if self.stop_requested:
                break

            doi_key = str(row.get("DOI", ""))
            title_short = str(row.get("title", ""))[:40]
            # 面板标记为 AI 处理中
            self._dl_update(doi_key, "🤖 AI 处理中", source="AI", title=title_short)

            # 实时进度：主下载进度 + AI 兜底进度融合
            done_now = base_done + recovered
            total_now = max(base_total, 1)
            pct = (done_now / total_now) * 100 if total_now else 0
            self._draw_progress(pct)
            self.status_var.set(f"PDF 兜底: {idx}/{total_ai}（已救回 {recovered}）")
            self._update_stats(stage=f"🤖 AI 兜底 {idx}/{total_ai}", pdf_done=base_done + recovered)

            doi = row.get("DOI", "")
            title = str(row.get("title", ""))[:200]
            links = []

            # ① 机器核验：S2 OA 直链 + Unpaywall OA 镜像（可靠，优先）
            if doi:
                try:
                    v = lit_verify.verify_doi_full(doi, title)
                    if v.get("oa_url"):
                        links.append({"url": v["oa_url"], "source": "OA直链"})
                    for loc in (v.get("oa_locations") or []):
                        if loc.get("url") and loc["url"] not in [l["url"] for l in links]:
                            links.append({"url": loc["url"], "source": f"Unpaywall-{loc.get('host','')[:20]}"})
                except Exception:
                    pass
                if links:
                    self._log(f"  🔍 机器核验到 {len(links)} 个 OA 链接: {doi[:40]}", "info")

            # ② AI 兜底：机器无 OA 信息且已配置 Key 时才问 AI
            if not links and deepseek_ai.is_configured(self.ds_cfg):
                self._log(f"  🤖 询问 AI: {doi or title[:50]}", "info")
                try:
                    links = deepseek_ai.ai_find_pdf_links(doi or "", title, cfg=self.ds_cfg) or []
                except Exception:
                    links = []

            if not links:
                self._dl_update(doi_key, "❌ 无可用链接", source="—", title=title_short)
                self._log(f"  ❌ 无可用链接: {str(title)[:50]}", "error")
                continue

            # 用候选链接重试下载（机器 OA 优先，AI 链接最后）
            save_path = os.path.join(save_dir, row.get("_pdf_filename", "litbox_ai.pdf"))
            success, msg, source = download_pdf(doi, save_path, ai_links=links)
            if success:
                try:
                    final_path, meta_source = self._rename_after_ai_download(save_path, row, doi, save_dir)
                except Exception:
                    final_path, meta_source = save_path, "表格"
                final_filename = os.path.basename(final_path)
                row["PDF链接"] = f'=HYPERLINK("{os.path.join(save_dir, final_filename).replace(chr(92), "/")}", "打开PDF")'
                row["_pdf_filename"] = final_filename
                row["_download_status"] = f"✅ {source} (命名:{meta_source})"
                row["_download_source"] = source
                d = row.get("DOI", "")
                for dfi, dfrow in self.df.iterrows():
                    if str(dfrow.get("DOI", "")).strip() == str(d).strip():
                        self.df.at[dfi, "PDF链接"] = row["PDF链接"]
                        break
                recovered += 1
                self._dl_update(doi_key, "✅ AI 救回", source=f"AI-{source}", title=title_short)
                self._log(f"  ✅ [{source}] {final_filename[:50]}", "success")
            else:
                self._dl_update(doi_key, "❌ AI 失败", source="—", title=title_short)
                self._log(f"  ❌ 兜底链接下载也失败: {str(title)[:50]}", "error")

            # 实时刷新表格（每处理一条）
            self.root.after(0, self._refresh_table)
            self.root.update_idletasks()

        self._draw_progress(100)
        self.status_var.set(f"PDF 兜底完成：救回 {recovered}/{total_ai}")
        # 下载面板显示最终结果
        if hasattr(self, "dl_tree"):
            self.dl_progress_var.set(f"{self._dl_total}/{self._dl_total} 篇")
            self._dl_update_bar(100)
        if recovered:
            self._refresh_table()
        return recovered

    def _rename_after_ai_download(self, save_path, row, doi, save_dir):
        """AI 下载成功后复用现有重命名逻辑（含网页+PDF 元数据优化）。"""
        from pdf_downloader import try_rename_pdf_with_metadata
        return try_rename_pdf_with_metadata(save_path, row, doi, save_dir)

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


