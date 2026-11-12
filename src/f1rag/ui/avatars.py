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
