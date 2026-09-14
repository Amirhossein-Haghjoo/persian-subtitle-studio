"""Glue code: video -> English SRT -> Persian SRT."""
from pathlib import Path

from . import transcriber, translator


def run_pipeline(input_path: Path, api_key: str, model_size: str, device: str,
                  language: str = "en", gemini_model: str = "gemini-2.5-flash", log=print):
    input_path = Path(input_path)
    en_srt_path = input_path.with_suffix("").with_suffix(".en.srt")
    fa_srt_path = input_path.with_suffix("").with_suffix(".fa.srt")

    cues = transcriber.transcribe(input_path, model_size, device, language, log=log)
    if not cues:
        raise RuntimeError("هیچ گفتاری در ویدیو تشخیص داده نشد.")

    transcriber.write_srt(cues, en_srt_path)
    log(f"زیرنویس انگلیسی ذخیره شد: {en_srt_path}")

    english_texts = [c[3] for c in cues]
    persian_texts = translator.translate_lines(english_texts, api_key, gemini_model, log=log)

    if len(persian_texts) != len(cues):
        raise RuntimeError("تعداد خطوط ترجمه‌شده با اصل مطابقت ندارد؛ برای جلوگیری از خرابی زمان‌بندی متوقف شد.")

    fa_cues = [(c[0], c[1], c[2], persian_texts[i]) for i, c in enumerate(cues)]
    transcriber.write_srt(fa_cues, fa_srt_path)
    log(f"زیرنویس فارسی ذخیره شد: {fa_srt_path}")

    return en_srt_path, fa_srt_path
