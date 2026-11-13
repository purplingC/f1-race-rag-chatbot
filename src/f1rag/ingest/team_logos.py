"""Team logos: download each team's official logo once per season and make a sheet to review them"""

# The logos are trademarks of their teams: they stay in the git-ignored data/team_logos folder,
# are never committed, and the dashboard only shows them when SHOW_TEAM_LOGOS=true

import argparse
import contextlib
import math
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from f1rag import config, store
from f1rag.ingest.headshots import PACE_S, fetch_photo, hex_color

# The official site names every logo after the season and the team name without spaces
URL = ("https://media.formula1.com/image/upload/c_lfill,w_96/q_auto/v1740000001/"
       "common/f1/{season}/{slug}/{season}{slug}logowhite.webp")
SEASON = re.compile(r"^\d{4}$")
# Review sheet layout in pixels
SHEET_COLUMNS = 4
TILE_W, TILE_H = 240, 120
SHEET_TOP = 50


def slug(team):
    """Turn a team name into the official link name, for example haasf1team"""
    return re.sub(r"[^a-z0-9]", "", (team or "").lower())


def logo_url(season, team):
    """Return the official logo link for a team in a season"""
    return URL.format(season=season, slug=slug(team))


def logo_urls(season, team):
    """Return the links to try: the full name, then its first word (2024 used haas)"""
    names = [slug(team), slug((team or "").split(" ")[0])]
    return [URL.format(season=season, slug=name) for name in dict.fromkeys(n for n in names if n)]


def logo_path(folder, season, team):
    """Return the file for a team's logo, refusing anything but a four-digit season"""
    if not SEASON.match(str(season)):
        raise ValueError(f"Not a season: {season!r}")
    return Path(folder) / str(season) / f"{slug(team)}.png"


def season_teams(conn, season):
    """List the teams of a season with their latest team colour"""
    sql = (
        "SELECT team, team_color FROM entries e WHERE season = ? AND team IS NOT NULL "
        "AND round = (SELECT MAX(x.round) FROM entries x WHERE x.season = e.season "
        "AND x.team = e.team) GROUP BY team ORDER BY team"
    )
    return [{"team": team, "color": color} for team, color in conn.execute(sql, (season,))]


def download_all(conn, season, folder, http=None, sleep=time.sleep, refresh=False):
    """Save every team's logo of the season as a PNG and report what was saved, kept or failed"""
    http = http or requests
    report = {"saved": [], "kept": [], "failed": []}
    first = True
    for entry in season_teams(conn, season):
        path = logo_path(folder, season, entry["team"])
        if path.exists() and not refresh:
            report["kept"].append(entry["team"])
            continue
        if not first:
            sleep(PACE_S)
        first = False
        image, error = None, None
        for url in logo_urls(season, entry["team"]):
            try:
                image = fetch_photo(http, url)
                break
            except (ValueError, requests.RequestException) as problem:
                error = problem
        if image is None:
            report["failed"].append((entry["team"], str(error)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG")
        report["saved"].append(entry["team"])
    return report


def review_sheet(conn, season, folder):
    """Draw every team's logo on its team colour with its name, and save the sheet"""
    teams = season_teams(conn, season)
    columns = max(1, min(len(teams), SHEET_COLUMNS))
    rows = max(1, math.ceil(len(teams) / columns))
    sheet = Image.new("RGB", (columns * TILE_W, SHEET_TOP + rows * TILE_H), (21, 21, 30))
    draw = ImageDraw.Draw(sheet)
    draw.text((16, 16), f"{season} team logos: check each logo and name",
              fill=(242, 242, 245), font=ImageFont.load_default(size=18))
    small = ImageFont.load_default(size=13)
    for index, entry in enumerate(teams):
        x = (index % columns) * TILE_W + 10
        y = SHEET_TOP + (index // columns) * TILE_H
        draw.rounded_rectangle((x, y, x + TILE_W - 20, y + TILE_H - 14), radius=10,
                               fill=hex_color(entry["color"]))
        path = logo_path(folder, season, entry["team"])
        if path.exists():
            logo = Image.open(path).convert("RGBA")
            logo.thumbnail((TILE_W - 60, 56))
            sheet.paste(logo, (x + (TILE_W - 20 - logo.width) // 2, y + 12), logo)
        else:
            draw.text((x + 12, y + 30), "no logo", fill=(242, 242, 245), font=small)
        draw.text((x + 12, y + TILE_H - 36), entry["team"], fill=(242, 242, 245), font=small)
    out = Path(folder) / f"review_sheet_{season}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def main(argv=None):
    """Download the team logos of the chosen seasons and make one review sheet per season"""
    parser = argparse.ArgumentParser(
        prog="python -m f1rag.ingest.team_logos",
        description="Download official team logos for local use and make review sheets",
    )
    parser.add_argument("--season", type=int, nargs="+", help="seasons, default all loaded")
    parser.add_argument("--refresh", action="store_true", help="download logos again")
    parser.add_argument("--db", default=str(config.DB_PATH), help="database file")
    parser.add_argument("--dir", default=str(config.TEAM_LOGO_DIR), help="logo folder")
    args = parser.parse_args(argv)
    if not Path(args.db).exists():
        print(f"No database at {args.db}. Load races first with python -m f1rag.ingest")
        return 1
    failed = 0
    with contextlib.closing(store.connect(args.db)) as conn:
        seasons = args.season or [row[0] for row in
                                  conn.execute("SELECT DISTINCT season FROM entries ORDER BY 1")]
        for season in seasons:
            report = download_all(conn, season, args.dir, refresh=args.refresh)
            sheet = review_sheet(conn, season, args.dir)
            print(f"{season}: saved {len(report['saved'])}, already there "
                  f"{len(report['kept'])}, failed {len(report['failed'])}")
            for team, reason in report["failed"]:
                print(f"  Failed {team}: {reason}")
            print(f"  Review sheet: {sheet}")
            failed += len(report["failed"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
