"""
文献核验模块 —— 实现《学术文献批量下载通用方法 v2》的核验三工具
优先级: Semantic Scholar → Unpaywall → Crossref

铁律：
1. 先核 DOI 再下载 —— 任何数据源的 DOI 都可能标错
2. 先免费后付费 —— 开放仓库 → 机构库 → OA 镜像 → 出版商开放 → 付费墙
3. 下载后验 %PDF 头 —— 反爬/付费墙返回的 HTML 也是 HTTP 200
"""

import re
import time
import requests

HEADERS = {
    "User-Agent": "LiteratureTool/3.5 (mailto:researcher@cdut.edu.cn)",
}

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s;,\"']+", re.IGNORECASE)

# Semantic Scholar 限流控制：约 1 req/s，429 时退避
_last_s2_call = 0.0
S2_MIN_INTERVAL = 1.0  # 相邻 S2 请求最小间隔（秒）
S2_MAX_RETRY = 2       # 429 时最多重试次数


def _s2_throttle():
    """S2 限流：保证请求间隔 >= 1s。"""
    global _last_s2_call
    elapsed = time.time() - _last_s2_call
    if elapsed < S2_MIN_INTERVAL:
        time.sleep(S2_MIN_INTERVAL - elapsed)
    _last_s2_call = time.time()


def _s2_get(url, timeout=15):
    """S2 专用 GET：限速 + 429 退避重试。"""
    for attempt in range(S2_MAX_RETRY + 1):
        _s2_throttle()
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp
        except Exception:
            if attempt < S2_MAX_RETRY:
                time.sleep(1 + attempt)
                continue
            return None
    return None


def normalize_doi(doi):
    """清理 DOI 格式：去 https://doi.org/ 前缀、doi: 前缀、大小写敏感保留。"""
    if not doi:
        return ""
    doi = str(doi).strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    return doi.strip()


