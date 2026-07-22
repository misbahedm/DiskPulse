"""
DiskPulse - Hard Drive Health Monitor
--------------------------------------
A lightweight Windows desktop utility that watches the S.M.A.R.T. health
of your internal drives (HDD/SSD/NVMe) and warns you before they fail.

Run:  python main.py
Build a standalone .exe: see build.bat (uses PyInstaller)

Requires smartmontools (smartctl) to be installed on the system.
"""

import csv
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox, filedialog

import smart_backend
import health_score
from ui_theme import LIGHT, DARK, status_color
from widgets import HealthBar, ProgressBar, LineGraph, score_to_color

APP_NAME = "DiskPulse"
APP_VERSION = "1.1.0"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "dark_mode": True,
    "refresh_seconds": 60,
    "smartctl_path": "",
    "minimize_to_tray": True,
    "notify_on_status_change": True,
    "left_panel_width": 340,
}


def load_config():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(json.load(f))
                return cfg
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def human_size(num_bytes):
    if not num_bytes:
        return "Unknown"
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if num_bytes < step:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= step
    return f"{num_bytes:.1f} EB"


def is_admin():
    """Best-effort check for Administrator privileges (Windows only)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return True  # don't nag if we can't tell


def drive_kind_label(data):
    """Return a short type tag: NVMe / SSD / HDD."""
    if data.get("nvme"):
        return "NVMe"
    rpm = data.get("rotation_rate")
    if rpm == 0:
        return "SSD"
    if isinstance(rpm, (int, float)) and rpm > 0:
        return "HDD"
    return "Disk"


class PollWorker(threading.Thread):
    """Background thread that periodically polls all drives via smartctl."""

    def __init__(self, out_queue, get_interval, get_smartctl_path):
        super().__init__(daemon=True)
        self.out_queue = out_queue
        self.get_interval = get_interval
        self.get_smartctl_path = get_smartctl_path
        self._stop_evt = threading.Event()
        self._force_evt = threading.Event()
        self.next_run_at = time.time()

    def stop(self):
        self._stop_evt.set()
        self._force_evt.set()

    def poll_now(self):
        self._force_evt.set()

    def run(self):
        while not self._stop_evt.is_set():
            self._poll_once()
            self._force_evt.clear()
            interval = max(5, self.get_interval())
            self.next_run_at = time.time() + interval
            waited = 0
            while waited < interval and not self._stop_evt.is_set() and not self._force_evt.is_set():
                time.sleep(0.5)
                waited += 0.5

    def _poll_once(self):
        smartctl_path = self.get_smartctl_path() or None
        try:
            drives = smart_backend.scan_drives(smartctl_path)
        except smart_backend.SmartctlNotFound as e:
            self.out_queue.put(("smartctl_missing", str(e)))
            return

        results = []
        for d in drives:
            try:
                data = smart_backend.get_drive_data(d["device"], d.get("type", "auto"), smartctl_path)
            except smart_backend.SmartctlNotFound as e:
                self.out_queue.put(("smartctl_missing", str(e)))
                return
            health = health_score.compute_health(data)
            results.append({"scan": d, "data": data, "health": health,
                             "timestamp": datetime.now().isoformat(timespec="seconds")})

        self.out_queue.put(("results", results))


class DiskPulseApp:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.theme = DARK if self.cfg["dark_mode"] else LIGHT
        self.out_queue = queue.Queue()
        self.drives = {}          # device -> latest result dict
        self.history = {}         # device -> list of (timestamp, temp, score)
        self.selected_device = None
        self.tray_icon = None
        self._last_status = {}    # device -> last known status, for change notifications
        self._sort_state = {"col": None, "reverse": False}
        self._search_text = ""
        self._active_test = None  # {"device", "type", "start", "expected_minutes"}

        self._build_ui()
        self._apply_theme()

        self.worker = PollWorker(self.out_queue, lambda: self.cfg["refresh_seconds"],
                                  lambda: self.cfg["smartctl_path"])
        self.worker.start()

        self.root.after(300, self._poll_queue)
        self.root.after(1000, self._tick_countdown)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        self.root.title(f"{APP_NAME} - Drive Health Monitor")
        self.root.geometry("1180x680")
        self.root.minsize(860, 520)

        self._build_menu()

        if not is_admin():
            banner = tk.Frame(self.root, bg="#ffb020")
            banner.pack(fill="x")
            tk.Label(banner, text="\u26a0 Not running as Administrator - drive health data and "
                                   "self-tests will likely fail to read. Right-click DiskPulse "
                                   "and choose 'Run as administrator'.",
                     bg="#ffb020", fg="#1c1c1e", font=("Segoe UI", 9, "bold")).pack(
                pady=4, padx=8, anchor="w")

        self.paned = ttk.PanedWindow(self.root, orient="horizontal")
        self.paned.pack(fill="both", expand=True)

        # ---------------- Left: drive list (resizable) ----------------
        left = ttk.Frame(self.paned)
        self.paned.add(left, weight=1)
        self._left_frame = left

        header_row = ttk.Frame(left)
        header_row.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(header_row, text="Drives", font=("Segoe UI", 12, "bold")).pack(side="left")
        self.summary_var = tk.StringVar(value="")
        ttk.Label(header_row, textvariable=self.summary_var, font=("Segoe UI", 9)).pack(side="right")

        self.system_bar = HealthBar(left, self.theme, height=20)
        self.system_bar.pack(fill="x", padx=10, pady=(0, 8))
        self.system_bar.set_score(0, "No data yet")

        search_row = ttk.Frame(left)
        search_row.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(search_row, text="\U0001F50D").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._on_search_changed())
        ttk.Entry(search_row, textvariable=self.search_var).pack(side="left", fill="x", expand=True, padx=6)

        cols = ("type", "status", "score")
        self.drive_tree = ttk.Treeview(left, columns=cols, show="tree headings", height=20)
        self.drive_tree.heading("#0", text="Drive", command=lambda: self._sort_by("#0"))
        self.drive_tree.heading("type", text="Type", command=lambda: self._sort_by("type"))
        self.drive_tree.heading("status", text="Status", command=lambda: self._sort_by("status"))
        self.drive_tree.heading("score", text="Health", command=lambda: self._sort_by("score"))
        self.drive_tree.column("#0", width=170)
        self.drive_tree.column("type", width=55, anchor="center")
        self.drive_tree.column("status", width=70, anchor="center")
        self.drive_tree.column("score", width=55, anchor="center")
        self.drive_tree.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.drive_tree.bind("<<TreeviewSelect>>", self._on_select_drive)

        bottom_row = ttk.Frame(left)
        bottom_row.pack(fill="x", padx=10, pady=(0, 10))
        self.status_var = tk.StringVar(value="Starting up...")
        ttk.Label(bottom_row, textvariable=self.status_var, wraplength=200,
                  foreground="#888").pack(side="left")
        self.countdown_var = tk.StringVar(value="")
        ttk.Label(bottom_row, textvariable=self.countdown_var, foreground="#888").pack(side="right")

        # ---------------- Right: details notebook ----------------
        right = ttk.Frame(self.paned)
        self.paned.add(right, weight=3)

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True, padx=(0, 10), pady=10)

        self.overview_tab = ttk.Frame(self.notebook)
        self.perf_tab = ttk.Frame(self.notebook)
        self.attrs_tab = ttk.Frame(self.notebook)
        self.selftest_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.overview_tab, text="Overview")
        self.notebook.add(self.perf_tab, text="Performance")
        self.notebook.add(self.attrs_tab, text="S.M.A.R.T. Attributes")
        self.notebook.add(self.selftest_tab, text="Self-Test")

        self._build_overview_tab()
        self._build_perf_tab()
        self._build_attrs_tab()
        self._build_selftest_tab()

        self.root.after(100, lambda: self.paned.sashpos(0, self.cfg.get("left_panel_width", 340)))
        left.bind("<Configure>", self._on_left_resize)

    def _on_left_resize(self, _evt):
        try:
            pos = self.paned.sashpos(0)
            if pos and abs(pos - self.cfg.get("left_panel_width", 340)) > 4:
                self.cfg["left_panel_width"] = pos
        except tk.TclError:
            pass

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Refresh Now", command=self._refresh_now)
        file_menu.add_command(label="Export Report (.txt)...", command=self._export_report_txt)
        file_menu.add_command(label="Export Report (.csv)...", command=self._export_report_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._quit_app)
        menubar.add_cascade(label="File", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        self.dark_mode_var = tk.BooleanVar(value=self.cfg["dark_mode"])
        view_menu.add_checkbutton(label="Dark Mode", variable=self.dark_mode_var,
                                   command=self._toggle_dark_mode)
        menubar.add_cascade(label="View", menu=view_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Preferences...", command=self._open_settings)
        menubar.add_cascade(label="Settings", menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _build_overview_tab(self):
        t = self.overview_tab
        top = ttk.Frame(t)
        top.pack(fill="x", padx=16, pady=(16, 4))
        self.ov_title = ttk.Label(top, text="Select a drive", font=("Segoe UI", 15, "bold"))
        self.ov_title.pack(anchor="w")
        self.ov_status = ttk.Label(top, text="", font=("Segoe UI", 11, "bold"))
        self.ov_status.pack(anchor="w", pady=(2, 8))

        self.ov_bar = HealthBar(t, self.theme, height=26)
        self.ov_bar.pack(fill="x", padx=16, pady=(0, 14))

        grid = ttk.Frame(t)
        grid.pack(fill="x", padx=16)
        self.ov_fields = {}
        labels = ["Model", "Serial Number", "Firmware", "Capacity", "Interface", "Drive Type",
                  "Temperature", "Power-On Hours", "Power Cycles", "SMART Self-Test"]
        for i, label in enumerate(labels):
            row, col = divmod(i, 2)
            ttk.Label(grid, text=label + ":", font=("Segoe UI", 10, "bold")).grid(
                row=row, column=col * 2, sticky="w", pady=3, padx=(0, 8))
            val = ttk.Label(grid, text="-")
            val.grid(row=row, column=col * 2 + 1, sticky="w", pady=3, padx=(0, 24))
            self.ov_fields[label] = val

        ttk.Label(t, text="Reasons / Warnings:", font=("Segoe UI", 10, "bold")).pack(
            anchor="w", padx=16, pady=(16, 4))
        self.ov_reasons = tk.Text(t, height=7, wrap="word", borderwidth=0)
        self.ov_reasons.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.ov_reasons.configure(state="disabled")

    def _build_perf_tab(self):
        t = self.perf_tab
        ttk.Label(t, text="Health Score History", font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=16, pady=(16, 2))
        self.score_graph = LineGraph(t, self.theme, y_suffix="%", y_min=0, y_max=100,
                                      line_color=None, height=180)
        self.score_graph.pack(fill="x", padx=16, pady=(0, 16))

        ttk.Label(t, text="Temperature History", font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=16, pady=(0, 2))
        self.temp_graph = LineGraph(t, self.theme, y_suffix="\u00b0C", line_color="#ff8a3d", height=180)
        self.temp_graph.pack(fill="x", padx=16, pady=(0, 16))

        note = ttk.Label(t, text="Charts show data collected since the app was started, "
                                  "sampled once per refresh cycle.", foreground="#888")
        note.pack(anchor="w", padx=16)

    def _build_attrs_tab(self):
        t = self.attrs_tab
        cols = ("id", "name", "value", "worst", "thresh", "raw", "flag")
        self.attrs_tree = ttk.Treeview(t, columns=cols, show="headings", height=20)
        headers = {"id": "ID", "name": "Attribute", "value": "Value", "worst": "Worst",
                   "thresh": "Threshold", "raw": "Raw Value", "flag": ""}
        widths = {"id": 40, "name": 220, "value": 60, "worst": 60, "thresh": 70, "raw": 140, "flag": 90}
        for c in cols:
            self.attrs_tree.heading(c, text=headers[c])
            self.attrs_tree.column(c, width=widths[c], anchor="center" if c != "name" else "w")
        self.attrs_tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.attrs_tree.tag_configure("critical", foreground="#d92d20")

    def _build_selftest_tab(self):
        t = self.selftest_tab
        info = ttk.Label(t, text="Run the drive's own built-in S.M.A.R.T. self-test. "
                                  "Short tests take ~2 minutes; long/extended tests can take hours "
                                  "and run in the background on the drive itself. Requires DiskPulse "
                                  "to be running as Administrator.",
                          wraplength=560, justify="left")
        info.pack(anchor="w", padx=16, pady=(16, 10))

        btn_row = ttk.Frame(t)
        btn_row.pack(anchor="w", padx=16, pady=(0, 10))
        ttk.Button(btn_row, text="Run Short Test", command=lambda: self._run_self_test("short")).pack(
            side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Run Long Test", command=lambda: self._run_self_test("long")).pack(
            side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Abort Test", command=self._abort_self_test).pack(side="left")

        self.selftest_status_var = tk.StringVar(value="Select a drive to see self-test status.")
        ttk.Label(t, textvariable=self.selftest_status_var, font=("Segoe UI", 10, "bold")).pack(
            anchor="w", padx=16, pady=(6, 6))

        self.selftest_progress = ProgressBar(t, self.theme, height=22)
        self.selftest_progress.pack(fill="x", padx=16, pady=(0, 4))
        self.selftest_progress.set_score(0, "No test currently running")

        self.selftest_eta_var = tk.StringVar(value="")
        ttk.Label(t, textvariable=self.selftest_eta_var, foreground="#888").pack(
            anchor="w", padx=16, pady=(0, 10))

        ttk.Label(t, text="Self-Test Log:", font=("Segoe UI", 10, "bold")).pack(
            anchor="w", padx=16, pady=(0, 4))
        self.selftest_tree = ttk.Treeview(t, columns=("type", "status", "hours"),
                                           show="headings", height=10)
        for c, label, w in (("type", "Test Type", 160), ("status", "Result", 260), ("hours", "Lifetime Hours", 120)):
            self.selftest_tree.heading(c, text=label)
            self.selftest_tree.column(c, width=w, anchor="w")
        self.selftest_tree.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    # ------------------------------------------------------------ theming
    def _apply_theme(self):
        th = self.theme
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.root.configure(bg=th["bg"])
        style.configure("TFrame", background=th["bg"])
        style.configure("TLabel", background=th["bg"], foreground=th["fg"])
        style.configure("TEntry", fieldbackground=th["panel_bg"], foreground=th["fg"])
        style.configure("Treeview", background=th["panel_bg"], fieldbackground=th["panel_bg"],
                         foreground=th["fg"], rowheight=24, borderwidth=0)
        style.configure("Treeview.Heading", background=th["header_bg"], foreground=th["fg"])
        style.map("Treeview", background=[("selected", th["select_bg"])],
                  foreground=[("selected", th["select_fg"])])
        style.configure("TNotebook", background=th["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=th["header_bg"], foreground=th["fg"], padding=(12, 6))
        style.map("TNotebook.Tab", background=[("selected", th["panel_bg"])])
        style.configure("TButton", background=th["header_bg"], foreground=th["fg"])

        for w in (self.system_bar, self.ov_bar, self.selftest_progress):
            w.set_theme(th)
        for g in (getattr(self, "score_graph", None), getattr(self, "temp_graph", None)):
            if g:
                g.set_theme(th)

        self.ov_reasons.configure(bg=th["panel_bg"], fg=th["fg"], insertbackground=th["fg"])

    def _toggle_dark_mode(self):
        self.cfg["dark_mode"] = self.dark_mode_var.get()
        self.theme = DARK if self.cfg["dark_mode"] else LIGHT
        self._apply_theme()
        self._render_drive_list()
        if self.selected_device:
            self._render_details(self.selected_device)
        save_config(self.cfg)

    # ------------------------------------------------------------- queue
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.out_queue.get_nowait()
                if kind == "results":
                    self._handle_results(payload)
                elif kind == "smartctl_missing":
                    self.status_var.set("smartctl not found - see Settings")
                    self._maybe_prompt_smartctl(payload)
        except queue.Empty:
            pass
        self.root.after(500, self._poll_queue)

    def _tick_countdown(self):
        remaining = int(max(0, self.worker.next_run_at - time.time()))
        self.countdown_var.set(f"Next refresh in {remaining}s")
        self.root.after(1000, self._tick_countdown)

    def _handle_results(self, results):
        now_str = datetime.now().strftime("%H:%M:%S")
        for r in results:
            device = r["data"]["device"]
            self.drives[device] = r
            hist = self.history.setdefault(device, [])
            hist.append((r["timestamp"], r["data"].get("temperature_c"), r["health"]["score"]))
            if len(hist) > 500:
                del hist[0]

            new_status = r["health"]["status"]
            old_status = self._last_status.get(device)
            if old_status and old_status != new_status and self.cfg["notify_on_status_change"]:
                self._notify(f"{r['data'].get('model', device)} status changed",
                             f"{old_status} -> {new_status}")
            self._last_status[device] = new_status

        self.status_var.set(f"Last updated {now_str}")
        self._render_drive_list()
        self._render_summary()
        if self.selected_device and self.selected_device in self.drives:
            self._render_details(self.selected_device)
        elif self.drives and not self.selected_device:
            first = next(iter(self.drives))
            self.selected_device = first
            self._render_details(first)
        self._update_tray_icon()

    def _maybe_prompt_smartctl(self, message):
        if getattr(self, "_smartctl_warned", False):
            return
        self._smartctl_warned = True
        self.root.after(500, lambda: messagebox.showwarning(
            "smartctl not found",
            message + "\n\nYou can set a custom path in Settings > Preferences."))

    # --------------------------------------------------------- rendering
    def _render_summary(self):
        if not self.drives:
            self.summary_var.set("")
            self.system_bar.set_score(0, "No data yet")
            return
        scores = [r["health"]["score"] for r in self.drives.values()]
        worst = min(scores)
        avg = sum(scores) / len(scores)
        warnings = sum(1 for r in self.drives.values() if r["health"]["status"] != "Good")
        self.summary_var.set(f"{len(self.drives)} drive(s) - {warnings} need attention")
        self.system_bar.set_score(int(avg), f"System health: {int(avg)}% (worst {worst}%)")

    def _drive_matches_search(self, r):
        if not self._search_text:
            return True
        data = r["data"]
        haystack = " ".join(str(x) for x in [data.get("model"), data.get("serial"),
                                              data.get("device"), drive_kind_label(data)]).lower()
        return self._search_text.lower() in haystack

    def _on_search_changed(self):
        self._search_text = self.search_var.get()
        self._render_drive_list()

    def _sort_by(self, col):
        if self._sort_state["col"] == col:
            self._sort_state["reverse"] = not self._sort_state["reverse"]
        else:
            self._sort_state = {"col": col, "reverse": False}
        self._render_drive_list()

    def _sorted_items(self):
        items = [(device, r) for device, r in self.drives.items() if self._drive_matches_search(r)]
        col = self._sort_state["col"]
        if col:
            def key(item):
                device, r = item
                if col == "#0":
                    return (r["data"].get("model") or device).lower()
                if col == "type":
                    return drive_kind_label(r["data"])
                if col == "status":
                    return r["health"]["status"]
                if col == "score":
                    return r["health"]["score"]
                return device
            items.sort(key=key, reverse=self._sort_state["reverse"])
        return items

    def _render_drive_list(self):
        self.drive_tree.delete(*self.drive_tree.get_children())
        for device, r in self._sorted_items():
            data = r["data"]
            health = r["health"]
            label = data.get("model") or device
            iid = self.drive_tree.insert("", "end", iid=device, text=label,
                                          values=(drive_kind_label(data), health["status"],
                                                  f"{health['score']}%"))
            color = status_color(self.theme, health["status"])
            tag = f"status_{health['status']}"
            self.drive_tree.tag_configure(tag, foreground=color)
            self.drive_tree.item(iid, tags=(tag,))
        if self.selected_device and self.selected_device in self.drives:
            try:
                self.drive_tree.selection_set(self.selected_device)
            except tk.TclError:
                pass

    def _on_select_drive(self, _evt):
        sel = self.drive_tree.selection()
        if sel:
            self.selected_device = sel[0]
            self._render_details(self.selected_device)

    def _render_details(self, device):
        r = self.drives.get(device)
        if not r:
            return
        data, health = r["data"], r["health"]

        self.ov_title.configure(text=data.get("model", device))
        self.ov_status.configure(text=f"{health['status']} status",
                                  foreground=status_color(self.theme, health["status"]))
        self.ov_bar.set_score(health["score"], f"{health['score']}% healthy")

        fields = {
            "Model": data.get("model", "-"),
            "Serial Number": data.get("serial", "-"),
            "Firmware": data.get("firmware", "-"),
            "Capacity": human_size(data.get("capacity_bytes")),
            "Interface": data.get("interface", "-"),
            "Drive Type": drive_kind_label(data),
            "Temperature": f"{data.get('temperature_c')} \u00b0C" if data.get("temperature_c") else "-",
            "Power-On Hours": str(data.get("power_on_hours") or "-"),
            "Power Cycles": str(data.get("power_cycles") or "-"),
            "SMART Self-Test": ("PASSED" if data.get("health_passed") else "FAILED")
                                 if data.get("health_passed") is not None else "Unknown",
        }
        for label, val in fields.items():
            self.ov_fields[label].configure(text=val)

        self.ov_reasons.configure(state="normal")
        self.ov_reasons.delete("1.0", "end")
        for reason in health["reasons"]:
            self.ov_reasons.insert("end", f"\u2022 {reason}\n")
        self.ov_reasons.configure(state="disabled")

        self._render_attrs(data)
        self._render_perf_graphs(device)
        self._render_selftest(device, data)

    def _render_attrs(self, data):
        self.attrs_tree.delete(*self.attrs_tree.get_children())
        for attr in data.get("attributes", []):
            tags = ("critical",) if attr.get("critical") and (attr.get("raw") or 0) > 0 else ()
            self.attrs_tree.insert("", "end", values=(
                attr.get("id"), attr.get("name"), attr.get("value"),
                attr.get("worst"), attr.get("thresh"),
                attr.get("raw_str") or attr.get("raw"),
                "FAILED" if attr.get("when_failed") else "",
            ), tags=tags)

        if data.get("nvme"):
            self.attrs_tree.delete(*self.attrs_tree.get_children())
            nvme = data["nvme"]
            rows = [
                ("Critical Warning", nvme.get("critical_warning")),
                ("Temperature", nvme.get("temperature")),
                ("Available Spare", nvme.get("available_spare")),
                ("Available Spare Threshold", nvme.get("available_spare_threshold")),
                ("Percentage Used", data.get("percentage_used")),
                ("Data Units Read", nvme.get("data_units_read")),
                ("Data Units Written", nvme.get("data_units_written")),
                ("Media Errors", data.get("media_errors")),
                ("Power Cycles", nvme.get("power_cycles")),
                ("Power On Hours", nvme.get("power_on_hours")),
            ]
            for name, val in rows:
                self.attrs_tree.insert("", "end", values=("", name, val, "", "", "", ""))

    def _render_perf_graphs(self, device):
        hist = self.history.get(device, [])
        score_points = [(ts.split("T")[-1], score) for ts, _temp, score in hist]
        temp_points = [(ts.split("T")[-1], temp) for ts, temp, _score in hist]
        self.score_graph.set_data(score_points)
        self.temp_graph.set_data(temp_points)

    def _render_selftest(self, device, data):
        status = data.get("self_test_status") or "No self-test has been run, or status unavailable."
        self.selftest_status_var.set(f"Status: {status}")
        self.selftest_tree.delete(*self.selftest_tree.get_children())
        for entry in data.get("self_test_log", []):
            self.selftest_tree.insert("", "end", values=(entry.get("type"), entry.get("status"),
                                                           entry.get("hours")))

        active = self._active_test
        is_this_drive_active = active is not None and active["device"] == device
        in_progress = data.get("self_test_in_progress")

        if in_progress is False and is_this_drive_active:
            # The drive itself now reports the test finished (or it wasn't
            # actually started) - stop tracking and notify.
            self._active_test = None
            self.selftest_progress.set_score(100, f"Finished - {status}")
            self.selftest_eta_var.set("")
            if self.cfg.get("notify_on_status_change", True):
                self._notify(f"{data.get('model', device)} self-test finished", status)
            return

        if is_this_drive_active and in_progress is not False:
            remaining_pct = data.get("self_test_remaining_pct")
            if remaining_pct is not None:
                pct = max(1, min(99, 100 - remaining_pct))
                self.selftest_progress.set_score(pct, f"{pct}% complete")
            else:
                # Fall back to an elapsed-time estimate when the drive
                # doesn't report a live percentage.
                elapsed_min = (time.time() - active["start"]) / 60
                expected = max(1, active.get("expected_minutes") or 2)
                pct = max(1, min(95, int(elapsed_min / expected * 100)))
                self.selftest_progress.set_score(pct, f"~{pct}% complete (estimated)")
            remaining_min = max(0, (active.get("expected_minutes") or 0) -
                                (time.time() - active["start"]) / 60)
            if active.get("expected_minutes"):
                self.selftest_eta_var.set(f"Estimated time remaining: ~{remaining_min:.0f} min "
                                           f"(expected total: {active['expected_minutes']} min)")
            else:
                self.selftest_eta_var.set("Test running...")
        elif not is_this_drive_active:
            # No test tracked by this session for the selected drive.
            if in_progress:
                self.selftest_progress.set_score(50, "In progress (started outside DiskPulse)")
                self.selftest_eta_var.set("")
            else:
                self.selftest_progress.set_score(0, "No test currently running")
                self.selftest_eta_var.set("")

    # ---------------------------------------------------------- actions
    def _refresh_now(self):
        self.status_var.set("Refreshing...")
        self.worker.poll_now()

    def _export_report_txt(self):
        if not self.drives:
            messagebox.showinfo(APP_NAME, "No drive data to export yet.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                             filetypes=[("Text file", "*.txt")],
                                             initialfile=f"{APP_NAME}_report.txt")
        if not path:
            return
        lines = [f"{APP_NAME} v{APP_VERSION} - Drive Health Report",
                 f"Generated: {datetime.now().isoformat(timespec='seconds')}", ""]
        for device, r in self.drives.items():
            data, health = r["data"], r["health"]
            lines.append("=" * 60)
            lines.append(f"Device: {device}  ({drive_kind_label(data)})")
            lines.append(f"Model: {data.get('model')}   Serial: {data.get('serial')}")
            lines.append(f"Status: {health['status']} ({health['score']}%)")
            lines.append(f"Temperature: {data.get('temperature_c')} C")
            lines.append(f"Power-On Hours: {data.get('power_on_hours')}")
            lines.append("Reasons:")
            for reason in health["reasons"]:
                lines.append(f"  - {reason}")
            lines.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        messagebox.showinfo(APP_NAME, f"Report saved to:\n{path}")

    def _export_report_csv(self):
        if not self.drives:
            messagebox.showinfo(APP_NAME, "No drive data to export yet.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                             filetypes=[("CSV file", "*.csv")],
                                             initialfile=f"{APP_NAME}_report.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Device", "Type", "Model", "Serial", "Status", "Health %",
                        "Temperature C", "Power-On Hours", "Power Cycles", "Reasons"])
            for device, r in self.drives.items():
                data, health = r["data"], r["health"]
                w.writerow([device, drive_kind_label(data), data.get("model"), data.get("serial"),
                            health["status"], health["score"], data.get("temperature_c"),
                            data.get("power_on_hours"), data.get("power_cycles"),
                            " | ".join(health["reasons"])])
        messagebox.showinfo(APP_NAME, f"Report saved to:\n{path}")

    def _open_settings(self):
        SettingsDialog(self.root, self.cfg, self._on_settings_saved)

    def _on_settings_saved(self, new_cfg):
        self.cfg.update(new_cfg)
        save_config(self.cfg)
        self.worker.poll_now()

    def _show_about(self):
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "A lightweight S.M.A.R.T. drive health monitor.\n"
            "Reads data via smartmontools (smartctl).\n\n"
            f"\u00a9 {datetime.now().year} FPS Motion"
        )

    def _notify(self, title, message):
        try:
            from win10toast import ToastNotifier
            ToastNotifier().show_toast(title, message, duration=6, threaded=True)
            return
        except Exception:
            pass
        if self.tray_icon:
            try:
                self.tray_icon.notify(message, title)
                return
            except Exception:
                pass

    # ------------------------------------------------------- self-test
    def _run_self_test(self, test_type):
        if not self.selected_device:
            messagebox.showinfo(APP_NAME, "Select a drive first.")
            return
        device = self.selected_device
        dev_type = self.drives[device]["scan"].get("type", "auto")
        self.selftest_status_var.set(f"Starting {test_type} test...")

        def worker():
            try:
                result = smart_backend.start_self_test(device, dev_type, test_type,
                                                         self.cfg.get("smartctl_path") or None)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(APP_NAME, f"Could not start test:\n{e}"))
                return

            def on_done():
                if result["success"]:
                    caps = self.drives[device]["data"].get("self_test_capabilities", {}) or {}
                    expected = caps.get(test_type) or (2 if test_type == "short" else 90)
                    self._active_test = {"device": device, "type": test_type,
                                          "start": time.time(), "expected_minutes": expected}
                    self.selftest_status_var.set(f"{test_type.capitalize()} test started on the drive.")
                    self.selftest_progress.set_score(1, "Starting...")
                    self._selftest_watch_loop()
                else:
                    self.selftest_status_var.set(f"Could not start test: {result['message']}")
                    messagebox.showwarning(APP_NAME, result["message"])

            self.root.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _selftest_watch_loop(self):
        if not self._active_test:
            return
        self.worker.poll_now()
        # Poll faster early on (short tests finish quickly), then back off.
        elapsed_min = (time.time() - self._active_test["start"]) / 60
        next_delay = 8000 if elapsed_min < 3 else 20000
        self.root.after(next_delay, self._selftest_watch_loop)

    def _abort_self_test(self):
        if not self.selected_device:
            return
        device = self.selected_device
        dev_type = self.drives[device]["scan"].get("type", "auto")

        def worker():
            try:
                result = smart_backend.abort_self_test(device, dev_type,
                                                         self.cfg.get("smartctl_path") or None)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(APP_NAME, f"Could not abort test:\n{e}"))
                return

            def on_done():
                if result["success"]:
                    self.selftest_status_var.set("Test aborted.")
                    if self._active_test and self._active_test["device"] == device:
                        self._active_test = None
                        self.selftest_progress.set_score(0, "No test currently running")
                        self.selftest_eta_var.set("")
                    self.worker.poll_now()
                else:
                    self.selftest_status_var.set(f"Could not abort test: {result['message']}")
                    messagebox.showwarning(APP_NAME, result["message"])

            self.root.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------- tray
    def _update_tray_icon(self):
        if not self.tray_icon:
            return
        worst = "Good"
        order = {"Good": 0, "Caution": 1, "Bad": 2}
        for r in self.drives.values():
            if order[r["health"]["status"]] > order[worst]:
                worst = r["health"]["status"]
        try:
            self.tray_icon.icon = _make_tray_image(status_color(self.theme, worst))
        except Exception:
            pass

    def _minimize_to_tray(self):
        self.root.withdraw()
        if self.tray_icon is None:
            self._start_tray_icon()

    def _start_tray_icon(self):
        try:
            import pystray
        except ImportError:
            messagebox.showwarning(APP_NAME, "pystray is not installed; minimizing to taskbar instead.")
            self.root.iconify()
            return

        def on_show(icon, item):
            self.root.after(0, self._restore_from_tray)

        def on_quit(icon, item):
            self.root.after(0, self._quit_app)

        menu = pystray_menu(on_show, on_quit)
        self.tray_icon = pystray.Icon(APP_NAME, _make_tray_image("#39d17c"), APP_NAME, menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _restore_from_tray(self):
        self.root.deiconify()
        self.root.lift()

    def _on_close(self):
        if self.cfg.get("minimize_to_tray", True):
            self._minimize_to_tray()
        else:
            self._quit_app()

    def _quit_app(self):
        save_config(self.cfg)
        self.worker.stop()
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()
        sys.exit(0)


def pystray_menu(on_show, on_quit):
    import pystray
    return pystray.Menu(
        pystray.MenuItem("Open DiskPulse", on_show, default=True),
        pystray.MenuItem("Quit", on_quit),
    )


def _make_tray_image(hex_color):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=hex_color)
    draw.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 230))
    return img


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg, on_save):
        super().__init__(parent)
        self.title("Preferences")
        self.geometry("420x260")
        self.resizable(False, False)
        self.cfg = cfg
        self.on_save = on_save

        pad = {"padx": 12, "pady": 6}

        ttk.Label(self, text="Refresh interval (seconds):").grid(row=0, column=0, sticky="w", **pad)
        self.interval_var = tk.IntVar(value=cfg["refresh_seconds"])
        ttk.Spinbox(self, from_=10, to=3600, textvariable=self.interval_var, width=10).grid(
            row=0, column=1, sticky="w", **pad)

        ttk.Label(self, text="smartctl.exe path (optional):").grid(row=1, column=0, sticky="w", **pad)
        self.path_var = tk.StringVar(value=cfg.get("smartctl_path", ""))
        ttk.Entry(self, textvariable=self.path_var, width=30).grid(row=1, column=1, sticky="w", **pad)

        self.minimize_var = tk.BooleanVar(value=cfg.get("minimize_to_tray", True))
        ttk.Checkbutton(self, text="Minimize to system tray on close",
                         variable=self.minimize_var).grid(row=2, column=0, columnspan=2, sticky="w", **pad)

        self.notify_var = tk.BooleanVar(value=cfg.get("notify_on_status_change", True))
        ttk.Checkbutton(self, text="Notify me when a drive's status changes",
                         variable=self.notify_var).grid(row=3, column=0, columnspan=2, sticky="w", **pad)

        btns = ttk.Frame(self)
        btns.grid(row=4, column=0, columnspan=2, pady=16)
        ttk.Button(btns, text="Save", command=self._save).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=6)

    def _save(self):
        new_cfg = {
            "refresh_seconds": int(self.interval_var.get()),
            "smartctl_path": self.path_var.get().strip(),
            "minimize_to_tray": self.minimize_var.get(),
            "notify_on_status_change": self.notify_var.get(),
        }
        self.on_save(new_cfg)
        self.destroy()


def main():
    root = tk.Tk()
    app = DiskPulseApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
