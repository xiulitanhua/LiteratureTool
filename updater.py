"""
自动更新模块 —— 应用内下载 → 自动替换 → 重启
"""

import json, os, sys, threading, time, tempfile
import tkinter as tk
from tkinter import ttk, messagebox
import requests

VERSION_URL = "https://raw.githubusercontent.com/xiulitanhua/LiteratureTool/main/version.json"
REPO_DOWNLOAD_URL = ("https://github.com/xiulitanhua/LiteratureTool/raw/main/dist/"
                     "%E6%96%87%E7%8C%AE%E7%BB%BC%E5%90%88%E5%B7%A5%E5%85%B7.exe")
CHECK_TIMEOUT = 8
MAX_RETRIES = 2


def _compare_versions(local, remote):
    try: return remote > local
    except: return False


def check_for_update(root, current_version, log_callback=None):
    """后台检查更新，有新版本弹窗"""

    def _do():
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

                if _compare_versions(current_version, rv):
                    if log_callback:
                        log_callback(f"🔄 新版本: {rv}", "info")
                    root.after(0, lambda: _dialog(root, current_version, rv, dl, log_callback))
                else:
                    if log_callback:
                        log_callback(f"已是最新 ({current_version})", "success")
                return
            except requests.exceptions.Timeout:
                last_err = "超时"
                if i < MAX_RETRIES: time.sleep(1)
            except requests.exceptions.ConnectionError:
                last_err = "网络不通"
                if i < MAX_RETRIES: time.sleep(1)
            except Exception as e:
                last_err = str(e)[:50]; break

        if log_callback:
            log_callback(f"更新检查失败: {last_err}", "warn")

    threading.Thread(target=_do, daemon=True).start()


def _dialog(root, cur_ver, new_ver, dl_url, log_cb):
    """更新弹窗：立即更新 / 稍后"""
    dlg = tk.Toplevel(root)
    dlg.title("🔄 发现新版本")
    dlg.geometry("420x300")
    dlg.resizable(False, False)
    dlg.configure(bg="#ffffff")
    dlg.transient(root)
    dlg.grab_set()

    dlg.update_idletasks()
    x = root.winfo_x() + (root.winfo_width() - 420) // 2
    y = root.winfo_y() + (root.winfo_height() - 300) // 2
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

    tk.Button(bf, text="稍后提醒", font=("Microsoft YaHei UI", 9),
              bg="#f1f5f9", fg="#64748b", relief=tk.FLAT,
              cursor="hand2", padx=16, pady=6, borderwidth=0,
              command=dlg.destroy).pack(side=tk.RIGHT, padx=(8, 0))

    def do_update():
        for w in bf.winfo_children(): w.config(state=tk.DISABLED)
        pbar.pack(fill=tk.X, pady=(8, 4))
        sv.set("正在下载...")

        def _dl():
            try:
                fd, tp = tempfile.mkstemp(suffix=".exe")
                os.close(fd)
                r = requests.get(dl_url, stream=True, timeout=120,
                                 headers={"User-Agent": "LitTool-Updater/1.0"})
                r.raise_for_status()
                total = int(r.headers.get('content-length', 0))
                done = 0
                with open(tp, 'wb') as wf:
                    for chunk in r.iter_content(8192):
                        wf.write(chunk); done += len(chunk)
                        if total > 0:
                            pvar.set(done / total * 100)
                            sv.set(f"{done//1048576}MB / {total//1048576}MB")
                            dlg.update_idletasks()

                pvar.set(100); sv.set("正在替换..."); dlg.update_idletasks()

                cur = sys.executable
                cur_dir = os.path.dirname(cur)
                old_backup = os.path.join(cur_dir, "_old_version.exe")
                bat = os.path.join(tempfile.gettempdir(), "_lit_update.bat")
                with open(bat, 'w', encoding='ascii') as bf:
                    bf.write('@echo off\n')
                    bf.write('echo Updating...\n')
                    bf.write('ping 127.0.0.1 -n 4 >nul\n')
                    # 先重命名旧文件（避免占用冲突）
                    bf.write(f'if exist "{cur}" ren "{cur}" "_old_version.exe"\n')
                    # 移入新文件
                    bf.write(f'move /y "{tp}" "{cur}" >nul 2>&1\n')
                    bf.write('if %errorlevel% equ 0 (\n')
                    bf.write('  echo Update OK, starting...\n')
                    bf.write(f'  start "" "{cur}"\n')
                    # 删除旧备份
                    bf.write(f'  del /f /q "{old_backup}" 2>nul\n')
                    bf.write(') else (\n')
                    bf.write(f'  if exist "{old_backup}" ren "{old_backup}" "{os.path.basename(cur)}"\n')
                    bf.write(f'  echo Update failed, new version saved to:\n')
                    bf.write(f'  echo {tp}\n')
                    bf.write('  pause\n')
                    bf.write(')\n')
                    bf.write(f'del /f /q "{bat}" 2>nul\n')
                os.startfile(bat)
                root.quit()
            except Exception as e:
                sv.set(f"失败: {e}")
                if log_cb: log_cb(f"更新失败: {e}", "error")

        threading.Thread(target=_dl, daemon=True).start()

    tk.Button(bf, text="🔄 立即更新", font=("Microsoft YaHei UI", 9, "bold"),
              bg="#2563eb", fg="white", activebackground="#1d4ed8",
              activeforeground="white", relief=tk.FLAT, cursor="hand2",
              padx=16, pady=6, borderwidth=0,
              command=do_update).pack(side=tk.RIGHT)

    dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
    dlg.wait_window()
