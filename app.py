"""زیرنویس‌ساز و مترجم هوشمند فارسی — رابط گرافیکی."""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src import config, pipeline, translator


def enable_hidpi():
    """Marks the process as DPI-aware on Windows.

    Without this, Windows renders the window at 96 DPI and then bitmap-stretches
    it to the monitor's actual scaling (125%, 150%...), which is what makes
    tkinter apps look blurry on modern laptops. Must run before the first
    window is created.
    """
    if not sys.platform.startswith("win"):
        return
    import ctypes
    for attempt in (
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),  # per-monitor v2
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(1),  # system aware
        lambda: ctypes.windll.user32.SetProcessDPIAware(),       # legacy fallback
    ):
        try:
            attempt()
            return
        except Exception:
            continue


def scale_for_dpi(root: tk.Tk):
    """Scales fonts and widget metrics to the real screen DPI."""
    try:
        dpi = root.winfo_fpixels("1i")
        if dpi > 0:
            root.tk.call("tk", "scaling", dpi / 72.0)
        return max(1.0, dpi / 96.0)
    except tk.TclError:
        return 1.0


# Tk has no bidi engine: it draws text in logical order, so a mixed
# Persian/English log line comes out visually jumbled. python-bidi reorders it
# for display. Entirely optional — without it the app just logs as before.
try:
    from bidi.algorithm import get_display as _bidi_display
except ImportError:
    _bidi_display = None

_RTL_RANGES = ("\u0600", "\u06FF", "\u0750", "\u077F", "\uFB50", "\uFDFF", "\uFE70", "\uFEFF")


def _has_rtl(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" or "\uFB50" <= ch <= "\uFEFF" for ch in text)


def for_display(text: str) -> str:
    """Returns text laid out for a widget that has no bidi support."""
    if _bidi_display is None or not _has_rtl(text):
        return text
    try:
        return _bidi_display(text)
    except Exception:
        return text

# --------------------------------------------------------------------------
# Navy theme
# --------------------------------------------------------------------------
NAVY_DARKEST = "#0B1220"
NAVY_DARK = "#121C2E"
NAVY = "#1B2A45"
NAVY_LIGHT = "#24375A"
ACCENT = "#4C8DFF"
ACCENT_HOVER = "#6BA2FF"
GOLD = "#FFC857"
TEXT = "#E8EDF7"
TEXT_MUTED = "#9AA9C4"
SUCCESS = "#3DD68C"
DANGER = "#FF6B6B"

# Segoe UI covers Persian well on Windows 10/11 and stays crisp at any scale.
_UI_FAMILY = "Segoe UI"
FONT = (_UI_FAMILY, 10)
FONT_BOLD = (_UI_FAMILY, 10, "bold")
FONT_TITLE = (_UI_FAMILY, 13, "bold")
FONT_SMALL = (_UI_FAMILY, 9)
FONT_MONO = ("Consolas", 9)

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
KNOWN_GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
    "gemini-3.6-pro",
]
HIGHLIGHT_STYLES = {
    "رنگی + بولد": "both",
    "فقط رنگی": "color",
    "فقط بولد": "bold",
    "بدون تمایز": "none",
}
TTS_MODES = {
    "هم‌زمان با ویدیو": "timed",
    "پشت سر هم": "sequential",
}


