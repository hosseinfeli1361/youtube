
import asyncio
import datetime as dt
import json
import os
import pathlib
import random
import re
import subprocess
import time
import urllib.parse
import requests
import edge_tts
import feedparser

from PIL import Image, ImageOps
from io import BytesIO

ROOT = pathlib.Path(__file__).parent
WORK = ROOT / "work"
HISTORY = ROOT / "history.json"

LANG = os.getenv("LANGUAGE", "fa")
VOICE = os.getenv("TTS_VOICE", "fa-IR-FaridNeural")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")
MODE = os.getenv("MODE", "auto")
DRY_RUN = True

# قفل سخت: تا تأیید کیفیت، هیچ آپلودی انجام نمی‌شود.
UPLOAD_ENABLED = False

W, H = 1080, 1920
FPS = 30
FONT_DIR = "/usr/share/fonts/truetype/noto"

FEEDS = [
    "https://hnrss.org/frontpage?points=150",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://techcrunch.com/feed/",
    "https://www.wired.com/feed/rss",
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "TrendifyVideoStudio/1.0 (educational video production)"
})


def log(*args):
    print("[trendify]", *args, flush=True)


def run_ffmpeg(args):
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        check=True,
        timeout=240,
    )


def load_history():
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_history(history):
    HISTORY.write_text(
        json.dumps(history[-300:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_news(history):
    used = {x.get("link") for x in history}
    items = []

    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                link = entry.get("link", "")
                title = entry.get("title", "")
                if not link or link in used or not title:
                    continue

                summary = re.sub(
                    r"<[^>]+>", "", entry.get("summary", "")
                )[:500]

                items.append({
                    "title": title,
                    "summary": summary,
                    "link": link,
                })
        except Exception as exc:
            log("RSS error:", url, str(exc)[:180])

    random.shuffle(items)
    return items[:25]


def pick_mode():
    if MODE in ("news", "story"):
        return MODE
    return "news" if dt.date.today().toordinal() % 2 == 0 else "story"


def call_llm(prompt):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{LLM_MODEL}:generateContent"
    )

    response = SESSION.post(
        url,
        params={"key": key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.65,
            },
        },
        timeout=120,
    )
    response.raise_for_status()

    data = response.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Gemini returned no usable script")


def make_plan(mode, news, history):
    past = [x.get("topic_key", "") for x in history[-60:]]

    if mode == "news":
        task = (
            "Choose one significant technology or AI news item from this JSON. "
            "Use only claims supported by the supplied item. "
            "Do not infer political bias or motives without strong evidence. "
            "Preserve the original source URL.\n"
            + json.dumps(news, ensure_ascii=False)
        )
    else:
        task = (
            "Create an engaging, factually conservative story about technology, "
            "computing, internet history or AI. Use well-documented facts."
        )

    language = "Persian (Farsi)" if LANG == "fa" else "English"

    prompt = f"""
You are a professional documentary Shorts writer.
Narration language: {language}.
{task}

Avoid previously covered topic keys: {past}

Return valid JSON only, with this exact structure:
{{
 "title": "short title",
 "description": "description with source link when relevant",
 "hashtags": ["#technology", "#AI"],
 "topic_key": "short-english-slug",
 "source_link": "",
 "scenes": [
   {{
     "narration": "natural spoken narration",
     "image_prompt": "specific English visual description",
     "search_terms": "short English keywords for finding real footage"
   }}
 ]
}}

Rules:
- 35 to 55 seconds of spoken narration in total.
- 5 to 7 scenes, approximately 6 to 10 seconds each.
- Narration must be natural and easy to speak.
- The opening must create curiosity without misleading viewers.
- Visual prompts describe real objects, locations or actions, not abstract gradients.
- No text, lettering or watermarks inside generated images.
- Each scene must have distinct visual search terms.
- Do not invent statistics, quotations or sources.
- Use a concise title, a useful description and 3 to 5 relevant hashtags.
"""

    raw = call_llm(prompt).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    plan = json.loads(raw)

    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not 4 <= len(scenes) <= 7:
        raise RuntimeError("Script must contain 4-7 scenes")

    for scene in scenes:
        if not scene.get("narration", "").strip():
            raise RuntimeError("A scene has empty narration")
        if not scene.get("image_prompt", "").strip():
            raise RuntimeError("A scene has no visual prompt")

    return plan


async def make_tts(text, output_path):
    await edge_tts.Communicate(
        text, VOICE, rate="+0%"
    ).save(str(output_path))


def audio_duration(path):
    result = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ], timeout=30)
    return float(result.decode().strip())


