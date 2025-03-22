import requests
import datetime
import smtplib
import email.utils
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from openai import OpenAI
import xml.etree.ElementTree as ET
import os
import csv
from urllib.parse import quote_plus


def get_yesterday_utc():
    """
    获取UTC时间的前一天日期
    """
    today = datetime.datetime.now(datetime.timezone.utc)
    yesterday = today - datetime.timedelta(days=1)
    return yesterday.strftime('%Y-%m-%d')


def search_arxiv_papers(search_term, target_date, max_results=50):
    """
    在arXiv按关键词搜索指定日期的计算机科学领域论文
    改进点：URL编码、布尔逻辑优化、调试输出
    """
    papers = []
    base_url = 'http://export.arxiv.org/api/query?'
    
    try:
        # 编码搜索词并构建查询
        encoded_term = quote_plus(search_term)
        search_query = (
            f'search_query=(ti:{encoded_term}+OR+abs:{encoded_term})'
            f'+AND+cat:cs.*'
            f'&start=0&max_results={max_results}'
            f'&sortBy=submittedDate&sortOrder=descending'
        )
        full_url = base_url + search_query
        print(f"DEBUG - 搜索URL: {full_url}")  # 调试输出

        response = requests.get(full_url, timeout=30)
        response.raise_for_status()  # 触发HTTP错误异常

        # 解析XML
        root = ET.fromstring(response.content)
        namespaces = {
            'atom': 'http://www.w3.org/2005/Atom',
            'arxiv': 'http://arxiv.org/schemas/atom'
        }

        entries = root.findall('.//atom:entry', namespaces)
        if not entries:
            print(f"未找到与 '{search_term}' 匹配的条目")
            return []

        for entry in entries:
            # 提取发布日期
            pub_date_str = entry.find('./atom:published', namespaces).text
            pub_date = datetime.datetime.strptime(
                pub_date_str, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")
            
            if pub_date != target_date:
                continue  # 跳过非目标日期的论文

            # 提取论文信息
            title = entry.find('./atom:title', namespaces).text.strip()
            summary = entry.find('./atom:summary', namespaces).text.strip()
            url = entry.find('./atom:id', namespaces).text.strip()
            arxiv_id = url.split('/')[-1]
            
            authors = [
                author.find('./atom:name', namespaces).text.strip()
                for author in entry.findall('./atom:author', namespaces)
            ]
            
            categories = [
                category.get('term')
                for category in entry.findall('./atom:category', namespaces)
            ]
            
            comments_elem = entry.find('./arxiv:comment', namespaces)
            comments = comments_elem.text.strip() if comments_elem is not None else None

            papers.append({
                'title': title,
                'authors': authors,
                'url': url,
                'arxiv_id': arxiv_id,
                'pub_date': pub_date,
                'summary': summary,
                'categories': categories,
                'comments': comments,
            })

        print(f"找到 {len(papers)} 篇符合日期要求的论文")
        return papers

    except requests.exceptions.RequestException as e:
        print(f"网络请求失败: {str(e)}")
        return []
    except ET.ParseError as e:
        print(f"XML解析失败: {str(e)}")
        return []


def process_with_openai(text, prompt_template, openai_api_key, model_name="gpt-3.5-turbo", api_base=None):
    """整合优化的AI处理函数"""
    try:
        prompt = prompt_template.format(text=text)
        client = OpenAI(
            api_key=openai_api_key,
            base_url=api_base if api_base else "https://api.openai.com/v1"
        )
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,  # 降低随机性
            max_tokens=2000    # 控制输出长度
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"AI处理失败: {str(e)}")
        return None


