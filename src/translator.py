"""Translate subtitle lines using Gemini with Multi-Key & Multi-Model Fallback."""
import re
import time


class TranslationError(RuntimeError):
    """Raised for known/expected translation failures, with a Persian message."""


# Wrap each subtitle line in an explicit RTL embedding. This keeps any
# remaining neutral characters (punctuation, digits, brackets) anchored on the
# correct side of the line. Set to False if a player shows stray boxes.
WRAP_RTL = True

RLE = "\u202b"  # RIGHT-TO-LEFT EMBEDDING
PDF = "\u202c"  # POP DIRECTIONAL FORMATTING


def fix_srt_bidi(text: str) -> str:
    """Cleans a translated line so it renders correctly as RTL subtitles.

    Markdown markers like ** are neutral characters: bidi moves them to the
    opposite visual edge of the line, which makes correct Persian text look
    scrambled. We strip them entirely, then optionally wrap the line in an
    explicit RTL embedding so punctuation stays where it belongs.
    """
    if not text:
        return text

    # Remove markdown emphasis markers (**bold**, *italic*, __x__, _x_, `code`).
    text = re.sub(r"\*{1,3}", "", text)
    text = re.sub(r"_{2,3}", "", text)
    text = text.replace("`", "")

    # Collapse whitespace and drop directional marks the model may have added.
    text = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", text)
    text = re.sub(r"[ \t]+", " ", text).strip()

    if not text:
        return text

    if WRAP_RTL:
        text = RLE + text + PDF

    return text



def _chunk_texts(texts, max_lines: int = 200):
    chunks = []
    for i in range(0, len(texts), max_lines):
        chunks.append(texts[i:i + max_lines])
    return chunks


def _is_retryable_error(exc) -> bool:
    msg = str(exc).lower()
    return (
        "resourceexhausted" in type(exc).__name__.lower()
        or "429" in msg
        or "quota" in msg
        or "rate limit" in msg
        or "404" in msg
        or "not found" in msg
        or "no longer available" in msg
        or "location" in msg
        or "permission" in msg
    )


def _execute_with_fallback(api_keys: list[str], models_priority: list[str], prompt: str, log):
    import google.generativeai as genai

    last_error = None
    for model_name in models_priority:
        for key_idx, key in enumerate(api_keys):
            try:
                genai.configure(api_key=key.strip())
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                return response
            except Exception as e:
                last_error = e
                if _is_retryable_error(e):
                    log(f" ⚠️ مدل/کلید ({model_name} - کلید {key_idx + 1}) ناموفق بود. علت: {e}")
                    continue
                else:
                    raise TranslationError(f"خطای غیرمنتظره در مدل {model_name}: {e}") from e

    raise TranslationError(f"تمام کلیدها و مدل‌ها ناموفق بودند. آخرین خطا: {last_error}")


def translate_lines(texts: list[str], api_keys: list[str], models_priority: list[str] = None,
                    batch_size: int = 200, log=print, progress=None):
    
    if not api_keys:
        raise TranslationError("حداقل یک کلید Gemini API باید وارد شود.")
    
    if not models_priority:
        models_priority = ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.1-flash-lite"]

    translated = []
    total = len(texts)
    if total == 0:
        return translated

    chunks = _chunk_texts(texts, max_lines=batch_size)
    start = 0

    for chunk in chunks:
        numbered_input = "\n".join(f"{i+1}. {t}" for i, t in enumerate(chunk))

        prompt = (
            "You are translating subtitles from English to natural, fluent Persian (Farsi).\n"
            "Rules:\n"
            "- Keep the EXACT SAME number of lines as input.\n"
            "- Output ONLY the numbered Persian translations, same numbering, nothing else.\n"
            "- Write everything using ONLY Persian (Arabic) script. Do NOT use any Latin letters "
            "anywhere in the output.\n"
            "- Output PLAIN TEXT ONLY. Absolutely NO markdown: no asterisks (*), no underscores, "
            "no backticks, no bold or italic markers of any kind.\n"
            "- Transliterate all product/tool/technical names phonetically into Persian script: "
            "'Burp Suite' -> 'برپ سوییت', 'Proxy' -> 'پروکسی', 'Firefox' -> 'فایرفاکس', "
            "'Chromium' -> 'کرومیوم', 'Intercept' -> 'اینترسپت', 'Open Browser' -> 'اوپن براوزر', "
            "'SQL injection' -> 'اس‌کیوال اینجکشن', 'API' -> 'ای‌پی‌آی'.\n"
            "- Use Persian digits (۰-۹) instead of Latin digits everywhere, including port numbers "
            "(e.g. 'Port 8080' -> 'پورت ۸۰۸۰').\n\n"
            f"{numbered_input}"
        )

        log(f"در حال ترجمه‌ی خطوط {start+1} تا {start+len(chunk)} از {total}...")
        response = _execute_with_fallback(api_keys, models_priority, prompt, log)
        raw = (getattr(response, "text", None) or "").strip()

        lines_out = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^\d+\.\s*(.*)$", line)
            lines_out.append(m.group(1).strip() if m else line)

        if len(lines_out) != len(chunk):
            log(" ⚠️ ناهمخوانی خطوط در چانک؛ اعمال اصلاح خط به خط...")
            while len(lines_out) < len(chunk):
                lines_out.append("...")
            lines_out = lines_out[:len(chunk)]

        translated.extend(lines_out)
        start += len(chunk)
        if progress:
            progress(min(start / total, 1.0), stage="translate", extra={"done": start, "total": total})

    return [fix_srt_bidi(line) for line in translated]