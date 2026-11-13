"""Driver photos: download each driver's official headshot once and make a sheet to review them"""

# The photos are copyrighted by Formula 1: they stay in the git-ignored data/headshots folder,
# are never committed, and the dashboard only shows them when SHOW_HEADSHOTS=true

import argparse
import contextlib
import io
import math
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from f1rag import __version__, config, store

USER_AGENT = f"f1-race-rag-chatbot/{__version__}"
# Seconds between downloads, to be polite to the image server
PACE_S = 1.0
TIMEOUT_S = 20
# A headshot is a few KB; anything far bigger is not what we asked for
MAX_BYTES = 2_000_000
CODE = re.compile(r"^[A-Z]{3}$")
# Review sheet layout in pixels
SHEET_COLUMNS = 6
TILE_W, TILE_H = 200, 190
SHEET_TOP = 60
AVATAR = 110
RING = 5
SKIP_FILE = "skip.txt"
# Photo links added by hand for drivers whose race data has none or only a placeholder
LINKS_FILE = "links.txt"
# Per-driver zoom and shift chosen by the owner for heads that look small, for example big hair
ADJUST_FILE = "adjust.txt"
ZOOM_LIMITS = (0.8, 1.5)
DOWN_LIMITS = (-0.1, 0.15)
# Downloads are kept untouched here, so the avatars can be cropped again without the network
ORIGINALS = "original"
# Avatar composition, tuned on Kimi Antonelli and Lando Norris: the head is 40% of the width,
# centred, with 10% of space above the hair so the circle never cuts it
PHOTO_SIZE = 128
HEAD_SHARE = 0.40
TOP_SHARE = 0.10
# The head is looked for in this share of the picture width below the top of the hair
HEAD_BAND = 0.30


def season_drivers(conn, season):
    """List each driver of the season with name, team, colour and the latest photo link"""
    sql = (
        "SELECT e.driver_code, e.full_name, e.team, e.team_color, "
        "COALESCE((SELECT x.headshot_url FROM entries x WHERE x.season = e.season "
        "AND x.driver_code = e.driver_code AND x.headshot_url <> '' "
        "ORDER BY x.round DESC LIMIT 1), '') "
        "FROM entries e WHERE e.season = ? AND e.round = (SELECT MAX(y.round) FROM entries y "
        "WHERE y.season = e.season AND y.driver_code = e.driver_code) "
        "ORDER BY e.team, e.driver_code"
    )
    keys = ("code", "name", "team", "color", "url")
    return [dict(zip(keys, row)) for row in conn.execute(sql, (season,))]


def photo_path(code, folder):
    """Return the file for a driver's photo, refusing anything but a three-letter code"""
    if not CODE.match(code or ""):
        raise ValueError(f"Not a driver code: {code!r}")
    return Path(folder) / f"{code}.png"


def read_skip_list(folder):
    """Return the driver codes whose photo the owner rejected, for example a placeholder"""
    path = Path(folder) / SKIP_FILE
    if not path.exists():
        return set()
    lines = (line.split("#")[0].strip().upper() for line in path.read_text().splitlines())
    return {code for code in lines if CODE.match(code)}


def add_to_skip_list(folder, codes):
    """Add driver codes to the skip list so they show initials and are never downloaded"""
    codes = {code.strip().upper() for code in codes}
    bad = [code for code in codes if not CODE.match(code)]
    if bad:
        raise ValueError(f"Not driver codes: {', '.join(bad)}")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    merged = sorted(read_skip_list(folder) | codes)
    header = "# Drivers whose photo was rejected in review: they show initials\n"
    (folder / SKIP_FILE).write_text(header + "\n".join(merged) + "\n")
    return merged


def remove_from_skip_list(folder, code):
    """Take one driver off the skip list"""
    remaining = read_skip_list(folder) - {code}
    header = "# Drivers whose photo was rejected in review: they show initials\n"
    (Path(folder) / SKIP_FILE).write_text(header + "".join(f"{c}\n" for c in sorted(remaining)))


