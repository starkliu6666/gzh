"""Build a standalone offline HTML report from retained wechat-search responses."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
SCORES = ('relevanceScore', 'popularityScore', 'recencyScore', 'totalScore')
COUNTS = ('clicksCount', 'likeCount', 'watchCount', 'commentsCount', 'shareCount')


def number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def canonical_url(url):
    parts = urlsplit(url or '')
    query = dict(parse_qsl(parts.query))
    identity = {key: query[key] for key in ('__biz', 'mid', 'idx') if key in query}
    if len(identity) == 3:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(identity), ''))
    return (url or '').split('#')[0]


def prepare(raw):
    records = []
    identities = {}
    stats = []
    outside = 0
    received = 0
    for query in raw['queries']:
        keyword = query['keyword']
        # Use raw pages to preserve entries the upstream script may have omitted.
        pages = query.get('rawPages') or [query.get('rawResponse') or {}]
        source = [a for page in pages for a in (page.get('data') or {}).get('articles', [])]
        if not source:
            source = query.get('articles') or []
        received += len(source)
        scoped_ids = set()
        for article in source:
            stamp = str(article.get('publicTime') or '')[:10]
            if not raw['startDate'] <= stamp <= raw['endDate']:
                outside += 1
                continue
            url = str(article.get('url') or '')
            aliases = [value for value in (
                'id:' + str(article['id']) if article.get('id') else None,
                'url:' + canonical_url(url) if url else None,
            ) if value]
            if not aliases:
                aliases = ['text:' + str((article.get('title'), article.get('author'), stamp))]
            record = next((identities[a] for a in aliases if a in identities), None)
            if record is None:
                record = {
                    'id': str(article.get('id') or len(records)),
                    'title': str(article.get('title') or '未命名文章'),
                    'author': str(article.get('author') or '未知公众号'),
                    'summary': str(article.get('summary') or ''),
                    'url': url, 'publicTime': str(article.get('publicTime') or ''),
                    'keywords': [], 'scoresByKeyword': {},
                    **{key: number(article[key]) if article.get(key) is not None else None for key in COUNTS},
                }
                records.append(record)
            for alias in aliases:
                identities[alias] = record
            if keyword not in record['keywords']:
                record['keywords'].append(keyword)
            scores = {key: number(article.get(key)) for key in SCORES}
            previous = record['scoresByKeyword'].get(keyword)
            if not previous or scores['totalScore'] > previous['totalScore']:
                record['scoresByKeyword'][keyword] = scores
            for key in COUNTS:
                if article.get(key) is not None:
                    record[key] = max(record[key] or 0, number(article[key]))
            scoped_ids.add(id(record))
        raw_count = sum(len((p.get('data') or {}).get('articles') or []) for p in pages)
        total = int((query.get('apiMeta') or {}).get('total') or 0)
        page_size = int((query.get('apiMeta') or {}).get('pageSize') or 50)
        page_count = (total + page_size - 1) // page_size
        complete = query['status'] == 'ok' and not query.get('paginationError') and len(pages) >= max(page_count, 1) and raw_count >= total
        error = query.get('error') or query.get('paginationError')
        if not complete and not error and query['status'] == 'ok':
            error = f'接口报告 {total} 条，但实际返回 {raw_count} 条'
        stats.append({
            'keyword': keyword, 'count': len(scoped_ids), 'received': raw_count,
            'reportedTotal': total, 'pages': len(pages), 'complete': complete,
            'status': query['status'], 'error': error,
        })
    return {
        'meta': {
            'title': '金融公众号监控', 'startDate': raw['startDate'], 'endDate': raw['endDate'],
            'generatedAt': raw['generatedAt'], 'timezone': raw['timezone'],
            'keywords': raw['keywords'], 'queries': stats,
            'source': '红狐 API · wechat-search', 'received': received,
            'outsideDateCount': outside,
            'authors': len({article['author'] for article in records}),
        },
        'articles': records,
    }


def build(source: Path, output: Path):
    raw = json.loads(source.read_text(encoding='utf-8'))
    data = prepare(raw)
    # The report contains public article data only; never copy the environment.
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    payload = payload.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    payload = payload.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    template = (ROOT / 'templates/wechat-report.html').read_text(encoding='utf-8')
    assert template.count('__REPORT_DATA__') == 1
    rendered = template.replace('__REPORT_DATA__', payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding='utf-8')
    summary = {
        'file': str(output.resolve()), 'articles': len(data['articles']),
        'authors': data['meta']['authors'], 'rawMatches': data['meta']['received'],
        'queries': data['meta']['queries'], 'outsideDateCount': data['meta']['outsideDateCount'],
        'bytes': output.stat().st_size,
    }
    (output.parent / 'report-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.input, args.output)
