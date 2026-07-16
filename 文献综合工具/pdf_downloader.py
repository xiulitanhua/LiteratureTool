"""
PDF 下载模块 —— 多源下载：tesble.com → doi.org 直链 → Sci-Hub
支持自动重命名、进度回调、来源标识
命名格式: 作者_年份_期刊首字母缩写_研究区域_题名简写.pdf
元数据优先从网页获取，无法获取时通过读取 PDF 提取
"""

import os
import re
import time
import random
import requests
from bs4 import BeautifulSoup

# PDF 元数据读取
try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

SCIHUB_DOMAINS = ["https://sci-hub.se", "https://sci-hub.ru", "https://sci-hub.st"]

# 期刊名首字母缩写时跳过的停用词
JOURNAL_STOP_WORDS = {
    'of', 'the', 'and', 'in', 'on', 'at', 'to', 'for', 'a', 'an',
    'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has',
    'had', 'with', 'by', 'from', 'or', 'no', 'not', 'its', 'et', 'al',
    'de', 'la', 'le', 'du', 'des', 'del', 'der', 'die', 'das', 'und'
}


def clean_filename(name):
    return re.sub(r'[\\/:*?"<>|]', '_', str(name))


def get_title_case(text):
    """将文本转为首字母大写的标题格式"""
    text = str(text).strip()
    if not text:
        return ""
    # 使用 title() 保持每个单词首字母大写
    result = text.title()
    # 修正 title() 对撇号的处理 (如 O'Brien -> O'Brian -> O'Brien)  
    # 但对中文名拼音不影响
    return result


def get_author_lastname(author_str):
    """提取第一作者姓（首字母大写）"""
    author_str = str(author_str).strip()
    if not author_str or author_str.lower() == 'nan':
        return "Unknown"
    first_author = author_str.split(';')[0].split(',')[0].strip()
    # 若为 "LastName, FirstName" 格式取逗号前
    if ',' in author_str.split(';')[0]:
        return get_title_case(first_author)
    # 否则返回整个第一作者（适用于中文名 Zhang Wei 或英文 Smith JA）
    return get_title_case(first_author)


def get_journal_initials(journal_str):
    """提取期刊首字母缩写，跳过常见停用词。
    例如: 'Science of The Total Environment' -> 'STTE'
          'Journal of Hydrology' -> 'JH'
    """
    journal_str = str(journal_str).strip()
    if not journal_str or journal_str.lower() == 'nan':
        return "J"
    # 提取所有英文单词
    words = re.findall(r'[A-Za-z]+', journal_str)
    initials = ''.join(w[0].upper() for w in words if w.lower() not in JOURNAL_STOP_WORDS)
    if not initials:
        # 如果全是停用词，取前 4 字母大写
        initials = re.sub(r'[^A-Za-z]', '', journal_str)[:4].upper()
    return initials if initials else "J"


def get_title_abbreviation(title, max_chars=30):
    """获取题名简写：取前 max_chars 个字符，在单词边界截断"""
    title = str(title).strip()
    if not title or title.lower() == 'nan':
        return "Untitled"
    # 去除特殊字符，保留字母数字中文和空格
    title = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5\s]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip()
    if len(title) <= max_chars:
        return title
    truncated = title[:max_chars].strip()
    # 在最后一个空格处截断
    last_space = truncated.rfind(' ')
    if last_space > max_chars // 2:
        truncated = truncated[:last_space]
    return truncated


def fetch_metadata_from_web(doi):
    """
    从 Crossref API 获取文献元数据（期刊缩写、学科/关键词用作研究区域参考）
    返回 dict: {journal_abbr, subjects, container_title, ...} 或 None
    """
    if not doi or str(doi).lower() == 'nan':
        return None
    doi = str(doi).strip()
    # 清理 DOI 前缀
    doi_clean = re.sub(r'^https?://(dx\.)?doi\.org/', '', doi, flags=re.IGNORECASE)
    url = f"https://api.crossref.org/works/{doi_clean}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message", {})
        result = {}

        # 期刊缩写（short-container-title 通常就是缩写）
        short_titles = msg.get("short-container-title", [])
        if short_titles and short_titles[0]:
            result["journal_abbr"] = short_titles[0].strip()
        else:
            # 从完整刊名提取缩写
            container = msg.get("container-title", [])
            if container and container[0]:
                result["journal_abbr"] = get_journal_initials(container[0])

        # 学科 / 主题（用作研究区域参考）
        subjects = msg.get("subject", [])
        if subjects:
            result["subjects"] = [s for s in subjects if s]
            # 选第一个有意义的学科作为研究区域
            result["research_area"] = result["subjects"][0] if result["subjects"] else ""

        # 关键词
        keywords = []
        for kw_obj in msg.get("keyword", []):
            if isinstance(kw_obj, dict):
                kw = kw_obj.get("kwd") or kw_obj.get("keyword", "")
            else:
                kw = str(kw_obj)
            if kw and len(kw) > 1:
                keywords.append(kw)
        if keywords:
            result["keywords"] = keywords
            if not result.get("research_area"):
                result["research_area"] = keywords[0]

        # 发布者
        publisher = msg.get("publisher", "")
        if publisher:
            result["publisher"] = publisher

        # 摘要（可用于提取研究区域）
        abstract = msg.get("abstract", "")
        if abstract:
            # 清理 HTML 标签
            abstract = re.sub(r'<[^>]+>', '', abstract)
            result["abstract"] = abstract[:500]

        return result if result else None
    except Exception:
        return None


