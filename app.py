"""
Persian Subtitle Maker - Desktop GUI
Drop in an English video, get back English + Persian subtitles.

Run with:  python app.py
"""
import os
import sys
import threading
import traceback
from pathlib import Path
from tkinter import (Tk, StringVar, Text, END, DISABLED, NORMAL, WORD,
                      filedialog, messagebox, ttk)

sys.path.insert(0, str(Path(__file__).parent))
from src import config as cfg
from src.pipeline import run_pipeline


class App:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("زیرنویس‌ساز فارسی")
        self.root.geometry("640x560")
        self.root.resizable(True, True)

        self.settings = cfg.load_config()
        self.selected_file = StringVar(value="")
        self.model_var = StringVar(value=self.settings.get("whisper_model", "medium"))
        self.device_var = StringVar(value=self.settings.get("device", "cpu"))
        self.api_key_var = StringVar(value=self.settings.get("gemini_api_key", ""))

        self.output_paths = None
        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        frm_key = ttk.LabelFrame(self.root, text="کلید Gemini API")
        frm_key.pack(fill="x", **pad)
        self.key_entry = ttk.Entry(frm_key, textvariable=self.api_key_var, show="*", width=50)
        self.key_entry.pack(side="right", padx=8, pady=8, fill="x", expand=True)
        ttk.Button(frm_key, text="ذخیره کلید", command=self.save_key).pack(side="left", padx=8, pady=8)

        frm_file = ttk.LabelFrame(self.root, text="فایل ویدیو")
        frm_file.pack(fill="x", **pad)
        ttk.Button(frm_file, text="انتخاب ویدیو...", command=self.choose_file).pack(side="left", padx=8, pady=8)
        self.file_label = ttk.Label(frm_file, text="فایلی انتخاب نشده", anchor="e", justify="right")
        self.file_label.pack(side="right", padx=8, pady=8, fill="x", expand=True)

        frm_opts = ttk.LabelFrame(self.root, text="تنظیمات")
        frm_opts.pack(fill="x", **pad)

        ttk.Label(frm_opts, text="مدل Whisper:").grid(row=0, column=1, sticky="e", padx=6, pady=6)
        model_combo = ttk.Combobox(frm_opts, textvariable=self.model_var, state="readonly",
                                    values=["tiny", "base", "small", "medium", "large-v3"], width=12)
        model_combo.grid(row=0, column=0, sticky="w", padx=6, pady=6)

        ttk.Label(frm_opts, text="دستگاه پردازش:").grid(row=1, column=1, sticky="e", padx=6, pady=6)
        device_combo = ttk.Combobox(frm_opts, textvariable=self.device_var, state="readonly",
                                     values=["cpu", "cuda"], width=12)
        device_combo.grid(row=1, column=0, sticky="w", padx=6, pady=6)

        self.start_btn = ttk.Button(self.root, text="شروع پردازش", command=self.start_clicked)
        self.start_btn.pack(pady=8)

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=10, pady=4)

        frm_log = ttk.LabelFrame(self.root, text="روند کار")
        frm_log.pack(fill="both", expand=True, **pad)
        self.log_text = Text(frm_log, wrap=WORD, height=14, state=DISABLED)
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

        self.open_folder_btn = ttk.Button(self.root, text="باز کردن پوشه‌ی خروجی",
                                           command=self.open_output_folder, state=DISABLED)
        self.open_folder_btn.pack(pady=6)

    # ---------- Actions ----------
    def save_key(self):
        cfg.save_config({"gemini_api_key": self.api_key_var.get().strip()})
        messagebox.showinfo("ذخیره شد", "کلید API ذخیره شد.")

    def choose_file(self):
        path = filedialog.askopenfilename(
            title="یک فایل ویدیو یا صوتی انتخاب کن",
            filetypes=[("Video/Audio", "*.mp4 *.mkv *.avi *.mov *.webm *.mp3 *.wav *.m4a"), ("همه فایل‌ها", "*.*")]
        )
        if path:
            self.selected_file.set(path)
            self.file_label.config(text=Path(path).name)

    def log(self, message: str):
        def _write():
            self.log_text.config(state=NORMAL)
            self.log_text.insert(END, message + "\n")
            self.log_text.see(END)
            self.log_text.config(state=DISABLED)
        self.root.after(0, _write)

    def start_clicked(self):
        video_path = self.selected_file.get()
        api_key = self.api_key_var.get().strip()

        if not video_path:
            messagebox.showwarning("فایل انتخاب نشده", "لطفاً اول یک فایل ویدیو انتخاب کن.")
            return
        if not api_key:
            messagebox.showwarning("کلید API خالیه", "لطفاً کلید Gemini API رو وارد و ذخیره کن.")
            return

        cfg.save_config({
            "gemini_api_key": api_key,
            "whisper_model": self.model_var.get(),
            "device": self.device_var.get(),
        })

        self.start_btn.config(state=DISABLED)
        self.open_folder_btn.config(state=DISABLED)
        self.progress.start(12)
        self.log_text.config(state=NORMAL)
        self.log_text.delete("1.0", END)
        self.log_text.config(state=DISABLED)

        thread = threading.Thread(target=self._run_pipeline_thread,
                                   args=(video_path, api_key), daemon=True)
        thread.start()

    def _run_pipeline_thread(self, video_path, api_key):
        try:
            en_path, fa_path = run_pipeline(
                input_path=Path(video_path),
                api_key=api_key,
                model_size=self.model_var.get(),
                device=self.device_var.get(),
                log=self.log,
            )
            self.output_paths = (en_path, fa_path)
            self.log("تمام شد! زیرنویس فارسی آماده است.")
            self.root.after(0, lambda: messagebox.showinfo(
                "تمام شد", f"زیرنویس فارسی ساخته شد:\n{fa_path}"))
            self.root.after(0, lambda: self.open_folder_btn.config(state=NORMAL))
        except Exception as e:
            err = f"خطا: {e}"
            self.log(err)
            self.log(traceback.format_exc())
            self.root.after(0, lambda: messagebox.showerror("خطا", str(e)))
        finally:
            self.root.after(0, self.progress.stop)
            self.root.after(0, lambda: self.start_btn.config(state=NORMAL))

    def open_output_folder(self):
        if not self.output_paths:
            return
        folder = str(self.output_paths[1].parent)
        if os.name == "nt":
            os.startfile(folder)
        else:
            os.system(f'xdg-open "{folder}"')


def main():
    root = Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
