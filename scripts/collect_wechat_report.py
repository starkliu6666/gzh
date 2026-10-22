"""Collect one query per keyword through the installed wechat-search skill.

The API key is read only from REDFOX_API_KEY. Raw responses are retained without
request headers so reports can be regenerated without another API call.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
import threading
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPT = ROOT / '.agents/skills/wechat-search/scripts/fetch_gzh_trends.py'
KEYWORDS = ['助贷', '消金', '消费金融', '互联网金融', '互金', '金融监管', '银行', '乐信', '度小满', '奇富', '拍拍', '小赢', '洋钱罐', '贷后', '催收']
TZ = timezone(timedelta(hours=8))


def collect(output: Path, today: date, workers: int = 3) -> dict:
    spec = importlib.util.spec_from_file_location('wechat_search', SKILL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._get_api_key()  # Fail before launching workers if credentials are absent.
    context = threading.local()
    original_post = module._http_post
    start = today - timedelta(days=7)
    end = today - timedelta(days=1)

    def capture_post(url, body, headers):
        # Fix the date boundary explicitly rather than use the host's timezone.
        body = {**body, 'startDate': start.isoformat(), 'endDate': today.isoformat()}
        response = original_post(url, body, headers)
        context.response = response
        if not isinstance(response, dict):
            raise RuntimeError('接口返回格式异常')
        if '__http_error__' in response:
            raise RuntimeError(f"HTTP {response['__http_error__']}")
        if '__error__' in response:
            # Avoid writing arbitrary transport details or headers to outputs.
            raise RuntimeError('网络请求失败')
        if response.get('code') != 2000:
            raise RuntimeError(f"接口业务错误，code={response.get('code')}")
        return response

    module._http_post = capture_post

    def run(keyword):
        try:
            result = module.fetch_gzh_trends(keyword, start.isoformat(), auto_expand=False)
            raw = context.response
            data = raw.get('data') or {}
            return {
                'keyword': keyword, 'status': 'ok',
                'articles': result.get('articles') or [],
                'latestHotArticles': result.get('latestHotArticles') or [],
                'relatedSearches': result.get('relatedSearches') or [],
                'apiMeta': {k: v for k, v in data.items() if k not in ('articles', 'latestHotArticles', 'hotTopics')},
                'rawResponse': raw,
            }
        except Exception as error:
            return {'keyword': keyword, 'status': 'error', 'error': str(error), 'articles': []}

    output.parent.mkdir(parents=True, exist_ok=True)
    collected = {}
    report = {
        'schemaVersion': 1, 'skill': 'wechat-search',
        'generatedAt': datetime.now(TZ).isoformat(timespec='seconds'),
        'timezone': 'Asia/Shanghai',
        'startDate': start.isoformat(), 'endDate': end.isoformat(),
        'apiEndDate': today.isoformat(), 'keywords': KEYWORDS,
        'queries': [],
        'scopeNote': '保留每个关键词本次接口返回的全部文章；未扩展关键词或时间范围。接口可能限制单次返回条数。',
    }
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, keyword): keyword for keyword in KEYWORDS}
        for future in as_completed(futures):
            item = future.result()
            collected[item['keyword']] = item
            report['queries'] = [collected[k] for k in KEYWORDS if k in collected]
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({
                'keyword': item['keyword'], 'status': item['status'],
                'count': len(item['articles']), 'apiMeta': item.get('apiMeta'),
                'error': item.get('error'),
            }, ensure_ascii=False), flush=True)
    print('Saved:', output, flush=True)
    return report


def complete_pages(output: Path, workers: int = 3) -> dict:
    """Continue from retained first pages; never repeat an already saved page."""
    report = json.loads(output.read_text(encoding='utf-8'))
    spec = importlib.util.spec_from_file_location('wechat_search', SKILL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    key = module._get_api_key()
    headers = {'Content-Type': 'application/json', 'X-API-Key': key}
    mutex = threading.Lock()

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def finish(item):
        if item['status'] != 'ok':
            return
        meta = item.get('apiMeta') or {}
        total = int(meta.get('total') or len(item['articles']))
        size = int(meta.get('pageSize') or 50)
        pages = math.ceil(total / size)
        raw_pages = item.setdefault('rawPages', [item['rawResponse']])
        seen = {str(a.get('id') or a.get('url')) for a in item['articles']}
        for page in range(len(raw_pages) + 1, pages + 1):
            body = {
                'keyword': item['keyword'], 'startDate': report['startDate'],
                'endDate': report['apiEndDate'], 'source': '公众号爆款文章洞察-GitHub',
                'pageNum': page, 'pageSize': size,
            }
            response = module._http_post('https://redfox.hk/story/api/gzh/search/hotArticleNew', body, headers)
            if not isinstance(response, dict) or response.get('code') != 2000:
                with mutex:
                    item['paginationError'] = f'第 {page} 页请求失败，已保留此前数据'
                    save()
                break
            data = response.get('data') or {}
            articles = data.get('articles') or []
            new = [a for a in articles if str(a.get('id') or a.get('url')) not in seen]
            if articles and not new:
                with mutex:
                    item['paginationError'] = f'接口未提供新的分页结果（请求第 {page} 页）'
                    save()
                break
            with mutex:
                raw_pages.append(response)
                item['articles'].extend(new)
                seen.update(str(a.get('id') or a.get('url')) for a in new)
                item['pagesFetched'] = len(raw_pages)
                save()
            print(json.dumps({'keyword': item['keyword'], 'page': page, 'pages': pages,
                              'received': len(articles), 'retained': len(item['articles']),
                              'returnedPage': data.get('pageNum')}, ensure_ascii=False), flush=True)
            if not articles:
                if len(item['articles']) < total:
                    item['paginationError'] = '接口提前返回空页，已保留全部已取到的文章'
                break
        with mutex:
            returned_count = sum(len((p.get('data') or {}).get('articles') or []) for p in raw_pages)
            item['complete'] = not item.get('paginationError') and len(raw_pages) >= max(pages, 1) and returned_count >= total
            if not item['complete'] and not item.get('paginationError'):
                item['paginationError'] = f'接口报告 {total} 条，但实际返回 {returned_count} 条'
            save()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(finish, item) for item in report['queries']]
        for future in as_completed(futures):
            future.result()
    report['generatedAt'] = datetime.now(TZ).isoformat(timespec='seconds')
    report['scopeNote'] = '逐关键词查询并续取分页；保留本次接口返回的全部文章。未扩展关键词或时间范围。'
    save()
    for item in report['queries']:
        print(json.dumps({'keyword': item['keyword'], 'retained': len(item['articles']),
                          'total': item.get('apiMeta', {}).get('total'),
                          'complete': item.get('complete'), 'paginationError': item.get('paginationError')}, ensure_ascii=False), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--today', type=date.fromisoformat, default=datetime.now(TZ).date())
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--complete-pages', action='store_true')
    args = parser.parse_args()
    if args.complete_pages:
        complete_pages(args.output, args.workers)
    else:
        collect(args.output, args.today, args.workers)
