"""
DeepSeek AI 补全模块 —— 文献获取的第三条兜底链路
当 Crossref/OpenAlex 匹配失败、常规 PDF 下载源失败、命名元数据缺失时，
调用 DeepSeek（OpenAI 兼容 API）用语义理解补足机器检索漏掉的文献。

特性：
- 配置持久化到 exe 同级 config.json
- 严格 JSON 输出 + 超时 + 重试，任何失败静默降级，绝不影响主流程
- 成本控制：max_tokens 上限、串行调用、失败快速返回
"""

import os
import sys
import json
import time
import re
import requests

# ═══════════════ 配置 ═══════════════

DEFAULT_CONFIG = {
    "deepseek_enabled": False,
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "timeout": 30,
    "max_retries": 2,
}

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s;,\"']+")
USER_AGENT_FOR_VERIFY = "LiteratureTool/3.5 (mailto:researcher@example.com)"


def _config_path():
    """配置文件位于 exe 同级目录（PyInstaller 打包后）；开发模式下位于源码目录。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "config.json")


def load_config():
    """读取 config.json；不存在或损坏时返回默认配置。"""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            cfg.update(saved)
    except Exception:
        pass
    return cfg


def save_config(cfg):
    """写回 config.json（保留未知字段）。"""
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def is_configured(cfg=None):
    """是否已配置并启用。"""
    cfg = cfg or load_config()
    return bool(cfg.get("deepseek_enabled") and str(cfg.get("api_key", "")).strip())


# ═══════════════ DOI 真实性验证 ═══════════════

def verify_doi(doi, title=None, timeout=15):
    """
    用 Crossref API 验证 DOI 是否真实存在，并检查标题是否匹配。

    返回 dict:
    {
      "exists": bool,          # DOI 在 Crossref 中是否存在
      "matched_title": str,    # Crossref 中的真实标题
      "similarity": float,     # 与输入标题的相似度 (0~1)，title 为空时为 0
      "year": int|None,        # 出版年份
    }
    任何异常返回 {"exists": False, "similarity": 0}（不阻塞主流程）。
    """
    doi = str(doi or "").strip()
    if not doi:
        return {"exists": False, "matched_title": "", "similarity": 0.0, "year": None}
    try:
        resp = requests.get(
            f"https://api.crossref.org/works/{doi}",
            headers={"User-Agent": USER_AGENT_FOR_VERIFY},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return {"exists": False, "matched_title": "", "similarity": 0.0, "year": None}
        msg = resp.json().get("message", {})
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
        similarity = 0.0
        if title and matched_title:
            similarity = _title_similarity(str(title), matched_title)
        return {
            "exists": True,
            "matched_title": matched_title,
            "similarity": similarity,
            "year": year,
        }
    except Exception:
        return {"exists": False, "matched_title": "", "similarity": 0.0, "year": None}


def _title_similarity(a, b):
    """轻量标题相似度（与 doi_fetcher.title_score 一致的口径）。"""
    import re
    def norm(s):
        s = str(s).lower()
        s = re.sub(r"&", " and ", s)
        s = re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", " ", s)
        return s.strip()
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.94
    ta, tb = set(na.split()), set(nb.split())
    hits = sum(1 for t in ta if t in tb)
    return hits / max(len(ta), len(tb))


def diagnose_network(cfg=None):
    """
    网络诊断：检查 DeepSeek API 可达性，返回 (ok, message)。
    用于 AI 调用失败时给用户明确的网络/代理提示。
    """
    cfg = cfg or load_config()
    base_url = str(cfg.get("base_url", "")).strip().rstrip("/") or DEFAULT_CONFIG["base_url"]
    try:
        resp = requests.get(f"{base_url}/", timeout=8,
                            headers={"User-Agent": "LiteratureTool/3.5"})
        return True, f"API 可达（HTTP {resp.status_code}）"
    except requests.exceptions.ProxyError as e:
        return False, f"网络代理错误：{e.__class__.__name__}。请检查 Clash/代理是否已开启（常见端口 7890/7897）。"
    except requests.exceptions.ConnectionError as e:
        return False, f"无法连接 {base_url}（{e.__class__.__name__}）。请检查网络/代理/VPN。"
    except requests.exceptions.Timeout:
        return False, "连接超时。请检查网络或代理。"
    except Exception as e:
        return False, f"网络异常：{e.__class__.__name__}: {e}"


# ═══════════════ 核心调用 ═══════════════

def chat_json(system_prompt, user_prompt, cfg=None, temperature=0.1):
    """
    调用 OpenAI 兼容 chat/completions 接口，强制 JSON 输出。

    端点: {base_url}/chat/completions
    返回解析后的 dict；任何异常/超时/解析失败返回 None（调用方静默降级）。
    """
    cfg = cfg or load_config()
    api_key = str(cfg.get("api_key", "")).strip()
    if not api_key:
        return None

    base_url = str(cfg.get("base_url", "")).strip().rstrip("/") or DEFAULT_CONFIG["base_url"]
    model = str(cfg.get("model", "")).strip() or DEFAULT_CONFIG["model"]
    timeout = int(cfg.get("timeout", 30))
    max_retries = int(cfg.get("max_retries", 2))

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }

    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            # 优先直接解析 JSON，失败则用正则抽取 { ... }
            try:
                return json.loads(content)
            except Exception:
                m = re.search(r"\{.*\}", content, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
                return None
        except Exception:
            if attempt < max_retries:
                time.sleep(1 + attempt)
                continue
            return None
    return None


def test_connection(cfg=None):
    """发送一条最小请求验证 API Key / 端点 / 模型是否可用。"""
    cfg = cfg or load_config()
    if not str(cfg.get("api_key", "")).strip():
        return False, "未填写 API Key"
    result = chat_json(
        "你是一个连通性测试助手。",
        '请只回复 JSON：{"ok": true}',
        cfg=cfg, temperature=0,
    )
    if result is not None:
        return True, "连接成功"
    return False, "连接失败（请检查 Key / Base URL / 网络）"


def chat_text(system_prompt, user_prompt, cfg=None, temperature=0.3, max_tokens=2000):
    """
    普通对话（不强制 JSON 输出），返回 AI 回复的纯文本。
    用于 AI 对话窗口。任何异常返回 None。
    """
    cfg = cfg or load_config()
    api_key = str(cfg.get("api_key", "")).strip()
    if not api_key:
        return None

    base_url = str(cfg.get("base_url", "")).strip().rstrip("/") or DEFAULT_CONFIG["base_url"]
    model = str(cfg.get("model", "")).strip() or DEFAULT_CONFIG["model"]
    timeout = int(cfg.get("timeout", 30))
    max_retries = int(cfg.get("max_retries", 2))

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception:
            if attempt < max_retries:
                time.sleep(1 + attempt)
                continue
            return None
    return None


# ═══════════════ 主题批量文献搜集 ═══════════════

def parse_topic_query(text, cfg=None):
    """
    用 AI 把自然语言主题解析成结构化搜索参数（JSON）。
    例: "找 2020 年以来北大西洋中脊地幔熔融的文献" →
        {"keywords": ["Mid-Atlantic Ridge", "mantle melting"], "year_from": 2020, "limit": 10}
    返回 dict 或 None。
    """
    if not text or not str(text).strip():
        return None
    cfg = cfg or load_config()
    if not is_configured(cfg):
        return None

    system = (
        "你是文献检索规划专家。把用户的自然语言需求解析成结构化搜索参数。\n"
        "输出 JSON: {\"keywords\": [英文关键词数组], \"year_from\": 年份或null, "
        "\"year_to\": 年份或null, \"limit\": 建议数量(5-30)}\n"
        "keywords 必须用英文，2-4 个关键词。必须只输出 JSON。"
    )
    result = chat_json(system, f"需求: {str(text)[:300]}", cfg=cfg, temperature=0.1)
    if not isinstance(result, dict):
        return None
    keywords = [str(k).strip() for k in (result.get("keywords") or []) if str(k).strip()]
    if not keywords:
        return None
    return {
        "keywords": keywords,
        "year_from": result.get("year_from") or None,
        "year_to": result.get("year_to") or None,
        "limit": max(5, min(30, int(result.get("limit") or 10))),
    }


def search_works_by_topic(query, limit=10, timeout=20):
    """
    用 Crossref 按主题批量搜索文献。
    query: {"keywords": [...], "year_from": ..., "year_to": ...}
    返回 [{title, authors, year, doi, journal}] 列表。
    """
    import requests as _req
    keywords = query.get("keywords", [])
    if not keywords:
        return []
    search_query = " ".join(keywords)
    filters = []
    if query.get("year_from"):
        filters.append(f"from-pub-date:{query['year_from']}-01-01")
    if query.get("year_to"):
        filters.append(f"until-pub-date:{query['year_to']}-12-31")
    filter_str = ",".join(filters)

    url = ("https://api.crossref.org/works?rows={}&select=DOI,title,author,issued,"
           "container-title,short-container-title".format(limit))
    if filter_str:
        url += f"&filter={filter_str}"
    url += f"&query.bibliographic={_req.utils.quote(search_query)}"
    try:
        resp = _req.get(url, headers={"User-Agent": "LiteratureTool/3.5 (mailto:researcher@cdut.edu.cn)"},
                        timeout=timeout)
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except Exception:
        return []

    out = []
    for item in items:
        titles = item.get("title") or []
        title = str(titles[0]) if titles else ""
        if not title:
            continue
        year = None
        for key in ("issued", "published", "published-print", "published-online"):
            try:
                year = item[key]["date-parts"][0][0]
                break
            except (KeyError, IndexError, TypeError):
                continue
        authors = []
        for a in (item.get("author") or [])[:5]:
            name = " ".join(x for x in [a.get("family", ""), a.get("given", "")] if x)
            if name:
                authors.append(name)
        journal = ""
        for key in ("short-container-title", "container-title"):
            vals = item.get(key) or []
            if vals and vals[0]:
                journal = str(vals[0])
                break
        out.append({
            "title": title,
            "authors": authors,
            "year": year,
            "doi": normalize_doi_light(item.get("DOI", "")),
            "journal": journal,
        })
    return out


def normalize_doi_light(doi):
    """轻量 DOI 清理（模块内复用，避免循环依赖）。"""
    doi = str(doi or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    return doi.strip()


# ═══════════════ 业务函数 ① AI 找 DOI ═══════════════

def ai_find_doi(title, year=None, author=None, journal=None, cfg=None):
    """
    根据标题 + 作者 + 年份，让 DeepSeek 直接给出最可能的 DOI。
    返回 dict: {found, doi, confidence, reason} 或 None（AI 不可用时）
    """
    if not title or not str(title).strip():
        return None
    cfg = cfg or load_config()
    if not is_configured(cfg):
        return None

    title = str(title).strip()[:200]
    system = (
        "你是文献数据库专家。根据用户提供的文献信息，给出最可能的 DOI。\n"
        "即使作者或期刊缺失，也要根据标题+年份给出最可能的 DOI 候选（confidence 表示你的确信度）。\n"
        "确信存在 confidence 给 0.8 以上；比较确定给 0.6-0.8；只是猜测给 0.5 以下。\n"
        "如果完全无法确定，doi 给空字符串，confidence 给 0。\n"
        "必须只输出 JSON，不要任何多余文字。"
    )
    user = f"标题: {title}\n年份: {year or '未知'}\n作者: {author or '未知'}\n期刊: {journal or '未知'}"

    result = chat_json(system, user, cfg=cfg)
    if not isinstance(result, dict):
        return None

    doi = str(result.get("doi", "") or "").strip()
    try:
        confidence = float(result.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0

    # DOI 格式校验，非法结果丢弃
    if doi and not DOI_RE.match(doi):
        doi = ""
        confidence = 0.0

    # AI 的 found 布尔值仅供参考：只要给出了有效 DOI，就以 confidence 为准
    return {
        "found": bool(doi),
        "doi": doi,
        "confidence": confidence,
        "reason": str(result.get("reason", ""))[:200],
    }


# ═══════════════ 业务函数 ② AI 找 PDF 链接 ═══════════════

def ai_find_pdf_links(doi, title=None, cfg=None):
    """
    让 DeepSeek 给出最多 5 个开放获取 PDF 链接候选。
    返回 [{"url", "source"}] 或 None（AI 不可用/无结果）
    """
    if not doi and not title:
        return None
    cfg = cfg or load_config()
    if not is_configured(cfg):
        return None

    doi = str(doi or "").strip()[:120]
    title = str(title or "").strip()[:200]
    system = (
        "你是开放获取文献专家。根据 DOI 或标题，列出最多 5 个可能下载到 "
        "PDF 的开放获取链接。优先 PMC、arXiv、机构知识库、CORE、ResearchGate。\n"
        "只给确定存在的 URL，不要编造。必须只输出 JSON。"
    )
    user = f"DOI: {doi or '未知'}\n标题: {title or '未知'}"

    result = chat_json(system, user, cfg=cfg)
    if not isinstance(result, dict):
        return None

    links = result.get("links")
    if not isinstance(links, list):
        return None

    cleaned = []
    for item in links:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "") or "").strip()
        if not url.startswith("https://"):
            continue
        source = str(item.get("source", ""))[:40]
        cleaned.append({"url": url, "source": source})
    return cleaned[:5] or None


# ═══════════════ 业务函数 ③ AI 补元数据 ═══════════════

def ai_fix_metadata(title, year=None, author=None, journal=None, doi=None, cfg=None):
    """
    补全文献元数据，用于命名优化。
    返回 dict: {author, year, journal, journal_abbr, research_area} 或 None
    """
    if not title:
        return None
    cfg = cfg or load_config()
    if not is_configured(cfg):
        return None

    title = str(title).strip()[:200]
    system = (
        "你是学术元数据专家。根据已知信息补全文献元数据，输出标准字段。\n"
        "不确定的字段输出 null，不要编造。必须只输出 JSON。"
    )
    user = (
        f"标题: {title}\n年份: {year or '未知'}\n作者: {author or '未知'}\n"
        f"期刊: {journal or '未知'}\nDOI: {doi or '未知'}"
    )

    result = chat_json(system, user, cfg=cfg)
    if not isinstance(result, dict):
        return None

    out = {}
    for key in ("author", "year", "journal", "journal_abbr", "research_area"):
        val = result.get(key)
        if val is not None and str(val).strip() and str(val).lower() != "null":
            out[key] = str(val).strip()
    return out or None


# ═══════════════ 便捷封装：单篇文献 AI 全兜底 ═══════════════

def ai_complete_missing(record, cfg=None, verify=None):
    """
    对一条获取失败的文献执行 AI 兜底（DOI + PDF 链接 + 元数据）。
    record: {"title", "year", "author", "journal", "doi"}
    verify: 可选核验函数 verify(doi, title) -> dict（默认 lit_verify.verify_doi_full）
    返回:
    {
      "doi": {found, doi, confidence, reason, verified} | None,
      "pdf_links": [{"url", "source"}] | None,
      "metadata": {...} | None
    }
    """
    cfg = cfg or load_config()
    if not is_configured(cfg):
        return {"doi": None, "pdf_links": None, "metadata": None}

    def _default_verify(doi, title):
        try:
            from lit_verify import verify_doi_full
            return verify_doi_full(doi, title)
        except Exception:
            return {"exists": None, "title_ok": False, "similarity": 0.0}

    verify_fn = verify or _default_verify

    out = {}
    title = str(record.get("title", "") or "").strip()
    year = record.get("year")
    author = record.get("author")
    journal = record.get("journal")
    doi = str(record.get("doi", "") or "").strip()

    # AI 找 DOI：结果必须通过真实性核验（Crossref 判定），防止 AI 编造
    if title:
        ai = ai_find_doi(title, year, author, journal, cfg=cfg)
        if ai and ai.get("doi"):
            v = verify_fn(ai["doi"], title)
            ai["verified"] = bool(v.get("exists") and v.get("similarity", 0) >= 0.6)
            ai["verified_similarity"] = v.get("similarity", 0)
            ai["verified_title"] = v.get("matched_title", "")
            out["doi"] = ai
        elif ai:
            out["doi"] = ai
            out["metadata"] = ai_fix_metadata(title, year, author, journal, doi, cfg=cfg)

    if doi or title:
        out["pdf_links"] = ai_find_pdf_links(doi or "", title, cfg=cfg)

    return out