def read_links(folder):
    """Return the photo links added by hand, by driver code"""
    path = Path(folder) / LINKS_FILE
    if not path.exists():
        return {}
    links = {}
    for line in path.read_text().splitlines():
        parts = line.split("#")[0].split()
        if len(parts) == 2 and CODE.match(parts[0].upper()) and parts[1].startswith("https://"):
            links[parts[0].upper()] = parts[1]
    return links


def add_link(folder, code, url):
    """Use this photo link for a driver from now on, replacing any earlier photo"""
    code = code.strip().upper()
    if not CODE.match(code):
        raise ValueError(f"Not a driver code: {code!r}")
    if not url.startswith("https://") or any(c.isspace() for c in url):
        raise ValueError("The link must be one https:// address")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    links = {**read_links(folder), code: url}
    header = "# Photo links added by hand (driver code, then link)\n"
    lines = "".join(f"{c} {u}\n" for c, u in sorted(links.items()))
    (folder / LINKS_FILE).write_text(header + lines)
    remove_from_skip_list(folder, code)
    # The old download belongs to another link, so it is fetched again
    photo_path(code, folder / ORIGINALS).unlink(missing_ok=True)
    return links


def make_square(image):
    """Cut a photo to a square that keeps the top, where the head is"""
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    return image.crop((left, 0, left + side, side))


def find_head(alpha):
    """Find the top of the hair, the head's centre and its width in a cut-out's alpha channel"""
    width, height = alpha.size
    pixels = alpha.load()
    rows = [[x for x in range(width) if pixels[x, y] > 128] for y in range(height)]
    top = next((y for y, row in enumerate(rows) if len(row) >= 3), None)
    if top is None:
        raise ValueError("there is nobody in the picture")
    band = [row for row in rows[top:top + max(3, int(HEAD_BAND * width))] if row]
    xs = [x for row in band for x in row]
    head_width = sorted(len(row) for row in band)[len(band) // 2]
    return top, sum(xs) / len(xs), head_width


def read_adjustments(folder):
    """Return the zoom and downward shift set for drivers whose head looks too small"""
    path = Path(folder) / ADJUST_FILE
    if not path.exists():
        return {}
    found = {}
    for line in path.read_text().splitlines():
        parts = line.split("#")[0].split()
        if len(parts) == 3 and CODE.match(parts[0].upper()):
            try:
                found[parts[0].upper()] = (float(parts[1]), float(parts[2]))
            except ValueError:
                continue
    return found


def add_adjustment(folder, code, zoom, down):
    """Save a zoom and downward shift for one driver's avatar, within safe limits"""
    code = code.strip().upper()
    if not CODE.match(code):
        raise ValueError(f"Not a driver code: {code!r}")
    if not ZOOM_LIMITS[0] <= zoom <= ZOOM_LIMITS[1]:
        raise ValueError(f"Zoom must be between {ZOOM_LIMITS[0]} and {ZOOM_LIMITS[1]}")
    if not DOWN_LIMITS[0] <= down <= DOWN_LIMITS[1]:
        raise ValueError(f"Down must be between {DOWN_LIMITS[0]} and {DOWN_LIMITS[1]}")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    found = {**read_adjustments(folder), code: (zoom, down)}
    header = "# Avatar adjustments (driver code, zoom, down as a share of the avatar)\n"
    lines = "".join(f"{c} {z} {d}\n" for c, (z, d) in sorted(found.items()))
    (folder / ADJUST_FILE).write_text(header + lines)
    return found


def centre_on_head(image, zoom=1.0, down=0.0):
    """Crop a cut-out so the head is centred with hair room, zoomed and moved down if asked"""
    top, centre, head_width = find_head(image.getchannel("A"))
    side = head_width / HEAD_SHARE / zoom
    left, upper = centre - side / 2, top - (TOP_SHARE + down) * side
    # Parts of the box outside the picture come out transparent
    box = tuple(round(v) for v in (left, upper, left + side, upper + side))
    return image.crop(box).resize((PHOTO_SIZE, PHOTO_SIZE), Image.LANCZOS)


def to_avatar(image, zoom=1.0, down=0.0):
    """Turn a downloaded photo into a square avatar picture"""
    image = image.convert("RGBA")
    if image.getchannel("A").getextrema()[0] < 255:
        return centre_on_head(image, zoom, down)
    # A photo with no transparent background cannot be measured, so keep its top square
    return make_square(image).resize((PHOTO_SIZE, PHOTO_SIZE), Image.LANCZOS)


def fetch_photo(http, url):
    """Download one photo and return it as an image, or raise ValueError"""
    response = http.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S)
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}")
    if not response.headers.get("Content-Type", "").startswith("image/"):
        raise ValueError("the answer is not an image")
    if len(response.content) > MAX_BYTES:
        raise ValueError("the file is too large")
    try:
        image = Image.open(io.BytesIO(response.content))
        image.load()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("the file is not a readable image") from error
    return image.convert("RGBA")