def extract_metadata_from_pdf(pdf_path):
    """
    从已下载的 PDF 文件中提取元数据（标题、关键词、摘要等）
    用于网页获取失败时的备选方案
    返回 dict 或 None
    """
    if not HAS_PYPDF2:
        return None
    try:
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            info = reader.metadata
            result = {}

            # 尝试从 PDF 元数据获取标题
            if info:
                pdf_title = info.get('/Title', '')
                if pdf_title:
                    result["pdf_title"] = str(pdf_title).strip()
                pdf_subject = info.get('/Subject', '')
                if pdf_subject:
                    result["research_area"] = str(pdf_subject).strip()[:50]
                pdf_keywords = info.get('/Keywords', '')
                if pdf_keywords:
                    result["keywords"] = str(pdf_keywords).strip()

            # 若元数据不够，尝试从第一页文本提取
            if not result.get("research_area") and len(reader.pages) > 0:
                try:
                    first_page_text = reader.pages[0].extract_text() or ""
                    # 尝试找 Keywords / 关键词 行
                    kw_match = re.search(
                        r'(?:Keywords|Key words|关键词|关键字)[\s:：]*(.+?)(?:\n|$)',
                        first_page_text, re.IGNORECASE
                    )
                    if kw_match:
                        kws = kw_match.group(1).strip()
                        result["keywords"] = kws[:100]
                        result["research_area"] = kws.split(';')[0].split(',')[0].strip()[:50]

                    # 如果标题未知，尝试从前几行提取
                    if not result.get("pdf_title"):
                        lines = first_page_text.strip().split('\n')
                        for line in lines[:10]:
                            line = line.strip()
                            if len(line) > 20 and len(line) < 300:
                                result["pdf_title"] = line
                                break
                except Exception:
                    pass

            return result if result else None
    except Exception:
        return None


def extract_research_area_from_text(text, keywords=None):
    """
    从文本/关键词中提取研究区域（地理区域或研究领域）
    优先使用关键词
    """
    if keywords:
        kws = keywords.split(';') if ';' in keywords else keywords.split(',')
        # 选最长的有意义关键词作为研究区域
        meaningful = [k.strip() for k in kws if len(k.strip()) > 2 and len(k.strip()) < 60]
        if meaningful:
            # 优先选包含地名/区域名的词
            location_indicators = [
                'basin', 'river', 'lake', 'mountain', 'forest', 'wetland', 'grassland',
                'region', 'area', 'city', 'province', 'county', 'delta', 'plateau',
                'watershed', 'catchment', 'estuary', 'coastal', 'marine', 'urban', 'rural',
                '流域', '河流', '湖泊', '山区', '森林', '湿地', '草地', '草原',
                '地区', '区域', '城市', '省份', '三角洲', '高原', '沿海', '海洋',
                'China', 'USA', 'Europe', 'Asia', 'Africa', 'Australia', 'India',
                '中国', '美国', '欧洲', '亚洲', '非洲', '澳大利亚', '印度',
            ]
            for kw in meaningful:
                kw_lower = kw.lower()
                for indicator in location_indicators:
                    if indicator.lower() in kw_lower:
                        return kw
            return meaningful[0]
    return ""


