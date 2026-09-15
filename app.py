import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from src import config, pipeline


class GeminiSettingsFrame(ttk.LabelFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, text="تنظیمات Gemini API و اولویت مدل‌ها", **kwargs)
        
        self.key_entries = []
        self.model_vars = []
        
        self.saved_config = config.load_config()
        
        self.keys_container = ttk.Frame(self)
        self.keys_container.pack(fill="x", padx=5, pady=5)
        
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=5, pady=2)
        
        self.add_key_btn = ttk.Button(btn_frame, text="+ افزودن کلید جدید", command=self.add_key_field)
        self.add_key_btn.pack(side="right", padx=5)
        
        self.save_btn = ttk.Button(btn_frame, text="💾 ذخیره تنظیمات", command=self.save_settings)
        self.save_btn.pack(side="left", padx=5)

        saved_keys = self.saved_config.get("api_keys", [])
        if saved_keys:
            for k in saved_keys:
                self.add_key_field(initial_value=k)
        else:
            self.add_key_field()

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(self, text="اولویت اجرای مدل‌ها (سوییچ خودکار در صورت خطای ۴۲۹):").pack(anchor="w", padx=5)
        
        self.models_frame = ttk.Frame(self)
        self.models_frame.pack(fill="x", padx=5, pady=5)
        
        self.available_models = [
            "gemini-3.6-flash",
            "gemini-3.7-flash",
            "gemini-3.1-flash-lite",
        ]
        saved_priority = self.saved_config.get("models_priority", self.available_models)

        for idx in range(3):
            row = ttk.Frame(self.models_frame)
            row.pack(fill="x", pady=2)
            
            ttk.Label(row, text=f"اولویت {idx + 1}:").pack(side="left", padx=5)
            
            default_val = saved_priority[idx] if idx < len(saved_priority) else self.available_models[0]
            var = tk.StringVar(value=default_val)
            combo = ttk.Combobox(row, textvariable=var, values=self.available_models, state="readonly", width=20)
            combo.pack(side="left", padx=5)
            
            self.model_vars.append(var)

    def _setup_entry_features(self, entry: ttk.Entry):
        """افزودن قابلیت پیست با کیبورد فارسی و منوی راست‌کلیک"""
        
        def paste_from_clipboard(event=None):
            try:
                text = self.clipboard_get()
                if entry.select_present():
                    entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
                entry.insert(tk.INSERT, text)
            except tk.TclError:
                pass
            return "break"

        def copy_to_clipboard(event=None):
            try:
                if entry.select_present():
                    text = entry.get()[entry.index(tk.SEL_FIRST):entry.index(tk.SEL_LAST)]
                    self.clipboard_clear()
                    self.clipboard_append(text)
            except tk.TclError:
                pass
            return "break"

        def cut_to_clipboard(event=None):
            copy_to_clipboard()
            try:
                if entry.select_present():
                    entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
            except tk.TclError:
                pass
            return "break"

        # میانبرهای کیبورد (حتی با کیبورد فارسی)
        entry.bind("<Control-v>", paste_from_clipboard)
        entry.bind("<Control-V>", paste_from_clipboard)
        entry.bind("<<Paste>>", paste_from_clipboard)

        # ساخت منوی راست‌کلیک
        context_menu = tk.Menu(entry, tearoff=0)
        context_menu.add_command(label="برش (Cut)", command=cut_to_clipboard)
        context_menu.add_command(label="کپی (Copy)", command=copy_to_clipboard)
        context_menu.add_command(label="چسباندن (Paste)", command=paste_from_clipboard)
        context_menu.add_separator()
        context_menu.add_command(label="انتخاب همه", command=lambda: entry.select_range(0, tk.END))

        def show_menu(event):
            context_menu.tk_popup(event.x_root, event.y_root)

        entry.bind("<Button-3>", show_menu)  # راست‌کلیک روی ویندوز

    def add_key_field(self, initial_value=""):
        row = ttk.Frame(self.keys_container)
        row.pack(fill="x", pady=2)
        
        lbl = ttk.Label(row, text=f"کلید {len(self.key_entries) + 1}:")
        lbl.pack(side="left", padx=2)
        
        entry = ttk.Entry(row, show="*", width=40)
        entry.insert(0, initial_value)
        entry.pack(side="left", fill="x", expand=True, padx=5)
        
        # فعال‌سازی راست‌کلیک و کلید میانبر
        self._setup_entry_features(entry)

        # دکمه چسباندن (Paste) مستقیم
        paste_btn = ttk.Button(row, text="📋 چسباندن", width=9, 
                               command=lambda e=entry: self._direct_paste(e))
        paste_btn.pack(side="right", padx=2)

        del_btn = ttk.Button(row, text="✕", width=3, command=lambda: self.remove_key_field(row, entry))
        del_btn.pack(side="right", padx=2)
        
        self.key_entries.append(entry)

    def _direct_paste(self, entry: ttk.Entry):
        """چسباندن مستقیم از کلیپ‌بورد سیستم با کلیک دکمه"""
        try:
            text = self.clipboard_get().strip()
            entry.delete(0, tk.END)
            entry.insert(0, text)
        except tk.TclError:
            messagebox.showwarning("هشدار", "هیچ متنی در کلیپ‌بورد کپی نشده است.")

    def remove_key_field(self, row_frame, entry_widget):
        if len(self.key_entries) <= 1:
            messagebox.showwarning("هشدار", "حداقل باید یک کلید API وجود داشته باشد.")
            return
        
        self.key_entries.remove(entry_widget)
        row_frame.destroy()
        self._reindex_labels()

    def _reindex_labels(self):
        for idx, row in enumerate(self.keys_container.winfo_children()):
            for child in row.winfo_children():
                if isinstance(child, ttk.Label):
                    child.config(text=f"کلید {idx + 1}:")

    def get_api_keys(self) -> list[str]:
        return [e.get().strip() for e in self.key_entries if e.get().strip()]

    def get_models_priority(self) -> list[str]:
        priority = []
        for var in self.model_vars:
            model = var.get().strip()
            if model and model not in priority:
                priority.append(model)
        return priority

    def save_settings(self):
        cfg = self.saved_config
        cfg["api_keys"] = self.get_api_keys()
        cfg["models_priority"] = self.get_models_priority()
        
        if config.save_config(cfg):
            messagebox.showinfo("موفقیت", "تنظیمات با موفقیت ذخیره شدند.")
        else:
            messagebox.showerror("خطا", "ذخیره‌سازی تنظیمات با خطا مواجه شد.")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("زیرنویس‌ساز فارسی و مترجم هوشمند")
        self.geometry("680x720")
        
        self.saved_cfg = config.load_config()

        self.gemini_frame = GeminiSettingsFrame(self)
        self.gemini_frame.pack(fill="x", padx=10, pady=5)

        file_frame = ttk.LabelFrame(self, text="فایل ویدیو")
        file_frame.pack(fill="x", padx=10, pady=5)

        self.file_path_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.file_path_var, width=50).pack(side="left", padx=5, pady=5, expand=True, fill="x")
        ttk.Button(file_frame, text="انتخاب ویدیو...", command=self.browse_file).pack(side="right", padx=5, pady=5)

        whisper_frame = ttk.LabelFrame(self, text="تنظیمات Whisper")
        whisper_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(whisper_frame, text="مدل:").pack(side="left", padx=5)
        self.whisper_model_var = tk.StringVar(value=self.saved_cfg.get("whisper_model", "small"))
        ttk.Combobox(whisper_frame, textvariable=self.whisper_model_var, values=["tiny", "base", "small", "medium", "large-v3"], state="readonly", width=10).pack(side="left", padx=5)

        ttk.Label(whisper_frame, text="دستگاه:").pack(side="left", padx=5)
        self.device_var = tk.StringVar(value=self.saved_cfg.get("device", "cpu"))
        ttk.Combobox(whisper_frame, textvariable=self.device_var, values=["cpu", "cuda"], state="readonly", width=10).pack(side="left", padx=5)

        self.start_btn = ttk.Button(self, text="شروع پردازش", command=self.start_processing)
        self.start_btn.pack(pady=10)

        self.progress_bar = ttk.Progressbar(self, mode="determinate")
        self.progress_bar.pack(fill="x", padx=10, pady=5)

        log_frame = ttk.LabelFrame(self, text="روند کار")
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.log_text = tk.Text(log_frame, wrap="word", height=12)
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

    def browse_file(self):
        filename = filedialog.askopenfilename(filetypes=[("Media Files", "*.mp4 *.mkv *.avi *.mp3 *.wav *.m4a")])
        if filename:
            self.file_path_var.set(filename)

    def log(self, message: str):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def update_progress(self, fraction, extra=None):
        self.progress_bar["value"] = fraction * 100

    def start_processing(self):
        video_path = self.file_path_var.get().strip()
        api_keys = self.gemini_frame.get_api_keys()
        models_priority = self.gemini_frame.get_models_priority()

        if not video_path or not os.path.exists(video_path):
            messagebox.showerror("خطا", "لطفاً یک فایل ویدیویی معتبر انتخاب کنید.")
            return

        if not api_keys:
            messagebox.showerror("خطا", "لطفاً حداقل یک کلید API وارد کنید.")
            return

        self.gemini_frame.save_settings()

        self.start_btn.config(state="disabled")
        self.log_text.delete("1.0", tk.END)

        def worker():
            try:
                pipeline.run_pipeline(
                    input_path=Path(video_path),
                    api_keys=api_keys,
                    models_priority=models_priority,
                    model_size=self.whisper_model_var.get(),
                    device=self.device_var.get(),
                    log=self.log,
                    progress=self.update_progress
                )
                messagebox.showinfo("پایان", "ترجمه و ساخت زیرنویس با موفقیت انجام شد!")
            except Exception as e:
                messagebox.showerror("خطا", str(e))
                self.log(f"\n❌ خطا: {e}")
            finally:
                self.start_btn.config(state="normal")

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()