def download_all(conn, season, folder, http=None, sleep=time.sleep, refresh=False):
    """Save every driver's photo, crop it to CODE.png, and report what happened to each driver"""
    http = http or requests
    folder = Path(folder)
    originals = folder / ORIGINALS
    originals.mkdir(parents=True, exist_ok=True)
    report = {"saved": [], "kept": [], "missing": [], "failed": [], "skipped": []}
    skip = read_skip_list(folder)
    links = read_links(folder)
    adjustments = read_adjustments(folder)
    first = True
    for driver in season_drivers(conn, season):
        driver = {**driver, "url": links.get(driver["code"], driver["url"])}
        path = photo_path(driver["code"], folder)
        original = photo_path(driver["code"], originals)
        if driver["code"] in skip:
            path.unlink(missing_ok=True)
            original.unlink(missing_ok=True)
            report["skipped"].append(driver["code"])
            continue
        if not driver["url"]:
            report["missing"].append(driver["code"])
            continue
        if original.exists() and not refresh:
            report["kept"].append(driver["code"])
        else:
            if not first:
                sleep(PACE_S)
            first = False
            try:
                fetch_photo(http, driver["url"]).save(original, format="PNG")
            except (ValueError, requests.RequestException) as error:
                report["failed"].append((driver["code"], str(error)))
                continue
            report["saved"].append(driver["code"])
        # The avatar is always cropped again, so a change in composition reaches every photo
        try:
            zoom, down = adjustments.get(driver["code"], (1.0, 0.0))
            to_avatar(Image.open(original), zoom, down).save(path, format="PNG")
        except ValueError as error:
            path.unlink(missing_ok=True)
            report["failed"].append((driver["code"], str(error)))
    return report


def initials(name):
    """Return the initials of a driver's first and last name"""
    parts = (name or "").split()
    return (parts[0][0] + parts[-1][0]).upper() if len(parts) > 1 else (name or "?")[:2].upper()


def hex_color(value, default="9A9AA8"):
    """Turn a hex colour such as 3671C6 into an RGB tuple"""
    value = (value or "").lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        value = default
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def avatar(photo, name, color):
    """Draw a round avatar with a team-colour ring, using initials when there is no photo"""
    size = AVATAR + 2 * RING
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    draw.ellipse((0, 0, size - 1, size - 1), fill=color)
    mask = Image.new("L", (AVATAR, AVATAR), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, AVATAR - 1, AVATAR - 1), fill=255)
    if photo is not None:
        face = photo.resize((AVATAR, AVATAR), Image.LANCZOS)
    else:
        face = Image.new("RGBA", (AVATAR, AVATAR), (31, 31, 43, 255))
        font = ImageFont.load_default(size=40)
        ImageDraw.Draw(face).text(
            (AVATAR / 2, AVATAR / 2), initials(name), fill=(242, 242, 245), font=font, anchor="mm"
        )
    tile.paste(face, (RING, RING), mask)
    return tile