def _safe_get(url, timeout=15, **kwargs):
    """带超时与 UA 的 GET，失败返回 None。"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, **kwargs)
        resp.raise_for_status()
        return resp
    except Exception:
        return None


def _norm_title(text):
    """轻量标题归一化（用于相似度比较）。"""
    if not text:
        return ""
    s = str(text).lower()
    s = re.sub(r"&", " and ", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", " ", s)
    return s.strip()


def title_similarity(a, b):
    """标题相似度 0~1（与 doi_fetcher.title_score 同口径）。"""
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.94
    ta, tb = set(na.split()), set(nb.split())
    hits = sum(1 for t in ta if t in tb)
    return hits / max(len(ta), len(tb))


# ═══════════════ 工具 1: Semantic Scholar（核验 + OA 镜像） ═══════════════

def check_semanticscholar(doi, title=None, timeout=15):
    """
    Semantic Scholar 核验 DOI + 附赠 OA 直链。
    返回 dict: {exists, matched_title, similarity, year, oa_url, status}
    限流约 1 req/s。
    """
    doi = normalize_doi(doi)
    url = (f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
           f"?fields=title,year,externalIds,openAccessPdf,isOpenAccess")
    resp = _s2_get(url, timeout=timeout)
    if resp is None:
        return {"exists": None, "matched_title": None, "similarity": 0.0,
                "year": None, "oa_url": None, "status": "API错误/限流"}

    if resp.status_code == 404:
        return {"exists": False, "matched_title": None, "similarity": 0.0,
                "year": None, "oa_url": None, "status": "不存在"}
    if resp.status_code == 429:
        return {"exists": None, "matched_title": None, "similarity": 0.0,
                "year": None, "oa_url": None, "status": "限流"}

    try:
        data = resp.json()
    except Exception:
        return {"exists": None, "matched_title": None, "similarity": 0.0,
                "year": None, "oa_url": None, "status": "解析失败"}

    matched_title = data.get("title")
    year = data.get("year")
    oa_url = None
    oapdf = data.get("openAccessPdf") or {}
    if oapdf.get("url"):
        oa_url = oapdf["url"]
    similarity = title_similarity(title, matched_title) if title and matched_title else 0.0
    return {
        "exists": True,
        "matched_title": matched_title,
        "similarity": similarity,
        "year": year,
        "oa_url": oa_url,
        "status": "GOLD" if data.get("isOpenAccess") else ("GREEN" if oa_url else "CLOSED"),
    }


def search_semanticscholar_by_title(title, year=None, timeout=15):
    """
    按标题搜索 Semantic Scholar，返回候选列表（含 DOI + OA）。
    用于 DOI 缺失时找回真实 DOI。
    """
    if not title:
        return []
    url = ("https://api.semanticscholar.org/graph/v1/paper/search"
           f"?query={requests.utils.quote(title)}&fields=title,year,externalIds,openAccessPdf&limit=3")
    resp = _s2_get(url, timeout=timeout)
    if resp is None or resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    out = []
    for item in data.get("data", []) or []:
        ext = item.get("externalIds") or {}
        doi = ext.get("DOI") or ""
        oa_url = None
        oapdf = item.get("openAccessPdf") or {}
        if oapdf.get("url"):
            oa_url = oapdf["url"]
        sim = title_similarity(title, item.get("title"))
        out.append({
            "doi": normalize_doi(doi),
            "matched_title": item.get("title"),
            "year": item.get("year"),
            "similarity": sim,
            "oa_url": oa_url,
        })
    return out


# ═══════════════ 工具 2: Unpaywall（OA 镜像权威） ═══════════════

def check_unpaywall(doi, email="researcher@cdut.edu.cn", timeout=15):
    """
    Unpaywall 查询 OA 状态与镜像。
    返回 dict: {is_oa, oa_url, oa_locations, title, year}
    老文献（1980s 前）常 404 —— 不代表无 OA，需 Crossref 复核。
    """
    doi = normalize_doi(doi)
    url = f"https://api.unpaywall.org/v2/{requests.utils.quote(doi)}?email={email}"
    resp = _safe_get(url, timeout=timeout)
    if resp is None or resp.status_code != 200:
        return {"is_oa": None, "oa_url": None, "oa_locations": [],
                "title": None, "year": None, "status": "无记录/API错误"}

    try:
        data = resp.json()
    except Exception:
        return {"is_oa": None, "oa_url": None, "oa_locations": [],
                "title": None, "year": None, "status": "解析失败"}

    best = data.get("best_oa_location") or {}
    oa_url = best.get("url_for_pdf") or best.get("url")
    locations = []
    for loc in data.get("oa_locations") or []:
        u = loc.get("url_for_pdf") or loc.get("url")
        if u:
            locations.append({
                "url": u,
                "host": (loc.get("host_type") or ""),
                "version": (loc.get("version") or ""),
            })
    return {
        "is_oa": bool(data.get("is_oa")),
        "oa_url": oa_url,
        "oa_locations": locations,
        "title": data.get("title"),
        "year": data.get("year"),
        "status": "OA" if data.get("is_oa") else "非OA",
    }


# ═══════════════ 工具 3: Crossref（标题权威核验） ═══════════════

def verify_doi_crossref(doi, title=None, timeout=15):
    """
    Crossref 核验 DOI 是否存在 + 标题是否匹配（权威判定）。
    返回 dict: {exists, matched_title, similarity, year, doi}
    """
    doi = normalize_doi(doi)
    url = f"https://api.crossref.org/works/{requests.utils.quote(doi)}"
    resp = _safe_get(url, timeout=timeout)
    if resp is None or resp.status_code != 200:
        return {"exists": False, "matched_title": "", "similarity": 0.0,
                "year": None, "doi": doi}

    try:
        msg = resp.json().get("message", {})
    except Exception:
        return {"exists": False, "matched_title": "", "similarity": 0.0,
                "year": None, "doi": doi}

    matched_title = ""
    titles = msg.get("title") or []
    if titles:
        matched_title = str(titles[0])
    year = None
    for key in ("issued", "published", "published-print", "published-online"):
        try:
            year = msg[key]["date-parts"][0][0]
            break
        except (KeyError, IndexError, TypeError):
            continue
    similarity = title_similarity(title, matched_title) if title and matched_title else 0.0
    return {
        "exists": True,
        "matched_title": matched_title,
        "similarity": similarity,
        "year": year,
        "doi": doi,
    }


def search_crossref_by_title(title, year=None, author=None, timeout=15):
    """
    Crossref 按标题检索，返回候选列表（DOI 缺失时找回真实 DOI）。
    """
    if not title:
        return []
    query = title
    if author:
        query += " " + author
    url = ("https://api.crossref.org/works?rows=5"
           f"&select=DOI,title,issued,container-title,short-container-title"
           f"&query.bibliographic={requests.utils.quote(query)}")
    resp = _safe_get(url, timeout=timeout)
    if resp is None or resp.status_code != 200:
        return []
    try:
        items = resp.json().get("message", {}).get("items", [])
    except Exception:
        return []
    out = []
    for item in items:
        matched_title = ""
        titles = item.get("title") or []
        if titles:
            matched_title = str(titles[0])
        yr = None
        for key in ("issued", "published", "published-print", "published-online"):
            try:
                yr = item[key]["date-parts"][0][0]
                break
            except (KeyError, IndexError, TypeError):
                continue
        sim = title_similarity(title, matched_title)
        out.append({
            "doi": normalize_doi(item.get("DOI", "")),
            "matched_title": matched_title,
            "year": yr,
            "similarity": sim,
        })
    return out


# ═══════════════ 综合核验流程 ═══════════════

def find_doi_by_title(title, year=None, author=None, threshold=0.6):
    """
    按标题找回真实 DOI（用于清单缺 DOI 或 DOI 标错的情况）。
    策略（按文档优先级）：Crossref 标题检索（权威）→ Semantic Scholar 标题搜索（尽力）。
    返回最佳候选 dict 或 None。所有候选都会带 similarity 供阈值过滤。
    """
    if not title or not str(title).strip():
        return None

    candidates = []

    # 1. Crossref 标题检索（权威，稳定，无限流问题）
    cr = search_crossref_by_title(title, year, author)
    for item in cr:
        if item.get("doi") and item.get("similarity", 0) >= threshold:
            candidates.append({**item, "source": "Crossref"})

    # 2. Semantic Scholar 标题搜索（尽力，可能限流返回空）
    try:
        s2 = search_semanticscholar_by_title(title, year)
        for item in s2:
            if item.get("doi") and item.get("similarity", 0) >= threshold:
                candidates.append({**item, "source": "SemanticScholar"})
    except Exception:
        pass

    if not candidates:
        return None

    # 年份加权：匹配年份的候选优先
    for item in candidates:
        item["score"] = item.get("similarity", 0) + (
            0.05 if year and item.get("year") and abs(int(year) - int(item["year"])) <= 1 else 0
        )
    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]

    # 最佳候选再过一遍 Crossref 权威核验（防止 S2 或检索结果出错）
    verify = verify_doi_crossref(best["doi"], title)
    if verify.get("exists") and verify.get("similarity", 0) >= threshold:
        return {
            "doi": verify["doi"],
            "matched_title": verify["matched_title"],
            "year": verify["year"],
            "similarity": verify["similarity"],
            "source": best.get("source", "Crossref"),
        }
    return best


def verify_doi_full(doi, title=None, year=None):
    """
    三工具综合核验一个 DOI：
    Semantic Scholar（最快核验）→ Crossref（权威判定标题匹配）→ Unpaywall（OA 信息）
    返回 dict:
    {
      "doi": 规范化后的 DOI,
      "exists": True/False/None,       # None=API 错误无法判定
      "title_ok": True/False,          # 标题匹配度 >= 0.6
      "similarity": float,
      "matched_title": str,
      "year": int|None,
      "oa_url": str|None,              # 免费 PDF 直链（如有）
      "oa_locations": [...],           # Unpaywall OA 镜像列表
      "sources": [...]                 # 各工具结果摘要
    }
    """
    doi = normalize_doi(doi)
    sources = []

    # 1. Semantic Scholar 快速核验 + OA
    s2 = check_semanticscholar(doi, title)
    sources.append({"tool": "SemanticScholar", **s2})

    # 2. Crossref 权威核验标题
    cr = verify_doi_crossref(doi, title)
    sources.append({"tool": "Crossref", **cr})

    # 3. Unpaywall OA 信息
    up = check_unpaywall(doi)
    sources.append({"tool": "Unpaywall", **up})

    exists = None
    if cr.get("exists"):
        exists = True
    elif s2.get("exists") is False and cr.get("exists") is False:
        exists = False

    matched_title = cr.get("matched_title") or s2.get("matched_title") or ""
    similarity = cr.get("similarity", 0.0) or s2.get("similarity", 0.0) or 0.0
    year_out = cr.get("year") or s2.get("year") or None

    oa_url = s2.get("oa_url") or up.get("oa_url")
    oa_locations = up.get("oa_locations") or []

    return {
        "doi": doi,
        "exists": exists,
        "title_ok": bool(matched_title) and similarity >= 0.6,
        "similarity": similarity,
        "matched_title": matched_title,
        "year": year_out,
        "oa_url": oa_url,
        "oa_locations": oa_locations,
        "sources": sources,
    }
