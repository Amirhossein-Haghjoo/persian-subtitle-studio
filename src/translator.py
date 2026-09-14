"""Translate a list of subtitle lines to Persian using Gemini, preserving line count."""
import re
import time


def translate_lines(texts, api_key: str, model_name: str = "gemini-2.5-flash",
                     batch_size: int = 40, log=print):
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    translated = []
    total = len(texts)

    for start in range(0, total, batch_size):
        chunk = texts[start:start + batch_size]
        numbered_input = "\n".join(f"{i+1}. {t}" for i, t in enumerate(chunk))

        prompt = (
            "You are translating subtitles from English to natural, fluent Persian (Farsi) "
            "for an educational video. Translate EACH numbered line into Persian.\n"
            "Rules:\n"
            "- Keep the SAME number of lines as input.\n"
            "- Output ONLY the numbered Persian translations, same numbering, nothing else.\n"
            "- Do not merge or split lines.\n"
            "- Keep technical terms accurate; use natural spoken Persian.\n\n"
            f"{numbered_input}"
        )

        log(f"در حال ترجمه‌ی خطوط {start+1} تا {start+len(chunk)} از {total}...")
        response = model.generate_content(prompt)
        raw = (response.text or "").strip()

        lines_out = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^\d+\.\s*(.*)$", line)
            lines_out.append(m.group(1).strip() if m else line)

        if len(lines_out) != len(chunk):
            log(f"  هشدار: تعداد خطوط برگشتی مطابقت نداشت، برای این بخش خط‌به‌خط ترجمه می‌شود...")
            lines_out = []
            for t in chunk:
                single_prompt = (
                    "Translate this single subtitle line from English to natural, "
                    "fluent Persian (Farsi). Output ONLY the translation, nothing else.\n\n"
                    f"{t}"
                )
                r = model.generate_content(single_prompt)
                lines_out.append((r.text or "").strip())
                time.sleep(0.3)

        translated.extend(lines_out)
        time.sleep(0.5)

    return translated
