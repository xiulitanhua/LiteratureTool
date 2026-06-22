# 文献综合处理工具 v3.0

一站式文献处理桌面应用：**Excel 导入 → 自动获取 DOI → 批量下载 PDF**

---

## 🚀 功能概述

| 功能 | 说明 |
|------|------|
| 📋 表格检测 | 自动识别 Excel 中的标题列、年份列、作者列、期刊列、DOI 列 |
| 📝 获取 DOI | 通过 Crossref / OpenAlex API 联网查询文献 DOI，支持标题匹配度筛选 |
| 📥 下载 PDF | 根据 DOI 从 tesble.com 批量下载文献 PDF，自动重命名 |
| 🚀 一键全流程 | 获取 DOI → 下载 PDF 一气呵成 |

---

## 📦 环境准备

**需要 Python 3.8+**

```powershell
# 安装依赖
pip install -r requirements.txt
```

---

## 🎯 使用方法

### 启动应用

```powershell
python main.py
```

### 操作流程

1. **选择 Excel 文件** → 点击"选择文件"按钮
2. **检测表格结构** → 自动识别各列（也可点击"检测表格结构"手动触发）
3. **设置匹配阈值** → 拖动滑块调整（默认 80%，即标题相似度 ≥ 80% 才写入 DOI）
4. **选择操作**：
   - `获取 DOI` — 仅查询并填入 DOI
   - `下载 PDF` — 根据已有 DOI 下载文献
   - `一键全流程` — 获取 DOI 后自动下载 PDF

### Excel 表格要求

表格需包含**标题列**，表头建议为以下任一：
- `TITLE` / `Title` / `题名` / `标题` / `篇名` / `论文题目`

建议同时包含（非必需）：
- 年份列：`YEAR` / `Year` / `年份` / `出版年`
- 作者列：`AUTHOR` / `Authors` / `作者`
- 期刊列：`JOURNAL` / `Source` / `期刊` / `刊名`
- DOI 列：`DOI`（已有 DOI 则跳过查询）

---

## 📁 输出说明

| 输出文件 | 内容 |
|----------|------|
| `xxx_已加DOI.xlsx` | 原表格 + `DOI`、`DOI链接`、`匹配度`、`DOI来源` 四列 |
| `xxx_WithLinks.xlsx` | 上述表格 + `PDF链接`（本地超链接） |
| `Downloaded_PDFs/` | 下载的 PDF 文件，命名格式：`作者_年份_期刊_标题.pdf` |

---

## 📊 DOI 来源

优先级：**Crossref API** → **OpenAlex API**

> 注：原 Node.js 版本的 Web of Science 搜索功能需要机构 VPN 访问，Python 版暂未集成。如需 WoS，可继续使用 `get_dois.js`。

---

## 🛠 项目结构

```
文献综合工具/
├── main.py            # GUI 主程序
├── doi_fetcher.py     # DOI 获取模块
├── pdf_downloader.py  # PDF 下载模块
├── requirements.txt   # Python 依赖
└── README.md          # 本文件
```

---

## 🔧 打包为 EXE

如需在没有 Python 环境的电脑上运行：

```powershell
pip install pyinstaller
pyinstaller --onefile --console --name 文献综合工具 main.py
```

生成的 `dist/文献综合工具.exe` 即可独立运行。
