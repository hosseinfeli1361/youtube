
import asyncio
import json
import os
import pathlib
import re
import subprocess
import time
from io import BytesIO

import requests
import edge_tts
from PIL import Image, ImageFilter, ImageEnhance, ImageOps

ROOT = pathlib.Path(__file__).parent
WORK = ROOT / "work"
HISTORY = ROOT / "history.json"

LANG = os.getenv("LANGUAGE", "fa").lower()
VOICE = os.getenv("TTS_VOICE", "fa-IR-FaridNeural")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")
FONT_DIR = os.getenv("FONT_DIR", "/usr/share/fonts/truetype/noto")

W, H = 1080, 1920
FPS = 30
MAX_SCENES = 6
MIN_SCENES = 5
UPLOAD_ENABLED = False

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "TrendifyAnimeStudio/1.0"
})


def log(*args):
    print("[Trendify Anime]", *args, flush=True)


def run_ffmpeg(args, timeout=300):
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        check=True,
        timeout=timeout,
    )


def load_history():
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_history(history):
    HISTORY.write_text(
        json.dumps(history[-100:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def call_gemini(prompt):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing in GitHub repository secrets."
        )

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
                "temperature": 0.9,
            },
        },
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(
            f"Gemini API error {response.status_code}: "
            f"{response.text[:500]}"
        )

    try:
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Gemini returned no usable story JSON.")


def make_story(history):
    previous = [x.get("title", "") for x in history[-12:]]
    language = "Persian (Farsi)" if LANG == "fa" else "English"

    prompt = f"""
You are the lead writer and visual director of Trendify Anime,
a short-form original science-fiction anime studio.

Write ONE original fictional anime short in {language}.
Audience: curious teens and adults who like technology, AI and mysteries.

STORY CONCEPT:
A young programmer receives a message from an AI that predicts tomorrow.
The first prediction comes true. The next prediction is terrifying.
Create an original plot and a memorable twist.

STORY RULES:
- 5 or 6 scenes, total narration around 42-55 seconds.
- Scene 1 must hook the viewer immediately.
- Clear escalation, emotional stakes, climax, surprising but logical ending.
- Every scene must advance the same story.
- Natural, concise spoken narration in {language}.
- No factual claims presented as real news.
- Avoid titles too similar to these previous titles: {previous}
- Do not put spoken dialogue in quotation marks unless it is actually narrated.
- Keep character identities, clothing, hair and colors consistent.
- Visuals should feel like premium cinematic Japanese anime,
  hand-painted backgrounds, expressive faces, dramatic lighting,
  detailed line art, atmospheric depth, film composition.
- No text, captions, logos, watermarks or letters in images.

Return valid JSON only:
{{
  "title": "short title",
  "description": "short video description",
  "topic_key": "unique-short-slug",
  "character_bible": "Detailed fixed appearance of the protagonist and any recurring character",
  "scenes": [
    {{
      "narration": "spoken narration for this scene",
      "visual": "Specific visual description of this exact story moment",
      "camera": "one simple cinematic camera movement",
      "mood": "emotional tone"
    }}
  ]
}}
"""

    raw = call_gemini(prompt).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned invalid JSON: {exc}")

    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not MIN_SCENES <= len(scenes) <= MAX_SCENES:
        raise RuntimeError("Story must contain 5 or 6 scenes.")

    if not plan.get("character_bible", "").strip():
        raise RuntimeError("Story has no character design description.")

    for i, scene in enumerate(scenes, 1):
        for field in ("narration", "visual", "camera", "mood"):
            if not str(scene.get(field, "")).strip():
                raise RuntimeError(f"Scene {i} is missing '{field}'.")

    return plan


def anime_prompt(plan, scene, index):
    character = plan["character_bible"]
    visual = scene["visual"]
    camera = scene["camera"]
    mood = scene["mood"]

    return (
        "Original Japanese anime film still, premium theatrical anime art, "
        "beautiful hand-painted background, clean expressive line art, "
        "detailed character illustration, dramatic cinematic lighting, "
        "rich but controlled colors, atmospheric perspective, "
        "carefully composed vertical portrait frame, 9:16 composition. "
        f"CHARACTER CONTINUITY - keep this exact design: {character}. "
        f"SCENE {index + 1}: {visual}. "
        f"CAMERA AND COMPOSITION: {camera}. "
        f"EMOTION AND LIGHTING: {mood}. "
        "Maintain the same face, hairstyle, age, clothing, accessories and "
        "color palette as the character description. "
        "One coherent scene, polished anime key visual, "
        "no text, no letters, no subtitles, no logo, no watermark."
    )


