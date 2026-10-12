"""抓取真实微信公众号文章，输出 JSON。"""
import asyncio, json, os, sys
from pathlib import Path
ROOT = Path(os.environ.get('PLATFORM_ROOT', Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(ROOT))
from app.modules import article_import

BUILD_DIR = Path(os.environ.get('DEMO_BUILD_DIR', ROOT / 'data/demo_pipeline'))
URLS = json.load(open(BUILD_DIR / 'article_urls.json'))

async def main():
    out = {}
    for i, u in enumerate(URLS, 1):
        try:
            item = await article_import.fetch_article(u)
            out[u] = item
            print(f'[{i}] OK {item.get("title","")[:50]} chars={len(item.get("content_text") or item.get("content") or "")}')
        except Exception as e:
            out[u] = {"error": str(e)[:120]}
            print(f'[{i}] FAIL {str(e)[:80]}')
    BUILD_DIR / 'articles.json'.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), 'utf-8')
asyncio.run(main())
