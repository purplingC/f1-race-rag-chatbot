import os

import pytest

from f1rag import config, sample, store, vectors

# Everything below is placeholder data for tests, not real F1 data

TABLE_ROWS = {
    "events": "INSERT INTO events (season, round, event_name) VALUES (2026, ?, 'Placeholder')",
    "entries": "INSERT INTO entries (season, round, driver_code) VALUES (2026, ?, 'AAA')",
    "results": "INSERT INTO results (season, round, driver_code) VALUES (2026, ?, 'AAA')",
    "laps": "INSERT INTO laps (season, round, driver_code, lap_number) VALUES (2026, ?, 'AAA', 1)",
    "stints": "INSERT INTO stints (season, round, driver_code, stint_no) "
              "VALUES (2026, ?, 'AAA', 1)",
    "pit_stops": "INSERT INTO pit_stops (season, round, driver_code, stop_no) "
                 "VALUES (2026, ?, 'AAA', 1)",
    "weather": "INSERT INTO weather (season, round, ts) VALUES (2026, ?, 't0')",
    "race_control": "INSERT INTO race_control (season, round, event_no) VALUES (2026, ?, 1)",
    "race_summaries": "INSERT INTO race_summaries (season, round, summary_md) "
                      "VALUES (2026, ?, 'Placeholder')",
    "ingest_log": "INSERT INTO ingest_log (season, round, source, status) "
                  "VALUES (2026, ?, 'wikipedia', 'ok')",
}


def chunk(round_no, idx):
    """Build one placeholder chunk for a race"""
    return {
        "id": f"r{round_no}-{idx}",
        "document": f"Placeholder race {round_no} - Race\nPlaceholder text {idx}",
        "metadata": {"season": 2026, "round": round_no, "source_type": "wiki",
                     "title": "Placeholder", "section": "Race", "chunk_idx": idx},
    }


@pytest.fixture
def source(tmp_path, fake_embedding):
    """Create a full placeholder database and vector store with rounds 1 and 2"""
    db = tmp_path / "full.db"
    conn = store.connect(db)
    store.create_schema(conn)
    with conn:
        for round_no in (1, 2):
            for sql in TABLE_ROWS.values():
                conn.execute(sql, (round_no,))
    conn.close()
    chroma = tmp_path / "full_chroma"
    col = vectors.open_collection(chroma, embedding_function=fake_embedding)
    for round_no in (1, 2):
        vectors.replace_race_docs(col, 2026, round_no, "wiki",
                                  [chunk(round_no, 0), chunk(round_no, 1)])
    return db, chroma


def counts(db):
    """Count each table's rows by round"""
    conn = store.connect(db)
    found = {table: sorted(r[0] for r in conn.execute(f"SELECT round FROM {table}"))
             for table in TABLE_ROWS}
    conn.close()
    return found


def test_build_copies_only_the_chosen_races_from_every_table(source, tmp_path, fake_embedding):
    """Check each table, the ingest log included, keeps only the chosen race"""
    db, chroma = source
    out = tmp_path / "sample"
    sample.build(db, chroma, out, [(2026, 1)], embedding_function=fake_embedding)
    assert counts(out / "f1_stats.db") == {table: [1] for table in TABLE_ROWS}


def test_build_copies_the_race_chunks_with_their_embeddings(source, tmp_path, fake_embedding):
    """Check the chosen race's chunks keep their ids, text, metadata and vectors"""
    db, chroma = source
    out = tmp_path / "sample"
    sample.build(db, chroma, out, [(2026, 1)], embedding_function=fake_embedding)
    copied = vectors.open_collection(out / "chroma", embedding_function=fake_embedding)
    got = copied.get(include=["documents", "metadatas", "embeddings"])
    assert sorted(got["ids"]) == ["r1-0", "r1-1"]
    original = vectors.open_collection(chroma, embedding_function=fake_embedding)
    want = original.get(ids=["r1-0"], include=["embeddings"])["embeddings"][0]
    index = got["ids"].index("r1-0")
    assert list(got["embeddings"][index]) == pytest.approx(list(want))
    assert got["metadatas"][index]["round"] == 1


def test_build_twice_replaces_the_sample(source, tmp_path, fake_embedding):
    """Check a rebuild leaves no rows or chunks from the earlier sample"""
    db, chroma = source
    out = tmp_path / "sample"
    sample.build(db, chroma, out, [(2026, 1), (2026, 2)], embedding_function=fake_embedding)
    sample.build(db, chroma, out, [(2026, 2)], embedding_function=fake_embedding)
    assert counts(out / "f1_stats.db")["laps"] == [2]
    copied = vectors.open_collection(out / "chroma", embedding_function=fake_embedding)
    assert sorted(copied.get()["ids"]) == ["r2-0", "r2-1"]


def test_build_refuses_a_race_that_is_not_loaded(source, tmp_path, fake_embedding):
    """Check a sample race missing from the full data is refused before anything is written"""
    db, chroma = source
    out = tmp_path / "sample"
    with pytest.raises(sample.SampleError, match="2026 round 9"):
        sample.build(db, chroma, out, [(2026, 9)], embedding_function=fake_embedding)
    assert not out.exists()


def test_query_paths_use_the_sample_only_when_the_full_data_is_missing(tmp_path, monkeypatch):
    """Check the app reads the full data when it exists and a copy of the sample otherwise"""
    full_db = tmp_path / "full.db"
    sample_dir, work_dir = tmp_path / "sample", tmp_path / "work"
    (sample_dir / "chroma").mkdir(parents=True)
    (sample_dir / "f1_stats.db").write_text("placeholder")
    (sample_dir / "chroma" / "index.bin").write_text("placeholder")
    monkeypatch.setattr(config, "DB_PATH", full_db)
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(config, "SAMPLE_DIR", sample_dir)
    monkeypatch.setattr(config, "SAMPLE_DB_PATH", sample_dir / "f1_stats.db")
    monkeypatch.setattr(config, "SAMPLE_WORK_DIR", work_dir)
    # ChromaDB writes to its folder even when only reading, so the app works on a copy
    assert config.query_paths() == (work_dir / "f1_stats.db", work_dir / "chroma", True)
    assert (work_dir / "chroma" / "index.bin").read_text() == "placeholder"
    full_db.touch()
    assert config.query_paths() == (full_db, tmp_path / "chroma", False)


def test_a_rebuilt_sample_refreshes_the_working_copy(tmp_path, monkeypatch):
    """Check a newer sample replaces the old working copy on the next start"""
    sample_dir, work_dir = tmp_path / "sample", tmp_path / "work"
    sample_dir.mkdir()
    (sample_dir / "f1_stats.db").write_text("old")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "missing.db")
    monkeypatch.setattr(config, "SAMPLE_DIR", sample_dir)
    monkeypatch.setattr(config, "SAMPLE_DB_PATH", sample_dir / "f1_stats.db")
    monkeypatch.setattr(config, "SAMPLE_WORK_DIR", work_dir)
    config.query_paths()
    (sample_dir / "f1_stats.db").write_text("new")
    os.utime(sample_dir / "f1_stats.db", (4_000_000_000, 4_000_000_000))
    config.query_paths()
    assert (work_dir / "f1_stats.db").read_text() == "new"
