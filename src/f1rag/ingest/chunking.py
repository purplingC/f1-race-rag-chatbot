"""Split a Wikipedia article into small, overlapping chunks with provenance metadata"""

import hashlib
import re

# The embedding model reads at most 256 word pieces; F1 text has many names and numbers that
# split into several pieces, so on the 20 real 2026 articles 160 words left 3 chunks over the
# limit and 140 words gives at most 221 pieces, about 150 words with the title and section
MAX_WORDS = 140
# Whole sentences from the end of a chunk are repeated at the start of the next one
OVERLAP_WORDS = 30
INTRODUCTION = "Introduction"
LICENCE = "CC BY-SA 4.0"
# Sections with lists, tables or links instead of narrative text
SKIP_TITLES = {
    "see also",
    "notes",
    "references",
    "external links",
    "footnotes",
    "bibliography",
    "further reading",
}
SKIP_PATTERN = re.compile(r"classification|standings", re.IGNORECASE)
HEADING = re.compile(r"^(=+)\s*(.+?)\s*=+\s*$")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sections(text):
    """Split plain article text into (section path, body) pairs using its == headings =="""
    sections = []
    stack = []
    path = (INTRODUCTION,)
    lines = []
    for line in text.split("\n"):
        match = HEADING.match(line)
        if not match:
            lines.append(line)
            continue
        sections.append((path, "\n".join(lines)))
        lines = []
        level, name = len(match.group(1)), match.group(2)
        stack = [(lvl, title) for lvl, title in stack if lvl < level] + [(level, name)]
        path = tuple(title for _, title in stack)
    sections.append((path, "\n".join(lines)))
    # Drop an empty introduction, keep empty parent headings so their children have a path
    return [(p, body) for p, body in sections if p != (INTRODUCTION,) or body.strip()]


def is_skipped(path):
    """Tell whether a section or any of its parents holds lists, tables or links"""
    return any(name.lower() in SKIP_TITLES or SKIP_PATTERN.search(name) for name in path)


def sentence_units(paragraphs):
    """Turn paragraphs into (paragraph number, words) units, cutting overlong sentences"""
    units = []
    for number, paragraph in enumerate(paragraphs):
        for sentence in SENTENCE_END.split(paragraph):
            words = sentence.split()
            for start in range(0, len(words), MAX_WORDS):
                units.append((number, words[start : start + MAX_WORDS]))
    return units


def word_count(units):
    """Count the words in a list of units"""
    return sum(len(words) for _, words in units)


def overlap_tail(units):
    """Return the last whole sentences of a chunk that fit in the overlap size"""
    tail = []
    for unit in reversed(units):
        if word_count(tail) + len(unit[1]) > OVERLAP_WORDS:
            break
        tail.insert(0, unit)
    return tail


def join_units(units):
    """Join units into text, starting a new line where the paragraph changes"""
    text = ""
    previous = None
    for number, words in units:
        if previous is not None:
            text += " " if number == previous else "\n"
        text += " ".join(words)
        previous = number
    return text


def section_chunks(paragraphs):
    """Pack a section's sentences into chunks of at most MAX_WORDS with a sentence overlap"""
    chunks = []
    current = []
    for unit in sentence_units(paragraphs):
        if current and word_count(current) + len(unit[1]) > MAX_WORDS:
            chunks.append(current)
            current = overlap_tail(current)
            if word_count(current) + len(unit[1]) > MAX_WORDS:
                current = []
        current.append(unit)
    if current:
        chunks.append(current)
    return [join_units(chunk) for chunk in chunks]


def chunk_id(url, index):
    """Build a stable id from the source URL and the chunk position"""
    return hashlib.sha256(f"{url}#{index}".encode()).hexdigest()[:24]


def chunk_article(article, season, round_no, ingested_at):
    """Turn one article into chunks with ids, prefixed text and provenance metadata"""
    chunks = []
    for path, text in split_sections(article["text"]):
        if is_skipped(path):
            continue
        paragraphs = [line.strip() for line in text.split("\n") if line.strip()]
        section = " > ".join(path)
        for body in section_chunks(paragraphs):
            index = len(chunks)
            chunks.append(
                {
                    "id": chunk_id(article["url"], index),
                    "document": f"{article['title']} - {section}\n{body}",
                    "metadata": {
                        "season": season,
                        "round": round_no,
                        "source_type": "wiki",
                        "source_name": "Wikipedia",
                        "title": article["title"],
                        "url": article["url"],
                        "revision_id": article["revision_id"],
                        "section": section,
                        "chunk_idx": index,
                        "licence": LICENCE,
                        "published_at": article.get("published_at") or "",
                        "ingested_at": ingested_at,
                    },
                }
            )
    return chunks
