"""Project paths used by ingestion and the query path"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "f1_stats.db"
CHROMA_DIR = DATA_DIR / "chroma"
CACHE_DIR = DATA_DIR / "cache"