def ai_horde_image(prompt, index):
    key = os.getenv("AI_HORDE_API_KEY", "").strip()
    headers = {
        "apikey": key or "0000000000",
        "Client-Agent": "TrendifyAnimeStudio:1.0:github",
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "params": {
            "sampler_name": "k_euler",
            "cfg_scale": 7,
            "width": 512,
            "height": 768,
            "steps": 24,
            "n": 1,
        },
        "nsfw": False,
        "censor_nsfw": True,
    }

    response = SESSION.post(
        "https://aihorde.net/api/v2/generate/async",
        headers=headers,
        json=payload,
        timeout=45,
    )
    if not response.ok:
        detail = response.text[:400]
        if key:
            detail = detail.replace(key, "[REDACTED]")
        raise RuntimeError(
            f"AI Horde rejected scene {index + 1}: "
            f"HTTP {response.status_code}: {detail}"
        )

    request_id = response.json().get("id")
    if not request_id:
        raise RuntimeError("AI Horde returned no generation ID.")

    log(f"Waiting for anime art, scene {index + 1}...")
    deadline = time.monotonic() + 240

    while time.monotonic() < deadline:
        time.sleep(8)
        check = SESSION.get(
            f"https://aihorde.net/api/v2/generate/check/{request_id}",
            headers=headers,
            timeout=30,
        )
        check.raise_for_status()
        state = check.json()

        if state.get("faulted"):
            raise RuntimeError(
                f"AI Horde generation failed for scene {index + 1}."
            )
        if state.get("done"):
            break
    else:
        raise RuntimeError(
            f"AI Horde queue timeout for scene {index + 1}. "
            "Try running the workflow again later."
        )

    result = SESSION.get(
        f"https://aihorde.net/api/v2/generate/status/{request_id}",
        headers=headers,
        timeout=35,
    )
    result.raise_for_status()
    generations = result.json().get("generations", [])
    if not generations:
        raise RuntimeError("AI Horde returned no generated image.")

    image_url = generations[0].get("img", "")
    if not image_url.startswith("https://"):
        raise RuntimeError("AI Horde returned an invalid image URL.")

    image_response = SESSION.get(image_url, timeout=45)
    image_response.raise_for_status()

    try:
        image = Image.open(BytesIO(image_response.content)).convert("RGB")
    except Exception as exc:
        raise RuntimeError(f"Generated image could not be opened: {exc}")

    path = WORK / f"anime_{index:02d}.jpg"
    image.save(path, quality=94)
    log(f"Anime artwork ready for scene {index + 1}.")
    return path


def make_portrait_canvas(image_path, output_path):
    """Keep the entire artwork visible; blur the background to fill 9:16."""
    with Image.open(image_path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")

    background = ImageOps.fit(
        source, (W, H), method=Image.Resampling.LANCZOS
    )
    background = background.filter(ImageFilter.GaussianBlur(34))
    background = ImageEnhance.Brightness(background).enhance(0.48)
    background = ImageEnhance.Color(background).enhance(0.8)

    foreground = ImageOps.contain(
        source,
        (W, H),
        method=Image.Resampling.LANCZOS,
    )

    x = (W - foreground.width) // 2
    y = (H - foreground.height) // 2
    background.paste(foreground, (x, y))
    background.save(output_path, quality=94)
    return output_path


async def make_tts(text, output_path):
    await edge_tts.Communicate(
        text,
        VOICE,
        rate="+0%",
    ).save(str(output_path))


def audio_duration(path):
    result = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=30,
    )
    return float(result.decode().strip())


def ass_time(seconds):
    cs = max(0, int(seconds * 100))
    hours, rest = divmod(cs, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, centiseconds = divmod(rest, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{centiseconds:02}"


def split_phrases(text, max_words=5):
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?؟؛])\s+", text)
    phrases = []

    for sentence in sentences:
        words = sentence.split()
        for start in range(0, len(words), max_words):
            phrase = " ".join(words[start:start + max_words])
            if phrase:
                phrases.append(phrase)
    return phrases


