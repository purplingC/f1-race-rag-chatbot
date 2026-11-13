"""Project paths used by ingestion and the query path"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "f1_stats.db"
CHROMA_DIR = DATA_DIR / "chroma"
CACHE_DIR = DATA_DIR / "cache"
# Official driver photos for local use only, never committed
HEADSHOT_DIR = DATA_DIR / "headshots"
# Official team logos for local use only, never committed
TEAM_LOGO_DIR = DATA_DIR / "team_logos"
