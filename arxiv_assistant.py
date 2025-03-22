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
import time
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

def get_yesterday_utc():
    """获取UTC时间前一天"""
    return (datetime.datetime.now(datetime.timezone.utc) 
            - datetime.timedelta(days=1)).strftime('%Y-%m-%d')

def safe_xml_text(element, path, namespaces, default="N/A"):
    """安全获取XML文本"""
    elem = element.find(path, namespaces)
    return elem.text.strip() if elem is not None and elem.text else default

def fetch_arxiv_papers(search_term, target_date, max_retries=3):
    """带重试和分页的论文获取"""
    papers = []
    start = 0
    max_results = 100  # 每页最大数量
    base_url = "http://export.arxiv.org/api/query?"
    
    while True:
        for attempt in range(max_retries):
            try:
                # 构造请求参数
                params = {
                    "search_query": f'(ti:{quote_plus(search_term)} OR abs:{quote_plus(search_term)}) AND cat:cs.*',
                    "start": start,
                    "max_results": max_results,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending"
                }
                response = requests.get(base_url, params=params, timeout=45)
                response.raise_for_status()
                
                root = ET.fromstring(response.content)
                entries = root.findall('.//{http://www.w3.org/2005/Atom}entry')
                if not entries:
                    return papers  # 无更多数据

                # 解析条目
                new_papers = []
                for entry in entries:
                    pub_date = datetime.datetime.strptime(
                        safe_xml_text(entry, './atom:published', namespaces),
                        "%Y-%m-%dT%H:%M:%SZ"
                    ).strftime("%Y-%m-%d")
                    
                    if pub_date != target_date:
                        continue
                        
                    paper = {
                        "arxiv_id": entry.find('./atom:id', namespaces).text.split('/')[-1],
                        "title": safe_xml_text(entry, './atom:title', namespaces),
                        "summary": safe_xml_text(entry, './atom:summary', namespaces),
                        "pub_date": pub_date,
                        "authors": [safe_xml_text(a, './atom:name', namespaces) 
                                  for a in entry.findall('./atom:author', namespaces)],
                        "categories": [c.get('term') 
                                     for c in entry.findall('./atom:category', namespaces)],
                        "comments": safe_xml_text(entry, './arxiv:comment', namespaces),
                    }
                    new_papers.append(paper)
                
                papers.extend(new_papers)
                start += len(entries)
                break  # 成功则跳出重试循环

            except (requests.RequestException, ET.ParseError) as e:
                logging.warning(f"请求失败 (尝试 {attempt+1}/{max_retries}): {str(e)}")
                time.sleep(5 * (attempt + 1))
                continue
        else:
            logging.error(f"无法获取 {search_term} 的论文")
            break
            
    return papers

def robust_ai_process(text, prompt_template, config, max_length=6000):
    """带长度检查和重试的AI处理"""
    text = text[:max_length]  # 防止超长文本
    
    for _ in range(3):
        try:
            result = process_with_openai(
                text, prompt_template,
                config['openai']['api_key'],
                config['openai']['model'],
                config['openai']['api_base']
            )
            if result and len(result) > 20:  # 验证有效响应
                return result
        except Exception as e:
            logging.warning(f"AI处理失败: {str(e)}")
            time.sleep(2)
    
    return "⚠️ 内容生成失败"

def format_paper(paper):
    """容错性更强的格式化"""
    parts = [
        f"📄 标题: {paper.get('title', '未知标题')}",
        f"👥 作者: {', '.join(paper.get('authors', ['未知作者']))}",
        f"🏷️ 分类: {', '.join(paper.get('categories', ['未分类']))}",
        f"📅 日期: {paper.get('pub_date', '未知日期')}",
    ]
    
    if paper.get('comments'):
        parts.append(f"💬 评论: {paper['comments']}")
        
    parts += [
        f"🔗 链接: https://arxiv.org/abs/{paper['arxiv_id']}",
        f"📄 PDF: https://arxiv.org/pdf/{paper['arxiv_id']}.pdf",
    ]
    
    if 'contribution' in paper:
        parts.append(f"\n🌟 贡献要点:\n{paper['contribution']}")
    
    if 'summary_zh' in paper:
        parts.append(f"\n🌐 中文摘要:\n{paper['summary_zh']}")
    
    parts.append(f"\n📜 原始摘要:\n{paper.get('summary', '无摘要')}")
    
    return "\n".join(parts) + "\n" + "-"*70 + "\n"

if __name__ == "__main__":
    # 配置初始化
    config = {
        "sender": {
            "email": os.getenv("SENDER_EMAIL"),
            "password": os.getenv("SENDER_PASSWORD"),
            "smtp_server": "smtp.qq.com",
            "smtp_port": 465
        },
        "openai": {
            "api_key": os.getenv("OPENAI_API_KEY"),
            "model": os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
            "api_base": os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
        }
    }
    
    # 关键词解析
    search_terms = next(csv.reader([os.getenv("SEARCH_TERMS", "")])) if os.getenv("SEARCH_TERMS") else []
    
    # 主流程
    target_date = get_yesterday_utc()
    all_papers = {}
    
    for term in search_terms:
        logging.info(f"处理关键词: {term}")
        papers = fetch_arxiv_papers(term, target_date)
        
        for paper in papers:
            pid = paper["arxiv_id"]
            if pid not in all_papers:
                # 并发处理AI请求
                paper["summary_zh"] = robust_ai_process(
                    paper["summary"], 
                    "翻译为中文，保留术语:\n{text}",
                    config
                )
                paper["contribution"] = robust_ai_process(
                    paper["summary"],
                    "用中文总结核心贡献:\n{text}",
                    config
                )
                all_papers[pid] = paper
                logging.info(f"已处理论文: {pid[:10]}...")

    # 发送邮件
    if all_papers:
        content = f"arXiv论文日报 {target_date}\n\n" + "\n".join([format_paper(p) for p in all_papers.values()])
        send_email(
            subject=f"arXiv日报 {target_date} - {len(all_papers)}篇",
            content=content,
            sender_info=config["sender"],
            receiver_emails=os.getenv("RECEIVER_EMAILS", "").split(",")
        )