def search_terms(text):
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{2,}", text)
    stop = {
        "cinematic", "highly", "detailed", "vertical", "realistic",
        "professional", "beautiful", "dramatic", "without", "text",
    }
    terms = [w for w in words if w.lower() not in stop]
    return " ".join(terms[:7]) or "technology science"


def commons_search(query, want_video):
    mime = "filemime:video" if want_video else "filemime:bitmap"
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{mime} {query}",
        "gsrnamespace": 6,
        "gsrlimit": 8,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1200,
    }

    response = SESSION.get(
        "https://commons.wikimedia.org/w/api.php",
        params=params,
        timeout=30,
    )
    response.raise_for_status()

    pages = response.json().get("query", {}).get("pages", {})
    candidates = []

    for page in pages.values():
        info_list = page.get("imageinfo", [])
        if not info_list:
            continue

        info = info_list[0]
        url = info.get("url", "")
        thumb = info.get("thumburl", "")
        title = page.get("title", "")

        if want_video:
            if not re.search(r"\.(mp4|webm|mov|ogv)(?:$|\?)", url, re.I):
                continue
        else:
            if not thumb:
                continue

        metadata = info.get("extmetadata", {})
        license_name = metadata.get("LicenseShortName", {}).get("value", "")
        license_url = metadata.get("LicenseUrl", {}).get("value", "")
        artist = metadata.get("Artist", {}).get("value", "")

        # Commons files are freely licensed/public domain, but preserve
        # the per-file attribution and license details for review.
        candidates.append({
            "title": title,
            "url": url,
            "thumb": thumb,
            "license": re.sub(r"<[^>]+>", "", license_name),
            "license_url": license_url,
            "artist": re.sub(r"<[^>]+>", "", artist),
        })

    random.shuffle(candidates)
    return candidates


def download_limited(url, path, max_bytes=30 * 1024 * 1024):
    with SESSION.get(url, stream=True, timeout=(15, 45)) as response:
        response.raise_for_status()

        declared = response.headers.get("Content-Length")
        if declared and int(declared) > max_bytes:
            raise RuntimeError("Media file exceeds size limit")

        total = 0
        with open(path, "wb") as output:
            for chunk in response.iter_content(1024 * 256):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("Media download exceeded size limit")
                output.write(chunk)

    if path.stat().st_size < 2048:
        raise RuntimeError("Downloaded media is unexpectedly small")


def commons_video(query, index):
    for item in commons_search(query, True)[:3]:
        try:
            ext = pathlib.Path(
                urllib.parse.urlparse(item["url"]).path
            ).suffix.lower()
            if ext not in {".mp4", ".webm", ".mov", ".ogv"}:
                continue

            path = WORK / f"source_{index}{ext}"
            download_limited(item["url"], path)

            # Validate that FFmpeg can read the downloaded media.
            subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "csv=p=0", str(path)],
                check=True, capture_output=True, timeout=25,
            )
            return path, item
        except Exception as exc:
            log("Commons video unavailable:", str(exc)[:140])

    return None, None


def commons_image(query, index):
    for item in commons_search(query, False)[:3]:
        try:
            response = SESSION.get(item["thumb"], timeout=35)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content)).convert("RGB")
            image = ImageOps.fit(image, (W, H))
            path = WORK / f"source_{index}.jpg"
            image.save(path, quality=92)
            return path, item
        except Exception as exc:
            log("Commons image unavailable:", str(exc)[:140])

    return None, None


