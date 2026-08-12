"""
本地 PDF 识别与批量重命名模块。

命名格式:
作者_年份_期刊首字母缩写_研究区域_题名简写.pdf
"""

import os
import re
import requests

from pdf_downloader import (
    build_filename,
    clean_filename,
    extract_metadata_from_pdf,
    _extract_geo_from_text,
    get_journal_initials,
)

try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False


HEADERS = {
    "User-Agent": "LiteratureTool/3.4 (mailto:researcher@example.com)"
}

DOI_PATTERN = re.compile(
    r'\b10\.\d{4,9}/[-._;()/:A-Z0-9]+',
    re.IGNORECASE
)


def _safe_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _extract_year(value):
    """从文本中提取出版年份，优先识别出版相关上下文。"""
    text = _safe_text(value)
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)

    priority_patterns = [
        r'(?:published|available online|online|issued|publication|copyright|©)\D{0,60}((?:19|20)\d{2})',
        r'((?:19|20)\d{2})\D{0,30}(?:Elsevier|Springer|Wiley|MDPI|IEEE|Taylor|Francis|Nature|ScienceDirect|SAGE)',
        r'D:((?:19|20)\d{2})',
        r'(?<!\d)((?:19|20)\d{2})(?!\d)',
    ]
    for pattern in priority_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return str(year)
    return ""


