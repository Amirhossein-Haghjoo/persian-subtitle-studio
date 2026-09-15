"""Speech-to-text using faster-whisper, plus SRT writing helpers."""
import contextlib
import socket
import sys
import threading
import time
from pathlib import Path


class TranscriptionError(RuntimeError):
    """Raised for known/expected transcription failures, with a Persian message."""


class _DownloadProgressCapture:
    """Captures tqdm-style download progress (written with \\r to stdout/stderr
    by huggingface_hub) and forwards throttled, readable updates to a log callback,
    so the GUI shows real download progress instead of looking frozen."""

    def __init__(self, log, label="دانلود مدل"):
        self._log = log
        self._label = label
        self._buffer = ""
        self._last_sent = 0.0

    def write(self, s):
        self._buffer += s
        while True:
            idx_r = self._buffer.find("\r")
            idx_n = self._buffer.find("\n")
            candidates = [x for x in (idx_r, idx_n) if x != -1]
            if not candidates:
                break
            idx = min(candidates)
            line, self._buffer = self._buffer[:idx], self._buffer[idx + 1:]
            line = line.strip()
            if not line:
                continue
            looks_like_progress = "%|" in line or "B/s" in line or "it/s" in line
            if looks_like_progress:
                now = time.time()
                if now - self._last_sent >= 0.5:
                    self._last_sent = now
                    self._log(f"\r{self._label}: {line}")

    def flush(self):
        pass


@contextlib.contextmanager
def _capture_download_progress(log, label="دانلود مدل"):
    capture = _DownloadProgressCapture(log, label)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = capture
        sys.stderr = capture
        yield
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr


@contextlib.contextmanager
def _heartbeat(log, label="در حال بارگذاری مدل"):
    """Emits a live 'still working' line once a second while the wrapped block
    runs, so the user always sees something moving even if no download actually
    happens (e.g. the model is already cached and is just being loaded into
    memory) and no tqdm output is produced at all."""
    stop_event = threading.Event()
    start = time.time()

    def _tick():
        while not stop_event.wait(1.0):
            elapsed = time.time() - start
            log(f"\r{label}... ({elapsed:.0f} ثانیه سپری شده)")

    thread = threading.Thread(target=_tick, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=1.0)


_NETWORK_ERROR_TYPES = {
    "ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout",
    "MaxRetryError", "NewConnectionError", "SSLError", "ProxyError",
    "URLError", "gaierror", "OfflineModeIsEnabled", "LocalEntryNotFoundError",
    "HTTPError", "RequestException",
}
_NETWORK_ERROR_HINTS = (
    "failed to resolve", "name or service not known", "getaddrinfo failed",
    "connection refused", "connection aborted", "connection reset",
    "timed out", "timeout", "max retries exceeded", "could not reach",
    "network is unreachable", "temporary failure in name resolution",
    "nodename nor servname", "unable to connect", "ssl", "proxy",
    "couldn't connect", "connection error",
)