def ai_horde_image(prompt, index):
    # Anonymous shared access is best-effort, slow and rate-limited.
    # Never assume this service will always return an image.
    headers = {
        "apikey": os.getenv("AI_HORDE_API_KEY", "0000000000"),
        "Client-Agent": "TrendifyVideoStudio:1.0:github",
        "Content-Type": "application/json",
    }

    payload = {
        "prompt": (
            prompt
            + ", cinematic documentary photography, realistic lighting, "
              "vertical composition, no text, no logos"
        ),
        "params": {
            "sampler_name": "k_euler",
            "cfg_scale": 7,
            "width": 512,
            "height": 768,
            "steps": 20,
            "n": 1,
        },
        "nsfw": False,
        "censor_nsfw": True,
    }

    try:
        response = SESSION.post(
            "https://aihorde.net/api/v2/generate/async",
            headers=headers, json=payload, timeout=40,
        )
        response.raise_for_status()
        request_id = response.json()["id"]

        for _ in range(15):
            time.sleep(8)
            check = SESSION.get(
                f"https://aihorde.net/api/v2/generate/check/{request_id}",
                headers=headers, timeout=25,
            )
            check.raise_for_status()
            state = check.json()

            if state.get("faulted"):
                raise RuntimeError("AI Horde generation failed")
            if state.get("done"):
                break
        else:
            raise RuntimeError("AI Horde queue timed out")

        result = SESSION.get(
            f"https://aihorde.net/api/v2/generate/status/{request_id}",
            headers=headers, timeout=30,
        )
        result.raise_for_status()
        generations = result.json().get("generations", [])
        if not generations:
            raise RuntimeError("AI Horde returned no image")

        image_url = generations[0].get("img", "")
        if not image_url.startswith("https://"):
            raise RuntimeError("Unexpected AI Horde image URL")

        image_response = SESSION.get(image_url, timeout=45)
        image_response.raise_for_status()
        image = Image.open(BytesIO(image_response.content)).convert("RGB")
        image = ImageOps.fit(image, (W, H))
        path = WORK / f"ai_{index}.jpg"
        image.save(path, quality=92)
        return path

    except Exception as exc:
        log("AI image unavailable:", str(exc)[:180])
        return None


def obtain_media(scene, index):
    query = search_terms(
        scene.get("search_terms", "") + " " + scene["image_prompt"]
    )

    # Prefer real video footage.
    video, video_info = commons_video(query, index)
    if video:
        return "video", video, video_info

    # Otherwise use a freely licensed real photograph.
    image, image_info = commons_image(query, index)
    if image:
        return "image", image, image_info

    # Finally attempt a generated image.
    image = ai_horde_image(scene["image_prompt"], index)
    if image:
        return "image", image, {
            "title": "AI-generated image",
            "license": "AI-generated",
            "license_url": "",
            "artist": "Trendify",
        }

    raise RuntimeError(
        f"No usable footage or image found for scene {index + 1}. "
        "Refusing to create a colored-gradient placeholder."
    )


def split_phrases(text, max_words=6):
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    sentences = re.split(r"(?<=[.!?؟؛])\s+", text)
    phrases = []

    for sentence in sentences:
        words = sentence.split()
        for start in range(0, len(words), max_words):
            phrase = " ".join(words[start:start + max_words])
            if phrase:
                phrases.append(phrase)

    return phrases


