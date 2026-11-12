import pytest

from f1rag import vectors

# Everything below is placeholder data for tests, not real F1 data


def chunk(season, round_no, n, source_type="wiki"):
    """Build one placeholder chunk with the metadata the store expects"""
    return {
        "id": f"{season}-{round_no}-{source_type}-{n}",
        "document": f"Placeholder chunk {n} for round {round_no}",
        "metadata": {"season": season, "round": round_no, "source_type": source_type},
    }


@pytest.fixture
def collection(tmp_path, fake_embedding):
    """Open a collection in a temporary folder with the fake embedding"""
    return vectors.open_collection(tmp_path, embedding_function=fake_embedding)


def ids_for(collection, season, round_no):
    """Return the sorted ids stored for one race"""
    found = collection.get(where={"$and": [{"season": season}, {"round": round_no}]})
    return sorted(found["ids"])


def test_client_has_telemetry_switched_off(tmp_path):
    """Check that chromadb's anonymous telemetry is off for our client"""
    assert vectors.open_client(tmp_path).get_settings().anonymized_telemetry is False


def test_replace_adds_the_chunks_and_returns_the_count(collection):
    """Check that a race's chunks are stored with their text and metadata"""
    count = vectors.replace_race_docs(collection, 2026, 1, "wiki", [chunk(2026, 1, 0)])
    assert count == 1
    stored = collection.get(ids=["2026-1-wiki-0"])
    assert stored["documents"] == ["Placeholder chunk 0 for round 1"]
    assert stored["metadatas"][0]["season"] == 2026


def test_replacing_twice_creates_no_duplicates(collection):
    """Check idempotency: the same chunks loaded twice are stored once"""
    chunks = [chunk(2026, 1, n) for n in range(3)]
    vectors.replace_race_docs(collection, 2026, 1, "wiki", chunks)
    vectors.replace_race_docs(collection, 2026, 1, "wiki", chunks)
    assert collection.count() == 3


def test_replace_removes_chunks_that_no_longer_exist(collection):
    """Check that a shorter new version of an article leaves no stale chunks"""
    vectors.replace_race_docs(collection, 2026, 1, "wiki", [chunk(2026, 1, n) for n in range(3)])
    vectors.replace_race_docs(collection, 2026, 1, "wiki", [chunk(2026, 1, 0)])
    assert ids_for(collection, 2026, 1) == ["2026-1-wiki-0"]


def test_replace_leaves_other_races_and_sources_alone(collection):
    """Check that loading one race does not touch other rounds or other source types"""
    vectors.replace_race_docs(collection, 2026, 1, "wiki", [chunk(2026, 1, 0)])
    vectors.replace_race_docs(collection, 2026, 2, "wiki", [chunk(2026, 2, 0)])
    vectors.replace_race_docs(collection, 2026, 1, "summary", [chunk(2026, 1, 0, "summary")])
    vectors.replace_race_docs(collection, 2026, 1, "wiki", [chunk(2026, 1, 5)])
    assert ids_for(collection, 2026, 2) == ["2026-2-wiki-0"]
    assert ids_for(collection, 2026, 1) == ["2026-1-summary-0", "2026-1-wiki-5"]


def test_replace_with_no_chunks_clears_the_race(collection):
    """Check that an empty load removes the race's old chunks of that source"""
    vectors.replace_race_docs(collection, 2026, 1, "wiki", [chunk(2026, 1, 0)])
    assert vectors.replace_race_docs(collection, 2026, 1, "wiki", []) == 0
    assert ids_for(collection, 2026, 1) == []


def test_query_can_be_limited_to_one_race(collection):
    """Check that a search filtered by season and round only returns that race"""
    vectors.replace_race_docs(collection, 2026, 1, "wiki", [chunk(2026, 1, 0)])
    vectors.replace_race_docs(collection, 2026, 2, "wiki", [chunk(2026, 2, 0)])
    result = collection.query(
        query_texts=["Placeholder chunk"],
        n_results=5,
        where={"$and": [{"season": 2026}, {"round": 2}]},
    )
    assert result["ids"] == [["2026-2-wiki-0"]]


def test_collection_is_kept_on_disk(tmp_path, fake_embedding):
    """Check that chunks are still there after reopening the folder"""
    first = vectors.open_collection(tmp_path, embedding_function=fake_embedding)
    vectors.replace_race_docs(first, 2026, 1, "wiki", [chunk(2026, 1, 0)])
    again = vectors.open_collection(tmp_path, embedding_function=fake_embedding)
    assert again.count() == 1
