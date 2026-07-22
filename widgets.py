"""
widgets.py
Small reusable Tkinter Canvas widgets used across the app:
  - HealthBar: a rounded, color-graded progress bar (green->yellow->red)
  - LineGraph: a minimal line chart for temperature / health history
Both are theme-aware (pass a theme dict from ui_theme.py).
"""

import tkinter as tk


def _lerp(a, b, t):
    return a + (b - a) * t


def _lerp_color(c1, c2, t):
    c1 = c1.lstrip("#")
    c2 = c2.lstrip("#")
    r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
    r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
    r = int(_lerp(r1, r2, t))
    g = int(_lerp(g1, g2, t))
    b = int(_lerp(b1, b2, t))
    return f"#{r:02x}{g:02x}{b:02x}"


def score_to_color(score):
    """0-100 -> smooth red->yellow->green gradient."""
    score = max(0, min(100, score))
    red, yellow, green = "#d92d20", "#ffb020", "#1fa14b"
    if score < 50:
        return _lerp_color(red, yellow, score / 50)
    else:
        return _lerp_color(yellow, green, (score - 50) / 50)


class HealthBar(tk.Canvas):
    """A rounded horizontal progress bar showing a 0-100 health score."""

    def __init__(self, parent, theme, height=26, **kwargs):
        super().__init__(parent, height=height, highlightthickness=0, **kwargs)
        self.theme = theme
        self.score = 0
        self.bar_height = height
        self.configure(bg=theme["panel_bg"])
        self.bind("<Configure>", lambda e: self.redraw())

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme["panel_bg"])
        self.redraw()

    def set_score(self, score, label=None):
        self.score = score
        self._label = label if label is not None else f"{score}%"
        self.redraw()

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
                  x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(points, smooth=True, **kw)

    def redraw(self):
        self.delete("all")
        w = max(self.winfo_width(), 40)
        h = self.bar_height
        pad = 2
        r = h / 2
        # track
        self._round_rect(pad, pad, w - pad, h - pad, r - pad, fill=self.theme["row_alt"],
                          outline=self.theme["border"])
        # fill
        fill_w = max(h, (w - 2 * pad) * (self.score / 100))
        if self.score > 0:
            color = score_to_color(self.score)
            self._round_rect(pad, pad, pad + fill_w, h - pad, r - pad, fill=color, outline="")
        # label
        self.create_text(w / 2, h / 2, text=getattr(self, "_label", f"{self.score}%"),
                          fill=self.theme["fg"], font=("Segoe UI", 9, "bold"))


class ProgressBar(HealthBar):
    """A HealthBar variant that uses a single accent color (not the
    red->yellow->green health gradient) - for tracking task progress
    like a running self-test."""

    def redraw(self):
        self.delete("all")
        w = max(self.winfo_width(), 40)
        h = self.bar_height
        pad = 2
        r = h / 2
        self._round_rect(pad, pad, w - pad, h - pad, r - pad, fill=self.theme["row_alt"],
                          outline=self.theme["border"])
        fill_w = max(0, (w - 2 * pad) * (self.score / 100))
        if self.score > 0:
            self._round_rect(pad, pad, pad + max(h, fill_w), h - pad, r - pad,
                              fill=self.theme["select_bg"], outline="")
        self.create_text(w / 2, h / 2, text=getattr(self, "_label", f"{self.score}%"),
                          fill=self.theme["fg"], font=("Segoe UI", 9, "bold"))


class LineGraph(tk.Canvas):
    """A minimal line chart for plotting a numeric series over time."""

    def __init__(self, parent, theme, y_suffix="", y_min=None, y_max=None,
                 line_color=None, height=180, **kwargs):
        super().__init__(parent, height=height, highlightthickness=0, **kwargs)
        self.theme = theme
        self.y_suffix = y_suffix
        self.y_min_fixed = y_min
        self.y_max_fixed = y_max
        self.line_color = line_color
        self.data = []  # list of (label, value)
        self.configure(bg=theme["panel_bg"])
        self.bind("<Configure>", lambda e: self.redraw())

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme["panel_bg"])
        self.redraw()

    def set_data(self, points):
        """points: list of (label:str, value:float-or-None)"""
        self.data = points
        self.redraw()

    def redraw(self):
        self.delete("all")
        w = max(self.winfo_width(), 100)
        h = max(self.winfo_height(), 80)
        pad_l, pad_r, pad_t, pad_b = 44, 14, 14, 24

        vals = [v for _, v in self.data if v is not None]
        if not vals:
            self.create_text(w / 2, h / 2, text="No data yet", fill=self.theme["sub_fg"],
                              font=("Segoe UI", 9))
            return

        y_min = self.y_min_fixed if self.y_min_fixed is not None else min(vals)
        y_max = self.y_max_fixed if self.y_max_fixed is not None else max(vals)
        if y_max == y_min:
            y_max += 1

        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b

        # gridlines + y-axis labels
        steps = 4
        for i in range(steps + 1):
            frac = i / steps
            y = pad_t + plot_h * (1 - frac)
            val = y_min + (y_max - y_min) * frac
            self.create_line(pad_l, y, w - pad_r, y, fill=self.theme["border"], dash=(2, 3))
            self.create_text(pad_l - 6, y, text=f"{val:.0f}{self.y_suffix}",
                              fill=self.theme["sub_fg"], font=("Segoe UI", 8), anchor="e")

        n = len(self.data)
        color = self.line_color or self.theme["select_bg"]

        def xy(i, v):
            x = pad_l + (plot_w * (i / max(1, n - 1)) if n > 1 else plot_w / 2)
            y = pad_t + plot_h * (1 - (v - y_min) / (y_max - y_min))
            return x, y

        points = []
        for i, (_, v) in enumerate(self.data):
            if v is None:
                continue
            points.append(xy(i, v))

        if len(points) >= 2:
            flat = [c for pt in points for c in pt]
            self.create_line(*flat, fill=color, width=2, smooth=True)
        for x, y in points[-1:]:
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")

        # x-axis: first / last label only, to avoid clutter
        if self.data:
            self.create_text(pad_l, h - 8, text=self.data[0][0], fill=self.theme["sub_fg"],
                              font=("Segoe UI", 8), anchor="w")
            self.create_text(w - pad_r, h - 8, text=self.data[-1][0], fill=self.theme["sub_fg"],
                              font=("Segoe UI", 8), anchor="e")
