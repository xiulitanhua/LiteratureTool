"""
DOI 获取模块 —— 通过 Crossref / OpenAlex 自动查询文献 DOI
使用 标题 + 第一作者 + 年份 进行综合匹配
"""

import re
import time
import requests
from urllib.parse import quote

DEFAULT_THRESHOLD = 0.8
USER_AGENT = "LiteratureTool/2.0 (mailto:researcher@example.com)"


def normalize_text(text):
    """文本标准化：去重音、小写、归一化空白"""
    if not text:
        return ""
    text = str(text).lower()
    # 简单版归一化：去除非字母数字中文的符号，合并空格
    text = re.sub(r"&", " and ", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", " ", text)
    return text.strip()


def normalize_doi(doi):
    """清理 DOI 格式"""
    if not doi:
        return ""
    doi = str(doi).strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    return doi


def title_score(source, candidate):
    """计算两个标题的匹配度 (0~1)"""
    a = normalize_text(source)
    b = normalize_text(candidate)
    if not a or not b:
        return 0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.94

    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens:
        return 0
    hits = sum(1 for t in a_tokens if t in b_tokens)
    return hits / max(len(a_tokens), len(b_tokens))


def extract_year(value):
    """从各种格式的值中提取年份"""
    if value is None or value == "":
        return None
    try:
        # 尝试作为 datetime 对象
        if hasattr(value, "year"):
            return int(value.year)
    except Exception:
        pass
    s = str(value).strip()
    m = re.search(r"(\d{4})", s)
    if m:
        y = int(m.group(1))
        if 1900 <= y <= 2100:
            return y
    return None


def is_year_compatible(input_year, matched_year):
    """年份兼容检查（±1 年）"""
    if not input_year or not matched_year:
        return True
    try:
        return abs(int(input_year) - int(matched_year)) <= 1
    except (ValueError, TypeError):
        return True


# ========== Crossref API ==========

def get_year_from_crossref(item):
    """从 Crossref item 提取年份"""
    for key in ("issued", "published", "published-print", "published-online"):
        try:
            return item[key]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            continue
    return None


def get_first_author_lastname(author_str):
    """提取第一作者的姓（英文）"""
    if not author_str or str(author_str).lower() == 'nan':
        return ""
    author_str = str(author_str).strip()
    # 取第一个作者（; 或 , 或 & 分隔）
    first = re.split(r'[;,&]', author_str)[0].strip()
    # 尝试 "LastName, FirstName" 格式
    if ',' in first:
        return first.split(',')[0].strip()
    # 尝试 "FirstName LastName" 格式，取最后一个词
    parts = first.split()
    if len(parts) >= 2:
        return parts[-1]
    return first


def search_crossref(title, threshold=DEFAULT_THRESHOLD, year=None, author=None):
    """通过 Crossref API 搜索文献（标题 + 第一作者），同时返回期刊缩写和学科信息"""
    # 构建 bibliographic 查询：标题 + 第一作者姓
    lastname = get_first_author_lastname(author) if author else ""
    query_parts = [title]
    if lastname:
        query_parts.append(lastname)
    bibliographic = " ".join(query_parts)
    url = (f"https://api.crossref.org/works?rows=5"
           f"&select=DOI,URL,title,author,issued,published,published-print,"
           f"published-online,short-container-title,container-title,subject,publisher"
           f"&query.bibliographic={quote(bibliographic)}")
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("message", {}).get("items", [])

        best = None
        best_score = 0
        for item in items:
            matched_title = item.get("title", [None])[0]
            matched_year = get_year_from_crossref(item)
            raw = title_score(title, matched_title)

            if raw < threshold:
                continue
            if not is_year_compatible(year, matched_year):
                continue

            score = raw + (0.05 if int(year or 0) == int(matched_year or 0) else 0)
            if score > best_score:
                best_score = score
                doi = normalize_doi(item.get("DOI", ""))
                # 提取期刊缩写
                journal_abbr = ""
                short_titles = item.get("short-container-title", [])
                if short_titles and short_titles[0]:
                    journal_abbr = short_titles[0].strip()
                else:
                    container = item.get("container-title", [])
                    if container and container[0]:
                        journal_abbr = container[0].strip()

                # 提取学科/主题
                subjects = item.get("subject", [])
                research_area = subjects[0] if subjects else ""

                best = {
                    "doi": doi,
                    "url": item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
                    "matched_title": matched_title,
                    "matched_year": matched_year,
                    "similarity": round(raw, 4),
                    "score": round(score, 4),
                    "source": "Crossref",
                    "journal_abbr": journal_abbr,
                    "research_area": research_area,
                    "subjects": subjects,
                }
        return best
    except Exception as e:
        return {"error": str(e), "source": "Crossref"}


# ========== OpenAlex API ==========

def search_openalex(title, threshold=DEFAULT_THRESHOLD, year=None, author=None):
    """通过 OpenAlex API 搜索文献（标题 + 第一作者）"""
    # 构建搜索词：标题 + 第一作者姓
    lastname = get_first_author_lastname(author) if author else ""
    search_terms = [title]
    if lastname:
        search_terms.append(lastname)
    search_query = " ".join(search_terms)
    year_filter = ""
    if year:
        year_filter = f"&filter=from_publication_date:{year}-01-01,to_publication_date:{year}-12-31"
    url = f"https://api.openalex.org/works?per-page=5&search={quote(search_query)}{year_filter}"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("results", [])

        best = None
        best_score = 0
        for item in items:
            matched_title = item.get("title") or item.get("display_name")
            matched_year = item.get("publication_year")
            raw = title_score(title, matched_title)

            if raw < threshold:
                continue
            if not is_year_compatible(year, matched_year):
                continue

            score = raw + (0.05 if int(year or 0) == int(matched_year or 0) else 0)
            if score > best_score:
                best_score = score
                doi = normalize_doi(item.get("doi", ""))
                best = {
                    "doi": doi,
                    "url": f"https://doi.org/{doi}" if doi else None,
                    "matched_title": matched_title,
                    "matched_year": matched_year,
                    "similarity": round(raw, 4),
                    "score": round(score, 4),
                    "source": "OpenAlex",
                }
        return best
    except Exception as e:
        return {"error": str(e), "source": "OpenAlex"}


def find_doi(title, threshold=DEFAULT_THRESHOLD, year=None, author=None):
    """
    综合查找 DOI：先 Crossref，再 OpenAlex
    利用标题 + 第一作者 + 年份 进行匹配
    返回 dict: {doi, url, similarity, source, ...} 或 None
    """
    if not title or not str(title).strip():
        return None

    # 1️⃣ Crossref
    result = search_crossref(title, threshold, year, author)
    if result and "doi" in result and result["doi"]:
        return result

    time.sleep(0.15)

    # 2️⃣ OpenAlex
    result2 = search_openalex(title, threshold, year, author)
    if result2 and "doi" in result2 and result2["doi"]:
        return result2

    # 返回错误信息（如果有的话）以便调试
    if result and "error" in result:
        return result
    if result2 and "error" in result2:
        return result2

    return None
