"""
HTML 结果报告生成模块
处理完成后自动生成可视化统计报告
"""

import os
import webbrowser
from datetime import datetime
import pandas as pd


def generate_report(output_dir, input_file, df, doi_results=None, pdf_results=None):
    """
    生成 HTML 报告并保存到 output_dir

    Args:
        output_dir: 报告输出目录
        input_file: 原始输入文件名
        df: 处理后的 DataFrame
        doi_results: DOI 获取统计 (dict or None)
        pdf_results: PDF 下载统计 (dict or None)
    Returns:
        报告文件路径
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(df)

    # DOI 统计
    doi_found = 0
    doi_failed = 0
    doi_source = {}
    if "DOI" in df.columns:
        doi_found = int(df["DOI"].notna().sum())
        doi_failed = total - doi_found
        if "DOI来源" in df.columns:
            doi_source = df["DOI来源"].value_counts().to_dict()

    # PDF 统计
    pdf_done = 0
    if "PDF链接" in df.columns:
        pdf_done = int(df["PDF链接"].notna().sum() & (df["PDF链接"] != "").sum())

    # 匹配度分布
    match_dist = {}
    if "匹配度" in df.columns:
        for v in df["匹配度"].dropna():
            try:
                pct = int(str(v).replace("%", ""))
                if pct >= 90: bucket = "90-100%"
                elif pct >= 80: bucket = "80-89%"
                elif pct >= 70: bucket = "70-79%"
                else: bucket = "<70%"
                match_dist[bucket] = match_dist.get(bucket, 0) + 1
            except: pass

    # 构建 HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>文献处理报告</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; background:#f1f5f9; color:#1e293b; padding:24px; }}
.container {{ max-width:960px; margin:0 auto; }}
h1 {{ font-size:24px; color:#2563eb; margin-bottom:4px; }}
.subtitle {{ color:#64748b; font-size:13px; margin-bottom:24px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:24px; }}
.card {{ background:#fff; border-radius:10px; padding:16px 20px; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
.card .label {{ font-size:11px; color:#94a3b8; text-transform:uppercase; letter-spacing:.5px; }}
.card .value {{ font-size:28px; font-weight:700; margin-top:4px; }}
.card .sub {{ font-size:11px; color:#94a3b8; margin-top:2px; }}
.section {{ background:#fff; border-radius:10px; padding:20px; margin-bottom:16px; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
.section h2 {{ font-size:15px; color:#334155; margin-bottom:12px; padding-bottom:8px; border-bottom:2px solid #f1f5f9; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th {{ text-align:left; padding:8px 10px; background:#f8fafc; color:#64748b; font-weight:600; border-bottom:1px solid #e2e8f0; }}
td {{ padding:7px 10px; border-bottom:1px solid #f1f5f9; }}
tr:hover {{ background:#f8fafc; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:600; }}
.badge-success {{ background:#dcfce7; color:#15803d; }}
.badge-warn {{ background:#fef3c7; color:#b45309; }}
.badge-fail {{ background:#fee2e2; color:#dc2626; }}
.badge-info {{ background:#dbeafe; color:#2563eb; }}
.bar {{ height:8px; border-radius:4px; background:#e2e8f0; overflow:hidden; margin-top:4px; }}
.bar-fill {{ height:100%; border-radius:4px; transition:width .3s; }}
.footer {{ text-align:center; color:#94a3b8; font-size:11px; margin-top:24px; }}
</style>
</head>
<body>
<div class="container">
<h1>📊 文献处理报告</h1>
<p class="subtitle">输入文件: {input_file} &nbsp;|&nbsp; 生成时间: {now}</p>

<div class="cards">
<div class="card">
<div class="label">文献总数</div>
<div class="value" style="color:#2563eb">{total}</div>
</div>
<div class="card">
<div class="label">DOI 已匹配</div>
<div class="value" style="color:#15803d">{doi_found}</div>
<div class="sub">匹配率 {int(doi_found/total*100) if total>0 else 0}%</div>
</div>
<div class="card">
<div class="label">DOI 未匹配</div>
<div class="value" style="color:#dc2626">{doi_failed}</div>
</div>
<div class="card">
<div class="label">PDF 已下载</div>
<div class="value" style="color:#b45309">{pdf_done}</div>
</div>
</div>
"""

    # 匹配度分布
    if match_dist:
        html += '<div class="section"><h2>📈 匹配度分布</h2>'
        colors = {"90-100%":"#15803d", "80-89%":"#2563eb", "70-79%":"#b45309", "<70%":"#dc2626"}
        for bucket in ["90-100%","80-89%","70-79%","<70%"]:
            count = match_dist.get(bucket, 0)
            pct = count / max(doi_found, 1) * 100
            c = colors.get(bucket, "#94a3b8")
            html += f'<div style="margin-bottom:8px"><span style="font-size:12px">{bucket}</span> '
            html += f'<span style="color:{c};font-weight:600">{count}</span>'
            html += f'<div class="bar"><div class="bar-fill" style="width:{pct}%;background:{c}"></div></div></div>'
        html += '</div>'

    # DOI 来源分布
    if doi_source:
        html += '<div class="section"><h2>🔗 DOI 来源分布</h2><table><tr><th>来源</th><th>数量</th></tr>'
        for src, cnt in sorted(doi_source.items(), key=lambda x:-x[1]):
            html += f'<tr><td>{src if src and src != "—" else "未知"}</td><td><span class="badge badge-info">{cnt}</span></td></tr>'
        html += '</table></div>'

    # 文献详情表（只显示前 50 条）
    html += '<div class="section"><h2>📋 文献详情</h2><table><tr><th>#</th><th>标题</th><th>DOI</th><th>匹配度</th><th>来源</th><th>PDF</th></tr>'
    title_col = None
    for h in df.columns:
        if "title" in str(h).lower() or "标题" in str(h) or "题名" in str(h):
            title_col = h; break
    if title_col is None:
        title_col = df.columns[0]

    for i, (_, row) in enumerate(df.head(50).iterrows()):
        title = str(row.get(title_col, ""))[:50]
        doi = str(row.get("DOI", ""))[:30] if pd.notna(row.get("DOI")) else "—"
        match = str(row.get("匹配度", "—"))
        source = str(row.get("DOI来源", "—"))
        has_pdf = "✅" if pd.notna(row.get("PDF链接")) and str(row.get("PDF链接", "")).strip() else "—"

        doi_badge = ""
        if doi != "—": doi_badge = '<span class="badge badge-success">有</span>'
        else: doi_badge = '<span class="badge badge-fail">无</span>'

        html += f'<tr><td>{i+1}</td><td title="{title}">{title}</td>'
        html += f'<td style="font-family:Consolas,monospace;font-size:10px">{doi}</td>'
        html += f'<td>{match}</td><td><span class="badge badge-info">{source}</span></td>'
        html += f'<td>{has_pdf}</td></tr>'

    if total > 50:
        html += f'<tr><td colspan="6" style="text-align:center;color:#94a3b8">... 还有 {total-50} 条记录，详见 Excel 文件</td></tr>'
    html += '</table></div>'

    html += '<div class="footer">文献综合处理工具 · 自动生成</div></div></body></html>'

    # 写入文件
    base = os.path.splitext(input_file)[0]
    for s in ["_已加DOI", "_WithLinks"]:
        base = base[:-len(s)] if base.endswith(s) else base
    report_path = os.path.join(output_dir, f"{base}_报告.html")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)

    # 自动打开
    webbrowser.open(f"file:///{report_path.replace(os.sep, '/')}")

    return report_path