def apply_theme(root: tk.Tk):
    # Recompute font sizes against the real DPI so text stays crisp and
    # correctly proportioned on scaled displays.
    global FONT, FONT_BOLD, FONT_TITLE, FONT_SMALL, FONT_MONO
    scale_for_dpi(root)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=NAVY_DARKEST)

    style.configure(".", background=NAVY_DARKEST, foreground=TEXT, font=FONT)
    style.configure("TFrame", background=NAVY_DARKEST)
    style.configure("Card.TFrame", background=NAVY_DARK, relief="flat")
    style.configure("TLabel", background=NAVY_DARKEST, foreground=TEXT, font=FONT)
    style.configure("Card.TLabel", background=NAVY_DARK, foreground=TEXT, font=FONT)
    style.configure("Muted.TLabel", background=NAVY_DARK, foreground=TEXT_MUTED, font=FONT_SMALL)
    style.configure("Title.TLabel", background=NAVY_DARK, foreground=TEXT, font=FONT_TITLE)
    style.configure("Header.TLabel", background=NAVY_DARKEST, foreground=TEXT, font=FONT_TITLE)

    style.configure("TLabelframe", background=NAVY_DARK, foreground=TEXT,
                    bordercolor=NAVY_LIGHT, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=NAVY_DARK, foreground=ACCENT, font=FONT_BOLD)

    style.configure("TButton", background=NAVY_LIGHT, foreground=TEXT,
                    borderwidth=0, focuscolor=NAVY_LIGHT, padding=(12, 7), font=FONT)
    style.map("TButton",
              background=[("active", ACCENT), ("disabled", NAVY)],
              foreground=[("disabled", TEXT_MUTED)])

    style.configure("Accent.TButton", background=ACCENT, foreground="#08101F",
                    font=FONT_BOLD, padding=(18, 10), borderwidth=0)
    style.map("Accent.TButton",
              background=[("active", ACCENT_HOVER), ("disabled", NAVY_LIGHT)],
              foreground=[("disabled", TEXT_MUTED)])

    style.configure("Icon.TButton", padding=(6, 4), background=NAVY_LIGHT)
    style.map("Icon.TButton", background=[("active", DANGER)])

    style.configure("TEntry", fieldbackground=NAVY, foreground=TEXT,
                    bordercolor=NAVY_LIGHT, insertcolor=TEXT, padding=6)
    style.configure("TCombobox", fieldbackground=NAVY, background=NAVY,
                    foreground=TEXT, arrowcolor=ACCENT, bordercolor=NAVY_LIGHT, padding=5)
    style.map("TCombobox", fieldbackground=[("readonly", NAVY)],
              foreground=[("readonly", TEXT)], selectbackground=[("readonly", NAVY)],
              selectforeground=[("readonly", TEXT)])
    root.option_add("*TCombobox*Listbox.background", NAVY)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.font", FONT)

    style.configure("TCheckbutton", background=NAVY_DARK, foreground=TEXT, font=FONT)
    style.map("TCheckbutton", background=[("active", NAVY_DARK)],
              foreground=[("active", ACCENT)])

    style.configure("TSeparator", background=NAVY_LIGHT)
    style.configure("Nav.Horizontal.TProgressbar", troughcolor=NAVY,
                    background=ACCENT, bordercolor=NAVY, lightcolor=ACCENT,
                    darkcolor=ACCENT, thickness=18)
    style.configure("Done.Horizontal.TProgressbar", troughcolor=NAVY,
                    background=SUCCESS, bordercolor=NAVY, lightcolor=SUCCESS,
                    darkcolor=SUCCESS, thickness=18)
    style.configure("Vertical.TScrollbar", background=NAVY_LIGHT,
                    troughcolor=NAVY_DARK, arrowcolor=TEXT, bordercolor=NAVY_DARK)


# --------------------------------------------------------------------------
# Reusable widgets
# --------------------------------------------------------------------------

class Card(ttk.Frame):
    """A titled panel with padding, used as the basic layout block."""

    def __init__(self, parent, title: str, **kwargs):
        super().__init__(parent, style="Card.TFrame", padding=14, **kwargs)
        header = ttk.Frame(self, style="Card.TFrame")
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text=title, style="Title.TLabel").pack(side="right")
        self.body = ttk.Frame(self, style="Card.TFrame")
        self.body.pack(fill="both", expand=True)


def attach_entry_menu(entry: ttk.Entry, root):
    """Adds copy/paste shortcuts and a right-click menu that work with any
    keyboard layout (the default Ctrl+V binding fails on a Persian layout)."""

    def paste(event=None):
        try:
            text = root.clipboard_get()
            if entry.select_present():
                entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
            entry.insert(tk.INSERT, text.strip())
        except tk.TclError:
            pass
        return "break"

    def copy(event=None):
        try:
            if entry.select_present():
                text = entry.get()[entry.index(tk.SEL_FIRST):entry.index(tk.SEL_LAST)]
                root.clipboard_clear()
                root.clipboard_append(text)
        except tk.TclError:
            pass
        return "break"

    def cut(event=None):
        copy()
        try:
            if entry.select_present():
                entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            pass
        return "break"

    for seq in ("<Control-v>", "<Control-V>", "<<Paste>>"):
        entry.bind(seq, paste)
    for seq in ("<Control-c>", "<Control-C>"):
        entry.bind(seq, copy)
    for seq in ("<Control-x>", "<Control-X>"):
        entry.bind(seq, cut)

    menu = tk.Menu(entry, tearoff=0, bg=NAVY, fg=TEXT,
                   activebackground=ACCENT, activeforeground="#08101F", font=FONT)
    menu.add_command(label="برش", command=cut)
    menu.add_command(label="کپی", command=copy)
    menu.add_command(label="چسباندن", command=paste)
    menu.add_separator()
    menu.add_command(label="انتخاب همه", command=lambda: entry.select_range(0, tk.END))
    entry.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
    return paste


# --------------------------------------------------------------------------
# Settings panels
# --------------------------------------------------------------------------

