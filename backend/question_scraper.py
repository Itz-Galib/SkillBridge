"""URL question scraping for the admin question-bank governance flow.

Fetches a public page, extracts question + answer pairs (JSON-LD Quiz/FAQ schema
first, then lightweight HTML/text heuristics), and returns raw records that the
caller normalises into the question-bank format and stages for human review.

This is a curation aid for admins only. Nothing is auto-graded, auto-scored or
auto-published. No live AI grading is used anywhere in the pipeline.
"""
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

MAX_BYTES = 5_000_000
TIMEOUT_SECONDS = 20.0
MAX_QUESTIONS = 1000
USER_AGENT = "SkillBridgeQuestionScraper/0.1 (admin question-bank curation)"


# --------------------------------------------------------------------- SSRF
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
)


def _assert_public_host(hostname):
    hostname = (hostname or "").strip().rstrip(".")
    if not hostname:
        raise ValueError("URL has no host.")
    if hostname.lower() == "localhost" or hostname.lower().endswith(".local"):
        raise ValueError("That host is not allowed.")
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError("Could not resolve the host.") from exc
    if not infos:
        raise ValueError("Could not resolve the host.")
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%", 1)[0])
        except ValueError:
            continue
        if any(ip in net for net in _BLOCKED_NETWORKS):
            raise ValueError("That host resolves to a private or reserved address and is not allowed.")


