"""
自动更新模块 —— 应用内下载、替换、重启
支持"跳过此版本"不再重复提示
"""

import json, os, sys, subprocess, threading, time, tempfile
import tkinter as tk
from tkinter import ttk, messagebox
import requests

VERSION_URL = "https://raw.githubusercontent.com/xiulitanhua/LiteratureTool/main/version.json"
REPO_DOWNLOAD_URL = ("https://github.com/xiulitanhua/LiteratureTool/raw/main/dist/"
                     "%E6%96%87%E7%8C%AE%E7%BB%BC%E5%90%88%E5%B7%A5%E5%85%B7.exe")
CHECK_TIMEOUT = 8
MAX_RETRIES = 2

SKIP_FILE = os.path.join(tempfile.gettempdir(), "_lit_skip_version.txt")


def _load_skipped():
    try:
        with open(SKIP_FILE, 'r') as f:
            return f.read().strip()
    except: return ""


def _save_skipped(ver):
    try:
        with open(SKIP_FILE, 'w') as f:
            f.write(ver)
    except: pass


def _compare_versions(local, remote):
    try: return remote > local
    except: return False


def check_for_update(root, current_version, log_callback=None):
    def _do():
        skipped = _load_skipped()
        last_err = None
        for i in range(MAX_RETRIES + 1):
            try:
                if log_callback:
                    log_callback(f"检查更新 ({i+1}/{MAX_RETRIES+1})...", "muted")
                r = requests.get(VERSION_URL, timeout=CHECK_TIMEOUT,
                                 headers={"User-Agent": "LitTool-Updater/1.0"})
                r.raise_for_status()
                d = r.json()
                rv = d.get("version", "")
                dl = d.get("download_url", REPO_DOWNLOAD_URL)

                if skipped and not _compare_versions(skipped, rv):
                    if log_callback:
                        log_callback(f"已跳过版本 {rv}", "muted")
                    return

                if _compare_versions(current_version, rv):
                    if log_callback:
                        log_callback(f"🔄 新版本: {rv}", "info")
                    root.after(0, lambda: _dialog(root, current_version, rv, dl, log_callback))
                else:
                    if log_callback:
                        log_callback(f"已是最新 ({current_version})", "success")
                return
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                last_err = "超时" if i == 0 else "网络不通"
                if i < MAX_RETRIES: time.sleep(1)
            except Exception as e:
                last_err = str(e)[:50]; break
        if log_callback:
            log_callback(f"更新检查失败: {last_err}", "warn")

    threading.Thread(target=_do, daemon=True).start()