def format_paper_for_email(paper):
    """改进的邮件格式化函数"""
    SEPARATOR = "\n" + "-"*70 + "\n"
    content = SEPARATOR
    content += f"📄 标题: {paper['title']}\n"
    content += f"👥 作者: {', '.join(paper['authors'])}\n"
    content += f"🏷️ 分类: {', '.join(paper.get('categories', ['N/A']))}\n"
    content += f"📅 日期: {paper['pub_date']}\n"
    
    if paper.get('comments'):
        content += f"💬 评论: {paper['comments']}\n"
    
    content += f"🔗 链接: https://arxiv.org/abs/{paper['arxiv_id']}\n"
    content += f"📄 PDF: https://arxiv.org/pdf/{paper['arxiv_id']}.pdf\n\n"
    
    # 动态添加AI处理内容
    for field in ['contribution', 'summary_zh']:
        if paper.get(field):
            header = "贡献要点" if field == 'contribution' else "中文摘要"
            content += f"🌟 {header}:\n{paper[field]}\n\n"
    
    content += "📜 原始摘要:\n" + paper['summary'] + "\n"
    return content + SEPARATOR


def send_email(subject, content, sender_info, receiver_emails):
    """改进的邮件发送函数"""
    msg = MIMEMultipart()
    msg['From'] = email.utils.formataddr((sender_info['name'], sender_info['email']))
    msg['To'] = ', '.join(receiver_emails) if isinstance(receiver_emails, list) else receiver_emails
    msg['Subject'] = Header(subject, 'utf-8')
    msg.attach(MIMEText(content, 'plain', 'utf-8'))

    try:
        with smtplib.SMTP_SSL(sender_info['smtp_server'], sender_info['smtp_port']) as server:
            server.login(sender_info['email'], sender_info['password'])
            server.sendmail(sender_info['email'], receiver_emails, msg.as_string())
        print(f"邮件成功发送至 {msg['To']}")
    except Exception as e:
        print(f"邮件发送失败: {str(e)}")


if __name__ == '__main__':
    # 配置加载
    config = {
        'sender': {
            'email': os.getenv("SENDER_EMAIL", "your_email@qq.com"),
            'password': os.getenv("SENDER_PASSWORD", "your_app_password"),
            'name': os.getenv("SENDER_NAME", "arXiv论文助手"),
            'smtp_server': os.getenv("SMTP_SERVER", "smtp.qq.com"),
            'smtp_port': int(os.getenv("SMTP_PORT", 465))
        },
        'openai': {
            'api_key': os.getenv("OPENAI_API_KEY", "your_api_key"),
            'model': os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
            'api_base': os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
        },
        'search_terms': [],
        'receiver_emails': list(filter(None, os.getenv("RECEIVER_EMAILS", "").split(','))),
        'max_results': int(os.getenv("MAX_RESULTS", 50))
    }

    # 解析关键词（支持带空格和引号）
    try:
        reader = csv.reader([os.getenv("SEARCH_TERMS", 'transformer,"large language model"')])
        config['search_terms'] = next(reader)
    except Exception as e:
        print(f"关键词解析失败: {str(e)}")
        config['search_terms'] = ['transformer', 'large language model']

    # 主逻辑
    target_date = get_yesterday_utc()
    all_papers = {}
    
    for term in config['search_terms']:
        print(f"\n🔍 正在搜索: {term}")
        papers = search_arxiv_papers(term, target_date, config['max_results'])
        
        for paper in papers:
            # AI处理
            paper['summary_zh'] = process_with_openai(
                paper['summary'],
                "将以下英文论文摘要翻译为中文，保持专业术语不变:\n{text}",
                config['openai']['api_key'],
                config['openai']['model'],
                config['openai']['api_base']
            )
            
            paper['contribution'] = process_with_openai(
                paper['summary'],
                "用一句中文说明论文的核心贡献，格式：方法+效果:\n{text}",
                config['openai']['api_key'],
                config['openai']['model'],
                config['openai']['api_base']
            )
            
            all_papers[paper['arxiv_id']] = paper

    # 生成邮件内容
    if not all_papers:
        email_content = f"【{target_date}】arXiv论文日报\n\n未找到符合要求的论文"
    else:
        email_content = f"【{target_date}】arXiv论文日报\n发现{len(all_papers)}篇重要论文\n\n"
        email_content += "\n".join([format_paper_for_email(p) for p in all_papers.values()])

    # 发送邮件
    if config['receiver_emails']:
        send_email(
            subject=f"arXiv日报 {target_date}",
            content=email_content,
            sender_info=config['sender'],
            receiver_emails=config['receiver_emails']
        )
    else:
        print("未配置接收邮箱，邮件未发送")
