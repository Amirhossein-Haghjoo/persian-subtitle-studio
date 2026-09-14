# زیرنویس‌ساز فارسی (Persian Subtitle Maker)

یک برنامه‌ی دسکتاپ ساده که یک ویدیوی انگلیسی می‌گیرد و دو فایل زیرنویس تحویل می‌دهد:
زیرنویس انگلیسی (با [faster-whisper](https://github.com/SYSTRAN/faster-whisper)) و ترجمه‌ی فارسی آن (با Gemini API).

## پیش‌نیازها

- Windows 10/11
- [Python 3.10+](https://www.python.org/downloads/) — هنگام نصب حتماً تیک "Add python.exe to PATH" را بزنید.
- [FFmpeg](https://www.gyan.dev/ffmpeg/builds/) — دانلود build با نام `ffmpeg-release-essentials.zip`، استخراج، و اضافه کردن پوشه‌ی `bin` آن به PATH ویندوز.
- یک کلید رایگان Gemini API از [aistudio.google.com](https://aistudio.google.com) (روی «Get API key» بزنید).

## نصب

```bash
git clone <repository-url>
cd persian-subtitle-app
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## اجرا

```bash
python app.py
```

پنجره‌ی برنامه باز می‌شود:

1. کلید Gemini API را در بالای پنجره وارد و ذخیره کنید (فقط بار اول لازم است، بعد از آن به‌صورت محلی روی سیستم شما ذخیره می‌ماند).
2. با دکمه‌ی «انتخاب ویدیو...» فایل ویدیوی خود را انتخاب کنید.
3. مدل Whisper و دستگاه پردازش (cpu/cuda) را در صورت نیاز تغییر دهید (پیش‌فرض `medium` روی `cpu` مناسب اکثر کاربران است).
4. روی «شروع پردازش» بزنید و روند کار را در پنجره‌ی لاگ دنبال کنید.
5. در پایان دو فایل `video.en.srt` و `video.fa.srt` کنار فایل ویدیوی اصلی ساخته می‌شوند. با دکمه‌ی «باز کردن پوشه‌ی خروجی» می‌توانید مستقیم به آن پوشه بروید.

## ساختار پروژه

```
persian-subtitle-app/
├── app.py              # رابط گرافیکی (tkinter)
├── src/
│   ├── config.py        # ذخیره‌ی محلی کلید API و تنظیمات
│   ├── transcriber.py    # تبدیل گفتار به متن با faster-whisper
│   ├── translator.py     # ترجمه‌ی زیرنویس با Gemini
│   └── pipeline.py       # اتصال مراحل بالا به هم
├── requirements.txt
└── README.md
```

## ساخت یک فایل exe مستقل (اختیاری)

اگر می‌خواهید برنامه را بدون نیاز به نصب پایتون روی سیستم دیگر اجرا کنید:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "PersianSubtitleMaker" app.py
```

فایل exe نهایی در پوشه‌ی `dist/` ساخته می‌شود.

## عیب‌یابی

- **خطای مربوط به ffmpeg**: مطمئن شوید مسیر `...\ffmpeg\bin` به PATH ویندوز اضافه شده و یک بار ترمینال/برنامه را ببندید و دوباره باز کنید.
- **خطای کلید API نامعتبر**: کلید را دوباره از aistudio.google.com بررسی و کپی کنید (بدون فاصله‌ی اضافه).
- **کندی روی ویدیوهای طولانی**: مدل کوچک‌تر (`small` یا `base`) را امتحان کنید یا در صورت داشتن GPU سازگار با CUDA، دستگاه را روی `cuda` بگذارید.
