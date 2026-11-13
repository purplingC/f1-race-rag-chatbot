"""Colours and chart styling for the dark F1-inspired interface (Section 7.2)"""

BACKGROUND = "#15151E"
PANEL = "#1F1F2B"
BORDER = "#34344A"
TEXT = "#F2F2F5"
MUTED = "#9A9AA8"
ACCENT = "#E10600"

# Familiar compound colours, with a letter so colour is never the only cue (Section 7.8)
TYRE_COLORS = {
    "SOFT": "#E10600", "MEDIUM": "#F5D000", "HARD": "#F2F2F5",
    "INTERMEDIATE": "#2BB24C", "WET": "#1E6BD6", "UNKNOWN": "#6C6C7A",
}
TYRE_LETTERS = {
    "SOFT": "S", "MEDIUM": "M", "HARD": "H", "INTERMEDIATE": "I", "WET": "W", "UNKNOWN": "?",
}
# Text on light tyre colours is dark, on dark ones light
TYRE_TEXT = {"MEDIUM": BACKGROUND, "HARD": BACKGROUND}

# Track strip above lap charts: amber safety car, outlined amber VSC, red flag in red.
# Amber, not tyre yellow, so a safety car is never read as a medium tyre
TRACK_AMBER = "#FFA200"
TRACK_STYLE = {
    "SC": {"fill": TRACK_AMBER, "line": TRACK_AMBER, "name": "Safety car", "key": "Safety car",
           "mark": "■"},
    "VSC": {"fill": "rgba(255,162,0,0.22)", "line": TRACK_AMBER, "name": "Virtual safety car",
            "key": "VSC", "mark": "□"},
    "RED": {"fill": ACCENT, "line": ACCENT, "name": "Red flag", "key": "Red flag", "mark": "■"},
}
# Height of the track strip in pixels, and how faint the band behind line charts is
TRACK_STRIP_PX = 18
TRACK_BAND_OPACITY = 0.07
# A tick shown before answers whose every statement cites a source
SOURCED = "#2BB24C"

# Teammates share a colour, so the second and third car get other line styles and markers
LINE_STYLES = ["solid", "dash", "dot"]
MARKERS = ["circle", "square", "diamond"]


def style_figure(fig, title=None, height=320):
    """Give a Plotly figure the dark look: transparent background, muted grid, light text"""
    # A track strip carries a small key above it, which needs room at the top
    has_strip = any(getattr(trace, "yaxis", None) == "y2" for trace in fig.data)
    fig.update_layout(
        title={"text": title, "font": {"size": 13, "color": MUTED}} if title else None,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"color": TEXT, "size": 12}, height=height,
        margin={"l": 48, "r": 16, "t": 36 if title else (30 if has_strip else 12), "b": 36},
        legend={"orientation": "h", "y": -0.18, "font": {"size": 11}},
        hoverlabel={"bgcolor": PANEL, "font": {"color": TEXT}},
    )
    fig.update_xaxes(gridcolor=BORDER, zerolinecolor=BORDER, color=MUTED)
    fig.update_yaxes(gridcolor=BORDER, zerolinecolor=BORDER, color=MUTED)
    return fig


def empty_figure(message="No data for this race"):
    """Return a blank figure that says why it is empty"""
    import plotly.graph_objects as go

    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, font={"color": MUTED, "size": 13},
                       x=0.5, y=0.5, xref="paper", yref="paper")
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return style_figure(fig, height=160)


def page_css():
    """Return the page stylesheet: carbon background, race banner, panels and chat"""
    panel = f"background:rgba(31,31,43,.72);border:1px solid {BORDER};border-radius:14px"
    return f"""<style>
.stApp {{
  background:
    radial-gradient(1100px 520px at -8% -12%, rgba(225,6,0,.26), transparent 62%),
    radial-gradient(900px 480px at 108% 4%, rgba(225,6,0,.12), transparent 60%),
    repeating-linear-gradient(135deg, rgba(255,255,255,.022) 0 2px, transparent 2px 7px),
    {BACKGROUND};
  background-attachment: fixed;
}}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 1.4rem; padding-bottom: 1rem; max-width: 1560px; }}
.st-key-hero {{
  position: relative; overflow: hidden; border-radius: 16px; padding: 18px 20px 16px;
  border: 1px solid rgba(255,255,255,.12);
  background: linear-gradient(112deg, {ACCENT} 0%, #A80500 20%, #4A1018 42%, {PANEL} 68%);
}}
.st-key-hero::after {{
  content: ""; position: absolute; right: -60px; top: 0; bottom: 0; width: 380px;
  background: repeating-conic-gradient(rgba(255,255,255,.075) 0 25%, transparent 0 50%)
    0 0 / 30px 30px;
  -webkit-mask-image: linear-gradient(90deg, transparent, #000);
  mask-image: linear-gradient(90deg, transparent, #000);
  transform: skewX(-14deg); pointer-events: none;
}}
.st-key-hero > * {{ position: relative; z-index: 1; }}
.st-key-hero label p {{ color: rgba(255,255,255,.85); font-size: 11px; text-transform: uppercase;
  letter-spacing: .08em; }}
[class*="st-key-w-"] {{ {panel}; padding: 14px 16px; }}
.st-key-chat {{ {panel}; padding: 14px 16px 10px; border-top: 3px solid {ACCENT}; }}
[data-testid="stColumn"]:has(> div .st-key-chat) {{
  position: sticky; top: 1rem; align-self: flex-start;
}}
.st-key-chatlog {{ height: calc(100vh - 330px) !important; min-height: 300px; }}
[class*="st-key-suggest-"] button, [class*="st-key-follow-"] button {{
  justify-content: flex-start; text-align: left; border-radius: 12px; min-height: 54px;
  border: 1px solid {BORDER}; background: rgba(21,21,30,.55);
}}
[class*="st-key-suggest-"] button > div, [class*="st-key-follow-"] button > div {{
  justify-content: flex-start; }}
[class*="st-key-suggest-"] button p, [class*="st-key-follow-"] button p {{ text-align: left; }}
[class*="st-key-suggest-"] button:hover, [class*="st-key-follow-"] button:hover {{
  border-color: {ACCENT}; color: {TEXT}; }}
[class*="st-key-follow-"] button {{ min-height: 38px; font-size: 13px; }}
</style>"""
