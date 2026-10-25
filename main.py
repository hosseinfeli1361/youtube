"""
ربات روزانه‌ی یوتیوب شورتس: اخبار تکنولوژی و داستان‌های دنیای دیجیتال
مراحل: انتخاب موضوع -> اسکریپت با Claude -> صدا (edge-tts) -> تصویر -> ویدیو (ffmpeg) -> آپلود
"""
import asyncio
import datetime as dt
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import urllib.parse

import arabic_reshaper
import edge_tts
import feedparser
import requests
from bidi.algorithm import get_display
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ---------------------------------------------------------------- تنظیمات
ROOT = pathlib.Path(__file__).parent
WORK = ROOT / "work"
HISTORY = ROOT / "history.json"

LANG = os.getenv("LANGUAGE", "fa")  # fa, en, ...
VOICE = os.getenv("TTS_VOICE", "fa-IR-FaridNeural")  # یا fa-IR-DilaraNeural
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")  # gemini (رایگان) | claude (پولی)
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash" if LLM_PROVIDER == "gemini" else "claude-sonnet-5-5")
PRIVACY = os.getenv("PRIVACY", "private")  # برای تست private؛ بعداً public
PUBLISH_DELAY_MIN = int(os.getenv("PUBLISH_DELAY_MIN", "0"))  # انتشار دقیق با publishAt
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"  # ۱ = بدون آپلود
MODE = os.getenv("MODE", "auto")  # auto | news | story

W, H = 1080, 1920
RTL = LANG in {"fa", "ar", "he", "ur"}

FEEDS = [
    "https://hnrss.org/frontpage?points=150",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://techcrunch.com/feed/",
    "https://www.wired.com/feed/rss",
]