class KeysPanel(Card):
    """Manages the list of Gemini API keys."""

    def __init__(self, parent, root, saved_keys):
        super().__init__(parent, "کلیدهای Gemini API")
        self.root = root
        self.rows = []

        self.container = ttk.Frame(self.body, style="Card.TFrame")
        self.container.pack(fill="x")

        ttk.Button(self.body, text="＋ افزودن کلید جدید",
                   command=self.add_key).pack(anchor="e", pady=(8, 0))

        ttk.Label(self.body, text="اگر سهمیه‌ی یک کلید تمام شود، به‌صورت خودکار کلید بعدی امتحان می‌شود.",
                  style="Muted.TLabel").pack(anchor="e", pady=(6, 0))

        for key in (saved_keys or []):
            self.add_key(key)
        if not self.rows:
            self.add_key()

    def add_key(self, value=""):
        row = ttk.Frame(self.container, style="Card.TFrame")
        row.pack(fill="x", pady=3)

        label = ttk.Label(row, text="", style="Card.TLabel", width=8)
        label.pack(side="right", padx=(0, 6))

        entry = ttk.Entry(row, show="●", font=FONT)
        entry.insert(0, value)
        entry.pack(side="right", fill="x", expand=True, padx=6)
        paste = attach_entry_menu(entry, self.root)

        ttk.Button(row, text="چسباندن", width=10,
                   command=lambda: self._paste_into(entry)).pack(side="right", padx=3)
        ttk.Button(row, text="نمایش", width=8,
                   command=lambda e=entry: self._toggle(e)).pack(side="right", padx=3)
        ttk.Button(row, text="✕", width=3, style="Icon.TButton",
                   command=lambda: self.remove(row, entry)).pack(side="right", padx=3)

        self.rows.append((row, entry, label))
        self._renumber()

    def _paste_into(self, entry):
        try:
            entry.delete(0, tk.END)
            entry.insert(0, self.root.clipboard_get().strip())
        except tk.TclError:
            messagebox.showwarning("هشدار", "چیزی در کلیپ‌بورد کپی نشده است.")

    @staticmethod
    def _toggle(entry):
        entry.config(show="" if entry.cget("show") else "●")

    def remove(self, row, entry):
        if len(self.rows) <= 1:
            messagebox.showwarning("هشدار", "حداقل یک کلید باید باقی بماند.")
            return
        self.rows = [r for r in self.rows if r[1] is not entry]
        row.destroy()
        self._renumber()

    def _renumber(self):
        for idx, (_, _, label) in enumerate(self.rows, start=1):
            label.config(text=f"کلید {idx}:")

    def get_keys(self):
        return [e.get().strip() for _, e, _ in self.rows if e.get().strip()]


class ModelsPanel(Card):
    """Ordered list of Gemini models to try, starting with a single row."""

    def __init__(self, parent, saved_priority):
        super().__init__(parent, "اولویت اجرای مدل‌ها")
        self.rows = []

        self.container = ttk.Frame(self.body, style="Card.TFrame")
        self.container.pack(fill="x")

        ttk.Button(self.body, text="＋ افزودن مدل",
                   command=self.add_model).pack(anchor="e", pady=(8, 0))
        ttk.Label(self.body,
                  text="از بالا به پایین امتحان می‌شوند؛ با خطای سهمیه یا در دسترس نبودن، مدل بعدی اجرا می‌شود.",
                  style="Muted.TLabel").pack(anchor="e", pady=(6, 0))

        for model in (saved_priority or [])[:8]:
            self.add_model(model)
        if not self.rows:
            self.add_model()

    def add_model(self, value=None):
        if len(self.rows) >= 8:
            messagebox.showinfo("توجه", "حداکثر ۸ مدل می‌توانی اضافه کنی.")
            return

        row = ttk.Frame(self.container, style="Card.TFrame")
        row.pack(fill="x", pady=3)

        label = ttk.Label(row, text="", style="Card.TLabel", width=10)
        label.pack(side="right", padx=(0, 6))

        var = tk.StringVar(value=value or KNOWN_GEMINI_MODELS[0])
        combo = ttk.Combobox(row, textvariable=var, values=KNOWN_GEMINI_MODELS,
                             font=FONT, width=28)
        combo.pack(side="right", padx=6)

        ttk.Button(row, text="▲", width=3,
                   command=lambda: self._move(row, -1)).pack(side="right", padx=2)
        ttk.Button(row, text="▼", width=3,
                   command=lambda: self._move(row, 1)).pack(side="right", padx=2)
        ttk.Button(row, text="✕", width=3, style="Icon.TButton",
                   command=lambda: self.remove(row)).pack(side="right", padx=2)

        self.rows.append((row, var, label))
        self._renumber()

    def _move(self, row, delta):
        idx = next((i for i, r in enumerate(self.rows) if r[0] is row), None)
        if idx is None:
            return
        new_idx = idx + delta
        if not 0 <= new_idx < len(self.rows):
            return
        self.rows[idx], self.rows[new_idx] = self.rows[new_idx], self.rows[idx]
        for r, _, _ in self.rows:
            r.pack_forget()
            r.pack(fill="x", pady=3)
        self._renumber()

    def remove(self, row):
        if len(self.rows) <= 1:
            messagebox.showwarning("هشدار", "حداقل یک مدل باید باقی بماند.")
            return
        self.rows = [r for r in self.rows if r[0] is not row]
        row.destroy()
        self._renumber()

    def _renumber(self):
        for idx, (_, _, label) in enumerate(self.rows, start=1):
            label.config(text=f"اولویت {idx}:")

    def get_models(self):
        seen, out = set(), []
        for _, var, _ in self.rows:
            m = var.get().strip()
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out


