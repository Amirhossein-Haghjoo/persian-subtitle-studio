"""Glue code: video -> English SRT -> Persian SRT -> Persian audio.

Every stage is optional and driven by the `outputs` set, so the UI can let the
user pick exactly which deliverables they want. Progress is reported per stage
(each stage runs 0% -> 100% on its own) rather than as one blended number.
"""
from pathlib import Path
from typing import Iterable, List, Optional, Union

from . import transcriber, translator

# Output identifiers used by both the pipeline and the UI.
OUT_EN_SRT = "en_srt"
OUT_FA_SRT = "fa_srt"
OUT_FA_AUDIO = "fa_audio"

OUTPUT_LABELS = {
    OUT_EN_SRT: "زیرنویس انگلیسی",
    OUT_FA_SRT: "زیرنویس فارسی",
    OUT_FA_AUDIO: "صوت فارسی",
}

# Stage identifiers reported through the progress callback.
STAGE_LABELS = {
    "transcribe": "تبدیل صوت به متن انگلیسی",
    "level_en": "ساده‌سازی زیرنویس انگلیسی طبق سطح زبان",
    "translate": "تبدیل متن انگلیسی به فارسی",
    "tts": "تبدیل متن فارسی به صوت",
    "done": "پایان",
}

# "original" means: use the transcript exactly as Whisper produced it.
ENGLISH_LEVELS = ["original"] + translator.CEFR_LEVELS
PERSIAN_STYLES = ("literal", "friendly")


class PipelineError(RuntimeError):
    """Raised for known/expected pipeline failures, with a Persian message."""


