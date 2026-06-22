"""
自动更新检测模块
启动时从 GitHub 获取最新版本号，对比本地版本，提示更新
"""

import json
import threading
import time
import tkinter as tk
from tkinter import messagebox
import requests

VERSION_URL = "https://raw.githubusercontent.com/xiulitanhua/LiteratureTool/main/version.json"
REPO_DOWNLOAD_URL = ("https://github.com/xiulitanhua/LiteratureTool/raw/main/dist/"
                     "%E6%96%87%E7%8C%AE%E7%BB%BC%E5%90%88%E5%B7%A5%E5%85%B7.exe")

CHECK_TIMEOUT = 8
MAX_RETRIES = 2


def _compare_versions(local: str, remote: str) -> bool:
    try:
        return remote > local
    except Exception:
        return False


def check_for_update(root, current_version, log_callback=None, callback=None):
    """后台检查更新，有新版本弹窗"""

    def _do_check():
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                if log_callback:
                    log_callback(f"检查更新 ({attempt+1}/{MAX_RETRIES+1})...", "muted")
                resp = requests.get(VERSION_URL, timeout=CHECK_TIMEOUT,
                                    headers={"User-Agent": "LiteratureTool-Updater/1.0"})
                resp.raise_for_status()
                data = resp.json()
                remote_version = data.get("version", "")
                download_url = data.get("download_url", REPO_DOWNLOAD_URL)

                if _compare_versions(current_version, remote_version):
                    if log_callback:
                        log_callback(f"🔄 新版本: {remote_version} (当前 {current_version})", "info")
                    root.after(0, lambda: _show_update_dialog(
                        root, current_version, remote_version, download_url))
                else:
                    if log_callback:
                        log_callback(f"已是最新版本 ({current_version})", "success")
                return
            except requests.exceptions.Timeout:
                last_error = "连接超时"
                if attempt < MAX_RETRIES:
                    time.sleep(1)
            except requests.exceptions.ConnectionError:
                last_error = "网络不可达"
                if attempt < MAX_RETRIES:
                    time.sleep(1)
            except Exception as e:
                last_error = str(e)[:60]
                break

        if log_callback:
            log_callback(f"更新检查失败: {last_error}", "warn")

    threading.Thread(target=_do_check, daemon=True).start()


def _show_update_dialog(root, current_ver, new_ver, download_url):
    msg = (
        f"发现新版本！\n\n"
        f"当前版本: {current_ver}\n"
        f"最新版本: {new_ver}\n\n"
        f"是否打开下载页面？"
    )
    if messagebox.askyesno("🔄 发现更新", msg):
        import webbrowser
        webbrowser.open(download_url)
