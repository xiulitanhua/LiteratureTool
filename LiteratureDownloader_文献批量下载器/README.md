# 文献批量下载工具

这是一个可以根据 Excel 表格中的 DOI 号批量从文献网站下载 PDF 并自动重命名的工具。

## 使用说明

1. **准备数据表格**：
   将你需要处理的一个或多个 Excel 表格和打包好的工具放在同一个文件夹内。表格中必须包含以下列（大小写需一致，建议严格照搬你的原表格式）：
   - `AUTHOR`: 作者名称
   - `YEAR`: 年份
   - `JOURNAL`: 期刊名
   - `title`: 文献标题
   - `DOI`: DOI 号

2. **运行程序**：
   双击运行打包好的 `download_pdfs.exe`。
   程序会自动读取当前目录下的 Excel 文件。如果文件夹里只有一个 `.xlsx` 文件，程序会自动识别并直接开始下载；如果有多个 `.xlsx` 的 Excel 文件，命令行会列出名称，让你输入序号进行选择。

3. **查看结果**：
   - 程序运行结束（由于自带1.5-3.5秒随机延迟，可能需要一些时间）后，根目录下会自动生成一个名为 `Downloaded_PDFs` 的文件夹，里面存放了所有下载好的 PDF 文献。
   - 文件名会自动命名为：`作者姓氏_年份_期刊全大写_标题首字母大写.pdf`
   - 同时会自动生成一个新文件 `副本MARrefer_已加DOI_WithLinks.xlsx`，该表格包含指向上述本地 PDF 文件的相对路径超链接。

## 迁移到其他电脑

为了能在其他没有 Python 环境的电脑上使用且保持 PDF 链接有效：
1. 只需要把本目录中的以下文件或文件夹拷贝到另一个人的电脑上放在同一个文件夹内：
    - `download_pdfs.exe` (或源码和Python环境)
    - `副本MARrefer_已加DOI.xlsx` (你的模板Excel)
    - `Downloaded_PDFs` 文件夹（当已经下载好之后）
    - `副本MARrefer_已加DOI_WithLinks.xlsx` (生成好的带链接的表格)
    通过相对路径关系，新生成的表格中的 PDF 超链接依然有效！

## Python 源代码运行/编译说明
如果是开发者，您可以直接通过 Python 运行 `download_pdfs.py`：
```cmd
pip install -r requirements.txt
python download_pdfs.py
```
如需自己打包 EXE：
```cmd
pip install pyinstaller
pyinstaller --onefile --console download_pdfs.py
```