import pandas as pd
import requests
import re
import os
from bs4 import BeautifulSoup

def clean_filename(name):
    """去除文件名中不能包含的特殊字符"""
    return re.sub(r'[\\/:*?"<>|]', '_', str(name))

def get_title_case(title):
    """文献名首字母大写，其余小写"""
    # capitalize 会将第一个字母大写，其他都变为小写
    title = str(title).strip()
    if not title:
        return ""
    return title.capitalize()

def get_author_lastname(author_str):
    """获取第一作者的姓氏"""
    author_str = str(author_str).strip()
    if not author_str or author_str == 'nan':
        return "Unknown"
    # 作者通常按分号或逗号分隔，取第一个作者
    first_author = author_str.split(';')[0].split(',')[0].strip()
    return get_title_case(first_author)

def format_journal(journal_str):
    """期刊缩写全大写"""
    journal_str = str(journal_str).strip()
    if not journal_str or journal_str == 'nan':
        return "JOURNAL"
    return journal_str.upper()

def process_excel(file_path):
    df = pd.read_excel(file_path)
    
    # 创建存放PDF的文件夹
    save_dir = "Downloaded_PDFs"
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)
    
    count = 0
    # 遍历DataFrame
    for index, row in df.iterrows():
        doi = row.get('DOI')
        if pd.isna(doi):
            continue
            
        author = row.get('AUTHOR', '')
        year = row.get('YEAR', '')
        journal = row.get('JOURNAL', '')
        title = row.get('title', '') # 注意Excel的原始列名为小写 'title'
        
        # 命名拼写
        last_name = get_author_lastname(author)
        year_str = str(int(year)) if pd.notna(year) and year != '' else "Year"
        journal_abbr = format_journal(journal)
        title_cased = get_title_case(title)
        
        # 截取长标题以免文件名过长
        if len(title_cased) > 50:
            title_cased = title_cased[:50].strip() + "..."
            
        file_name = f"{last_name}_{year_str}_{journal_abbr}_{title_cased}.pdf"
        file_name = clean_filename(file_name)
        save_path = os.path.join(save_dir, file_name)
        
        print(f"正在尝试下载: {doi} -> {file_name}")
        
        # ------ 网络请求部分 ------
        url = f"https://www.tesble.com/{doi}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"
        }
        
        try:
            # 1. 获取网站HTML
            import time
            import random
            
            # 加入随机延迟，避免请求过快
            time.sleep(random.uniform(1.5, 3.5))
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            pdf_url = None
            
            # 2. 寻找包含pdf的iframe或embed标签
            iframe = soup.find('iframe')
            embed = soup.find('embed')
            
            if iframe and iframe.get('src'):
                pdf_url = iframe.get('src')
            elif embed and embed.get('src'):
                pdf_url = embed.get('src')
                
            if pdf_url:
                # 补全相对路径
                if pdf_url.startswith('//'):
                    pdf_url = 'https:' + pdf_url
                elif pdf_url.startswith('/'):
                    pdf_url = 'https://www.tesble.com' + pdf_url
                    
                print(f"找到PDF链接，正在下载: {pdf_url}")
                pdf_response = requests.get(pdf_url, headers=headers, timeout=20)
                pdf_response.raise_for_status()
                
                # 写入真实PDF文件
                with open(save_path, 'wb') as f:
                    f.write(pdf_response.content)
            else:
                print(f"未在页面上找到PDF链接: {doi}")
                continue
            
            # 在Excel中添加相对路径以作超链接
            # Excel支持 =HYPERLINK("路径", "显示名字")
            relative_path = f"{save_dir}/{file_name}"
            df.at[index, 'PDF Link'] = f'=HYPERLINK("{relative_path}", "打开PDF")'
            print(" ---> 链接已添加！")
            
        except Exception as e:
            print(f"下载文献 {doi} 时出错: {e}")

    # 将带有超链接的新列保存为一个新的 Excel
    output_file = file_path.replace(".xlsx", "_WithLinks.xlsx")
    df.to_excel(output_file, index=False)
    print(f"\n处理完毕！已经保存最新的Excel文件：{output_file}")

if __name__ == "__main__":
    import glob
    
    xlsx_files = [f for f in glob.glob("*.xlsx") if not f.endswith("_WithLinks.xlsx") and not f.startswith("~$")]
    
    if not xlsx_files:
        print("错误: 在当前文件夹下没有找到任何待处理的 Excel (.xlsx) 文件！")
        input("请放入 Excel 文件后重试，按回车键退出...")
    elif len(xlsx_files) == 1:
        target_file = xlsx_files[0]
        print(f"自动检测到待处理的 Excel 文件: {target_file}")
        process_excel(target_file)
        input("处理完成！按回车键退出程序...")
    else:
        print("当前文件夹下检测到多个 Excel 文件，请选择你要处理的文件：")
        for i, f in enumerate(xlsx_files, 1):
            print(f"  [{i}]. {f}")
        
        while True:
            choice = input(f"请输入序号 (1-{len(xlsx_files)}) 并按回车: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(xlsx_files):
                target_file = xlsx_files[int(choice) - 1]
                print(f"==> 你选择了: {target_file}\n")
                process_excel(target_file)
                input("处理完成！按回车键退出程序...")
                break
            else:
                print("输入无效，请重新输入正确的序号。")