def ass_time(seconds):
    centiseconds = max(0, int(seconds * 100))
    hours, rest = divmod(centiseconds, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{cs:02}"


def ass_wrap(text, max_chars=26):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    # Maximum two lines; prefer shorter phrases over tiny text.
    return r"\N".join(lines[:2])


def create_ass(subtitle_events):
    path = WORK / "subtitles.ass"

    header = """[Script Info]
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans Arabic,48,&H00FFFFFF,&H0000FFFF,&H00101010,&H80000000,1,0,1,3,1,2,90,90,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    rows = []
    for start, end, text in subtitle_events:
        text = text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")
        rows.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},"
            f"Default,,0,0,0,,{ass_wrap(text)}"
        )

    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def render_scene(media_kind, media_path, audio_path, output_path, duration):
    if media_kind == "video":
        video_filter = (
            f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},fps={FPS},setsar=1,format=yuv420p"
        )
        run_ffmpeg([
            "-stream_loop", "-1", "-i", str(media_path),
            "-i", str(audio_path),
            "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", video_filter,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-ac", "2", "-shortest", str(output_path),
        ])
    else:
        frames = max(1, int(duration * FPS))
        video_filter = (
            f"scale={W * 1.2}:{H * 1.2}:force_original_aspect_ratio=increase,"
            f"crop={W * 1.2}:{H * 1.2},"
            f"zoompan=z='min(zoom+0.0005,1.10)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={W}x{H}:fps={FPS},"
            "setsar=1,format=yuv420p"
        )
        run_ffmpeg([
            "-loop", "1", "-framerate", str(FPS), "-i", str(media_path),
            "-i", str(audio_path),
            "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", video_filter,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-ac", "2", "-shortest", str(output_path),
        ])


def build_video(plan):
    WORK.mkdir(parents=True, exist_ok=True)
    clips = []
    credits = []
    subtitle_events = []
    timeline = 0.0

    for index, scene in enumerate(plan["scenes"]):
        log(f"Scene {index + 1}/{len(plan['scenes'])}: acquiring media")

        audio = WORK / f"audio_{index}.mp3"
        asyncio.run(make_tts(scene["narration"], audio))
        duration = audio_duration(audio)

        if duration <= 0:
            raise RuntimeError("Invalid narration audio duration")

        kind, media, info = obtain_media(scene, index)
        credits.append({
            "scene": index + 1,
            "kind": kind,
            "title": info.get("title", ""),
            "artist": info.get("artist", ""),
            "license": info.get("license", ""),
            "license_url": info.get("license_url", ""),
            "url": info.get("url", ""),
        })

        phrases = split_phrases(scene["narration"])
        if not phrases:
            raise RuntimeError("Empty subtitle text")

        weights = [max(1, len(p)) for p in phrases]
        total_weight = sum(weights)
        phrase_start = timeline

        for phrase, weight in zip(phrases, weights):
            phrase_duration = duration * weight / total_weight
            subtitle_events.append((
                phrase_start,
                phrase_start + phrase_duration,
                phrase,
            ))
            phrase_start += phrase_duration

        clip = WORK / f"scene_{index}.mp4"
        render_scene(kind, media, audio, clip, duration)
        clips.append(clip)
        timeline += duration

    if not clips:
        raise RuntimeError("No scenes were rendered")

    concat_list = WORK / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{p.name}'\n" for p in clips),
        encoding="utf-8",
    )

    raw_video = WORK / "raw.mp4"
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(raw_video),
    ])

    subtitle_path = create_ass(subtitle_events)
    output = WORK / "short.mp4"

    run_ffmpeg([
        "-i", str(raw_video),
        "-vf", f"subtitles={subtitle_path}:fontsdir={FONT_DIR}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output),
    ])

    final_duration = audio_duration(output)
    if final_duration < 10 or final_duration > 59:
        raise RuntimeError(
            f"Output duration {final_duration:.1f}s is outside the Shorts target"
        )

    (WORK / "media_credits.json").write_text(
        json.dumps(credits, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log(f"Video ready: {final_duration:.1f}s")
    log(f"Output: {output}")
    log("Media credits saved to work/media_credits.json")
    return output


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    history = load_history()
    mode = pick_mode()

    log("Mode:", mode)
    news = fetch_news(history) if mode == "news" else []

    if mode == "news" and not news:
        log("No new RSS items; switching to story mode")
        mode = "story"

    plan = make_plan(mode, news, history)
    log("Title:", plan["title"])

    video = build_video(plan)

    # Quality gate: upload stays disabled in this revision.
    if not UPLOAD_ENABLED:
        log("QUALITY GATE: YouTube upload is disabled.")
        log("Download the short.mp4 artifact and review it before enabling publishing.")
        return

    raise RuntimeError(
        "Upload code intentionally unavailable until quality is approved."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("FATAL:", str(exc))
        raise
