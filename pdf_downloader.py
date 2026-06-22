"""
PDF 下载模块 —— 多源下载：tesble.com → doi.org 直链 → Sci-Hub
支持自动重命名、进度回调、来源标识
"""

import os
import re
import time
import random
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

SCIHUB_DOMAINS = ["https://sci-hub.se", "https://sci-hub.ru", "https://sci-hub.st"]


def clean_filename(name):
    return re.sub(r'[\\/:*?"<>|]', '_', str(name))


def get_title_case(title):
    title = str(title).strip()
    if not title:
        return ""
    return title[:1].upper() + title[1:].lower() if len(title) > 1 else title.upper()


def get_author_lastname(author_str):
    author_str = str(author_str).strip()
    if not author_str or author_str.lower() == 'nan':
        return "Unknown"
    first_author = author_str.split(';')[0].split(',')[0].strip()
    return get_title_case(first_author)


def format_journal(journal_str):
    journal_str = str(journal_str).strip()
    if not journal_str or journal_str.lower() == 'nan':
        return "JOURNAL"
    return journal_str.upper()


def build_filename(row):
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


# ═══════════════ 下载源 1: tesble.com ═══════════════

def _try_tesble(doi, timeout=15):
    url = f"https://www.tesble.com/{doi}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        pdf_url = None
        iframe = soup.find('iframe')
        embed = soup.find('embed')
        if iframe and iframe.get('src'): pdf_url = iframe.get('src')
        elif embed and embed.get('src'): pdf_url = embed.get('src')
        if not pdf_url:
            for a in soup.find_all('a', href=True):
                if '.pdf' in a['href'].lower():
                    pdf_url = a['href']; break
        if not pdf_url: return None
        if pdf_url.startswith('//'): pdf_url = 'https:' + pdf_url
        elif pdf_url.startswith('/'): pdf_url = 'https://www.tesble.com' + pdf_url
        pdf_resp = requests.get(pdf_url, headers=HEADERS, timeout=timeout + 10)
        pdf_resp.raise_for_status()
        ct = pdf_resp.headers.get('Content-Type', '')
        if 'html' in ct.lower(): return None
        return pdf_resp.content
    except Exception:
        return None


# ═══════════════ 下载源 2: doi.org 直链 ═══════════════

def _try_doi_direct(doi, timeout=15):
    try:
        doi_url = f"https://doi.org/{doi}"
        resp = requests.get(doi_url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        pdf_url = None
        meta_pdf = soup.find('meta', attrs={'name': 'citation_pdf_url'})
        if meta_pdf and meta_pdf.get('content'): pdf_url = meta_pdf['content']
        if not pdf_url:
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '.pdf' in href.lower() or 'pdf' in a.get_text().lower():
                    pdf_url = href; break
        if not pdf_url: return None
        if pdf_url.startswith('//'): pdf_url = 'https:' + pdf_url
        elif pdf_url.startswith('/'):
            from urllib.parse import urljoin
            pdf_url = urljoin(resp.url, pdf_url)
        pdf_resp = requests.get(pdf_url, headers=HEADERS, timeout=timeout + 10)
        pdf_resp.raise_for_status()
        ct = pdf_resp.headers.get('Content-Type', '')
        if 'html' in ct.lower(): return None
        return pdf_resp.content
    except Exception:
        return None


# ═══════════════ 下载源 3: Sci-Hub ═══════════════

def _try_scihub(doi, timeout=20):
    for domain in SCIHUB_DOMAINS:
        try:
            url = f"{domain}/{doi}"
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            pdf_url = None
            iframe = soup.find('iframe')
            embed = soup.find('embed')
            if iframe and iframe.get('src') and '.pdf' in iframe.get('src', ''):
                pdf_url = iframe['src']
            elif embed and embed.get('src') and '.pdf' in embed.get('src', ''):
                pdf_url = embed['src']
            if not pdf_url:
                for a in soup.find_all('a', href=True):
                    if '.pdf' in a['href'].lower():
                        pdf_url = a['href']; break
            if not pdf_url:
                for btn in soup.find_all('button'):
                    onclick = btn.get('onclick', '')
                    m = re.search(r"location\.href\s*=\s*['\"]([^'\"]+\.pdf[^'\"]*)", onclick)
                    if m: pdf_url = m.group(1); break
            if not pdf_url: continue
            if pdf_url.startswith('//'): pdf_url = 'https:' + pdf_url
            elif pdf_url.startswith('/'): pdf_url = domain + pdf_url
            pdf_resp = requests.get(pdf_url, headers=HEADERS, timeout=timeout + 15)
            pdf_resp.raise_for_status()
            ct = pdf_resp.headers.get('Content-Type', '')
            if 'html' in ct.lower(): continue
            return pdf_resp.content
        except Exception:
            continue
    return None


# ═══════════════ 主下载函数（多源尝试） ═══════════════

def download_pdf(doi, save_path, timeout=20):
    """
    多源下载，依次尝试：tesble.com → doi.org → Sci-Hub
    返回: (success, message, source_name)
    """
    sources = [
        ("tesble.com", _try_tesble),
        ("doi.org", _try_doi_direct),
        ("Sci-Hub", _try_scihub),
    ]
    for source_name, source_func in sources:
        try:
            content = source_func(doi, min(timeout, 15))
            if content is not None and len(content) > 1000:
                with open(save_path, 'wb') as f:
                    f.write(content)
                return True, "下载成功", source_name
        except Exception:
            continue
    return False, "所有下载源均失败", "—"


def download_all(rows, save_dir="Downloaded_PDFs", progress_callback=None):
    """
    批量下载 PDF，返回更新后的 rows
    """
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    total = len(rows)
    results = []

    for i, row in enumerate(rows):
        doi = row.get("DOI", "")
        if not doi or str(doi).lower() == "nan":
            row["_download_status"] = "无 DOI"
            row["_download_source"] = "—"
            results.append(row)
            if progress_callback:
                progress_callback(i + 1, total, row)
            continue

        filename = build_filename(row)
        save_path = os.path.join(save_dir, filename)
        row["_pdf_filename"] = filename

        time.sleep(random.uniform(1.5, 3.5))

        success, msg, source = download_pdf(doi, save_path)

        rel_path = f"{save_dir}/{filename}"
        row["_download_source"] = source
        if success:
            row["PDF链接"] = f'=HYPERLINK("{rel_path}", "打开PDF")'
            row["_download_status"] = f"✅ {source}"
        else:
            row["PDF链接"] = ""
            row["_download_status"] = f"❌ {msg}"

        results.append(row)
        if progress_callback:
            progress_callback(i + 1, total, row)

    return results
