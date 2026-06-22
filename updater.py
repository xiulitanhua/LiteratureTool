"""
自动更新检测模块
启动时从 GitHub 获取最新版本号，对比本地版本，提示更新
"""

import json
import threading
import tkinter as tk
from tkinter import messagebox
import requests

# GitHub raw 文件的 URL（version.json）
VERSION_URL = "https://raw.githubusercontent.com/xiulitanhua/LiteratureTool/main/version.json"

# 超时时间（秒），避免网络慢导致启动卡顿
CHECK_TIMEOUT = 5


def _compare_versions(local: str, remote: str) -> bool:
    """比较版本号，remote > local 返回 True"""
    try:
        # 版本格式: "yyyyMMdd-HHmm"
        return remote > local
    except Exception:
        return False


def check_for_update(root: tk.Tk, current_version: str, callback=None):
    """
    后台检查更新，有新版本时弹窗提示

    Args:
        root: Tkinter 根窗口
        current_version: 当前本地版本号 (如 "20260622-1200")
        callback: 检查完成后的回调
    """

    def _do_check():
        try:
            resp = requests.get(VERSION_URL, timeout=CHECK_TIMEOUT,
                                headers={"User-Agent": "LiteratureTool-Updater/1.0"})
            resp.raise_for_status()
            data = resp.json()
            remote_version = data.get("version", "")
            download_url = data.get("download_url", "")
            release_notes = data.get("release_notes", "")

            if _compare_versions(current_version, remote_version):
                # 有新版本！在主线程弹窗
                root.after(0, lambda: _show_update_dialog(
                    root, current_version, remote_version, download_url, release_notes))
        except Exception:
            pass  # 网络错误静默处理，不影响主程序
        finally:
            if callback:
                root.after(0, callback)

    # 后台线程执行，避免阻塞 UI
    threading.Thread(target=_do_check, daemon=True).start()


def _show_update_dialog(root, current_ver, new_ver, download_url, release_notes):
    """显示更新提示对话框"""
    msg = (
        f"发现新版本！\n\n"
        f"当前版本: {current_ver}\n"
        f"最新版本: {new_ver}\n\n"
        f"是否打开下载页面？"
    )
    if messagebox.askyesno("🔄 发现更新", msg):
        import webbrowser
        webbrowser.open(download_url if download_url else release_notes)
