import pytest

from f1rag import vectors
from f1rag.ingest import text

# Everything below is placeholder data for tests, not real F1 data
MEETING = {"meeting_name": "Placeholder Grand Prix", "location": "Mainville"}
ARTICLE = {
    "title": "2026 Placeholder Grand Prix",
    "url": "https://en.wikipedia.org/wiki/2026_Placeholder_Grand_Prix",
    "revision_id": 42,
    "published_at": "2026-10-05T10:00:00Z",
    "text": "The race was a Formula One race. It had placeholder events.\n== Race ==\nA report.",
}


class FakeWikipedia:
    """A stand-in for WikipediaClient that returns one article or raises"""

    def __init__(self, article=None):
        """Keep the article to return, or None to fail"""
        self.article = article
        self.asked = []

    def article_for_race(self, season, event_name, location):
        """Record the request and return the article, or raise when there is none"""
        self.asked.append((season, event_name, location))
        if self.article is None:
            raise LookupError(f"No Wikipedia article found for {season} {event_name}")
        return self.article


@pytest.fixture
def collection(tmp_path, fake_embedding):
    """Open a collection in a temporary folder with the fake embedding"""
    return vectors.open_collection(tmp_path, embedding_function=fake_embedding)


def test_ingest_race_text_stores_chunks_for_the_race(collection):
    """Check that the race's article is chunked and stored with season, round and times"""
    wiki = FakeWikipedia(ARTICLE)
    count = text.ingest_race_text(collection, wiki, 2026, 3, MEETING, "2026-11-13T00:00:00+00:00")
    assert wiki.asked == [(2026, "Placeholder Grand Prix", "Mainville")]
    stored = collection.get(where={"$and": [{"season": 2026}, {"round": 3}]})
    assert count == len(stored["ids"]) == 2
    meta = stored["metadatas"][0]
    assert meta["source_type"] == "wiki"
    assert meta["revision_id"] == 42
    assert meta["ingested_at"] == "2026-11-13T00:00:00+00:00"


def test_missing_article_fails_and_keeps_existing_chunks(collection):
    """Check that a failed lookup raises and leaves the race's earlier chunks in place"""
    text.ingest_race_text(collection, FakeWikipedia(ARTICLE), 2026, 3, MEETING, "t1")
    with pytest.raises(LookupError):
        text.ingest_race_text(collection, FakeWikipedia(None), 2026, 3, MEETING, "t2")
    assert collection.count() == 2