# ------------------------------------------------------------------- HTML
class _TextExtractor(HTMLParser):
    """Line-oriented text plus structured captures from HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts = []
        self.list_items = []
        self.headings = []
        self._li = None
        self._heading = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "template", "iframe"):
            self._skip += 1
        if self._skip:
            return
        if tag == "li":
            self._li = []
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "template", "iframe"):
            self._skip = max(0, self._skip - 1)
        if self._skip:
            return
        if tag == "li" and self._li is not None:
            text = " ".join(self._li).strip()
            if text:
                self.list_items.append(text)
            self._li = None
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading is not None:
            text = " ".join(self._heading).strip()
            if text:
                self.headings.append(text)
            self._heading = None
        if tag in ("p", "div", "li", "br", "tr", "section", "article", "blockquote",
                   "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._li is not None:
            self._li.append(data)
        if self._heading is not None:
            self._heading.append(data)
        self.parts.append(data)


def _clean(s):
    return re.sub(r"[ \t]+", " ", s or "").strip()


# ------------------------------------------------------------- JSON-LD
def _iter_objects(node):
    if isinstance(node, list):
        for item in node:
            yield from _iter_objects(item)
    elif isinstance(node, dict):
        yield node
        if isinstance(node.get("@graph"), list):
            for item in node["@graph"]:
                yield from _iter_objects(item)


def _jsonld_questions(data):
    records = []
    for obj in _iter_objects(data):
        types = obj.get("@type")
        if isinstance(types, str):
            types = [types]
        types = [str(t).lower() for t in (types or [])]
        if "quiz" in types or isinstance(obj.get("hasPart"), list):
            for part in obj.get("hasPart", []):
                rec = _jsonld_question_part(part)
                if rec:
                    records.append(rec)
        elif "faqpage" in types or isinstance(obj.get("mainEntity"), (list, dict)):
            entities = obj.get("mainEntity", [])
            if isinstance(entities, dict):
                entities = [entities]
            for ent in entities:
                rec = _jsonld_question_part(ent)
                if rec:
                    records.append(rec)
        elif any(t in ("question", "qapage") for t in types):
            rec = _jsonld_question_part(obj)
            if rec:
                records.append(rec)
    return records


def _jsonld_answer_text(accepted):
    if not accepted:
        return ""
    if isinstance(accepted, list):
        accepted = accepted[0] if accepted else None
    if isinstance(accepted, dict):
        return str(accepted.get("text") or accepted.get("name") or "").strip()
    return str(accepted).strip()


def _jsonld_question_part(part):
    if not isinstance(part, dict):
        return None
    qtext = str(part.get("name") or part.get("text") or "").strip()
    if not qtext:
        return None
    suggested = part.get("suggestedAnswer") or part.get("suggestedAnswers") or []
    if isinstance(suggested, dict):
        suggested = [suggested]
    options = []
    for ans in suggested:
        if isinstance(ans, dict):
            txt = str(ans.get("text") or ans.get("name") or "").strip()
            if txt:
                options.append(txt)
    correct = _jsonld_answer_text(part.get("acceptedAnswer"))
    return {"question_text": qtext, "options": options, "correct_answer": correct,
            "explanation": ""}


# ------------------------------------------------------------- text heuristics
_OPTION_LINE = re.compile(r"^\s*([A-D])[\s).:\-–—]+\s*(.{1,400})$")
_ANSWER_INLINE = re.compile(r"\b(?:correct\s+answer|answer|ans)\b\s*[:=\-]?\s*([A-Da-d])\b", re.I)
_Q_NUMBER = re.compile(r"^(?:Q(?:uestion)?\s*)?(\d{1,4})\s*[\).:\-–—]\s*")


def _split_inline_options(text):
    parts = re.split(r"\s+(?=[A-D]\s*[).:\-–—]\s*\S)", text)
    options, rest = [], []
    for p in parts:
        m = re.match(r"^([A-D])\s*[).:\-–—]\s*(.*)$", p.strip())
        if m:
            options.append({"letter": m.group(1).upper(), "text": m.group(2).strip()})
        else:
            rest.append(p)
    return " ".join(rest).strip(), options


def _parse_chunk(lines):
    qtext, options, explanation = [], [], []
    answer_letter = None
    first = True
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        om = _OPTION_LINE.match(line)
        if om:
            options.append({"letter": om.group(1).upper(), "text": om.group(2).strip()})
            continue
        am = _ANSWER_INLINE.search(line)
        if am:
            answer_letter = am.group(1).upper()
            rest = _ANSWER_INLINE.sub("", line, count=1).strip(" :-–—\t")
            if rest:
                explanation.append(rest)
            continue
        if first and _Q_NUMBER.match(line):
            line = _Q_NUMBER.sub("", line, count=1)
        first = False
        qtext.append(line)
    question_text = " ".join(qtext).strip()
    if not options:
        question_text, options = _split_inline_options(question_text)
    correct = ""
    if answer_letter:
        match = next((o for o in options if o["letter"] == answer_letter), None)
        correct = match["text"] if match else answer_letter
    return {"question_text": question_text, "options": [o["text"] for o in options],
            "correct_answer": correct, "explanation": " ".join(explanation).strip()}


def _extract_from_text(text):
    lines = [_clean(l) for l in text.splitlines()]
    lines = [l for l in lines if l]
    chunks = []
    current = []
    numbered = False
    for line in lines:
        if _Q_NUMBER.match(line):
            numbered = True
            if current:
                chunks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append(current)
    if numbered:
        # Drop a leading title/intro chunk that precedes the first numbered question.
        if len(chunks) > 1 and not _Q_NUMBER.match(chunks[0][0]):
            chunks = chunks[1:]
    else:
        # No numbering detected: split by blank-line blocks instead.
        chunks = []
        for block in re.split(r"\n{2,}", text):
            bl = [_clean(l) for l in block.splitlines()]
            bl = [l for l in bl if l]
            if bl:
                chunks.append(bl)
    records = []
    for chunk in chunks:
        rec = _parse_chunk(chunk)
        if rec["question_text"]:
            records.append(rec)
    return records


def _list_item_fallback(items):
    records = []
    for item in items:
        text = _clean(item)
        if not text or len(text) < 10 or len(text) > 5000:
            continue
        if re.search(r"\?$", text) or re.search(r"\b(what|which|how|explain|define|why|name|list)\b", text, re.I):
            records.append({"question_text": text, "options": [], "correct_answer": "", "explanation": ""})
    return records


# ------------------------------------------------------------------- entry
def scrape_url(url):
    """Fetch a URL and return (final_url, records). Raises ValueError on bad input."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http:// and https:// URLs are supported.")
    if not parsed.hostname:
        raise ValueError("Invalid URL.")
    _assert_public_host(parsed.hostname)

    headers = {"User-Agent": USER_AGENT,
               "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
    try:
        with httpx.Client(follow_redirects=True, timeout=TIMEOUT_SECONDS, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError("Could not fetch the URL: " + str(exc)[:160]) from exc

    body = resp.content
    if len(body) > MAX_BYTES:
        raise ValueError("Page is larger than 5 MB.")
    text = body.decode("utf-8", "ignore")
    final_url = str(resp.url)

    records = _jsonld_questions(_maybe_jsonld(text))

    if not records:
        extractor = _TextExtractor()
        extractor.feed(text)
        extractor.close()
        records = _extract_from_text(extractor.text())

    if not records and "<html" in text[:400].lower():
        extractor = _TextExtractor()
        extractor.feed(text)
        extractor.close()
        records = _list_item_fallback(extractor.list_items)

    records = records[:MAX_QUESTIONS]
    return final_url, records


def _maybe_jsonld(html_text):
    data = []
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                         html_text, re.S | re.I):
        try:
            data.append(json.loads(m.group(1)))
        except (ValueError, TypeError):
            continue
    return data
