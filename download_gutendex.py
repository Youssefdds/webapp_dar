#!/usr/bin/env python3
"""
download_gutendex_library.py

Constitue une bibliothèque locale de livres depuis Gutendex (https://gutendex.com)
Objectif:
 - au moins TARGET_BOOKS livres (default 1664)
 - chaque livre >= MIN_WORDS mots (default 10_000)

Usage:
 - pip install aiohttp aiofiles tqdm python-slugify beautifulsoup4
 - python download_gutendex_library.py
"""

import asyncio
import aiohttp
import aiofiles
import json
import os
import re
import time
from pathlib import Path
from slugify import slugify
from tqdm.asyncio import tqdm_asyncio
from bs4 import BeautifulSoup

# ---------- CONFIG ----------
BASE_API = "https://gutendex.com"
BOOKS_ENDPOINT = f"{BASE_API}/books"
TARGET_BOOKS = 1664
MIN_WORDS = 10_000
OUTPUT_DIR = Path("library")
CONCURRENT_REQUESTS = 10
REQUESTS_DELAY = 0.12   # délai entre requêtes pour politesse (s)
RETRY_LIMIT = 4
INITIAL_BACKOFF = 1.0
# ----------------------------

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
METADATA_FILE = OUTPUT_DIR / "metadata.json"
COLLECTED_FILE = OUTPUT_DIR / "collected_ids.json"

_word_re = re.compile(r"\w+", flags=re.UNICODE)

def count_words(text: str) -> int:
    return len(_word_re.findall(text))

def choose_text_format(formats: dict) -> str | None:
    """
    formats is the Gutendex 'formats' dict mapping mime-type -> URL.
    Preference order:
     - text/plain; charset=utf-8
     - text/plain
     - text/plain; charset=us-ascii
     - text/html
    Return URL or None.
    """
    if not formats:
        return None
    preferred = [
        "text/plain; charset=utf-8",
        "text/plain",
        "text/plain; charset=us-ascii",
        "text/html"
    ]
    for p in preferred:
        if p in formats:
            return formats[p]
    for k, v in formats.items():
        if k.startswith("text/"):
            return v
    for v in formats.values():
        if isinstance(v, str):
            return v
    return None

async def http_get_with_retries(session: aiohttp.ClientSession, url: str, is_text=True) -> tuple[int, str]:
    backoff = INITIAL_BACKOFF
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            async with session.get(url, timeout=60) as resp:
                status = resp.status
                if status == 200:
                    if is_text:
                        return status, await resp.text(encoding='utf-8', errors='ignore')
                    else:
                        return status, await resp.read()
                elif status in (429, 503, 504):
                    await asyncio.sleep(backoff)
                    backoff *= 2
                else:
                    txt = await resp.text()
                    raise Exception(f"HTTP {status}: {txt[:200]}")
        except Exception as e:
            if attempt == RETRY_LIMIT:
                raise
            await asyncio.sleep(backoff)
            backoff *= 2
    raise Exception("Unreachable")

class GutendexDownloader:
    def __init__(self):
        self.collected = set()
        if COLLECTED_FILE.exists():
            try:
                self.collected = set(json.loads(COLLECTED_FILE.read_text(encoding="utf-8")))
            except Exception:
                self.collected = set()
        self.total_saved = len(self.collected)
        self.sem = asyncio.Semaphore(CONCURRENT_REQUESTS)
        self.meta_lock = asyncio.Lock()
        self.meta = {}
        if METADATA_FILE.exists():
            try:
                self.meta = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.meta = {}

    async def save_text_and_meta(self, book_id: int, title: str, authors: list, text: str, cover_image: str, raw_meta: dict):
        fname = f"{book_id}.txt"
        fpath = OUTPUT_DIR / fname
        async with aiofiles.open(fpath, "w", encoding="utf-8") as f:
            await f.write(text)

        meta_entry = {
            "id": book_id,
            "title": title,
            "authors": authors,
            "filename": fname,
            "cover_image": cover_image,
            "word_count": count_words(text),
            "download_count": raw_meta.get("download_count"),
            "bookshelves": raw_meta.get("bookshelves"),
            "subjects": raw_meta.get("subjects"),
            "languages": raw_meta.get("languages"),
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }

        async with self.meta_lock:
            self.meta[str(book_id)] = meta_entry
            async with aiofiles.open(METADATA_FILE, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self.meta, ensure_ascii=False, indent=2))

    async def fetch_books_page(self, session: aiohttp.ClientSession, page_url: str):
        await asyncio.sleep(REQUESTS_DELAY)
        status, text = await http_get_with_retries(session, page_url, is_text=True)
        return json.loads(text)

    async def process_book(self, session: aiohttp.ClientSession, book_meta: dict):
        book_id = book_meta.get("id")
        if not book_id or str(book_id) in self.collected:
            return False
        formats = book_meta.get("formats", {})
        url = choose_text_format(formats)
        if not url:
            return False
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("http:"):
            url = "https:" + url[5:]
        try:
            async with self.sem:
                await asyncio.sleep(REQUESTS_DELAY)
                is_html = url.endswith(".htm") or url.endswith(".html") or "text/html" in url
                status, raw = await http_get_with_retries(session, url, is_text=True)
                text = raw
                if is_html or raw.strip().startswith("<"):
                    soup = BeautifulSoup(raw, "html.parser")
                    for s in soup(["script", "style"]):
                        s.decompose()
                    text = soup.get_text(separator="\n")
                words = count_words(text)
                if words >= MIN_WORDS:
                    title = book_meta.get("title", f"book_{book_id}")
                    authors = book_meta.get("authors", [])
                    cover_image = book_meta.get("formats", {}).get("image/jpeg", "")
                    await self.save_text_and_meta(book_id, title, authors, text, cover_image, book_meta)
                    self.collected.add(str(book_id))
                    async with aiofiles.open(COLLECTED_FILE, "w", encoding="utf-8") as f:
                        await f.write(json.dumps(list(self.collected), ensure_ascii=False, indent=2))
                    self.total_saved += 1
                    print(f"[SAVED] id={book_id} words={words} total_saved={self.total_saved}")
                    return True
                else:
                    print(f"[SKIP] id={book_id} too short ({words} words).")
                    return False
        except Exception as e:
            print(f"[ERROR] book {book_id} -> {e}")
            return False

    async def run(self):
        timeout = aiohttp.ClientTimeout(total=120)
        conn = aiohttp.TCPConnector(limit=CONCURRENT_REQUESTS)
        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
            next_url = f"{BOOKS_ENDPOINT}"
            page = 1
            while self.total_saved < TARGET_BOOKS and next_url:
                print(f"[INFO] page {page} -> fetching {next_url} (collected {self.total_saved}/{TARGET_BOOKS})")
                try:
                    data = await self.fetch_books_page(session, next_url)
                except Exception as e:
                    print(f"[ERROR] fetching page {page}: {e}")
                    break
                results = data.get("results", [])
                tasks = [self.process_book(session, bm) for bm in results if bm and bm.get("id")]
                if tasks:
                    for ok in await tqdm_asyncio.gather(*tasks, desc=f"Processing page {page}", leave=False):
                        if self.total_saved >= TARGET_BOOKS:
                            break
                next_url = data.get("next")
                page += 1
                await asyncio.sleep(REQUESTS_DELAY * 2)

            print(f"[DONE] saved {self.total_saved} books into {OUTPUT_DIR.resolve()}")

def main():
    dl = GutendexDownloader()
    asyncio.run(dl.run())

if __name__ == "__main__":
    main()