def _dialog(root, cur_ver, new_ver, dl_url, log_cb):
    dlg = tk.Toplevel(root)
    dlg.title("发现新版本")
    dlg.geometry("440x320")
    dlg.resizable(False, False)
    dlg.configure(bg="#ffffff")
    dlg.transient(root)
    dlg.grab_set()
    dlg.update_idletasks()
    x = root.winfo_x() + (root.winfo_width() - 440) // 2
    y = root.winfo_y() + (root.winfo_height() - 320) // 2
    dlg.geometry(f"+{x}+{y}")

    f = tk.Frame(dlg, bg="#ffffff", padx=24, pady=20)
    f.pack(fill=tk.BOTH, expand=True)

    tk.Label(f, text="🔄 发现新版本", font=("Microsoft YaHei UI", 14, "bold"),
             fg="#2563eb", bg="#ffffff").pack(anchor=tk.W)
    tk.Label(f, text=f"当前: {cur_ver}\n最新: {new_ver}",
             font=("Consolas", 10), fg="#64748b", bg="#ffffff",
             justify=tk.LEFT).pack(anchor=tk.W, pady=(12, 8))

    pvar = tk.DoubleVar()
    pbar = ttk.Progressbar(f, variable=pvar, maximum=100)
    sv = tk.StringVar(value="")
    tk.Label(f, textvariable=sv, font=("", 8), fg="#94a3b8", bg="#ffffff").pack(anchor=tk.W)
    bf = tk.Frame(f, bg="#ffffff")
    bf.pack(fill=tk.X, pady=(16, 0))

    _cancel = [False]
    cancel_btn = tk.Button(bf, text="取消", font=("Microsoft YaHei UI", 9),
                           bg="#fee2e2", fg="#dc2626", relief=tk.FLAT,
                           cursor="hand2", padx=12, pady=6, borderwidth=0,
                           state=tk.DISABLED,
                           command=lambda: _cancel.__setitem__(0, True))

    def do_update():
        for w in bf.winfo_children():
            if w != cancel_btn: w.config(state=tk.DISABLED)
        cancel_btn.config(state=tk.NORMAL)
        cancel_btn.pack(side=tk.LEFT)
        pbar.pack(fill=tk.X, pady=(8, 4))
        sv.set("正在下载...")

        def _dl():
            try:
                fd, tp = tempfile.mkstemp(suffix=".exe")
                os.close(fd)
                r = requests.get(dl_url, stream=True, timeout=(15, 120),
                                 headers={"User-Agent": "LitTool-Updater/1.0"})
                r.raise_for_status()
                total = int(r.headers.get('content-length', 0))
                done = 0
                with open(tp, 'wb') as wf:
                    for chunk in r.iter_content(8192):
                        if _cancel[0]:
                            wf.close()
                            try: os.remove(tp)
                            except: pass
                            dlg.destroy()
                            return
                        wf.write(chunk); done += len(chunk)
                        if total > 0:
                            pvar.set(done / total * 100)
                            sv.set(f"{done//1048576}MB / {total//1048576}MB")
                        dlg.update_idletasks()
                if _cancel[0]: return
                pvar.set(100); sv.set("正在替换..."); dlg.update_idletasks()

                cur = sys.executable
                cur_dir = os.path.dirname(cur)
                cur_name = os.path.basename(cur)
                old_backup = os.path.join(cur_dir, "_old_version.exe")
                err_log = os.path.join(tempfile.gettempdir(), "_lit_update_err.txt")
                script = os.path.join(tempfile.gettempdir(), "_lit_update.ps1")
                with open(script, 'w', encoding='utf-8') as sf:
                    sf.write('$cur = ' + json.dumps(cur) + '\n')
                    sf.write('$tp  = ' + json.dumps(tp) + '\n')
                    sf.write('$bak = ' + json.dumps(old_backup) + '\n')
                    sf.write('$err = ' + json.dumps(err_log) + '\n')
                    sf.write('$name = ' + json.dumps(cur_name) + '\n')
                    sf.write('Start-Sleep -Seconds 4\n')
                    sf.write('try {\n')
                    sf.write('    if (Test-Path $cur) { Rename-Item $cur "_old_version.exe" -Force -ErrorAction Stop }\n')
                    sf.write('    Move-Item $tp $cur -Force -ErrorAction Stop\n')
                    sf.write('    Start-Process $cur\n')
                    sf.write('    if (Test-Path $bak) { Remove-Item $bak -Force -ErrorAction SilentlyContinue }\n')
                    sf.write('} catch {\n')
                    sf.write('    $_ | Out-File $err -Encoding UTF8\n')
                    sf.write('    if (Test-Path $bak) { Rename-Item $bak $name -Force -ErrorAction SilentlyContinue }\n')
                    sf.write('    Start-Sleep -Seconds 8\n')
                    sf.write('}\n')
                    sf.write(f'Remove-Item "{script}" -Force -ErrorAction SilentlyContinue\n')
                # 非阻塞启动 PowerShell，不等待结果
                subprocess.Popen(
                    f'powershell -ExecutionPolicy Bypass -File "{script}"',
                    shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
                root.quit()
            except Exception as e:
                if not _cancel[0]:
                    sv.set(f"下载失败: {e}")
                    if log_cb: log_cb(f"更新失败: {e}", "error")
                    for w in bf.winfo_children(): w.config(state=tk.NORMAL)
                    cancel_btn.config(state=tk.DISABLED)

        threading.Thread(target=_dl, daemon=True).start()

    # 按钮：跳过此版本 | 稍后提醒 | 立即更新
    tk.Button(bf, text="跳过此版本", font=("Microsoft YaHei UI", 9),
              bg="#f1f5f9", fg="#94a3b8", relief=tk.FLAT, cursor="hand2",
              padx=12, pady=6, borderwidth=0,
              command=lambda: [_save_skipped(new_ver), dlg.destroy()]).pack(side=tk.RIGHT, padx=(8, 0))

    tk.Button(bf, text="稍后提醒", font=("Microsoft YaHei UI", 9),
              bg="#f1f5f9", fg="#64748b", relief=tk.FLAT, cursor="hand2",
              padx=12, pady=6, borderwidth=0,
              command=dlg.destroy).pack(side=tk.RIGHT, padx=(4, 0))

    tk.Button(bf, text="🔄 立即更新", font=("Microsoft YaHei UI", 9, "bold"),
              bg="#2563eb", fg="white", activebackground="#1d4ed8",
              activeforeground="white", relief=tk.FLAT, cursor="hand2",
              padx=16, pady=6, borderwidth=0,
              command=do_update).pack(side=tk.RIGHT)

    dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
    dlg.wait_window()
