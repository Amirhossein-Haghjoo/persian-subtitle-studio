"""Speech-to-text using faster-whisper, plus SRT writing helpers."""
from pathlib import Path


def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe(input_path: Path, model_size: str, device: str, language: str, log=print):
    """Yields cue tuples (index, start, end, text) as they are produced."""
    from faster_whisper import WhisperModel

    log(f"در حال بارگذاری مدل Whisper ({model_size})...")
    compute_type = "int8" if device == "cpu" else "float16"
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    log("در حال تبدیل صدا به متن...")
    segments, info = model.transcribe(str(input_path), language=language, vad_filter=True)

    cues = []
    for i, seg in enumerate(segments, start=1):
        text = seg.text.strip()
        if not text:
            continue
        cues.append((i, seg.start, seg.end, text))
        log(f"  [{format_timestamp(seg.start)}] {text}")
    return cues


def write_srt(cues, out_path: Path):
    with open(out_path, "w", encoding="utf-8") as f:
        for i, (_, start, end, text) in enumerate(cues, start=1):
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(start)} --> {format_timestamp(end)}\n")
            f.write(f"{text}\n\n")
