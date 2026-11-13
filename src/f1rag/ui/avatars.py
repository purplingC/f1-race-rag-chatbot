"""Round driver avatars with a team-colour ring: local photo when enabled, otherwise initials"""

import base64
import html
import os
import re
from functools import lru_cache
from pathlib import Path

from f1rag import config
from f1rag.ui import theme

CODE = re.compile(r"^[A-Z]{3}$")
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
SEASON = re.compile(r"^\d{4}$")


def team_logos_enabled():
    """Tell whether SHOW_TEAM_LOGOS=true is set, so official team logos may be shown"""
    return os.getenv("SHOW_TEAM_LOGOS", "false").strip().lower() == "true"


def team_logo_html(team, season, folder=None, show=None, height=14):
    """Return a small team logo for the start of a team name, or nothing when off or missing"""
    show = team_logos_enabled() if show is None else show
    if not show or not team or not SEASON.match(str(season)):
        return ""
    # Same file names as python -m f1rag.ingest.team_logos: season folder, name without spaces
    name = re.sub(r"[^a-z0-9]", "", team.lower())
    path = Path(folder or config.TEAM_LOGO_DIR) / str(season) / f"{name}.png"
    if not path.exists():
        return ""
    data = photo_data(str(path), path.stat().st_mtime)
    return (f'<img src="data:image/png;base64,{data}" alt="{html.escape(team)} logo" '
            f'style="height:{height}px;width:auto;vertical-align:-2px;margin-right:5px">')


def headshots_enabled():
    """Tell whether SHOW_HEADSHOTS=true is set, so official photos may be shown"""
    return os.getenv("SHOW_HEADSHOTS", "false").strip().lower() == "true"


def initials(name):
    """Return the initials of a driver's first and last name"""
    parts = (name or "").split()
    return (parts[0][0] + parts[-1][0]).upper() if len(parts) > 1 else (name or "?")[:2].upper()


def safe_color(value):
    """Return the colour if it is #RRGGBB, otherwise the neutral grey"""
    return value if isinstance(value, str) and HEX.match(value) else theme.MUTED


@lru_cache(maxsize=64)
def photo_data(path, modified):
    """Read a photo once and return it as base64 text for an inline image"""
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def avatar_html(code, name, color, folder=None, show=None, size=40):
    """Return HTML for one round avatar ringed in the team colour, with the name as alt text"""
    show = headshots_enabled() if show is None else show
    folder = Path(folder or config.HEADSHOT_DIR)
    ring = safe_color(color)
    label = html.escape(name or code or "")
    style = (
        f"display:inline-flex;align-items:center;justify-content:center;width:{size}px;"
        f"height:{size}px;border-radius:50%;border:2px solid {ring};overflow:hidden;"
        f"background:{theme.PANEL};flex-shrink:0"
    )
    path = folder / f"{code}.png" if isinstance(code, str) and CODE.match(code) else None
    if show and path is not None and path.exists():
        data = photo_data(str(path), path.stat().st_mtime)
        return (f'<span class="f1-avatar" style="{style}"><img src="data:image/png;base64,{data}" '
                f'alt="{label}" style="width:100%;height:100%;object-fit:cover"></span>')
    letters = html.escape(initials(name))
    return (f'<span class="f1-avatar" role="img" aria-label="{label}" style="{style};'
            f'color:{theme.TEXT};font-size:{size * 0.38:.0f}px;font-weight:600">{letters}</span>')