def create_ass(events):
    path = WORK / "subtitles.ass"
    header = r"""[Script Info]
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans Arabic,49,&H00FFFFFF,&H0000FFFF,&H00101010,&H90000000,1,0,1,3,1,2,90,90,250,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for start, end, text in events:
        text = (
            text.replace("\\", r"\\")
                .replace("{", r"\{")
                .replace("}", r"\}")
        )
        rows.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},"
            f"Default,,0,0,0,,{text}"
        )

    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def render_scene(canvas_path, audio_path, output_path, duration, camera):
    # All frames start from a complete portrait canvas.
    # Zoom is deliberately subtle so the main subject stays visible.
    direction = str(camera).lower()
    if any(word in direction for word in ("left", "pan left", "move left")):
        x_expr = "iw/2-(iw/zoom/2)-min((zoom-1)*iw*0.18,iw*0.04)"
    elif any(word in direction for word in ("right", "pan right", "move right")):
        x_expr = "iw/2-(iw/zoom/2)+min((zoom-1)*iw*0.18,iw*0.04)"
    else:
        x_expr = "iw/2-(iw/zoom/2)"

    frames = max(1, int(duration * FPS))
    vf = (
        "zoompan="
        "z='min(zoom+0.00018,1.045)':"
        f"x='{x_expr}':"
        "y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={W}x{H}:fps={FPS},"
        "setsar=1,format=yuv420p"
    )

    run_ffmpeg([
        "-loop", "1",
        "-framerate", str(FPS),
        "-i", str(canvas_path),
        "-i", str(audio_path),
        "-vf", vf,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
        "-ar", "44100",
        "-ac", "2",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ], timeout=360)


def build_video(plan):
    clips = []
    subtitle_events = []
    timeline = 0.0
    credits = []

    for index, scene in enumerate(plan["scenes"]):
        log(f"Creating scene {index + 1}/{len(plan['scenes'])}")

        prompt = anime_prompt(plan, scene, index)
        artwork = ai_horde_image(prompt, index)

        canvas = WORK / f"canvas_{index:02d}.jpg"
        make_portrait_canvas(artwork, canvas)

        audio = WORK / f"voice_{index:02d}.mp3"
        asyncio.run(make_tts(scene["narration"], audio))
        duration = audio_duration(audio)

        if duration <= 0:
            raise RuntimeError(f"Invalid narration duration in scene {index + 1}.")

        phrases = split_phrases(scene["narration"])
        if not phrases:
            raise RuntimeError(f"No subtitle phrases in scene {index + 1}.")

        weights = [max(1, len(p)) for p in phrases]
        total_weight = sum(weights)
        cursor = timeline

        for phrase, weight in zip(phrases, weights):
            part = duration * weight / total_weight
            subtitle_events.append((cursor, cursor + part, phrase))
            cursor += part

        clip = WORK / f"scene_{index:02d}.mp4"
        render_scene(canvas, audio, clip, duration, scene["camera"])
        clips.append(clip)

        credits.append({
            "scene": index + 1,
            "source": "AI Horde community image generation",
            "story_visual": scene["visual"],
            "prompt": prompt,
        })
        timeline += duration

    if not clips:
        raise RuntimeError("No scenes were rendered.")

    concat_list = WORK / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{clip.name}'\n" for clip in clips),
        encoding="utf-8",
    )

    raw = WORK / "raw.mp4"
    run_ffmpeg([
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(raw),
    ])

    subtitles = create_ass(subtitle_events)
    output = WORK / "short.mp4"

    # Escape the path for FFmpeg's subtitles filter.
    subtitle_filter_path = str(subtitles.resolve()).replace("\\", "/")
    subtitle_filter_path = subtitle_filter_path.replace(":", r"\:")

    run_ffmpeg([
        "-i", str(raw),
        "-vf", f"subtitles='{subtitle_filter_path}':fontsdir='{FONT_DIR}'",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output),
    ], timeout=600)

    final_duration = audio_duration(output)
    if final_duration < 10 or final_duration > 59:
        raise RuntimeError(
            f"Video is {final_duration:.1f}s; Shorts test target is 10-59s. "
            "Adjust narration length and run again."
        )

    (WORK / "media_credits.json").write_text(
        json.dumps(credits, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (WORK / "story.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log(f"Video ready: {final_duration:.1f} seconds")
    log(f"Output: {output}")
    return output, final_duration


def main():
    WORK.mkdir(parents=True, exist_ok=True)

    # Never publish automatically in this quality-test version.
    history = load_history()
    plan = make_story(history)
    log("Story:", plan.get("title", "Untitled"))

    output, duration = build_video(plan)

    history.append({
        "title": plan.get("title", ""),
        "topic_key": plan.get("topic_key", ""),
        "duration": round(duration, 2),
    })
    save_history(history)

    log("QUALITY GATE: YouTube upload is disabled.")
    log(f"Review the artifact containing: {output.name}")
    log("Also inspect work/story.json and work/media_credits.json.")
    if UPLOAD_ENABLED:
        raise RuntimeError("Publishing is not implemented in this version.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("FATAL:", str(exc))
        raise
