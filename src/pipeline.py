"""Glue code: video -> English SRT -> Persian SRT."""
from pathlib import Path
from typing import List, Union, Optional

from . import transcriber, translator

# درصد پیشرفت هر مرحله در نوار پیشرفت کل
TRANSCRIBE_WEIGHT = 0.7
TRANSLATE_WEIGHT = 0.3


class PipelineError(RuntimeError):
    """Raised for known/expected pipeline failures, with a Persian message."""


def run_pipeline(input_path: Path, 
                 api_keys: Union[str, List[str]], 
                 model_size: str, 
                 device: str,
                 language: str = "en", 
                 models_priority: Optional[Union[str, List[str]]] = None,
                 log=print, 
                 progress=None):
    """Runs the full pipeline.

    progress, if given, is called as progress(overall_fraction, extra) where
    overall_fraction is in [0, 1] and extra is a dict with stage-specific details.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise PipelineError(f"فایل ورودی پیدا نشد: {input_path}")

    en_srt_path = input_path.with_suffix("").with_suffix(".en.srt")
    fa_srt_path = input_path.with_suffix("").with_suffix(".fa.srt")

    # تبدیل ورودی کلیدها به لیست (پشتیبانی از رشته جداشده با کاما یا لیست مستقیم)
    if isinstance(api_keys, str):
        api_keys_list = [k.strip() for k in api_keys.split(",") if k.strip()]
    else:
        api_keys_list = [k.strip() for k in api_keys if k.strip()]

    if not api_keys_list:
        raise PipelineError("لطفاً حداقل یک کلید Gemini API معتبر وارد کنید.")

    # تنظیم و آماده‌سازی اولویت مدل‌ها (به‌روزرسانی با مدل‌های معتبر و فعال)
    if models_priority is None:
        models_list = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.5-flash", "gemini-3.0-flash"]
    elif isinstance(models_priority, str):
        models_list = [m.strip() for m in models_priority.split(",") if m.strip()]
    else:
        models_list = models_priority

    def _on_transcribe_progress(frac, stage, extra):
        if progress:
            progress(frac * TRANSCRIBE_WEIGHT, {"stage": "transcribe", **(extra or {})})

    def _on_translate_progress(frac, stage, extra):
        if progress:
            progress(TRANSCRIBE_WEIGHT + frac * TRANSLATE_WEIGHT, {"stage": "translate", **(extra or {})})

    # ۱. مرحله تبدیل صدا به زیرنویس انگلیسی
    try:
        cues = transcriber.transcribe(input_path, model_size, device, language,
                                       log=log, progress=_on_transcribe_progress)
    except transcriber.TranscriptionError:
        raise
    except Exception as e:
        raise PipelineError(f"خطای غیرمنتظره در مرحله‌ی تبدیل صدا به متن: {e}") from e

    if not cues:
        raise PipelineError("هیچ گفتاری در ویدیو تشخیص داده نشد.")

    transcriber.write_srt(cues, en_srt_path)
    log(f"زیرنویس انگلیسی ذخیره شد: {en_srt_path}")

    # ۲. مرحله ترجمه به فارسی (ارسال لیست کلیدها و اولویت مدل‌ها)
    english_texts = [c[3] for c in cues]
    try:
        persian_texts = translator.translate_lines(
            texts=english_texts,
            api_keys=api_keys_list,
            models_priority=models_list,
            log=log,
            progress=_on_translate_progress
        )
    except translator.TranslationError:
        raise
    except Exception as e:
        raise PipelineError(f"خطای غیرمنتظره در مرحله‌ی ترجمه: {e}") from e

    if len(persian_texts) != len(cues):
        raise PipelineError("تعداد خطوط ترجمه‌شده با اصل مطابقت ندارد؛ برای جلوگیری از خرابی زمان‌بندی متوقف شد.")

    # ۳. ساخت و ذخیره فایل زیرنویس فارسی نهایی
    fa_cues = [(c[0], c[1], c[2], persian_texts[i]) for i, c in enumerate(cues)]
    transcriber.write_srt(fa_cues, fa_srt_path)
    log(f"زیرنویس فارسی ذخیره شد: {fa_srt_path}")

    if progress:
        progress(1.0, {"stage": "done"})

    return en_srt_path, fa_srt_path