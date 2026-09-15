"""Translate subtitle lines using Gemini with Multi-Key & Multi-Model Fallback.

The model is asked to wrap transliterated technical terms in «...» markers.
Those markers are NOT meant to be shown to the user: they are converted either
into subtitle styling tags (for the .fa.srt file) or stripped completely (for
the text-to-speech track), via to_styled() / to_plain().
"""
import contextlib
import re
import threading
import time

# Markers the model wraps transliterated terms in. Guillemets are used instead
# of ** because they are a normal part of Persian typography, so even if a
# marker ever leaks through unprocessed it looks intentional rather than broken.
TERM_OPEN = "«"
TERM_CLOSE = "»"
_TERM_RE = re.compile(r"«([^»]{1,60})»")

# Explicit RTL embedding. Keeps neutral characters (digits, punctuation,
# brackets) anchored on the correct side of each line.
RLE = "\u202b"  # RIGHT-TO-LEFT EMBEDDING
PDF = "\u202c"  # POP DIRECTIONAL FORMATTING

DEFAULT_MODELS = ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.1-flash-lite"]

# Free-tier Gemini keys are limited to a handful of requests per minute.
MIN_SECONDS_BETWEEN_REQUESTS = 4.0
# The SDK has NO default timeout: a half-open connection (common behind
# filtering or an unstable VPN) would otherwise hang the app forever.
REQUEST_TIMEOUT_SECONDS = 120
_last_request_time = 0.0


class TranslationError(RuntimeError):
    """Raised for known/expected translation failures, with a Persian message."""


# --------------------------------------------------------------------------
# Text post-processing
# --------------------------------------------------------------------------

def _scrub(text: str) -> str:
    """Removes markdown junk and stray directional marks from a model reply."""
    if not text:
        return ""
    text = re.sub(r"\*{1,3}", "", text)
    text = re.sub(r"_{2,3}", "", text)
    text = text.replace("`", "")
    text = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def to_plain(text: str, wrap_rtl: bool = False) -> str:
    """Returns the line with term markers removed — used for TTS and previews."""
    text = _TERM_RE.sub(r"\1", _scrub(text))
    text = text.replace(TERM_OPEN, "").replace(TERM_CLOSE, "").strip()
    if wrap_rtl and text:
        text = RLE + text + PDF
    return text


def to_styled(text: str, style: str = "both", color: str = "#FFC857",
              wrap_rtl: bool = True) -> str:
    """Returns the line with term markers converted into SRT styling tags.

    style: "color", "bold", "both", or "none".
    Players that understand SRT markup (VLC, MPV, PotPlayer) render the
    transliterated terms differently from the translated words, so the viewer
    can tell "this word is the English term written in Persian" apart from an
    actual translation.
    """
    text = _scrub(text)
    if not text:
        return ""

    if style == "none":
        return to_plain(text, wrap_rtl=wrap_rtl)

    def _wrap(match):
        term = match.group(1).strip()
        if not term:
            return ""
        if style in ("color", "both"):
            term = f'<font color="{color}">{term}</font>'
        if style in ("bold", "both"):
            term = f"<b>{term}</b>"
        return term

    text = _TERM_RE.sub(_wrap, text)
    # Any unmatched stray marker gets dropped rather than shown.
    text = text.replace(TERM_OPEN, "").replace(TERM_CLOSE, "").strip()

    if wrap_rtl and text:
        text = RLE + text + PDF
    return text


# --------------------------------------------------------------------------
# API calling with key/model fallback
# --------------------------------------------------------------------------

def _wait_for_rate_limit():
    global _last_request_time
    wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.time() - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.time()


@contextlib.contextmanager
def _heartbeat(log, label):
    """Emits a 'still waiting' line every few seconds so a slow API call never
    looks like a frozen program."""
    stop = threading.Event()
    started = time.time()

    def _tick():
        while not stop.wait(5.0):
            log(f"\r  {label}... ({int(time.time() - started)} ثانیه)")

    thread = threading.Thread(target=_tick, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)


