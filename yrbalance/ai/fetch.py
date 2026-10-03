"""
Fetch a single web page and reduce it to readable text for Eva's knowledge.

Deliberately simple and polite: it fetches exactly the URL you give it (http/https
only), with a size cap and timeout, and does NOT follow links or crawl. HTML is
stripped to text with the stdlib parser — good enough for wikis/forums/tutorials
(e.g. ModENC), though JS-rendered pages may yield little.

The fetched text is data, not instructions: Eva uses it as reference, and you still
review every script before it's applied.
"""

import re
import urllib.request
from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "head", "noscript", "nav", "footer", "header",
              "aside", "form", "svg"}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5",
               "h6", "section", "article", "pre"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if data.strip():
            self.parts.append(data)


def html_to_text(html):
    """Return (title, text) extracted from HTML."""
    p = _Extractor()
    try:
        p.feed(html)
    except Exception:
        pass
    text = "".join(p.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return p.title.strip(), text.strip()


def _default_reader(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "YRBalanceTool/Eva"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read(4_000_000)
    return raw.decode(charset, errors="replace")


def fetch_url(url, timeout=20, reader=None):
    """
    Fetch URL and return (title, text). Raises ValueError for non-http(s) URLs and
    re-raises network errors. `reader` is injectable for testing.
    """
    if not re.match(r"^https?://", url.strip(), re.IGNORECASE):
        raise ValueError("Only http:// and https:// URLs are supported.")
    html = (reader or _default_reader)(url.strip(), timeout)
    title, text = html_to_text(html)
    if not title:
        title = re.sub(r"^https?://", "", url).strip("/")
    return title, text
