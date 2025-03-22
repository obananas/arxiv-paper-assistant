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


def get_yesterday():
    """获取前一天的日期"""
    today = datetime.datetime.now()
    yesterday = today - datetime.timedelta(days=1)
    return yesterday.strftime('%Y-%m-%d')


def search_arxiv_papers(search_term, target_date, max_results=50):
    """在arXiv搜索指定日期和关键词的CS领域论文"""
    try:
        target_date_dt = datetime.datetime.strptime(target_date, "%Y-%m-%d")
        from_date = target_date_dt.strftime("%Y%m%d")
    except ValueError as e:
        print(f"日期格式错误: {target_date}")
        return []

    params = {
        'search_query': f'all:"{search_term}" AND cat:cs.* AND submittedDate:[{from_date} TO {from_date}]',
        'start': 0,
        'max_results': max_results,
        'sortBy': 'submittedDate',
        'sortOrder': 'ascending'
    }

    try:
        response = requests.get('http://export.arxiv.org/api/query', params=params)
        response.raise_for_status()
    except Exception as e:
        print(f"API请求失败: {e}")
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        print(f"XML解析失败: {e}")
        return []

    namespaces = {
        'atom': 'http://www.w3.org/2005/Atom',
        'arxiv': 'http://arxiv.org/schemas/atom'
    }

    papers = []
    for entry in root.findall('.//atom:entry', namespaces):
        try:
            title = entry.find('./atom:title', namespaces).text.strip()
            summary = entry.find('./atom:summary', namespaces).text.strip()
            url = entry.find('./atom:id', namespaces).text.strip()
            pub_date = entry.find('./atom:published', namespaces).text[:10]
            
            authors = [a.find('./atom:name', namespaces).text.strip() 
                      for a in entry.findall('./atom:author', namespaces)]
            
            paper = {
                'title': title,
                'authors': authors,
                'url': url,
                'arxiv_id': url.split('/')[-1],
                'pub_date': pub_date,
                'summary': summary,
                'categories': [c.get('term') for c in entry.findall('./atom:category', namespaces)],
                'comments': (c.text.strip() if (c := entry.find('./arxiv:comment', namespaces)) is not None else None)
            }
            papers.append(paper)
        except Exception as e:
            print(f"解析论文条目时出错: {e}")
            continue

    return papers


def process_with_openai(text, prompt_template, openai_api_key, model_name="gpt-3.5-turbo", api_base=None):
    """使用OpenAI处理文本"""
    prompt = prompt_template.format(text=text)
    
    try:
        client = OpenAI(
            api_key=openai_api_key,
            base_url=api_base if api_base else "https://api.deepseek.com/v1"
        )
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2048
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"OpenAI处理失败: {e}")
        return None


def format_paper_for_email(paper, translated_summary=None, contribution_summary=None):
    """格式化论文信息"""
    content = f"\n{'='*60}\n"
    content += f"📄 标题: {paper['title']}\n"
    content += f"👥 作者: {', '.join(paper['authors'])}\n"
    content += f"🏷️ 分类: {', '.join(paper['categories'])}\n"
    content += f"📅 日期: {paper['pub_date']}\n"
    
    if paper['comments']:
        content += f"💬 评论: {paper['comments']}\n"
        
    content += f"🔗 链接: https://arxiv.org/abs/{paper['arxiv_id']}\n"
    
    if contribution_summary:
        content += f"\n🌟 核心贡献: {contribution_summary}\n"
        
    content += f"\n📑 摘要:\n{paper['summary']}\n"
    
    if translated_summary:
        content += f"\n🌏 中文摘要:\n{translated_summary}\n"
        
    content += f"{'='*60}\n"
    return content


def send_email(subject, content, sender_email, sender_password, receiver_emails):
    """发送邮件"""
    msg = MIMEMultipart()
    msg['From'] = email.utils.formataddr(("ArXiv助手", sender_email))
    msg['To'] = ', '.join(receiver_emails) if isinstance(receiver_emails, list) else receiver_emails
    msg['Subject'] = Header(subject, 'utf-8')
    
    body = MIMEText(content, 'plain', 'utf-8')
    msg.attach(body)
    
    try:
        with smtplib.SMTP_SSL('smtp.qq.com', 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
            print("邮件发送成功")
    except Exception as e:
        print(f"邮件发送失败: {e}")


if __name__ == '__main__':
    # 配置参数
    search_terms = [t.strip().strip('"') for t in os.getenv("SEARCH_TERMS", "LLM,transformer").split(",")]
    yesterday = get_yesterday()
    
    # 收集论文
    all_papers = {}
    for term in search_terms:
        print(f"正在搜索: {term}")
        papers = search_arxiv_papers(term, yesterday)
        for p in papers:
            all_papers[p['arxiv_id']] = p
    
    if not all_papers:
        print("未找到相关论文")
        exit()
        
    # 处理论文
    email_content = f"ArXiv论文日报 - {yesterday}\n\n"
    email_content += f"搜索关键词: {', '.join(search_terms)}\n"
    email_content += f"共找到 {len(all_papers)} 篇论文\n\n"
    
    for i, (pid, paper) in enumerate(all_papers.items(), 1):
        print(f"处理论文 {i}/{len(all_papers)}: {paper['title']}")
        
        # 生成中文摘要
        translated = process_with_openai(
            paper['summary'],
            "将以下论文摘要翻译为中文，保留专业术语:\n{text}",
            os.getenv("OPENAI_API_KEY"),
            model_name=os.getenv("OPENAI_MODEL", "deepseek-chat")
        )
        
        # 生成贡献总结
        contribution = process_with_openai(
            paper['summary'],
            "用中文一句话总结论文的核心贡献，格式：提出XX方法解决XX问题:\n{text}",
            os.getenv("OPENAI_API_KEY")
        )
        
        email_content += format_paper_for_email(paper, translated, contribution)
    
    # 发送邮件
    send_email(
        f"ArXiv日报 {yesterday} - {len(all_papers)}篇",
        email_content,
        os.getenv("SENDER_EMAIL"),
        os.getenv("SENDER_PASSWORD"),
        os.getenv("RECEIVER_EMAILS").split(",")
    )