def _extract_year_from_pdf_info(pdf_path):
    """从 PDF 元数据和文件名中兜底提取年份。"""
    chunks = []
    if HAS_PYPDF2:
        try:
            with open(pdf_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                info = reader.metadata or {}
                for key in ("/CreationDate", "/ModDate", "/Title", "/Subject", "/Keywords"):
                    value = info.get(key)
                    if value:
                        chunks.append(str(value))
        except Exception:
            pass
    chunks.append(os.path.basename(pdf_path))
    return _extract_year(" ".join(chunks))




def _has_year(value):
    return bool(_extract_year(value))


def extract_year_from_pdf(pdf_path):
    """从 PDF 元数据、正文前几页和文件名中提取年份。"""
    chunks = []
    info_year = _extract_year_from_pdf_info(pdf_path)
    if info_year:
        chunks.append(info_year)
    text = extract_text_from_pdf(pdf_path, max_pages=8)
    if text:
        chunks.append(text)
    chunks.append(os.path.basename(pdf_path))
    return _extract_year(" ".join(chunks))

def _first_author_from_crossref(authors):
    if not authors:
        return ""
    first = authors[0]
    family = _safe_text(first.get("family"))
    given = _safe_text(first.get("given"))
    if family:
        return family
    return given


def _year_from_crossref(item):
    for key in ("issued", "published", "published-print", "published-online"):
        try:
            return str(item[key]["date-parts"][0][0])
        except (KeyError, IndexError, TypeError):
            continue
    return ""


def _clean_doi(raw):
    doi = _safe_text(raw)
    doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', doi, flags=re.IGNORECASE)
    doi = re.sub(r'^doi:\s*', '', doi, flags=re.IGNORECASE)
    doi = doi.rstrip('.,;)]}>')
    return doi




def _normalize_for_match(text):
    text = _safe_text(text).lower()
    text = re.sub(r'[^a-z0-9\u4e00-\u9fa5]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _title_score(source, candidate):
    a = _normalize_for_match(source)
    b = _normalize_for_match(candidate)
    if not a or not b:
        return 0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.92
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return 0
    hits = len(a_tokens & b_tokens)
    return hits / max(len(a_tokens), len(b_tokens))


def _nonempty_dict(row):
    return {k: v for k, v in dict(row).items() if _safe_text(v)}


def _find_excel_match(pdf_path, current_meta, excel_rows):
    """按 DOI 优先，其次标题和文件名，从 Excel 行中找最可能的文献。"""
    if not excel_rows:
        return {}, 0
    current_doi = _clean_doi(current_meta.get("DOI", "")).lower()
    old_name = os.path.splitext(os.path.basename(pdf_path))[0]
    current_title = current_meta.get("title", "")

    if current_doi:
        for row in excel_rows:
            row_doi = _clean_doi(row.get("DOI", "")).lower()
            if row_doi and row_doi == current_doi:
                return _nonempty_dict(row), 1.0

    best = {}
    best_score = 0
    for row in excel_rows:
        title = row.get("title", "")
        score = max(
            _title_score(current_title, title),
            _title_score(old_name, title),
            _title_score(old_name, row.get("PDF_NAME", "")),
        )
        row_pdf = _normalize_for_match(row.get("PDF_NAME", ""))
        if row_pdf and row_pdf in _normalize_for_match(old_name):
            score = max(score, 0.95)
        if score > best_score:
            best_score = score
            best = row

    if best_score >= 0.35:
        return _nonempty_dict(best), best_score
    return {}, best_score


def extract_text_from_pdf(pdf_path, max_pages=3):
    """读取 PDF 前几页文本，用于 DOI 和研究区识别。"""
    if not HAS_PYPDF2:
        return ""
    try:
        parts = []
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages[:max_pages]:
                try:
                    parts.append(page.extract_text() or "")
                except Exception:
                    continue
        return "\n".join(parts)
    except Exception:
        return ""


def extract_doi_from_pdf(pdf_path):
    """优先从前几页文本识别 DOI。"""
    text = extract_text_from_pdf(pdf_path, max_pages=3)
    match = DOI_PATTERN.search(text)
    return _clean_doi(match.group(0)) if match else ""


def fetch_crossref_by_doi(doi):
    """根据 DOI 获取准确元数据。"""
    doi = _clean_doi(doi)
    if not doi:
        return {}
    url = f"https://api.crossref.org/works/{doi}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        item = resp.json().get("message", {})
    except Exception:
        return {}

    title = ""
    titles = item.get("title") or []
    if titles:
        title = _safe_text(titles[0])

    journal = ""
    containers = item.get("container-title") or []
    if containers:
        journal = _safe_text(containers[0])

    journal_abbr = ""
    short_titles = item.get("short-container-title") or []
    if short_titles and short_titles[0]:
        journal_abbr = _safe_text(short_titles[0])
    elif journal:
        journal_abbr = get_journal_initials(journal)

    abstract = _safe_text(item.get("abstract"))
    abstract = re.sub(r'<[^>]+>', '', abstract)
    area = _extract_geo_from_text(abstract) if abstract else ""

    return {
        "DOI": doi,
        "title": title,
        "AUTHOR": _first_author_from_crossref(item.get("author") or []),
        "YEAR": _year_from_crossref(item),
        "JOURNAL": journal,
        "JOURNAL_ABBR": journal_abbr,
        "RESEARCH_AREA": area,
    }


def guess_metadata_from_pdf(pdf_path):
    """无 DOI 或联网失败时，从 PDF 自身尽量猜测元数据。"""
    meta = extract_metadata_from_pdf(pdf_path) or {}
    text = meta.get("first_page_text") or extract_text_from_pdf(pdf_path, max_pages=2)
    title = meta.get("pdf_title") or ""
    if not title and text:
        for line in text.splitlines()[:12]:
            line = line.strip()
            if 20 <= len(line) <= 220:
                title = line
                break

    area = meta.get("research_area") or ""
    if not area:
        area = _extract_geo_from_text(text)

    return {
        "DOI": extract_doi_from_pdf(pdf_path),
        "title": title or os.path.splitext(os.path.basename(pdf_path))[0],
        "AUTHOR": "",
        "YEAR": extract_year_from_pdf(pdf_path),
        "JOURNAL": "",
        "JOURNAL_ABBR": "",
        "RESEARCH_AREA": area,
    }


def analyze_pdf(pdf_path, excel_rows=None, use_ai=False, ai_cfg=None):
    """分析单个 PDF，返回预览信息；Excel 元数据优先。

    use_ai=True 时：Excel 匹配失败或关键字段缺失，调用 DeepSeek 补全元数据再命名。
    """
    doi = extract_doi_from_pdf(pdf_path)
    row = fetch_crossref_by_doi(doi) if doi else {}
    fallback = guess_metadata_from_pdf(pdf_path)
    merged = {**fallback, **{k: v for k, v in row.items() if _safe_text(v)}}

    excel_row, excel_score = _find_excel_match(pdf_path, merged, excel_rows)
    if excel_row:
        # Excel 是人工整理过的数据源，作者/年份/期刊/题名优先使用 Excel。
        merged = {**merged, **excel_row}
        source = f"Excel({int(excel_score * 100)}%)"
    else:
        source = "Crossref" if row else "PDF"

    if not merged.get("RESEARCH_AREA"):
        text = extract_text_from_pdf(pdf_path, max_pages=3)
        merged["RESEARCH_AREA"] = _extract_geo_from_text(text) or "Area"

    if not _has_year(merged.get("YEAR", "")):
        pdf_year = extract_year_from_pdf(pdf_path)
        if pdf_year:
            merged["YEAR"] = pdf_year

    # AI 命名兜底：关键字段缺失且已启用 AI 时，调用 DeepSeek 补全
    if use_ai and ai_cfg:
        need_ai = False
        if not merged.get("title") or not _safe_text(merged.get("title")):
            need_ai = True
        if not merged.get("AUTHOR") or str(merged.get("AUTHOR", "")).lower() in ("unknown", "nan", ""):
            need_ai = True
        if not merged.get("RESEARCH_AREA") or merged.get("RESEARCH_AREA") in ("Area", "Unknown"):
            need_ai = True
        if not _has_year(merged.get("YEAR", "")):
            need_ai = True

        if need_ai:
            try:
                from deepseek_ai import ai_fix_metadata
                ai_meta = ai_fix_metadata(
                    str(merged.get("title", ""))[:200],
                    year=merged.get("YEAR"),
                    author=merged.get("AUTHOR"),
                    journal=merged.get("JOURNAL"),
                    doi=doi,
                    cfg=ai_cfg,
                )
                if ai_meta:
                    mapping = {
                        "author": "AUTHOR", "year": "YEAR",
                        "journal": "JOURNAL", "journal_abbr": "JOURNAL_ABBR",
                        "research_area": "RESEARCH_AREA",
                    }
                    for src_key, dst_key in mapping.items():
                        if ai_meta.get(src_key) and not _safe_text(merged.get(dst_key)):
                            merged[dst_key] = ai_meta[src_key]
                    source = "AI" if source in ("PDF", "Crossref") else source + "+AI"
            except Exception:
                pass

    new_name = build_filename(merged)
    return {
        "path": pdf_path,
        "old_name": os.path.basename(pdf_path),
        "new_name": new_name,
        "source": source,
        "doi": merged.get("DOI", ""),
        "title": merged.get("title", ""),
        "metadata": merged,
    }


def scan_pdf_folder(folder, recursive=False, progress_callback=None, excel_rows=None,
                    use_ai=False, ai_cfg=None):
    """扫描文件夹中的 PDF 并生成重命名预览。

    use_ai=True 时对元数据缺失的 PDF 调用 DeepSeek 补全。
    """
    pdfs = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(".pdf"):
                    pdfs.append(os.path.join(root, name))
    else:
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and name.lower().endswith(".pdf"):
                pdfs.append(path)

    results = []
    total = len(pdfs)
    for i, path in enumerate(pdfs, start=1):
        try:
            item = analyze_pdf(path, excel_rows=excel_rows, use_ai=use_ai, ai_cfg=ai_cfg)
        except Exception as exc:
            item = {
                "path": path,
                "old_name": os.path.basename(path),
                "new_name": "",
                "source": "错误",
                "doi": "",
                "title": str(exc),
                "metadata": {},
            }
        results.append(item)
        if progress_callback:
            progress_callback(i, total, item)
    return results


def apply_renames(items):
    """执行重命名，返回更新后的结果列表。"""
    updated = []
    for item in items:
        item = dict(item)
        old_path = item.get("path", "")
        new_name = clean_filename(item.get("new_name", ""))
        if not old_path or not os.path.isfile(old_path) or not new_name:
            item["status"] = "跳过"
            updated.append(item)
            continue

        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if os.path.normcase(os.path.normpath(old_path)) == os.path.normcase(os.path.normpath(new_path)):
            item["status"] = "已是目标名称"
            updated.append(item)
            continue

        base, ext = os.path.splitext(new_path)
        counter = 1
        candidate = new_path
        while os.path.exists(candidate):
            candidate = f"{base}_{counter}{ext}"
            counter += 1

        try:
            os.rename(old_path, candidate)
            item["status"] = "已重命名"
            item["new_path"] = candidate
            item["new_name"] = os.path.basename(candidate)
        except Exception as exc:
            item["status"] = f"失败: {exc}"
        updated.append(item)
    return updated