def review_sheet(conn, season, folder):
    """Draw every driver as a round avatar with name, code and team, and save the sheet"""
    folder = Path(folder)
    drivers = season_drivers(conn, season)
    skip = read_skip_list(folder)
    columns = max(1, min(len(drivers), SHEET_COLUMNS))
    rows = math.ceil(len(drivers) / columns)
    sheet = Image.new("RGB", (columns * TILE_W, SHEET_TOP + rows * TILE_H), (21, 21, 30))
    draw = ImageDraw.Draw(sheet)
    title_font = ImageFont.load_default(size=20)
    small = ImageFont.load_default(size=13)
    title = f"{season} driver photos: check each face, name and team (no photo = initials)"
    draw.text((16, 20), title, fill=(242, 242, 245), font=title_font)
    for index, driver in enumerate(drivers):
        x = (index % columns) * TILE_W
        y = SHEET_TOP + (index // columns) * TILE_H
        path = photo_path(driver["code"], folder)
        photo = Image.open(path).convert("RGBA") if path.exists() else None
        tile = avatar(photo, driver["name"], hex_color(driver["color"]))
        sheet.paste(tile, (x + (TILE_W - tile.width) // 2, y), tile)
        status = ""
        if photo is None:
            status = "  (skipped)" if driver["code"] in skip else "  (initials)"
        lines = [(f"{driver['code']}  {driver['name']}", (242, 242, 245)),
                 (f"{driver['team']}{status}", (154, 154, 168))]
        for line_no, (text, fill) in enumerate(lines):
            draw.text((x + TILE_W / 2, y + tile.height + 14 + line_no * 18), text, fill=fill,
                      font=small, anchor="mm")
    out = folder / "review_sheet.png"
    sheet.save(out)
    return out


def latest_season(conn):
    """Return the most recent season that has entries"""
    row = conn.execute("SELECT MAX(season) FROM entries").fetchone()
    return row[0] if row else None


def main(argv=None):
    """Download the season's driver photos, make the review sheet and print what happened"""
    parser = argparse.ArgumentParser(
        prog="python -m f1rag.ingest.headshots",
        description="Download official driver photos for local use and make a review sheet",
    )
    parser.add_argument("--season", type=int, help="season, default the latest loaded")
    parser.add_argument("--refresh", action="store_true", help="download photos again")
    parser.add_argument(
        "--link", nargs=2, action="append", metavar=("CODE", "URL"),
        help="use this photo link for a driver whose race data has none or a wrong one",
    )
    parser.add_argument(
        "--adjust", nargs=3, action="append", metavar=("CODE", "ZOOM", "DOWN"),
        help="zoom in (for example 1.1) and move down (for example 0.03) one driver's avatar",
    )
    parser.add_argument(
        "--skip", nargs="+", metavar="CODE",
        help="reject these drivers' photos (for example a placeholder), so they show initials",
    )
    parser.add_argument("--db", default=str(config.DB_PATH), help="database file")
    parser.add_argument("--dir", default=str(config.HEADSHOT_DIR), help="photo folder")
    args = parser.parse_args(argv)
    if not Path(args.db).exists():
        print(f"No database at {args.db}. Load races first with python -m f1rag.ingest")
        return 1
    if args.skip:
        print("Skip list: " + ", ".join(add_to_skip_list(args.dir, args.skip)))
    for code, zoom, down in args.adjust or []:
        add_adjustment(args.dir, code, float(zoom), float(down))
        print(f"Adjustment set for {code.upper()}: zoom {zoom}, down {down}")
    for code, url in args.link or []:
        add_link(args.dir, code, url)
        print(f"Photo link set for {code.upper()}")
    with contextlib.closing(store.connect(args.db)) as conn:
        season = args.season or latest_season(conn)
        report = download_all(conn, season, args.dir, refresh=args.refresh)
        sheet = review_sheet(conn, season, args.dir)
    print(f"Saved {len(report['saved'])}, already there {len(report['kept'])}, "
          f"no photo link {len(report['missing'])}, skipped {len(report['skipped'])}, "
          f"failed {len(report['failed'])}")
    if report["missing"]:
        print("No photo link (shown as initials): " + ", ".join(report["missing"]))
    for code, reason in report["failed"]:
        print(f"Failed {code}: {reason}")
    print(f"Review sheet: {sheet}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
