# ربات روزانه‌ی شورتس یوتیوب (تکنولوژی و دنیای دیجیتال)

هر روز: انتخاب موضوع ← اسکریپت با LLM ← صدا ← تصویر ← ویدیو ← آپلود با عنوان، توضیحات و هشتگ.

- روزهای زوج: **خبر روز** تکنولوژی (از RSS سایت‌های Hacker News، The Verge، Ars Technica، TechCrunch، Wired)
- روزهای فرد: **داستان جالب** از دنیای کامپیوتر و AI
- (با `MODE=news` یا `MODE=story` می‌تونی ثابتش کنی)

## راه‌اندازی (یک‌بار)

### ۱) کلید مدل نویسنده (رایگان با Gemini)
1. به aistudio.google.com برو و با اکانت گوگل لاگین کن.
2. Get API key ← Create API key ← کلید رو کپی کن ← `GEMINI_API_KEY`
3. پلن رایگان برای روزی یک درخواست کاملاً کافیه (محدودیت‌ها و نام مدل‌ها ممکنه عوض بشن؛ اگه مدل پیش‌فرض کار نکرد، نام مدل فعلی رو از AI Studio بگیر و در `LLM_MODEL` بذار).
4. اگه خواستی با Claude کار کنه: `LLM_PROVIDER=claude`، کلید `ANTHROPIC_API_KEY` و فعال‌کردن خط `anthropic` در requirements.txt (پولی است).

### ۲) دسترسی یوتیوب
1. در Google Cloud Console یک پروژه بساز و **YouTube Data API v3** رو فعال کن.
2. OAuth consent screen رو تنظیم کن و **حتماً Publishing status رو روی "In production" بذار**
   (در حالت Testing، refresh token بعد از ۷ روز منقضی می‌شه و ربات از کار می‌افته).
3. Credentials ← Create ← OAuth client ID ← نوع **Desktop app** ← فایل JSON رو با نام `client_secret.json` کنار پروژه بذار.
4. روی کامپیوتر خودت:
   ```
   pip install google-auth-oauthlib
   python get_refresh_token.py
   ```
   با اکانت کانال لاگین کن. سه مقدار چاپ می‌شه: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.

### ۳) GitHub
1. پروژه رو در یک ریپوی **خصوصی** آپلود کن.
2. Settings ← Secrets and variables ← Actions، این‌ها رو اضافه کن:
   `GEMINI_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`
   (اختیاری برای اعلان: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)
3. (پیشنهادی) فونت **Vazirmatn-Bold.ttf** رو در پوشه `fonts/` بذار تا زیرنویس فارسی زیباتر بشه.

### ۴) تست
Actions ← daily-short ← Run workflow ← `dry_run = 1`. ویدیو در Artifacts دانلود می‌شه؛ ببین خوبه یا نه.
بعدش با `dry_run = 0` یک آپلود واقعی (private) بزن و در YouTube Studio چک کن.
وقتی راضی بودی، در `daily.yml` مقدار `PRIVACY` رو `public` کن.

## زمان‌بندی
- cron در GitHub به **UTC** است و ممکنه ۵ تا ۳۰ دقیقه تأخیر داشته باشه.
- برای انتشار دقیق: ربات زودتر اجرا می‌شه و ویدیو با `publishAt` در ساعت دقیق منتشر می‌شه (`PUBLISH_DELAY_MIN`).
- مثال: انتشار ۱۸:۰۰ به وقت تهران = ۱۴:۳۰ UTC ← cron روی `30 13 * * *` و `PUBLISH_DELAY_MIN=60`.

## تنظیمات مهم (متغیرهای محیطی)
| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `LANGUAGE` | `fa` | زبان روایت و کپشن (`en` هم ممکنه) |
| `TTS_VOICE` | `fa-IR-FaridNeural` | برای انگلیسی مثلاً `en-US-GuyNeural` |
| `LLM_PROVIDER` | `gemini` | `gemini` (رایگان) یا `claude` (پولی) |
| `LLM_MODEL` | `gemini-2.5-flash` | نام مدل نویسنده |
| `PRIVACY` | `private` | `public` برای انتشار عمومی |
| `MODE` | `auto` | `news` / `story` |

## نکات
- هزینه: با Gemini و بقیه‌ی اجزا (edge-tts، Pollinations، GitHub Actions) کل پروژه رایگانه. با Claude هم فقط یک فراخوانی در روز می‌شه (چند سنت در ماه).
- کیفیت تصاویر: Pollinations رایگانه ولی گاهی کند یا ناپایداره؛ در این صورت یک پس‌زمینه‌ی گرادیانی جایگزین می‌شه. بعداً می‌تونی تابع `get_image` رو با Flux/Replicate یا OpenAI Images عوض کنی.
- برچسب AI: ویدیو با `containsSyntheticMedia=True` آپلود می‌شه.
- داستان‌ها و خبرها رو هفته‌ای چند بار دستی نگاه کن؛ مدل ممکنه اشتباه کنه و کانال شما مسئول صحت محتواست.
- اگه کانال کاملاً خودکار و شبیه‌به‌هم بشه، یوتیوب ممکنه درآمدزایی رو رد کنه؛ با تغییر قالب، موضوع‌ها و صدا تنوع نگه دار.
