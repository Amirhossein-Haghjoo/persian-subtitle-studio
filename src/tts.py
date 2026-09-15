"""Optional Persian text-to-speech: turns the translated subtitle cues into a
spoken Persian audio track.

This module is deliberately self-contained. The rest of the app never imports
anything heavy from here unless the user actually enabled the audio output, so
the program still runs fine when the TTS dependencies are not installed.

Two engines are supported:
  - "edge"  : Microsoft Edge neural voices via the `edge-tts` package.
              Free, natural-sounding Persian, but needs an internet connection.
  - "gtts"  : Google Translate TTS via the `gTTS` package. Also online, lower
              quality, used as a fallback.

Two assembly modes:
  - "timed"      : each cue is placed at its real subtitle timestamp, so the
                   audio stays in sync with the video. Needs `pydub` + ffmpeg.
  - "sequential" : cues are simply spoken back-to-back. No extra dependencies
                   beyond the engine, but timing will drift from the video.
"""
import asyncio
import re
import shutil
import tempfile
from pathlib import Path

PERSIAN_VOICES = {
    "زن (دلارا)": "fa-IR-DilaraNeural",
    "مرد (فرید)": "fa-IR-FaridNeural",
}
DEFAULT_VOICE = "fa-IR-DilaraNeural"

# Rough average Persian speaking rate at normal pace, used to estimate how
# long a line will take to speak before we actually render it. This lets us
# ask the TTS engine to naturally speak faster/slower to fit a subtitle's
# time slot, instead of digitally stretching the finished audio (which
# sounds robotic/chipmunk-y once you go much past ~1.15x).
# We correct a clip's speed only after actually measuring it, never by
# guessing from text length (guesses don't account for numbers, punctuation,
# or transliterated terms, and produced inconsistent results).
#
# Philosophy: we do NOT try to force a clip to exactly match its subtitle's
# own duration. If a sentence sounds completely natural at normal pace and
# finishes before the NEXT sentence needs to start, that's fine — we leave it
# alone. We only ever speed a clip up, and only enough to avoid it running
# into the next sentence. We never slow a clip down: playing early and then
# waiting in silence is completely natural; artificially stretching speech
# to fill a time slot is not.
RATE_CORRECTION_TOLERANCE = 0.03     # ignore overrun smaller than 3% (inaudible)
MIN_GAP_BEFORE_NEXT_MS = 120         # always leave at least this much silence before the next line
MAX_CORRECTION_ATTEMPTS = 2          # each attempt re-measures and refines further
MAX_ADAPTIVE_SPEEDUP_PERCENT = 35    # how much faster we'll ask the voice to go, at most
MAX_POST_SPEEDUP = 1.15              # last-resort digital speedup, kept subtle


class TTSError(RuntimeError):
    """Raised for known/expected text-to-speech failures, with a Persian message."""


# --------------------------------------------------------------------------
# Availability checks
# --------------------------------------------------------------------------

def available_engines() -> list:
    """Returns the engine names that are actually importable right now."""
    engines = []
    try:
        import edge_tts  # noqa: F401
        engines.append("edge")
    except ImportError:
        pass
    try:
        import gtts  # noqa: F401
        engines.append("gtts")
    except ImportError:
        pass
    return engines


def has_ffmpeg() -> bool:
    """True when ffmpeg is on PATH, which pydub needs for timed assembly."""
    return shutil.which("ffmpeg") is not None or shutil.which("ffmpeg.exe") is not None


def _require_pydub():
    try:
        from pydub import AudioSegment
        return AudioSegment
    except ImportError as e:
        raise TTSError(
            "برای هم‌زمان‌سازی دقیق صوت با ویدیو به کتابخانه‌ی pydub نیاز است.\n"
            "با دستور «pip install pydub» نصبش کن، یا حالت چیدمان را روی «پشت سر هم» بگذار."
        ) from e


def _trim_silence(audio, silence_thresh_db: float = -42.0, chunk_ms: int = 10):
    """Strips near-silent lead/trail padding that TTS engines often add, so
    consecutive clips don't accumulate uneven, unpredictable gaps."""
    from pydub.silence import detect_leading_silence

    start = detect_leading_silence(audio, silence_threshold=silence_thresh_db, chunk_size=chunk_ms)
    end = detect_leading_silence(audio.reverse(), silence_threshold=silence_thresh_db, chunk_size=chunk_ms)
    trimmed = audio[start: len(audio) - end]
    return trimmed if len(trimmed) > 50 else audio  # never trim a clip down to nothing