FONT_CANDIDATES = [
    os.getenv("FONT_PATH", ""),
    str(ROOT / "fonts" / "Vazirmatn-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def log(*a):
    print("[bot]", *a, flush=True)


# ---------------------------------------------------------------- تاریخچه
def load_history():
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(h):
    HISTORY.write_text(json.dumps(h[-300:], ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- ۱) موضوع
def fetch_news(history):
    used = {x.get("link") for x in history}
    items = []
    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:8]:
                link = e.get("link", "")
                if link and link not in used:
                    summary = re.sub(r"<[^>]+>", "", e.get("summary", ""))[:400]
                    items.append({"title": e.get("title", ""), "summary": summary, "link": link})
        except Exception as ex:
            log("feed failed", url, ex)
    random.shuffle(items)
    return items[:25]


def pick_mode():
    if MODE in ("news", "story"):
        return MODE
    return "news" if dt.date.today().toordinal() % 2 == 0 else "story"


# ---------------------------------------------------------------- ۲) اسکریپت با LLM
def call_llm(prompt):
    if LLM_PROVIDER == "claude":
        import anthropic

        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=LLM_MODEL, max_tokens=3000, messages=[{"role": "user", "content": prompt}]
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    # Gemini از طریق REST (بدون نیاز به پکیج اضافه)
    import time

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{LLM_MODEL}:generateContent?key={os.environ['GEMINI_API_KEY']}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
    }
    r = None
    for attempt in range(3):
        r = requests.post(url, json=body, timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(15 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    r.raise_for_status()


def make_plan(mode, news, history):
    past = [x.get("topic_key", "") for x in history[-60:]]
    lang_name = {"fa": "Persian (Farsi)", "en": "English"}.get(LANG, LANG)

    if mode == "news":
        material = json.dumps(news, ensure_ascii=False)
        task = (
            "From the news items below, pick the single most interesting and significant tech/AI story "
            "for a general audience. Use ONLY facts present in the item; do not invent numbers, quotes or details. "
            f"Items:\n{material}"
        )
    else:
        task = (
            "Tell a fascinating, TRUE story from the world of computers, the internet, hacking history, "
            "AI or digital culture (e.g. famous bugs, first-of-their-kind events, legendary products, "
            "surprising origins). Choose only well-documented facts you are highly confident about; "
            "avoid exact dates/numbers if unsure."
        )

    prompt = f"""You write scripts for a daily YouTube Short (vertical, 35-50 seconds).
{task}

Avoid these already-covered topics: {past}

Rules:
- Narration language: {lang_name}. Natural spoken style, strong hook in the first sentence, ends with a punchy closing line.
- Total narration about 90-120 words, split into 5-7 scenes.
- Each scene: "narration" (spoken), "caption" (on-screen text, max 7 words, same language),
  "image_prompt" (ENGLISH, vivid cinematic description, no text/letters in image, no real people's faces, no logos).
- "title": max 90 chars, curiosity-driven, same language, no hashtags.
- "description": 2-3 sentences + (for news) a line with the source link. No hashtags inside.
- "hashtags": 4-5 relevant hashtags (include a mix of Persian/English as fits), without #Shorts.
- "topic_key": short English slug of the topic.
- "source_link": the link of the chosen item for news, else "".

Return ONLY valid JSON:
{{"title":"","description":"","hashtags":["#..."],"topic_key":"","source_link":"",
"scenes":[{{"narration":"","caption":"","image_prompt":""}}]}}"""

    text = call_llm(prompt)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    plan = json.loads(text)
    assert 3 <= len(plan["scenes"]) <= 9, "unexpected scene count"
    return plan


# ---------------------------------------------------------------- ۳) صدا
async def _tts(text, path):
    await edge_tts.Communicate(text, VOICE, rate="+5%").save(str(path))


def audio_duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    )
    return float(out.decode().strip())


# ---------------------------------------------------------------- ۴) تصویر
def shape(text):
    return get_display(arabic_reshaper.reshape(text)) if RTL else text


def get_font(size):
    for p in FONT_CANDIDATES:
        if p and pathlib.Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def fallback_image():
    c1 = tuple(random.randint(10, 80) for _ in range(3))
    c2 = tuple(random.randint(60, 200) for _ in range(3))
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / H
        col = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
        for x in range(W):
            px[x, y] = col
    return img


def get_image(prompt, seed):
    full = f"{prompt}, vertical cinematic composition, highly detailed, no text"
    url = (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(full)
        + f"?width={W}&height={H}&nologo=true&seed={seed}"
    )
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            import io

            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            return ImageOps.fit(img, (W, H))
        except Exception as ex:
            log("image attempt failed", attempt, ex)
    return fallback_image()


def wrap_text(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(shape(trial), font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def add_caption(img, text):
    d = ImageDraw.Draw(img, "RGBA")
    font = get_font(78)
    lines = wrap_text(d, text, font, 900)
    lh = 110
    box_h = lh * len(lines) + 50
    y0 = H - 380 - box_h
    d.rounded_rectangle([50, y0, W - 50, y0 + box_h], radius=40, fill=(0, 0, 0, 165))
    for i, line in enumerate(lines):
        disp = shape(line)
        w = d.textlength(disp, font=font)
        d.text(((W - w) / 2, y0 + 25 + i * lh), disp, font=font, fill="white")
    return img


# ---------------------------------------------------------------- ۵) ویدیو
def build_video(plan):
    WORK.mkdir(exist_ok=True)
    clips = []
    for i, sc in enumerate(plan["scenes"]):
        audio = WORK / f"a{i}.mp3"
        asyncio.run(_tts(sc["narration"], audio))
        dur = audio_duration(audio) + 0.25

        img = add_caption(get_image(sc["image_prompt"], random.randint(1, 10**6)), sc["caption"])
        png = WORK / f"i{i}.png"
        img.save(png)

        clip = WORK / f"c{i}.mp4"
        frames = int(dur * 30) + 1
        vf = (
            f"scale=1296:2304,zoompan=z='min(zoom+0.0007,1.2)':d={frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=30"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(png),
             "-i", str(audio), "-t", f"{dur:.2f}", "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-r", "30", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", str(clip)],
            check=True,
        )
        clips.append(clip)

    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in clips))
    out = WORK / "short.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(out)],
        check=True,
    )
    total = audio_duration(out)
    log(f"video ready: {total:.1f}s")
    if total > 59:
        log("WARNING: video longer than 59s")
    return out


# ---------------------------------------------------------------- ۶) آپلود
def upload(video, plan):
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    yt = build("youtube", "v3", credentials=creds)

    tags_line = " ".join(plan["hashtags"] + ["#Shorts"])
    description = f"{plan['description']}\n\n{tags_line}\n\n🤖 AI-generated content"
    title = plan["title"][:95]
    tags = [h.lstrip("#") for h in plan["hashtags"]] + ["Shorts"]

    status = {
        "privacyStatus": PRIVACY,
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True,
    }
    if PUBLISH_DELAY_MIN > 0:
        when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=PUBLISH_DELAY_MIN)
        status["privacyStatus"] = "private"
        status["publishAt"] = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "28",  # Science & Technology
            "defaultLanguage": LANG,
            "defaultAudioLanguage": LANG,
        },
        "status": status,
    }
    media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=-1, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def notify(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat, "text": text}, timeout=20,
            )
        except Exception:
            pass


# ---------------------------------------------------------------- اجرای اصلی
def main():
    history = load_history()
    mode = pick_mode()
    log("mode:", mode)
    news = fetch_news(history) if mode == "news" else []
    if mode == "news" and not news:
        mode = "story"
    plan = make_plan(mode, news, history)
    log("title:", plan["title"])

    video = build_video(plan)

    if DRY_RUN:
        log("DRY_RUN: skipping upload ->", video)
        return
    vid = upload(video, plan)
    url = f"https://youtube.com/shorts/{vid}"
    log("uploaded:", url)

    history.append({
        "date": dt.date.today().isoformat(),
        "topic_key": plan.get("topic_key", ""),
        "link": plan.get("source_link", ""),
        "video_id": vid,
    })
    save_history(history)
    notify(f"✅ شورت امروز آپلود شد:\n{plan['title']}\n{url}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        notify(f"❌ خطا در ربات شورتس: {e}")
        raise