class OutputsPanel(Card):
    """Lets the user pick which deliverables to produce."""

    def __init__(self, parent, saved_outputs, on_change=None):
        super().__init__(parent, "خروجی‌های مورد نظر")
        self.vars = {}
        self.on_change = on_change

        saved = set(saved_outputs or [pipeline.OUT_EN_SRT, pipeline.OUT_FA_SRT])
        grid = ttk.Frame(self.body, style="Card.TFrame")
        grid.pack(fill="x")

        for out_id, label in pipeline.OUTPUT_LABELS.items():
            var = tk.BooleanVar(value=out_id in saved)
            var.trace_add("write", lambda *_: self.on_change and self.on_change())
            ttk.Checkbutton(grid, text=label, variable=var).pack(side="right", padx=14)
            self.vars[out_id] = var

        ttk.Label(self.body,
                  text="می‌توانی هم‌زمان چند خروجی انتخاب کنی. همه کنار فایل ویدیوی اصلی ساخته می‌شوند.",
                  style="Muted.TLabel").pack(anchor="e", pady=(8, 0))

    def get_outputs(self):
        return [k for k, v in self.vars.items() if v.get()]


class ProgressPanel(Card):
    """Shows one 0–100% bar per stage; the bar resets when a stage starts."""

    STAGES = ["transcribe", "translate", "tts"]

    def __init__(self, parent):
        super().__init__(parent, "روند پردازش")
        self.bars = {}
        self.percent_labels = {}
        self.detail_labels = {}
        self.rows = {}

        for stage in self.STAGES:
            row = ttk.Frame(self.body, style="Card.TFrame")
            row.pack(fill="x", pady=6)

            top = ttk.Frame(row, style="Card.TFrame")
            top.pack(fill="x")
            ttk.Label(top, text=pipeline.STAGE_LABELS[stage],
                      style="Card.TLabel").pack(side="right")
            percent = ttk.Label(top, text="۰٪", style="Card.TLabel")
            percent.pack(side="left")

            bar = ttk.Progressbar(row, style="Nav.Horizontal.TProgressbar",
                                  mode="determinate", maximum=100)
            bar.pack(fill="x", pady=(4, 2))

            detail = ttk.Label(row, text="در انتظار...", style="Muted.TLabel")
            detail.pack(anchor="e")

            self.rows[stage] = row
            self.bars[stage] = bar
            self.percent_labels[stage] = percent
            self.detail_labels[stage] = detail

    def set_visible_stages(self, stages):
        """Hides the bars for stages that aren't part of this run."""
        for stage in self.STAGES:
            self.rows[stage].pack_forget()
        for stage in self.STAGES:
            if stage in stages:
                self.rows[stage].pack(fill="x", pady=6)

    def reset(self):
        for stage in self.STAGES:
            self.bars[stage].config(style="Nav.Horizontal.TProgressbar", value=0)
            self.percent_labels[stage].config(text="۰٪")
            self.detail_labels[stage].config(text="در انتظار...")

    def update_stage(self, stage, fraction, extra):
        if stage not in self.bars:
            return
        percent = fraction * 100
        self.bars[stage].config(value=percent)
        self.percent_labels[stage].config(text=f"{to_persian_digits(f'{percent:.0f}')}٪")

        if fraction >= 0.999:
            self.bars[stage].config(style="Done.Horizontal.TProgressbar")
            self.detail_labels[stage].config(text="✔ انجام شد")
            return

        self.detail_labels[stage].config(text=self._describe(stage, extra))

    @staticmethod
    def _describe(stage, extra):
        extra = extra or {}
        if stage == "transcribe" and extra.get("total_time"):
            cur = format_mmss(extra.get("current_time", 0))
            total = format_mmss(extra["total_time"])
            eta = extra.get("eta_seconds")
            eta_txt = f" | باقی‌مانده: {format_mmss(eta)}" if eta else ""
            return to_persian_digits(f"دقیقه {cur} از {total}{eta_txt}")
        if extra.get("total"):
            return to_persian_digits(f"{extra.get('done', 0)} از {extra['total']} خط")
        return "در حال پردازش..."


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def to_persian_digits(text: str) -> str:
    return str(text).translate(_PERSIAN_DIGITS)