def _looks_like_network_error(exc: Exception) -> bool:
    """Best-effort detection of connectivity failures (as opposed to disk,
    memory, or programming errors) when talking to Hugging Face Hub. This is
    heuristic because we don't want a hard dependency on requests/urllib3's
    exact exception classes across versions."""
    seen = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        name = type(cur).__name__
        msg = str(cur).lower()
        if name in _NETWORK_ERROR_TYPES:
            return True
        if any(h in msg for h in _NETWORK_ERROR_HINTS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _quick_connectivity_check(host="huggingface.co", port=443, timeout=4.0) -> bool:
    """A fast, bounded-time TCP-connect probe. Unlike huggingface_hub's own
    request/retry logic (which can take a very long time to give up on
    networks that silently drop packets, common with censorship/filtering),
    this returns a definitive yes/no within `timeout` seconds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _load_model(model_size, device, compute_type, log):
    """Loads the Whisper model, downloading it if needed. If Hugging Face
    Hub is unreachable (blocked, filtered, offline, etc.), falls back to a
    local copy if one is already cached, and otherwise raises a clear,
    actionable error instead of hanging or failing with a cryptic message."""
    from faster_whisper import WhisperModel

    def _use_local_or_fail(reason: str, cause: Exception = None):
        log(f"  {reason} در حال بررسی نسخه‌ی محلی مدل روی سیستم...")
        try:
            with _heartbeat(log, label="در حال بارگذاری از حافظه‌ی محلی"):
                model = WhisperModel(model_size, device=device, compute_type=compute_type,
                                      local_files_only=True)
            log("  یک نسخه‌ی از پیش دانلودشده روی سیستم پیدا شد و از همان استفاده می‌شود.")
            return model
        except Exception as e2:
            raise TranscriptionError(
                "دسترسی به سرور Hugging Face برای دانلود مدل برقرار نشد، و نسخه‌ی از پیش دانلودشده‌ای هم "
                "روی سیستم پیدا نشد.\n"
                "این معمولاً یعنی huggingface.co از شبکه‌ی فعلی‌ات فیلتر/بلاک شده (مثلاً به‌خاطر تحریم یا "
                "محدودیت شبکه). راه‌حل‌های پیشنهادی:\n"
                "  ۱) یک‌بار با VPN فعال برنامه را اجرا کن؛ بعد از یک بار دانلود موفق، مدل روی سیستم ذخیره "
                "می‌شود و دفعات بعد دیگر بدون VPN هم کار می‌کند.\n"
                "  ۲) یا فایل‌های مدل را از یک منبع/آینه‌ی جایگزین به‌صورت دستی در پوشه‌ی کش هاگینگ‌فیس "
                "(معمولاً C:\\Users\\<کاربر>\\.cache\\huggingface\\hub) کپی کن.\n"
                f"جزئیات فنی: {cause}"
            ) from e2

    # Fast, bounded probe first: don't rely on huggingface_hub's own retry/timeout
    # logic, which can hang for a long time on networks that silently drop
    # packets instead of actively refusing the connection.
    log("  در حال بررسی سریع اتصال به سرور مدل...")
    if not _quick_connectivity_check():
        return _use_local_or_fail(
            "اتصال به huggingface.co برقرار نشد (بررسی سریع شبکه ناموفق بود).",
            cause="quick connectivity probe failed within 4s",
        )

    try:
        with _capture_download_progress(log), _heartbeat(log):
            return WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception as e:
        if not _looks_like_network_error(e):
            raise
        return _use_local_or_fail("اتصال به سرور مدل (Hugging Face) در میانه‌ی کار قطع شد.", cause=e)


def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def transcribe(input_path: Path, model_size: str, device: str, language: str,
                log=print, progress=None):
    """Transcribes input_path and returns cue tuples (index, start, end, text).

    progress, if given, is called as progress(fraction, stage="transcribe", extra={...})
    with fraction in [0, 1].

    Note: faster-whisper decodes audio via the bundled PyAV library, so a
    system-wide ffmpeg installation is NOT required.
    """
    try:
        import faster_whisper  # noqa: F401
    except ImportError as e:
        raise TranscriptionError(
            "کتابخانه‌ی faster-whisper نصب نیست. با دستور «pip install -r requirements.txt» آن را نصب کن."
        ) from e

    log(f"در حال بارگذاری مدل Whisper ({model_size})... (اگر بار اول است، ممکن است دانلود مدل طول بکشد)")
    load_start = time.time()
    compute_type = "int8" if device == "cpu" else "float16"
    try:
        model = _load_model(model_size, device, compute_type, log)
    except TranscriptionError:
        raise
    except ValueError as e:
        raise TranscriptionError(
            f"مقدار نامعتبر برای مدل/دستگاه پردازش: {e}"
        ) from e
    except RuntimeError as e:
        msg = str(e).lower()
        if "cuda" in msg or "gpu" in msg:
            raise TranscriptionError(
                "دستگاه پردازش cuda انتخاب شده ولی کارت گرافیک/درایور مناسب پیدا نشد. "
                "«دستگاه پردازش» را روی cpu بگذار."
            ) from e
        raise TranscriptionError(f"بارگذاری مدل Whisper با خطا مواجه شد: {e}") from e
    except OSError as e:
        raise TranscriptionError(
            "دانلود یا بارگذاری مدل Whisper با خطا مواجه شد (احتمالاً قطعی اینترنت یا کمبود فضای دیسک). "
            f"جزئیات: {e}"
        ) from e

    log(f"مدل در {time.time() - load_start:.1f} ثانیه بارگذاری شد.")
    log("در حال تبدیل صدا به متن...")

    try:
        segments, info = model.transcribe(str(input_path), language=language, vad_filter=True)
    except FileNotFoundError as e:
        raise TranscriptionError(f"فایل ورودی پیدا نشد: {input_path}") from e
    except Exception as e:
        msg = str(e).lower()
        if "out of memory" in msg or "oom" in msg:
            raise TranscriptionError(
                "حافظه کافی برای این مدل وجود ندارد. یک مدل کوچک‌تر (مثلاً small یا medium) انتخاب کن."
            ) from e
        if "av" in msg or "decode" in msg or "codec" in msg or "invalid data" in msg:
            raise TranscriptionError(
                "فایل صوتی/ویدیویی قابل خواندن نبود (احتمالاً فایل خراب است یا فرمت آن پشتیبانی نمی‌شود). "
                f"جزئیات: {e}"
            ) from e
        raise TranscriptionError(f"خواندن/پردازش فایل ورودی با خطا مواجه شد: {e}") from e

    total_duration = getattr(info, "duration", None) or 0.0
    cues = []
    start_time = time.time()
    last_end = 0.0

    for i, seg in enumerate(segments, start=1):
        text = seg.text.strip()
        last_end = seg.end
        if total_duration > 0:
            frac = min(max(seg.end / total_duration, 0.0), 1.0)
            elapsed = time.time() - start_time
            eta = (elapsed / frac - elapsed) if frac > 0.02 else None
            if progress:
                progress(frac, stage="transcribe", extra={
                    "current_time": seg.end,
                    "total_time": total_duration,
                    "eta_seconds": eta,
                })
            eta_txt = f" | باقی‌مانده تقریبی: {format_mmss(eta)}" if eta is not None else ""
            log(f"[{format_mmss(seg.end)} / {format_mmss(total_duration)}  ~{frac*100:.0f}%{eta_txt}] "
                + (text if text else "(بی‌صدا)"))
        elif text:
            log(f"  [{format_timestamp(seg.start)}] {text}")

        if not text:
            continue
        cues.append((i, seg.start, seg.end, text))

    if progress and total_duration > 0:
        progress(1.0, stage="transcribe", extra={
            "current_time": total_duration, "total_time": total_duration, "eta_seconds": 0,
        })

    return cues


def write_srt(cues, out_path: Path):
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            for i, (_, start, end, text) in enumerate(cues, start=1):
                f.write(f"{i}\n")
                f.write(f"{format_timestamp(start)} --> {format_timestamp(end)}\n")
                f.write(f"{text}\n\n")
    except OSError as e:
        raise TranscriptionError(f"ذخیره‌ی فایل زیرنویس با خطا مواجه شد: {e}") from e