def _classify_error(exc) -> str:
    """Returns one of: 'quota', 'model', 'auth', 'network', 'safety', 'other'."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()

    if "resourceexhausted" in name or "429" in msg or "quota" in msg or "rate limit" in msg:
        return "quota"
    if ("404" in msg or "not found" in msg or "no longer available" in msg
            or "is not supported" in msg):
        return "model"
    if ("permissiondenied" in name or "unauthenticated" in name or "401" in msg
            or "403" in msg or "api key" in msg or "api_key" in msg
            or "user location is not supported" in msg):
        return "auth"
    if ("deadlineexceeded" in name or "serviceunavailable" in name
            or "connection" in name or "timeout" in msg or "timed out" in msg
            or "503" in msg or "500" in msg or "unavailable" in msg
            or "failed to resolve" in msg or "network" in msg or "ssl" in msg):
        return "network"
    if "safety" in msg or "blocked" in msg or "recitation" in msg:
        return "safety"
    return "other"


def _extract_text(response) -> str:
    """Safely pulls text out of a Gemini response, with clear errors when the
    reply was blocked or empty instead of raising an opaque ValueError."""
    text = getattr(response, "text", None)
    if text:
        return text.strip()

    feedback = getattr(response, "prompt_feedback", None)
    reason = getattr(feedback, "block_reason", None)
    if reason:
        raise TranslationError(
            f"سرویس Gemini این بخش از متن را به دلیل فیلترهای محتوایی رد کرد (دلیل: {reason})."
        )

    for cand in (getattr(response, "candidates", None) or []):
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        joined = "".join(getattr(p, "text", "") or "" for p in parts).strip()
        if joined:
            return joined
        finish = getattr(cand, "finish_reason", None)
        if finish and str(finish).upper() not in ("STOP", "FINISH_REASON_STOP", "1"):
            raise TranslationError(f"پاسخ Gemini ناقص برگشت (finish_reason: {finish}).")
    return ""


def _execute_with_fallback(api_keys, models_priority, prompt, log, max_rounds: int = 2):
    """Tries every (model, key) combination, then retries the whole cycle for
    transient failures before giving up."""
    try:
        import google.generativeai as genai
    except ImportError as e:
        raise TranslationError(
            "کتابخانه‌ی google-generativeai نصب نیست. با دستور "
            "«pip install -r requirements.txt» آن را نصب کن."
        ) from e

    last_error = None
    auth_failures = 0
    attempts = 0

    for round_idx in range(max_rounds):
        for model_name in models_priority:
            for key_idx, key in enumerate(api_keys):
                attempts += 1
                _wait_for_rate_limit()
                try:
                    genai.configure(api_key=key.strip())
                    model = genai.GenerativeModel(model_name)
                    with _heartbeat(log, f"در انتظار پاسخ {model_name}"):
                        response = model.generate_content(
                            prompt,
                            request_options={"timeout": REQUEST_TIMEOUT_SECONDS},
                        )
                    return _extract_text(response)
                except TranslationError:
                    raise
                except Exception as e:
                    last_error = e
                    kind = _classify_error(e)
                    label = f"{model_name} / کلید {key_idx + 1}"

                    if kind == "auth":
                        auth_failures += 1
                        log(f"  ⚠️ کلید {key_idx + 1} نامعتبر است یا در این منطقه دسترسی ندارد.")
                    elif kind == "model":
                        log(f"  ⚠️ مدل {model_name} در دسترس نیست؛ مدل بعدی امتحان می‌شود.")
                        break  # no point trying other keys with a dead model
                    elif kind == "quota":
                        log(f"  ⚠️ سهمیه‌ی {label} تمام شد؛ گزینه‌ی بعدی امتحان می‌شود.")
                    elif kind == "network":
                        log(f"  ⚠️ مشکل اتصال با {label}. تلاش دوباره...")
                        time.sleep(3)
                    else:
                        log(f"  ⚠️ خطای غیرمنتظره با {label}: {e}")

        if round_idx + 1 < max_rounds:
            log("  ⏳ همه‌ی گزینه‌ها ناموفق بودند؛ ۲۰ ثانیه صبر و تلاش دوباره...")
            time.sleep(20)

    if auth_failures and auth_failures >= attempts:
        raise TranslationError(
            "هیچ‌کدام از کلیدهای Gemini معتبر نبودند یا از منطقه‌ی جغرافیایی فعلی دسترسی ندارند.\n"
            "کلیدها را در aistudio.google.com بررسی کن، و اگر در منطقه‌ی محدودشده هستی با VPN امتحان کن."
        )

    raise TranslationError(f"تمام کلیدها و مدل‌ها ناموفق بودند. آخرین خطا: {last_error}")


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def _chunk_texts(texts, max_lines: int, max_chars: int):
    """Groups lines into chunks bounded by both line count and character budget."""
    chunks, current, current_chars = [], [], 0
    for t in texts:
        t_len = len(t) + 10
        if current and (len(current) >= max_lines or current_chars + t_len > max_chars):
            chunks.append(current)
            current, current_chars = [], 0
        current.append(t)
        current_chars += t_len
    if current:
        chunks.append(current)
    return chunks


def _build_prompt(numbered_input: str, context_block: str = "") -> str:
    return (
        "You are translating subtitles from English to natural, fluent Persian (Farsi).\n"
        f"{context_block}"
        "Rules:\n"
        "- Keep the EXACT SAME number of lines as input, with the same numbering.\n"
        "- Output ONLY the numbered Persian translations, nothing else.\n"
        "- Output PLAIN TEXT. No markdown, no asterisks, no backticks, no bold markers.\n"
        "- Write everything using ONLY Persian (Arabic) script. Do NOT use Latin letters.\n"
        "- Transliterate product/tool/technical names phonetically into Persian script "
        f"and wrap ONLY the transliterated term in {TERM_OPEN}...{TERM_CLOSE}, like this: "
        f"'Burp Suite' -> {TERM_OPEN}برپ سوییت{TERM_CLOSE}, "
        f"'Proxy' -> {TERM_OPEN}پروکسی{TERM_CLOSE}, "
        f"'Chromium' -> {TERM_OPEN}کرومیوم{TERM_CLOSE}, "
        f"'SQL injection' -> {TERM_OPEN}اس‌کیوال اینجکشن{TERM_CLOSE}.\n"
        "- Wrap ONLY the term itself, never the surrounding Persian words or punctuation.\n"
        "- Ordinary translated words must NOT be wrapped.\n"
        "- Use Persian digits (۰-۹) instead of Latin digits, including port numbers "
        f"('Port 8080' -> {TERM_OPEN}پورت ۸۰۸۰{TERM_CLOSE}).\n\n"
        "Translate these lines:\n"
        f"{numbered_input}"
    )


def translate_lines(texts, api_keys, models_priority=None,
                    batch_size: int = 60, chunk_char_budget: int = 4000,
                    log=print, progress=None):
    """Translates subtitle lines to Persian, preserving order and line count.

    Returns raw lines that still contain «term» markers. Use to_styled() for the
    subtitle file and to_plain() for text-to-speech.

    progress, if given, is called as progress(fraction, extra={...}).
    """
    if isinstance(api_keys, str):
        api_keys = [k.strip() for k in api_keys.split(",") if k.strip()]
    api_keys = [k.strip() for k in (api_keys or []) if k and k.strip()]
    if not api_keys:
        raise TranslationError("حداقل یک کلید Gemini API باید وارد شود.")

    if isinstance(models_priority, str):
        models_priority = [m.strip() for m in models_priority.split(",") if m.strip()]
    models_priority = [m for m in (models_priority or []) if m] or list(DEFAULT_MODELS)

    total = len(texts)
    if total == 0:
        return []

    chunks = _chunk_texts(texts, max_lines=batch_size, max_chars=chunk_char_budget)
    multi_chunk = len(chunks) > 1
    full_transcript = "\n".join(texts) if multi_chunk else None

    translated = []
    start = 0

    for chunk in chunks:
        numbered_input = "\n".join(f"{i+1}. {t}" for i, t in enumerate(chunk))

        context_block = ""
        if multi_chunk:
            context_block = (
                "For consistent terminology across the whole video, here is the full "
                "English transcript as CONTEXT ONLY. Do not translate it; only translate "
                "the numbered lines at the end.\n"
                f"--- FULL TRANSCRIPT (context only) ---\n{full_transcript}\n"
                "--- END CONTEXT ---\n\n"
            )

        log(f"در حال ترجمه‌ی خطوط {start+1} تا {start+len(chunk)} از {total}...")
        raw = _execute_with_fallback(api_keys, models_priority,
                                     _build_prompt(numbered_input, context_block), log)

        lines_out = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^\d+[\.\)]\s*(.*)$", line)
            lines_out.append(m.group(1).strip() if m else line)

        if len(lines_out) != len(chunk):
            log(f"  ⚠️ تعداد خطوط برگشتی ({len(lines_out)}) با ورودی ({len(chunk)}) "
                "نخواند؛ این بخش خط‌به‌خط ترجمه می‌شود...")
            lines_out = _translate_one_by_one(chunk, api_keys, models_priority,
                                              log, progress, start, total)

        translated.extend(lines_out)
        start += len(chunk)
        if progress:
            progress(min(start / total, 1.0), extra={"done": start, "total": total})

    return translated


def _translate_one_by_one(chunk, api_keys, models_priority, log, progress, start, total):
    """Fallback path when a batch comes back with a mismatched line count."""
    out = []
    for j, t in enumerate(chunk):
        prompt = (
            "Translate this single subtitle line from English to natural, fluent Persian.\n"
            "Output ONLY the translation, plain text, Persian script only.\n"
            f"Wrap transliterated technical terms in {TERM_OPEN}...{TERM_CLOSE}.\n\n"
            f"{t}"
        )
        try:
            out.append(_execute_with_fallback(api_keys, models_priority, prompt, log).strip())
        except TranslationError as e:
            log(f"  ⚠️ ترجمه‌ی یک خط ناموفق بود، متن انگلیسی حفظ شد. ({e})")
            out.append(t)
        if progress:
            done = start + j + 1
            progress(min(done / total, 1.0), extra={"done": done, "total": total})
    return out