def format_mmss(seconds) -> str:
    seconds = max(0, int(round(seconds or 0)))
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def open_folder(path: Path):
    """Opens the containing folder in the OS file manager."""
    folder = str(Path(path).parent)
    try:
        if sys.platform.startswith("win"):
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        messagebox.showwarning("هشدار", f"باز کردن پوشه ممکن نشد: {e}")


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("زیرنویس‌ساز و مترجم هوشمند فارسی")
        self.geometry("880x860")
        self.minsize(760, 640)
        apply_theme(self)

        self.cfg = config.load_config()
        self.results = {}
        self.worker = None
        self.ui_queue = queue.Queue()

        self._build_header()
        self._build_scroll_area()
        self._build_panels()
        self._build_footer()

        self._on_outputs_changed()
        self.after(80, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout ----------------------------------------------------------
    def _build_header(self):
        header = ttk.Frame(self, padding=(18, 14, 18, 6))
        header.pack(fill="x")
        ttk.Label(header, text="زیرنویس‌ساز و مترجم هوشمند فارسی",
                  style="Header.TLabel").pack(side="right")
        ttk.Label(header, text="ویدیو → زیرنویس انگلیسی → زیرنویس فارسی → صوت فارسی",
                  foreground=TEXT_MUTED, font=FONT_SMALL).pack(side="right", padx=14)

    def _build_scroll_area(self):
        wrapper = ttk.Frame(self)
        wrapper.pack(fill="both", expand=True, padx=14)

        self.canvas = tk.Canvas(wrapper, bg=NAVY_DARKEST, highlightthickness=0)
        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=self.canvas.yview)
        self.scroll_frame = ttk.Frame(self.canvas)

        self.scroll_frame.bind("<Configure>",
                               lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.window_id = self.canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfig(self.window_id, width=e.width))
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.bind_all("<MouseWheel>",
                      lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

    def _build_panels(self):
        p = self.scroll_frame

        self.keys_panel = KeysPanel(p, self, self.cfg.get("api_keys"))
        self.keys_panel.pack(fill="x", pady=6)

        self.models_panel = ModelsPanel(p, self.cfg.get("models_priority"))
        self.models_panel.pack(fill="x", pady=6)

        # --- file ---
        self.file_card = file_card = Card(p, "فایل ورودی")
        file_card.pack(fill="x", pady=6)
        row = ttk.Frame(file_card.body, style="Card.TFrame")
        row.pack(fill="x")
        self.file_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.file_var, font=FONT)
        entry.pack(side="right", fill="x", expand=True, padx=(0, 8))
        attach_entry_menu(entry, self)
        ttk.Button(row, text="انتخاب ویدیو...", command=self.browse_file).pack(side="right")

        self.outputs_panel = OutputsPanel(p, self.cfg.get("outputs"),
                                          on_change=self._on_outputs_changed)
        self.outputs_panel.pack(fill="x", pady=6)

        # --- transcription settings ---
        whisper_card = Card(p, "تنظیمات تبدیل صوت به متن")
        whisper_card.pack(fill="x", pady=6)
        wrow = ttk.Frame(whisper_card.body, style="Card.TFrame")
        wrow.pack(fill="x")

        ttk.Label(wrow, text="مدل Whisper:", style="Card.TLabel").pack(side="right", padx=(0, 6))
        self.whisper_var = tk.StringVar(value=self.cfg.get("whisper_model", "small"))
        ttk.Combobox(wrow, textvariable=self.whisper_var, values=WHISPER_MODELS,
                     state="readonly", width=12, font=FONT).pack(side="right", padx=(0, 20))

        ttk.Label(wrow, text="دستگاه پردازش:", style="Card.TLabel").pack(side="right", padx=(0, 6))
        self.device_var = tk.StringVar(value=self.cfg.get("device", "cpu"))
        ttk.Combobox(wrow, textvariable=self.device_var, values=["cpu", "cuda"],
                     state="readonly", width=10, font=FONT).pack(side="right")

        ttk.Label(whisper_card.body,
                  text="مدل‌های medium و large-v3 روی CPU بسیار کندتر از small و base هستند.",
                  style="Muted.TLabel").pack(anchor="e", pady=(8, 0))

        # --- subtitle appearance ---
        self.style_card = Card(p, "ظاهر زیرنویس فارسی")
        self.style_card.pack(fill="x", pady=6)
        srow = ttk.Frame(self.style_card.body, style="Card.TFrame")
        srow.pack(fill="x")

        ttk.Label(srow, text="تمایز اصطلاحات انگلیسی:", style="Card.TLabel").pack(side="right", padx=(0, 6))
        saved_style = self.cfg.get("highlight_style", "both")
        self.style_label_var = tk.StringVar(
            value=next((k for k, v in HIGHLIGHT_STYLES.items() if v == saved_style), "رنگی + بولد"))
        ttk.Combobox(srow, textvariable=self.style_label_var, values=list(HIGHLIGHT_STYLES),
                     state="readonly", width=16, font=FONT).pack(side="right", padx=(0, 20))

        ttk.Label(srow, text="رنگ:", style="Card.TLabel").pack(side="right", padx=(0, 6))
        self.color_var = tk.StringVar(value=self.cfg.get("highlight_color", GOLD))
        color_entry = ttk.Entry(srow, textvariable=self.color_var, width=12, font=FONT)
        color_entry.pack(side="right")
        attach_entry_menu(color_entry, self)

        ttk.Label(self.style_card.body,
                  text="اصطلاحاتی که آوانویسی شده‌اند (مثل «برپ سوییت») متمایز نمایش داده می‌شوند "
                       "تا مشخص باشد ترجمه نیستند، بلکه همان واژه‌ی انگلیسی به خط فارسی‌اند.",
                  style="Muted.TLabel", wraplength=700, justify="right").pack(anchor="e", pady=(8, 0))

        # --- TTS ---
        self.tts_card = Card(p, "تنظیمات صوت فارسی")
        self.tts_card.pack(fill="x", pady=6)
        trow = ttk.Frame(self.tts_card.body, style="Card.TFrame")
        trow.pack(fill="x")

        ttk.Label(trow, text="صدا:", style="Card.TLabel").pack(side="right", padx=(0, 6))
        from src import tts as tts_mod
        saved_voice = self.cfg.get("tts_voice", tts_mod.DEFAULT_VOICE)
        self.voice_label_var = tk.StringVar(
            value=next((k for k, v in tts_mod.PERSIAN_VOICES.items() if v == saved_voice),
                       list(tts_mod.PERSIAN_VOICES)[0]))
        ttk.Combobox(trow, textvariable=self.voice_label_var,
                     values=list(tts_mod.PERSIAN_VOICES), state="readonly",
                     width=14, font=FONT).pack(side="right", padx=(0, 20))

        ttk.Label(trow, text="سرعت:", style="Card.TLabel").pack(side="right", padx=(0, 6))
        self.rate_var = tk.StringVar(value=self.cfg.get("tts_rate", "+0%"))
        ttk.Combobox(trow, textvariable=self.rate_var,
                     values=["-25%", "-10%", "+0%", "+10%", "+25%", "+50%"],
                     state="readonly", width=8, font=FONT).pack(side="right", padx=(0, 20))

        ttk.Label(trow, text="چیدمان:", style="Card.TLabel").pack(side="right", padx=(0, 6))
        saved_mode = self.cfg.get("tts_mode", "timed")
        self.tts_mode_var = tk.StringVar(
            value=next((k for k, v in TTS_MODES.items() if v == saved_mode), "هم‌زمان با ویدیو"))
        ttk.Combobox(trow, textvariable=self.tts_mode_var, values=list(TTS_MODES),
                     state="readonly", width=16, font=FONT).pack(side="right")

        self.tts_hint = ttk.Label(self.tts_card.body, text="", style="Muted.TLabel",
                                  wraplength=700, justify="right")
        self.tts_hint.pack(anchor="e", pady=(8, 0))
        self._refresh_tts_hint()

        # --- progress + log ---
        self.progress_panel = ProgressPanel(p)
        self.progress_panel.pack(fill="x", pady=6)

        log_card = Card(p, "گزارش کار")
        log_card.pack(fill="both", expand=True, pady=6)
        log_wrap = ttk.Frame(log_card.body, style="Card.TFrame")
        log_wrap.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_wrap, wrap="word", height=12, bg=NAVY,
                                fg=TEXT, insertbackground=TEXT, relief="flat",
                                font=FONT_MONO, padx=10, pady=8)
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self.log_text.tag_config("warn", foreground=GOLD)
        self.log_text.tag_config("error", foreground=DANGER)
        self.log_text.tag_config("ok", foreground=SUCCESS)
        self.log_text.tag_config("stage", foreground=ACCENT)

    def _build_footer(self):
        footer = ttk.Frame(self, padding=(18, 10))
        footer.pack(fill="x")

        self.start_btn = ttk.Button(footer, text="شروع پردازش", style="Accent.TButton",
                                    command=self.start_processing)
        self.start_btn.pack(side="right")

        self.open_btn = ttk.Button(footer, text="باز کردن پوشه‌ی خروجی",
                                   command=self._open_output, state="disabled")
        self.open_btn.pack(side="right", padx=10)

        ttk.Button(footer, text="ذخیره تنظیمات", command=self.save_settings).pack(side="right")

        self.status_var = tk.StringVar(value="آماده")
        ttk.Label(footer, textvariable=self.status_var,
                  foreground=TEXT_MUTED, font=FONT_SMALL).pack(side="left")

    # -- reactive UI -----------------------------------------------------
    def _on_outputs_changed(self):
        outputs = set(self.outputs_panel.get_outputs())

        stages = ["transcribe"]
        if outputs & {pipeline.OUT_FA_SRT, pipeline.OUT_FA_AUDIO}:
            stages.append("translate")
        if pipeline.OUT_FA_AUDIO in outputs:
            stages.append("tts")
        self.progress_panel.set_visible_stages(stages)

        # Only surface the settings that apply to the selected outputs, so the
        # window isn't cluttered with controls that would have no effect.
        needs_gemini = bool(outputs & {pipeline.OUT_FA_SRT, pipeline.OUT_FA_AUDIO})
        self.keys_panel.pack_forget()
        self.models_panel.pack_forget()
        if needs_gemini:
            self.keys_panel.pack(fill="x", pady=6, before=self.file_card)
            self.models_panel.pack(fill="x", pady=6, before=self.file_card)

        self.style_card.pack_forget()
        self.tts_card.pack_forget()
        if pipeline.OUT_FA_SRT in outputs:
            self.style_card.pack(fill="x", pady=6, before=self.progress_panel)
        if pipeline.OUT_FA_AUDIO in outputs:
            self.tts_card.pack(fill="x", pady=6, before=self.progress_panel)

        self._refresh_tts_hint()

    def _refresh_tts_hint(self):
        if not hasattr(self, "tts_hint"):
            return
        from src import tts as tts_mod
        engines = tts_mod.available_engines()
        if not engines:
            self.tts_hint.config(
                text="⚠️ موتور گفتار نصب نیست. در همان ترمینالی که برنامه را اجرا می‌کنی این را بزن:\n"
                     "pip install edge-tts pydub\n"
                     "سپس برنامه را ببند و دوباره باز کن.", foreground=GOLD)
        elif not tts_mod.has_ffmpeg():
            self.tts_hint.config(
                text="ℹ️ موتور گفتار آماده است، ولی ffmpeg پیدا نشد؛ پس حالت «هم‌زمان با ویدیو» "
                     "به «پشت سر هم» تغییر می‌کند. برای هم‌زمانی دقیق، ffmpeg را از gyan.dev "
                     "دانلود کن و پوشه‌ی bin آن را به PATH ویندوز اضافه کن.", foreground=TEXT_MUTED)
        else:
            self.tts_hint.config(
                text="✔ آماده است. در حالت «هم‌زمان با ویدیو» هر جمله دقیقاً سر زمان خودش پخش می‌شود "
                     "و خروجی کنار ویدیو با نام video.fa.mp3 ساخته می‌شود.", foreground=TEXT_MUTED)

    # -- actions ---------------------------------------------------------
    def browse_file(self):
        filename = filedialog.askopenfilename(
            title="انتخاب فایل ویدیو یا صوت",
            filetypes=[("فایل‌های صوتی و تصویری", "*.mp4 *.mkv *.avi *.mov *.webm *.mp3 *.wav *.m4a *.flac"),
                       ("همه‌ی فایل‌ها", "*.*")])
        if filename:
            self.file_var.set(filename)

    def _collect_settings(self) -> dict:
        from src import tts as tts_mod
        cfg = dict(self.cfg)
        cfg.update({
            "api_keys": self.keys_panel.get_keys(),
            "models_priority": self.models_panel.get_models(),
            "whisper_model": self.whisper_var.get(),
            "device": self.device_var.get(),
            "outputs": self.outputs_panel.get_outputs(),
            "highlight_style": HIGHLIGHT_STYLES.get(self.style_label_var.get(), "both"),
            "highlight_color": self.color_var.get().strip() or GOLD,
            "tts_voice": tts_mod.PERSIAN_VOICES.get(self.voice_label_var.get(),
                                                    tts_mod.DEFAULT_VOICE),
            "tts_rate": self.rate_var.get(),
            "tts_mode": TTS_MODES.get(self.tts_mode_var.get(), "timed"),
        })
        return cfg

    def save_settings(self, silent=False):
        self.cfg = self._collect_settings()
        ok = config.save_config(self.cfg)
        if not silent:
            if ok:
                messagebox.showinfo("ذخیره شد", "تنظیمات با موفقیت ذخیره شدند.")
            else:
                messagebox.showerror("خطا", "ذخیره‌سازی تنظیمات ناموفق بود.")
        return ok

    def _validate(self, cfg) -> bool:
        path = self.file_var.get().strip().strip('"')
        if not path:
            messagebox.showerror("خطا", "لطفاً یک فایل ویدیو یا صوت انتخاب کن.")
            return False
        if not os.path.exists(path):
            messagebox.showerror("خطا", "فایل انتخاب‌شده روی سیستم پیدا نشد.")
            return False
        if not cfg["outputs"]:
            messagebox.showerror("خطا", "حداقل یک خروجی را انتخاب کن.")
            return False

        needs_api = bool(set(cfg["outputs"]) & {pipeline.OUT_FA_SRT, pipeline.OUT_FA_AUDIO})
        if needs_api and not cfg["api_keys"]:
            messagebox.showerror("خطا", "برای خروجی فارسی باید حداقل یک کلید Gemini API وارد کنی.")
            return False
        if needs_api and not cfg["models_priority"]:
            messagebox.showerror("خطا", "حداقل یک مدل Gemini باید انتخاب شده باشد.")
            return False

        color = cfg["highlight_color"]
        if cfg["highlight_style"] in ("color", "both") and not _is_hex_color(color):
            messagebox.showerror("خطا", f"کد رنگ نامعتبر است: {color}\nنمونه‌ی درست: #FFC857")
            return False
        return True

    def start_processing(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("در حال اجرا", "یک پردازش در حال انجام است.")
            return

        cfg = self._collect_settings()
        if not self._validate(cfg):
            return

        self.cfg = cfg
        self.save_settings(silent=True)

        self.results = {}
        self.progress_panel.reset()
        self._on_outputs_changed()
        self.log_text.delete("1.0", tk.END)
        self.start_btn.config(state="disabled", text="در حال پردازش...")
        self.open_btn.config(state="disabled")
        self.status_var.set("در حال پردازش...")

        video_path = Path(self.file_var.get().strip().strip('"'))
        self.worker = threading.Thread(target=self._run_worker, args=(video_path, cfg), daemon=True)
        self.worker.start()

    def _run_worker(self, video_path, cfg):
        """Runs the pipeline off the UI thread; all UI updates go via a queue."""
        try:
            results = pipeline.run_pipeline(
                input_path=video_path,
                api_keys=cfg["api_keys"],
                models_priority=cfg["models_priority"],
                model_size=cfg["whisper_model"],
                device=cfg["device"],
                outputs=cfg["outputs"],
                highlight_style=cfg["highlight_style"],
                highlight_color=cfg["highlight_color"],
                tts_voice=cfg["tts_voice"],
                tts_rate=cfg["tts_rate"],
                tts_mode=cfg["tts_mode"],
                log=lambda msg: self.ui_queue.put(("log", msg)),
                progress=lambda stage, frac, extra: self.ui_queue.put(("progress", (stage, frac, extra))),
            )
            self.ui_queue.put(("done", results))
        except KeyboardInterrupt:
            self.ui_queue.put(("failed", "پردازش توسط کاربر متوقف شد."))
        except Exception as e:
            self.ui_queue.put(("failed", str(e) or type(e).__name__))

    # -- UI queue --------------------------------------------------------
    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    stage, frac, extra = payload
                    if stage != "done":
                        self.progress_panel.update_stage(stage, frac, extra)
                elif kind == "done":
                    self._on_success(payload)
                elif kind == "failed":
                    self._on_failure(payload)
        except queue.Empty:
            pass
        self.after(80, self._drain_queue)

    def _append_log(self, message: str):
        message = str(message)
        tag = ""
        if message.startswith("\r"):
            # Carriage-return updates (download progress) replace the last line.
            message = message.lstrip("\r")
            last = self.log_text.index("end-2c linestart")
            self.log_text.delete(last, "end-1c")
            self.log_text.insert(last, for_display(message))
            self.log_text.see(tk.END)
            return
        if "⚠️" in message:
            tag = "warn"
        elif message.startswith("✔"):
            tag = "ok"
        elif message.startswith("▶"):
            tag = "stage"
        elif "❌" in message:
            tag = "error"

        self.log_text.insert(tk.END, for_display(message) + "\n", tag)
        self.log_text.see(tk.END)

    def _on_success(self, results):
        self.results = results or {}
        self.start_btn.config(state="normal", text="شروع پردازش")
        self.status_var.set("پایان یافت")
        if self.results:
            self.open_btn.config(state="normal")
        names = "\n".join(f"• {pipeline.OUTPUT_LABELS[k]}: {Path(v).name}"
                          for k, v in self.results.items())
        self._append_log("✔ همه‌ی مراحل با موفقیت انجام شد.")
        messagebox.showinfo("پایان", f"پردازش با موفقیت تمام شد.\n\n{names}")

    def _on_failure(self, message):
        self.start_btn.config(state="normal", text="شروع پردازش")
        self.status_var.set("متوقف شد به دلیل خطا")
        self._append_log(f"❌ خطا: {message}")
        messagebox.showerror("خطا", message)

    def _open_output(self):
        if self.results:
            open_folder(next(iter(self.results.values())))

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("خروج", "پردازشی در حال اجراست. واقعاً می‌خواهی خارج شوی؟"):
                return
        self.save_settings(silent=True)
        self.destroy()


def _is_hex_color(value: str) -> bool:
    value = (value or "").strip()
    return (len(value) in (4, 7) and value.startswith("#")
            and all(c in "0123456789abcdefABCDEF" for c in value[1:]))


if __name__ == "__main__":
    enable_hidpi()  # must run before the first Tk window exists
    try:
        App().mainloop()
    except Exception as exc:  # last-resort guard so the window never dies silently
        try:
            messagebox.showerror("خطای غیرمنتظره", str(exc))
        except Exception:
            print(f"خطای غیرمنتظره: {exc}")
        raise
