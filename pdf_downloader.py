"""
PDF 下载模块 —— 根据 DOI 从 tesble.com 下载文献 PDF
支持自动重命名、进度回调
"""

import os
import re
import time
import random
import requests
from bs4 import BeautifulSoup


def clean_filename(name):
    """去除文件名中不能包含的特殊字符"""
    return re.sub(r'[\\/:*?"<>|]', '_', str(name))


def get_title_case(title):
    """首字母大写，其余小写"""
    title = str(title).strip()
    if not title:
        return ""
    return title[:1].upper() + title[1:].lower() if len(title) > 1 else title.upper()


def get_author_lastname(author_str):
    """获取第一作者姓氏"""
    author_str = str(author_str).strip()
    if not author_str or author_str.lower() == 'nan':
        return "Unknown"
    first_author = author_str.split(';')[0].split(',')[0].strip()
    return get_title_case(first_author)


def format_journal(journal_str):
    """期刊名全大写"""
    journal_str = str(journal_str).strip()
    if not journal_str or journal_str.lower() == 'nan':
        return "JOURNAL"
    return journal_str.upper()


def build_filename(row):
    """
    根据行数据构建文件名：作者姓氏_年份_期刊全大写_标题.pdf
    row: dict with keys: AUTHOR, YEAR, JOURNAL, title
    """
    last_name = get_author_lastname(row.get("AUTHOR", ""))
    year_val = row.get("YEAR", "")
    try:
        year_str = str(int(float(year_val))) if year_val and str(year_val).lower() != 'nan' else "Year"
    except (ValueError, TypeError):
        year_str = "Year"

    journal_abbr = format_journal(row.get("JOURNAL", ""))

    title = str(row.get("title", "")).strip()
    title_cased = get_title_case(title) if title else "Untitled"
    if len(title_cased) > 50:
        title_cased = title_cased[:50].strip() + "..."

    filename = f"{last_name}_{year_str}_{journal_abbr}_{title_cased}.pdf"
    return clean_filename(filename)


def download_pdf(doi, save_path, timeout=20):
    """
    下载单个 PDF
    返回: True 成功, False 失败
    """
    url = f"https://www.tesble.com/{doi}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

    try:
        # 获取页面
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, 'html.parser')
        pdf_url = None

        # 查找 PDF 链接
        iframe = soup.find('iframe')
        embed = soup.find('embed')

        if iframe and iframe.get('src'):
            pdf_url = iframe.get('src')
        elif embed and embed.get('src'):
            pdf_url = embed.get('src')

        if not pdf_url:
            # 尝试查找其他可能的 PDF 链接
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '.pdf' in href.lower():
                    pdf_url = href
                    break

        if not pdf_url:
            return False, "未找到 PDF 链接"

        # 补全 URL
        if pdf_url.startswith('//'):
            pdf_url = 'https:' + pdf_url
        elif pdf_url.startswith('/'):
            pdf_url = 'https://www.tesble.com' + pdf_url

        # 下载 PDF
        pdf_resp = requests.get(pdf_url, headers=headers, timeout=timeout + 10)
        pdf_resp.raise_for_status()

        # 检查是否是有效的 PDF
        content_type = pdf_resp.headers.get('Content-Type', '')
        if 'html' in content_type.lower():
            return False, "返回的是 HTML 而非 PDF"

        with open(save_path, 'wb') as f:
            f.write(pdf_resp.content)

        return True, "下载成功"

    except requests.exceptions.Timeout:
        return False, "请求超时"
    except requests.exceptions.ConnectionError:
        return False, "连接失败"
    except Exception as e:
        return False, str(e)


def download_all(rows, save_dir="Downloaded_PDFs", progress_callback=None):
    """
    批量下载 PDF
    rows: 文献记录列表，每条包含 DOI, AUTHOR, YEAR, JOURNAL, title
    save_dir: 保存目录
    progress_callback: 进度回调函数 callback(index, total, status_dict)

    返回: 更新后的 rows（含 PDF 链接）
    """
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    total = len(rows)
    results = []

    for i, row in enumerate(rows):
        doi = row.get("DOI", "")
        if not doi or str(doi).lower() == "nan":
            row["_download_status"] = "无 DOI，跳过"
            results.append(row)
            if progress_callback:
                progress_callback(i + 1, total, row)
            continue

        filename = build_filename(row)
        save_path = os.path.join(save_dir, filename)
        row["_pdf_filename"] = filename

        # 随机延迟
        time.sleep(random.uniform(1.5, 3.5))

        success, msg = download_pdf(doi, save_path)

        if success:
            relative_path = f"{save_dir}/{filename}"
            row["PDF链接"] = f'=HYPERLINK("{relative_path}", "打开PDF")'
            row["_download_status"] = "下载成功"
        else:
            row["_download_status"] = f"失败: {msg}"

        results.append(row)

        if progress_callback:
            progress_callback(i + 1, total, row)

    return results
