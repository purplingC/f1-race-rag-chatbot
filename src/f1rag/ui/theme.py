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

# Teammates share a colour, so the second and third car get other line styles and markers
LINE_STYLES = ["solid", "dash", "dot"]
MARKERS = ["circle", "square", "diamond"]


def style_figure(fig, title=None, height=320):
    """Give a Plotly figure the dark look: transparent background, muted grid, light text"""
    fig.update_layout(
        title={"text": title, "font": {"size": 13, "color": MUTED}} if title else None,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"color": TEXT, "size": 12}, height=height,
        margin={"l": 48, "r": 16, "t": 36 if title else 12, "b": 36},
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