def _as_list(value: Union[str, Iterable, None]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _check_writable(path: Path):
    """Fails early with a clear message instead of after a long transcription."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        probe = path.parent / ".pearsian_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except PermissionError as e:
        raise PipelineError(
            f"اجازه‌ی نوشتن فایل خروجی در این پوشه وجود ندارد: {path.parent}\n"
            "ویدیو را در پوشه‌ای دیگر (مثلاً Desktop) بگذار یا برنامه را با دسترسی مدیر اجرا کن."
        ) from e
    except OSError as e:
        raise PipelineError(f"پوشه‌ی خروجی قابل استفاده نیست: {path.parent} ({e})") from e


def run_pipeline(input_path: Path,
                 api_keys: Union[str, List[str]],
                 model_size: str = "small",
                 device: str = "cpu",
                 language: str = "en",
                 models_priority: Optional[Union[str, List[str]]] = None,
                 outputs: Optional[Iterable[str]] = None,
                 english_level: str = "original",
                 persian_style: str = "literal",
                 highlight_style: str = "both",
                 highlight_color: str = "#FFC857",
                 tts_engine: str = "auto",
                 tts_voice: str = "fa-IR-DilaraNeural",
                 tts_rate: str = "+0%",
                 tts_mode: str = "timed",
                 log=print,
                 progress=None):
    """Runs the requested stages and returns a dict of {output_id: Path}.

    progress, if given, is called as progress(stage, fraction, extra) where
    `stage` is one of STAGE_LABELS and `fraction` is that stage's own 0..1.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise PipelineError(f"فایل ورودی پیدا نشد: {input_path}")
    if input_path.stat().st_size == 0:
        raise PipelineError("فایل ورودی خالی است.")

    outputs = set(outputs or [OUT_EN_SRT, OUT_FA_SRT])
    unknown = outputs - set(OUTPUT_LABELS)
    if unknown:
        raise PipelineError(f"خروجی ناشناخته درخواست شد: {', '.join(sorted(unknown))}")
    if not outputs:
        raise PipelineError("حداقل یک خروجی باید انتخاب شود.")

    base = input_path.with_suffix("")
    paths = {
        OUT_EN_SRT: base.with_suffix(".en.srt"),
        OUT_FA_SRT: base.with_suffix(".fa.srt"),
        OUT_FA_AUDIO: base.with_suffix(".fa.mp3"),
    }
    _check_writable(paths[OUT_EN_SRT])

    # Persian subtitle and Persian audio both require the translation stage.
    needs_translation = bool(outputs & {OUT_FA_SRT, OUT_FA_AUDIO})
    needs_leveling = OUT_EN_SRT in outputs and english_level not in (None, "original")
    needs_gemini = needs_translation or needs_leveling

    api_keys_list = _as_list(api_keys)
    if needs_gemini and not api_keys_list:
        raise PipelineError("لطفاً حداقل یک کلید Gemini API معتبر وارد کنید.")

    models_list = _as_list(models_priority) or list(translator.DEFAULT_MODELS)

    # Fail fast on a missing TTS engine rather than after a long transcription.
    if OUT_FA_AUDIO in outputs:
        from . import tts
        if not tts.available_engines():
            raise PipelineError(
                "برای ساخت «صوت فارسی» باید یک موتور تبدیل متن به گفتار نصب باشد.\n"
                "با دستور «pip install edge-tts» نصبش کن و دوباره تلاش کن."
            )

    results = {}

    def _stage(name):
        def _cb(frac, extra=None):
            if progress:
                progress(name, max(0.0, min(frac, 1.0)), extra or {})
        return _cb

    # ---- Stage 1: speech -> English text -------------------------------
    log(f"▶ مرحله ۱: {STAGE_LABELS['transcribe']}")
    transcribe_cb = _stage("transcribe")
    try:
        cues = transcriber.transcribe(
            input_path, model_size, device, language, log=log,
            progress=lambda frac, stage=None, extra=None: transcribe_cb(frac, extra),
        )
    except transcriber.TranscriptionError:
        raise
    except MemoryError as e:
        raise PipelineError(
            "حافظه‌ی سیستم برای این مدل کافی نبود. مدل کوچک‌تری (small یا base) انتخاب کن."
        ) from e
    except Exception as e:
        raise PipelineError(f"خطای غیرمنتظره در مرحله‌ی تبدیل صدا به متن: {e}") from e

    if not cues:
        raise PipelineError(
            "هیچ گفتاری در فایل تشخیص داده نشد. مطمئن شو فایل صدا دارد و زبان گفتار انگلیسی است."
        )
    transcribe_cb(1.0, {"done": len(cues), "total": len(cues)})

    if OUT_EN_SRT in outputs:
        english_cues = cues
        if needs_leveling:
            log(f"▶ مرحله: {STAGE_LABELS['level_en']} ({english_level})")
            level_cb = _stage("level_en")
            try:
                leveled = translator.simplify_english_lines(
                    texts=[c[3] for c in cues],
                    api_keys=api_keys_list,
                    models_priority=models_list,
                    level=english_level,
                    log=log,
                    progress=lambda frac, extra=None: level_cb(frac, extra),
                )
            except translator.TranslationError:
                raise
            except Exception as e:
                raise PipelineError(f"خطای غیرمنتظره در ساده‌سازی زیرنویس انگلیسی: {e}") from e

            if len(leveled) != len(cues):
                raise PipelineError(
                    "تعداد خطوط ساده‌شده با اصل مطابقت ندارد؛ برای جلوگیری از خرابی زمان‌بندی متوقف شد."
                )
            level_cb(1.0, {"done": len(cues), "total": len(cues)})
            english_cues = [(c[0], c[1], c[2], leveled[i]) for i, c in enumerate(cues)]

        transcriber.write_srt(english_cues, paths[OUT_EN_SRT])
        results[OUT_EN_SRT] = paths[OUT_EN_SRT]
        log(f"✔ زیرنویس انگلیسی ذخیره شد: {paths[OUT_EN_SRT]}")

    if not needs_translation:
        if progress:
            progress("done", 1.0, {})
        return results

    # ---- Stage 2: English text -> Persian text -------------------------
    # NOTE: this always uses the ORIGINAL transcript (`cues`), never the
    # CEFR-leveled English text above — the two settings are independent.
    log(f"▶ مرحله ۲: {STAGE_LABELS['translate']}")
    translate_cb = _stage("translate")
    try:
        raw_persian = translator.translate_lines(
            texts=[c[3] for c in cues],
            api_keys=api_keys_list,
            models_priority=models_list,
            style=persian_style,
            log=log,
            progress=lambda frac, extra=None: translate_cb(frac, extra),
        )
    except translator.TranslationError:
        raise
    except Exception as e:
        raise PipelineError(f"خطای غیرمنتظره در مرحله‌ی ترجمه: {e}") from e

    if len(raw_persian) != len(cues):
        raise PipelineError(
            "تعداد خطوط ترجمه‌شده با اصل مطابقت ندارد؛ برای جلوگیری از خرابی زمان‌بندی متوقف شد."
        )
    translate_cb(1.0, {"done": len(cues), "total": len(cues)})

    if OUT_FA_SRT in outputs:
        styled = [translator.to_styled(t, style=highlight_style, color=highlight_color)
                  for t in raw_persian]
        fa_cues = [(c[0], c[1], c[2], styled[i]) for i, c in enumerate(cues)]
        transcriber.write_srt(fa_cues, paths[OUT_FA_SRT])
        results[OUT_FA_SRT] = paths[OUT_FA_SRT]
        log(f"✔ زیرنویس فارسی ذخیره شد: {paths[OUT_FA_SRT]}")

    # ---- Stage 3: Persian text -> Persian speech -----------------------
    if OUT_FA_AUDIO in outputs:
        from . import tts
        log(f"▶ مرحله ۳: {STAGE_LABELS['tts']}")
        tts_cb = _stage("tts")
        spoken = [translator.to_plain(t) for t in raw_persian]
        try:
            tts.synthesize_cues(
                cues=cues, texts=spoken, out_path=paths[OUT_FA_AUDIO],
                engine=tts_engine, voice=tts_voice, rate=tts_rate, mode=tts_mode,
                log=log, progress=lambda frac, extra=None: tts_cb(frac, extra),
            )
        except tts.TTSError:
            raise
        except Exception as e:
            raise PipelineError(f"خطای غیرمنتظره در مرحله‌ی ساخت صوت فارسی: {e}") from e

        results[OUT_FA_AUDIO] = paths[OUT_FA_AUDIO]
        tts_cb(1.0, {"done": len(cues), "total": len(cues)})

    if progress:
        progress("done", 1.0, {})
    return results
