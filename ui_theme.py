"""ui_theme.py - color palettes for light/dark mode."""

LIGHT = {
    "bg": "#f5f5f7",
    "panel_bg": "#ffffff",
    "fg": "#1c1c1e",
    "sub_fg": "#6e6e73",
    "border": "#d9d9df",
    "select_bg": "#0a84ff",
    "select_fg": "#ffffff",
    "row_alt": "#f0f0f2",
    "good": "#1fa14b",
    "caution": "#c98a00",
    "bad": "#d92d20",
    "header_bg": "#eaeaee",
}

DARK = {
    "bg": "#1e1e22",
    "panel_bg": "#26262b",
    "fg": "#e8e8ea",
    "sub_fg": "#9a9aa2",
    "border": "#3a3a40",
    "select_bg": "#0a84ff",
    "select_fg": "#ffffff",
    "row_alt": "#2c2c31",
    "good": "#39d17c",
    "caution": "#ffb020",
    "bad": "#ff5c5c",
    "header_bg": "#2c2c31",
}


def status_color(theme, status):
    return {"Good": theme["good"], "Caution": theme["caution"], "Bad": theme["bad"]}.get(status, theme["fg"])
