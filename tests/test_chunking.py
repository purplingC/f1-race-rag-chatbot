import re

from f1rag.ingest import chunking

# Everything below is placeholder text for tests, not real F1 data


def sentence(n, words=10):
    """Build one placeholder sentence of a known word count, numbered so it can be found"""
    return " ".join([f"S{n}"] + ["word"] * (words - 2) + ["end."])


def paragraph(start, count, words=10):
    """Build a paragraph of numbered placeholder sentences"""
    return " ".join(sentence(start + i, words) for i in range(count))


ARTICLE_TEXT = "\n".join(
    [
        "Intro sentence one is here. Intro sentence two is here.",
        "",
        "== Background ==",
        "Background text about the placeholder event.",
        "=== Relocation ===",
        "Relocation text about the placeholder event.",
        "=== Championship standings before the race ===",
        "Standings text that should be skipped.",
        "== Race ==",
        "=== Race report ===",
        paragraph(1, 12),
        paragraph(13, 12),
        "=== Race classification ===",
        "Classification note that should be skipped.",
        "== See also ==",
        "List of placeholder races.",
        "== References ==",
        "Reference text that should be skipped.",
    ]
)

ARTICLE = {
    "title": "2026 Placeholder Grand Prix",
    "url": "https://en.wikipedia.org/wiki/2026_Placeholder_Grand_Prix",
    "revision_id": 123456,
    "published_at": "2026-10-05T10:00:00Z",
    "text": ARTICLE_TEXT,
}


def chunks():
    """Chunk the placeholder article for season 2026 round 1"""
    return chunking.chunk_article(ARTICLE, 2026, 1, "2026-11-13T00:00:00+00:00")


def body(chunk):
    """Return the chunk text without its title and section prefix line"""
    return chunk["document"].split("\n", 1)[1]


def test_split_sections_reads_heading_levels():
    """Check that headings of every level build the section path"""
    text = "Intro.\n== Race ==\n=== Race report ===\nReport text.\n==== Reactions ====\nQuotes."
    sections = chunking.split_sections(text)
    assert [path for path, _ in sections] == [
        ("Introduction",),
        ("Race",),
        ("Race", "Race report"),
        ("Race", "Race report", "Reactions"),
    ]
    assert sections[2][1].strip() == "Report text."


def test_skipped_sections_and_their_subsections_are_left_out():
    """Check reference lists, tables and standings never become chunks"""
    text = " ".join(c["document"] for c in chunks())
    for left_out in ("should be skipped", "List of placeholder races"):
        assert left_out not in text
    assert "Relocation text" in text
    assert "Intro sentence one" in text


def test_chunk_bodies_stay_within_the_word_limit():
    """Check no chunk body is longer than the limit the embedding model can read"""
    assert all(len(body(c).split()) <= chunking.MAX_WORDS for c in chunks())


def test_every_kept_sentence_appears_in_some_chunk():
    """Check that chunking loses no text from the kept sections"""
    text = " ".join(c["document"] for c in chunks())
    for n in range(1, 25):
        assert f"S{n} " in text, n


def test_neighbouring_chunks_overlap_by_whole_sentences():
    """Check the next chunk repeats the last sentences of the previous one, within the limit"""
    report = [c for c in chunks() if c["metadata"]["section"] == "Race > Race report"]
    assert len(report) >= 2
    for first, second in zip(report, report[1:]):
        last = re.findall(r"S\d+ ", body(first))
        nxt = re.findall(r"S\d+ ", body(second))
        shared = [s for s in nxt if s in last]
        assert shared, "expected overlap"
        assert len(shared) * 10 <= chunking.OVERLAP_WORDS


def test_chunks_never_mix_sections():
    """Check each chunk belongs to exactly one section"""
    for c in chunks():
        section = c["metadata"]["section"]
        if section != "Race > Race report":
            assert "S1 " not in c["document"]


def test_document_starts_with_title_and_section():
    """Check every chunk is prefixed with the article title and section for context"""
    first = chunks()[0]
    assert first["document"].startswith("2026 Placeholder Grand Prix - Introduction\n")


def test_overlong_sentence_is_split_by_words():
    """Check a single sentence longer than the limit is cut into pieces"""
    long_text = "== Race ==\n" + sentence(1, words=chunking.MAX_WORDS * 2 + 5)
    article = dict(ARTICLE, text=long_text)
    result = chunking.chunk_article(article, 2026, 1, "2026-11-13T00:00:00+00:00")
    assert len(result) >= 3
    assert all(len(body(c).split()) <= chunking.MAX_WORDS for c in result)


def test_ids_are_deterministic_and_unique():
    """Check the same article always gives the same ids, and ids never repeat"""
    first = [c["id"] for c in chunks()]
    second = [c["id"] for c in chunks()]
    assert first == second
    assert len(set(first)) == len(first)
    other = dict(ARTICLE, url="https://en.wikipedia.org/wiki/Another_Placeholder")
    moved = chunking.chunk_article(other, 2026, 1, "2026-11-13T00:00:00+00:00")
    assert moved[0]["id"] != first[0]


def test_metadata_has_provenance_and_no_empty_values():
    """Check each chunk carries season, round, source and attribution, with no null values"""
    result = chunks()
    meta = result[0]["metadata"]
    assert meta["season"] == 2026 and meta["round"] == 1
    assert meta["source_type"] == "wiki"
    assert meta["source_name"] == "Wikipedia"
    assert meta["title"] == "2026 Placeholder Grand Prix"
    assert meta["url"] == ARTICLE["url"]
    assert meta["revision_id"] == 123456
    assert meta["licence"] == "CC BY-SA 4.0"
    assert meta["published_at"] == "2026-10-05T10:00:00Z"
    assert meta["ingested_at"] == "2026-11-13T00:00:00+00:00"
    assert [c["metadata"]["chunk_idx"] for c in result] == list(range(len(result)))
    for c in result:
        assert all(value is not None for value in c["metadata"].values())


def test_empty_article_gives_no_chunks():
    """Check an article with no text gives an empty list"""
    article = dict(ARTICLE, text="")
    assert chunking.chunk_article(article, 2026, 1, "2026-11-13T00:00:00+00:00") == []