def build_filename(row, research_area=""):
    """
    构建 PDF 文件名
    格式: 作者_年份_期刊首字母缩写_研究区域_题名简写.pdf
    优先从 row 中获取各字段，research_area 为备选
    """
    last_name = get_author_lastname(row.get("AUTHOR", ""))

    # 年份
    year_val = row.get("YEAR", "")
    try:
        year_str = str(int(float(year_val))) if year_val and str(year_val).lower() != 'nan' else "Year"
    except (ValueError, TypeError):
        year_str = "Year"

    # 期刊首字母缩写
    journal_abbr = row.get("JOURNAL_ABBR", "")
    if not journal_abbr or journal_abbr == "nan":
        journal_abbr = get_journal_initials(row.get("JOURNAL", ""))

    # 研究区域
    area = row.get("RESEARCH_AREA", "")
    if not area or area.lower() == "nan":
        area = research_area
    if not area or area.lower() == "nan":
        area = "Area"

    # 题名简写
    title = str(row.get("title", "")).strip()
    title_short = get_title_abbreviation(title) if title else "Untitled"

    filename = f"{last_name}_{year_str}_{journal_abbr}_{area}_{title_short}.pdf"
    return clean_filename(filename)


def try_rename_pdf_with_metadata(old_path, row, doi="", save_dir=""):
    """
    下载后尝试用网页元数据 + PDF 元数据优化文件名并重命名
    返回: (新的完整路径, 使用的元数据来源)
    """
    row = dict(row)
    research_area = ""
    journal_abbr = ""
    source_used = "表格"

    # 1️⃣ 优先从网页获取元数据
    web_meta = fetch_metadata_from_web(doi)
    if web_meta:
        source_used = "Crossref网页"
        if web_meta.get("journal_abbr"):
            journal_abbr = web_meta["journal_abbr"]
            row["JOURNAL_ABBR"] = journal_abbr
        if web_meta.get("research_area"):
            research_area = web_meta["research_area"]
            row["RESEARCH_AREA"] = research_area
        elif web_meta.get("keywords"):
            research_area = extract_research_area_from_text("", ";".join(web_meta["keywords"]))
            row["RESEARCH_AREA"] = research_area
        # 也尝试从 subject 列表提取
        if not research_area and web_meta.get("subjects"):
            research_area = extract_research_area_from_text("", ";".join(web_meta["subjects"]))
            row["RESEARCH_AREA"] = research_area

    # 2️⃣ 网页不够，从 PDF 文件读取
    if not research_area or research_area == "Area":
        pdf_meta = extract_metadata_from_pdf(old_path)
        if pdf_meta:
            if source_used == "表格":
                source_used = "PDF元数据"
            # 从 PDF 提取的关键词中获取研究区域
            if pdf_meta.get("research_area"):
                research_area = pdf_meta["research_area"]
                row["RESEARCH_AREA"] = research_area
            elif pdf_meta.get("keywords"):
                research_area = extract_research_area_from_text("", pdf_meta["keywords"])
                row["RESEARCH_AREA"] = research_area

    # 3️⃣ 如果还没有研究区域，尝试从标题中提取
    if not research_area or research_area == "Area":
        title = str(row.get("title", ""))
        research_area = extract_research_area_from_text(title)
        if research_area:
            row["RESEARCH_AREA"] = research_area

    # 构建新文件名
    new_filename = build_filename(row, research_area)
    new_path = os.path.join(save_dir, new_filename) if save_dir else os.path.join(os.path.dirname(old_path), new_filename)

    # 避免重名
    if os.path.normpath(old_path) != os.path.normpath(new_path):
        counter = 1
        base, ext = os.path.splitext(new_path)
        while os.path.exists(new_path):
            new_path = f"{base}_{counter}{ext}"
            counter += 1
        try:
            os.rename(old_path, new_path)
        except Exception:
            new_path = old_path  # 重命名失败则保留原名

    return new_path, source_used


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
    下载后自动从网页获取元数据优化文件名，网页获取失败则从 PDF 读取
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

        # 先用表格信息生成临时文件名
        filename = build_filename(row)
        save_path = os.path.join(save_dir, filename)

        time.sleep(random.uniform(1.5, 3.5))

        success, msg, source = download_pdf(doi, save_path)

        row["_download_source"] = source
        if success:
            # 下载成功后，尝试用网页元数据+PDF元数据优化文件名
            final_path, meta_source = try_rename_pdf_with_metadata(
                save_path, row, doi, save_dir
            )
            final_filename = os.path.basename(final_path)

            rel_path = os.path.join(save_dir, final_filename).replace("\\", "/")
            row["PDF链接"] = f'=HYPERLINK("{rel_path}", "打开PDF")'
            row["_pdf_filename"] = final_filename
            row["_download_status"] = f"✅ {source} (命名:{meta_source})"
            row["_final_path"] = final_path
        else:
            row["PDF链接"] = ""
            row["_pdf_filename"] = filename
            row["_download_status"] = f"❌ {msg}"

        results.append(row)
        if progress_callback:
            progress_callback(i + 1, total, row)

    return results
