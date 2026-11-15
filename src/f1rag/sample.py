"""Sample data: copy a few races from the full data into the small committed sample folder"""

import argparse
import contextlib
import shutil
import sys
from pathlib import Path

from chromadb.api.client import SharedSystemClient

from f1rag import config, store, vectors

# One race per season, each with a golden question: a red flag, rain and a relocated race
SAMPLE_RACES = [(2024, 8), (2025, 12), (2026, 16)]


class SampleError(Exception):
    """Raised when the sample cannot be built, with a message naming the problem"""


def race_filter(races):
    """Return a SQL condition and its values that match the given (season, round) pairs"""
    condition = " OR ".join("(season = ? AND round = ?)" for _ in races)
    return f"({condition})", [value for race in races for value in race]


def build(db_path, chroma_path, out_dir, races, embedding_function=None):
    """Write a sample database and vector store holding only the given races"""
    out_dir, races = Path(out_dir), [tuple(race) for race in races]
    with contextlib.closing(store.connect(db_path)) as source:
        loaded = {tuple(row) for row in source.execute("SELECT season, round FROM events")}
    missing = [race for race in races if race not in loaded]
    if missing:
        names = ", ".join(f"{season} round {round_no}" for season, round_no in missing)
        raise SampleError(f"not in the full data: {names}. Load it first: python -m f1rag.ingest")
    # A rebuild starts clean, so no rows or chunks of an earlier sample remain
    for old in (out_dir / "f1_stats.db", out_dir / "chroma"):
        if old.is_dir():
            shutil.rmtree(old)
        elif old.exists():
            old.unlink()
    SharedSystemClient.clear_system_cache()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = copy_tables(db_path, out_dir / "f1_stats.db", races)
    chunks = copy_chunks(chroma_path, out_dir / "chroma", races, embedding_function)
    return rows, chunks


def copy_tables(db_path, target_path, races):
    """Copy the races' rows of every table into a new database and return the row count"""
    condition, values = race_filter(races)
    with contextlib.closing(store.connect(target_path)) as target:
        store.create_schema(target)
        target.execute("ATTACH DATABASE ? AS source", (str(db_path),))
        tables = [r[0] for r in target.execute(
            "SELECT name FROM main.sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'")]
        total = 0
        with target:
            for table in tables:
                cursor = target.execute(
                    f"INSERT INTO main.{table} SELECT * FROM source.{table} WHERE {condition}",
                    values)
                total += cursor.rowcount
        target.execute("DETACH DATABASE source")
        # Drop the free pages so the committed file stays small
        target.execute("VACUUM")
    return total


def copy_chunks(chroma_path, target_path, races, embedding_function=None):
    """Copy the races' chunks with their stored vectors and return how many were copied"""
    source = vectors.open_collection(chroma_path, embedding_function=embedding_function)
    target = vectors.open_collection(target_path, embedding_function=embedding_function)
    total = 0
    for season, round_no in races:
        found = source.get(where={"$and": [{"season": season}, {"round": round_no}]},
                           include=["documents", "metadatas", "embeddings"])
        if found["ids"]:
            # Stored vectors are copied, so the embedding model is not needed here
            target.add(ids=found["ids"], documents=found["documents"],
                       metadatas=found["metadatas"], embeddings=found["embeddings"])
            total += len(found["ids"])
    return total


def main(argv=None, out=print):
    """Build the committed sample from the full data and return the exit code"""
    parser = argparse.ArgumentParser(
        prog="python -m f1rag.sample",
        description="Copy the sample races from data/ into sample/ for a fresh clone to try",
    )
    parser.add_argument("--db", default=str(config.DB_PATH), help="full database")
    parser.add_argument("--chroma", default=str(config.CHROMA_DIR), help="full vector store")
    parser.add_argument("--out", default=str(config.SAMPLE_DIR), help="sample folder")
    args = parser.parse_args(argv)
    if not Path(args.db).exists():
        out(f"No database at {args.db}. Load races first: python -m f1rag.ingest --year 2026 --all")
        return 1
    try:
        rows, chunks = build(args.db, args.chroma, args.out, SAMPLE_RACES)
    except SampleError as error:
        out(f"Cannot build the sample: {error}")
        return 1
    races = ", ".join(f"{season} round {round_no}" for season, round_no in SAMPLE_RACES)
    out(f"Sample written to {args.out}: {races}; {rows} rows and {chunks} report chunks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
