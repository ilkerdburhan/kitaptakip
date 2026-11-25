import os
import re
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass


BASE_URL = "https://www.kitapyurdu.com"
SEARCH_URL = BASE_URL + "/index.php?route=product/search&filter_name="
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36"
}
COVER_DIR = Path(__file__).resolve().parent / "static" / "kapaklar"


class SearchUnavailableError(Exception):
    pass


def _text(card, selector):
    node = card.select_one(selector)
    return node.get_text(" ", strip=True) if node else ""


def _absolute_url(url):
    if not url:
        return ""
    if url.startswith("http"):
        return url
    return BASE_URL + "/" + url.lstrip("/")


def search_books(query):
    try:
        response = requests.get(
            SEARCH_URL + quote(query),
            headers=HEADERS,
            timeout=5,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SearchUnavailableError from exc

    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    seen = set()
    for card in soup.select(".ky-product"):
        title_link = card.select_one("a.ky-product-title")
        if not title_link:
            continue
        title = title_link.get_text(" ", strip=True)
        source_url = _absolute_url(title_link.get("href"))
        if not title or source_url in seen:
            continue
        seen.add(source_url)

        author_node = card.select_one(".ky-product-author a")
        publisher_node = card.select_one(".ky-product-publisher a")
        cover_node = card.select_one("a.ky-product-cover img")
        cover_url = ""
        if cover_node:
            cover_url = cover_node.get("data-src") or cover_node.get("src") or ""
            cover_url = _absolute_url(cover_url)

        results.append({
            "title": title,
            "author": author_node.get_text(" ", strip=True) if author_node else "",
            "publisher": publisher_node.get_text(" ", strip=True) if publisher_node else "",
            "cover_url": cover_url,
            "source_url": source_url,
        })
    return results


def get_book_details(book):
    source_url = book.get("source_url")
    if not source_url:
        return book
    try:
        response = requests.get(source_url, headers=HEADERS, timeout=5)
        response.raise_for_status()
    except requests.RequestException:
        return book

    soup = BeautifulSoup(response.text, "html.parser")
    details = dict(book)
    for item in soup.select(".ky-pd-attributes__item"):
        text = item.get_text(" ", strip=True)
        if "Sayfa Sayısı:" in text:
            match = re.search(r"(\d+)", text)
            if match:
                details["page_count"] = int(match.group(1))
        elif "ISBN:" in text:
            match = re.search(r"[\dXx-]+", text)
            if match:
                details["isbn"] = match.group(0).replace("-", "")
        elif "Yayın Yılı:" in text or "Basım Yılı:" in text:
            match = re.search(r"(19|20)\d{2}", text)
            if match:
                details["publication_year"] = match.group(0)

    if not details.get("publisher"):
        publisher = soup.select_one(".ky-publisher a")
        if publisher:
            details["publisher"] = publisher.get_text(" ", strip=True)
    return details


def download_cover(url, book_id):
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    if not url:
        return ""
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        extension = ".jpg"
        if "png" in content_type:
            extension = ".png"
        elif "webp" in content_type:
            extension = ".webp"
        filename = f"{book_id}{extension}"
        (COVER_DIR / filename).write_bytes(response.content)
        return filename
    except requests.RequestException:
        return ""