def _parse_rate_percent(rate: str) -> float:
    try:
        return float(str(rate).strip().replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


def _measure_ms(path: Path):
    """Returns the real duration of a rendered clip in milliseconds, or None
    if it can't be measured (pydub missing / unreadable file)."""
    try:
        from pydub import AudioSegment
        return len(AudioSegment.from_file(path))
    except Exception:
        return None


def _corrected_rate(measured_ms: float, budget_ms: float, base_rate: str) -> str:
    """Given how long a clip ACTUALLY turned out to be (not a guess), computes
    the rate needed to fit it within budget_ms. Only ever speeds up — if the
    clip is already shorter than the budget, the caller shouldn't call this
    at all (natural pace is always preferred over artificial slowdown)."""
    base_percent = _parse_rate_percent(base_rate)
    ratio = measured_ms / budget_ms
    extra_percent = max(0.0, min(MAX_ADAPTIVE_SPEEDUP_PERCENT, (ratio - 1) * 100))
    combined = max(-50.0, min(60.0, base_percent + extra_percent))
    sign = "+" if combined >= 0 else ""
    return f"{sign}{combined:.0f}%"


# Terminal punctuation that marks a sentence as actually finished. If a cue's
# text does NOT end in one of these, the next cue is treated as a
# continuation of the same sentence rather than a new one.
_SENTENCE_END = ".!؟?…"
# If the gap between two cues is larger than this, don't merge them even if
# punctuation suggests they continue — a long pause usually means the
# speaker actually paused (or Whisper just split awkwardly).
MAX_MERGE_GAP_MS = 900
MAX_GROUP_CUES = 6  # safety cap so one bad transcript can't merge the whole video


def _group_cues(cues, spoken):
    """Groups consecutive cues that are really one sentence split across
    subtitle blocks, so they get spoken as a single fluid clip instead of
    with an artificial pause in the middle of a sentence — most noticeable,
    and worst-sounding, when the two cues are close together in time.

    Returns a list of groups; each group is a list of cue indices.
    """
    groups = []
    current = []

    for i in range(len(cues)):
        text = (spoken[i] or "").strip()
        if not text:
            if current:
                groups.append(current)
                current = []
            continue

        if current:
            prev_i = current[-1]
            prev_text = (spoken[prev_i] or "").strip()
            gap_ms = (cues[i][1] - cues[prev_i][2]) * 1000
            prev_finished = bool(prev_text) and prev_text[-1] in _SENTENCE_END
            can_merge = (not prev_finished) and gap_ms <= MAX_MERGE_GAP_MS and len(current) < MAX_GROUP_CUES
            if not can_merge:
                groups.append(current)
                current = []

        current.append(i)

    if current:
        groups.append(current)
    return groups

def _speak_edge(text: str, out_path: Path, voice: str, rate: str = "+0%"):
    import edge_tts

    async def _run():
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(str(out_path))

    try:
        asyncio.run(_run())
    except RuntimeError:
        # Already inside a running loop (rare in this app, but be safe).
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()


def _speak_gtts(text: str, out_path: Path, voice: str = None, rate: str = None):
    from gtts import gTTS
    gTTS(text=text, lang="fa").save(str(out_path))


_ENGINES = {
    "edge": _speak_edge,
    "gtts": _speak_gtts,
}


def _resolve_engine(engine: str):
    available = available_engines()
    if not available:
        raise TTSError(
            "هیچ موتور تبدیل متن به گفتاری نصب نیست.\n"
            "با دستور «pip install edge-tts» آن را نصب کن (کیفیت بهتر و رایگان)."
        )
    if engine == "auto":
        engine = available[0]
    if engine not in available:
        fallback = available[0]
        engine = fallback
    return engine, _ENGINES[engine]


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def synthesize_cues(cues, out_path: Path, texts=None, engine: str = "auto",
                    voice: str = DEFAULT_VOICE, rate: str = "+0%",
                    mode: str = "timed", fit_to_timing: bool = True,
                    log=print, progress=None):
    """Renders spoken Persian audio for the given cues.

    cues:  list of (index, start_seconds, end_seconds, text) tuples.
    texts: optional list of plain strings to speak instead of the cue text
           (used to pass translator.to_plain() output).
    progress, if given, is called as progress(fraction, extra={...}).

    Returns the path of the written audio file.
    """
    out_path = Path(out_path)
    if not cues:
        raise TTSError("هیچ متنی برای تبدیل به گفتار وجود ندارد.")

    spoken = list(texts) if texts is not None else [c[3] for c in cues]
    if len(spoken) != len(cues):
        raise TTSError("تعداد متن‌های گفتار با تعداد بخش‌های زیرنویس یکسان نیست.")

    engine_name, speak = _resolve_engine(engine)
    log(f"موتور تبدیل متن به گفتار: {engine_name} | صدا: {voice}")

    if mode == "timed":
        if not has_ffmpeg():
            log("  ⚠️ ffmpeg روی سیستم پیدا نشد؛ صوت به‌صورت پشت‌سرهم ساخته می‌شود "
                "(هم‌زمانی دقیق با ویدیو نخواهد داشت).")
            mode = "sequential"
        else:
            _require_pydub()

    adaptive = mode == "timed" and fit_to_timing

    tmp_dir = Path(tempfile.mkdtemp(prefix="pearsian_tts_"))
    try:
        clips = _render_clips(cues, spoken, tmp_dir, speak, voice, rate, log, progress,
                              adaptive=adaptive, engine_name=engine_name)
        if not clips:
            raise TTSError("هیچ بخشی از متن با موفقیت به گفتار تبدیل نشد.")

        if mode == "timed":
            _assemble_timed(clips, cues, out_path, fit_to_timing, log)
        else:
            _assemble_sequential(clips, spoken, out_path, log)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    log(f"صوت فارسی ذخیره شد: {out_path}")
    return out_path


def _render_clips(cues, spoken, tmp_dir, speak, voice, rate, log, progress,
                  adaptive=False, engine_name=""):
    """Renders one clip per GROUP of cues (see _group_cues), not per raw
    subtitle line — so a sentence split across two+ subtitle blocks is
    spoken fluidly as one clip instead of with a pause stuck in the middle
    of it. Returns [(group, clip_path)] where group is a list of the
    original cue indices that clip covers.

    When adaptive=True, each clip is rendered at the base rate, its REAL
    duration is measured against the group's combined time slot, and —
    unless it's already within 3% — it's re-rendered with a corrected rate.
    This repeats up to MAX_CORRECTION_ATTEMPTS times, refining further each
    time based on what the voice actually did, not a text-length guess.
    """
    groups = _group_cues(cues, spoken)
    clips = []
    total = len(cues)
    done = 0
    failures = 0
    corrections = 0
    untouched = 0
    merged_count = sum(1 for g in groups if len(g) > 1)
    if merged_count:
        log(f"  ℹ️ {merged_count} جمله که بین چند زیرنویس شکسته شده بود، یکپارچه خوانده می‌شود.")

    # gTTS ignores the rate parameter entirely, so re-rendering it would be
    # pointless — only Edge's voices actually respond to a rate change.
    can_correct = adaptive and engine_name == "edge"

    # How much time each group actually has before the NEXT group's audio
    # starts — this is the real constraint (avoid two lines overlapping),
    # not the subtitle's own on-screen duration.
    group_start_ms = [cues[g[0]][1] * 1000 for g in groups]

    for gi, group in enumerate(groups):
        text = " ".join((spoken[i] or "").strip() for i in group).strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            done += len(group)
            continue

        if gi + 1 < len(groups):
            budget_ms = group_start_ms[gi + 1] - group_start_ms[gi] - MIN_GAP_BEFORE_NEXT_MS
        else:
            budget_ms = None  # last group: nothing after it to collide with

        clip_path = tmp_dir / f"clip_{gi:05d}.mp3"
        try:
            speak(text, clip_path, voice, rate)
        except Exception as e:
            failures += 1
            log(f"  ⚠️ تبدیل خط {group[0]+1} به گفتار ناموفق بود: {e}")
            if failures > max(5, len(groups) // 10):
                raise TTSError(
                    "تبدیل متن به گفتار بارها ناموفق بود. معمولاً یعنی اتصال اینترنت "
                    f"برقرار نیست یا سرویس در دسترس نیست. آخرین خطا: {e}"
                ) from e
            done += len(group)
            continue

        if can_correct and budget_ms and budget_ms > 100:
            current_rate = rate
            for attempt in range(MAX_CORRECTION_ATTEMPTS):
                measured_ms = _measure_ms(clip_path)
                if not measured_ms:
                    break
                # Natural pace already fits before the next line starts —
                # leave it exactly as it is. No slowdown, no unnecessary tweak.
                if measured_ms <= budget_ms * (1 + RATE_CORRECTION_TOLERANCE):
                    if attempt == 0:
                        untouched += 1
                    break
                fixed_rate = _corrected_rate(measured_ms, budget_ms, current_rate)
                if fixed_rate == current_rate:
                    break  # already at the speed cap, more attempts won't help
                try:
                    speak(text, clip_path, voice, fixed_rate)
                    current_rate = fixed_rate
                    corrections += 1
                except Exception as e:
                    log(f"  ⚠️ اصلاح سرعت خط {group[0]+1} ناموفق بود، نسخه‌ی قبلی نگه داشته شد: {e}")
                    break
        elif can_correct:
            untouched += 1

        if clip_path.exists() and clip_path.stat().st_size > 0:
            clips.append((group, clip_path))

        done += len(group)
        if progress:
            progress(min(done / total, 1.0), extra={"done": done, "total": total})

    if corrections or untouched:
        log(f"  ℹ️ {untouched} جمله با سرعت طبیعی خونده شد، سرعت {corrections} جمله "
            "برای جلوگیری از تداخل با جمله‌ی بعدی کمی تنظیم شد.")

    return clips


def _assemble_timed(clips, cues, out_path, fit_to_timing, log):
    """Places every clip at its group's real subtitle timestamp on a silent
    track. A group spanning multiple cues (a merged sentence) is placed once,
    starting at its first cue. Clips are only ever shortened (never
    stretched) if they'd otherwise run into the next clip's start time."""
    AudioSegment = _require_pydub()

    total_ms = int(max(c[2] for c in cues) * 1000) + 2000
    track = AudioSegment.silent(duration=total_ms)
    overflow = 0

    start_positions = [int(cues[group[0]][1] * 1000) for group, _ in clips]

    for k, (group, clip_path) in enumerate(clips):
        try:
            audio = AudioSegment.from_file(clip_path)
        except Exception as e:
            log(f"  ⚠️ خواندن قطعه‌ی صوتی {group[0]+1} ناموفق بود: {e}")
            continue

        audio = _trim_silence(audio)
        start_ms = start_positions[k]

        # The render-time correction already did the real work of fitting
        # speech before the next line starts. This is only a tiny mop-up for
        # whatever's left, so it stays subtle (never the primary mechanism).
        if fit_to_timing and k + 1 < len(start_positions):
            budget_ms = start_positions[k + 1] - start_ms - MIN_GAP_BEFORE_NEXT_MS
            if budget_ms > 300 and len(audio) > budget_ms:
                speed = min(len(audio) / budget_ms, MAX_POST_SPEEDUP)
                if speed > 1.02:
                    try:
                        audio = audio.speedup(playback_speed=speed)
                    except Exception:
                        pass
                if len(audio) > budget_ms:
                    overflow += 1

        track = track.overlay(audio, position=start_ms)

    if overflow:
        log(f"  ℹ️ {overflow} جمله کمی بلندتر از فاصله‌ی تا جمله‌ی بعدی بود و ممکن است "
            "خیلی جزئی با آن هم‌پوشانی داشته باشد.")

    _export(track, out_path, log)


def _assemble_sequential(clips, spoken, out_path, log):
    """Concatenates group clips back to back with a pause sized to the
    punctuation at the end of each group's LAST line, for a more natural
    spoken rhythm (no pause is inserted inside a merged sentence, since
    that's now a single clip)."""
    try:
        AudioSegment = _require_pydub()
    except TTSError:
        # No pydub: raw mp3 frames can simply be concatenated byte-wise.
        log("  ℹ️ pydub نصب نیست؛ قطعات صوتی به‌صورت خام به هم متصل می‌شوند.")
        with open(out_path, "wb") as out:
            for _, clip_path in clips:
                out.write(clip_path.read_bytes())
        return

    track = AudioSegment.empty()
    for group, clip_path in clips:
        try:
            audio = _trim_silence(AudioSegment.from_file(clip_path))
        except Exception as e:
            log(f"  ⚠️ خواندن یک قطعه‌ی صوتی ناموفق بود: {e}")
            continue
        last_idx = group[-1]
        gap_ms = _gap_for(spoken[last_idx] if last_idx < len(spoken) else "")
        track += audio + AudioSegment.silent(duration=gap_ms)
    _export(track, out_path, log)


def _gap_for(text: str) -> int:
    """A short pause after commas, a longer one after sentence endings."""
    text = (text or "").rstrip()
    if not text:
        return 200
    if text[-1] in ".!؟?":
        return 380
    if text[-1] in "،,:;":
        return 180
    return 250


def _export(track, out_path: Path, log):
    fmt = out_path.suffix.lstrip(".").lower() or "mp3"
    try:
        track.export(str(out_path), format=fmt)
    except PermissionError as e:
        raise TTSError(
            f"اجازه‌ی نوشتن فایل صوتی در این مسیر وجود ندارد: {out_path}\n"
            "اگر فایل در حال پخش است آن را ببند و دوباره تلاش کن."
        ) from e
    except Exception as e:
        raise TTSError(f"ذخیره‌ی فایل صوتی با خطا مواجه شد: {e}") from e
