"""Project paths used by ingestion and the query path"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "f1_stats.db"
CHROMA_DIR = DATA_DIR / "chroma"
CACHE_DIR = DATA_DIR / "cache"
# Small committed sample (three races) that the app and ask read when the full data is missing
SAMPLE_DIR = ROOT / "sample"
SAMPLE_DB_PATH = SAMPLE_DIR / "f1_stats.db"
# ChromaDB writes to its folder even when only reading, so the sample is used from a local copy
SAMPLE_WORK_DIR = DATA_DIR / "sample"
# Golden question set (committed) and the evaluation reports it produces (local only)
GOLDEN_PATH = ROOT / "eval" / "golden.toml"
EVAL_DIR = DATA_DIR / "eval"
# Official driver photos for local use only, never committed
HEADSHOT_DIR = DATA_DIR / "headshots"
# Official team logos for local use only, never committed
TEAM_LOGO_DIR = DATA_DIR / "team_logos"


def query_paths():
    """Return the database and vector store to answer from, and whether they are the sample"""
    if DB_PATH.exists() or not SAMPLE_DB_PATH.exists():
        return DB_PATH, CHROMA_DIR, False
    copy = SAMPLE_WORK_DIR / "f1_stats.db"
    # Copied on first use, and again when the committed sample is newer than the copy
    if not copy.exists() or copy.stat().st_mtime < SAMPLE_DB_PATH.stat().st_mtime:
        shutil.rmtree(SAMPLE_WORK_DIR, ignore_errors=True)
        shutil.copytree(SAMPLE_DIR, SAMPLE_WORK_DIR)
    return copy, SAMPLE_WORK_DIR / "chroma", True
