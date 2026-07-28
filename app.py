import os
import re
import json
import uuid
import sqlite3
import subprocess
from datetime import datetime
from flask import Flask, request, jsonify, render_template, session, send_from_directory

from utils.db import init_db, get_db
from utils.tts_utils import DEFAULT_VOICES, generate_tts, clone_tts
from utils.video_builder import build_video, concat_audio_segments, generate_text_card_images, get_audio_duration, WIDTH, HEIGHT
from utils.captions import make_title_card
from utils.image_fetcher import fetch_topic_images

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
DEFAULT_IMAGES_DIR = os.path.join(BASE_DIR, "static", "default_images")
DEFAULT_MUSIC_DIR = os.path.join(BASE_DIR, "static", "audio")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
init_db()

ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}
ALLOWED_AUDIO_EXT = {"mp3", "wav", "m4a", "ogg"}


def get_user_id():
    if "uid" not in session:
        session["uid"] = str(uuid.uuid4())
    return session["uid"]


def allowed_file(filename, allowed_ext):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_ext


@app.route("/")
def index():
    music_files = sorted(os.listdir(DEFAULT_MUSIC_DIR)) if os.path.isdir(DEFAULT_MUSIC_DIR) else []
    default_images = sorted(os.listdir(DEFAULT_IMAGES_DIR)) if os.path.isdir(DEFAULT_IMAGES_DIR) else []
    return render_template(
        "index.html",
        default_voices=DEFAULT_VOICES,
        music_files=music_files,
        default_images=default_images,
    )


@app.route("/assets/thumb/<int:asset_id>")
def asset_thumb(asset_id):
    """Serve an uploaded image asset back to the browser for the library grid."""
    user_id = get_user_id()
    db = get_db()
    row = db.execute(
        "SELECT file_path FROM user_assets WHERE id=? AND user_id=? AND type='image'",
        (asset_id, user_id),
    ).fetchone()
    if not row:
        return "not found", 404
    directory, filename = os.path.split(row[0])
    return send_from_directory(directory, filename)


@app.route("/assets/upload", methods=["POST"])
def upload_asset():
    """Upload a voice (audio) or image asset and save it to the user's library."""
    asset_type = request.form.get("type")  # 'voice' or 'image'
    file = request.files.get("file")
    label = request.form.get("label") or (file.filename if file else "untitled")

    if asset_type not in ("voice", "image"):
        return jsonify({"error": "invalid asset type"}), 400
    if not file or file.filename == "":
        return jsonify({"error": "no file provided"}), 400

    allowed = ALLOWED_AUDIO_EXT if asset_type == "voice" else ALLOWED_IMAGE_EXT
    if not allowed_file(file.filename, allowed):
        return jsonify({"error": f"unsupported file type for {asset_type}"}), 400

    user_id = get_user_id()
    ext = file.filename.rsplit(".", 1)[1].lower()
    stored_name = f"{asset_type}_{uuid.uuid4().hex}.{ext}"
    user_dir = os.path.join(UPLOAD_DIR, user_id)
    os.makedirs(user_dir, exist_ok=True)
    file_path = os.path.join(user_dir, stored_name)
    file.save(file_path)

    db = get_db()
    db.execute(
        "INSERT INTO user_assets (user_id, type, file_path, label, uploaded_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, asset_type, file_path, label, datetime.utcnow().isoformat()),
    )
    db.commit()
    asset_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    return jsonify({"id": asset_id, "type": asset_type, "label": label})


@app.route("/assets/<asset_type>")
def list_assets(asset_type):
    """Return the current user's saved assets of a given type ('voice' or 'image')."""
    if asset_type not in ("voice", "image"):
        return jsonify({"error": "invalid asset type"}), 400
    user_id = get_user_id()
    db = get_db()
    rows = db.execute(
        "SELECT id, label, uploaded_at FROM user_assets WHERE user_id=? AND type=? ORDER BY uploaded_at DESC",
        (user_id, asset_type),
    ).fetchall()
    return jsonify([{"id": r[0], "label": r[1], "uploaded_at": r[2]} for r in rows])


def resolve_asset_path(user_id, kind, source_mode, source_value, default_dir):
    """
    kind: 'voice' or 'image'
    source_mode: 'default' | 'library' | 'upload'
    source_value: default filename, or library asset id, or uploaded temp path
    """
    if source_mode == "default":
        return os.path.join(default_dir, source_value)
    if source_mode == "library":
        db = get_db()
        row = db.execute(
            "SELECT file_path FROM user_assets WHERE id=? AND user_id=? AND type=?",
            (source_value, user_id, kind),
        ).fetchone()
        if not row:
            raise ValueError(f"library asset not found for {kind}")
        return row[0]
    if source_mode == "upload":
        return source_value  # already an absolute temp path saved this request
    raise ValueError("invalid source_mode")


def split_into_scenes(text, target_chunk_words=12, min_scenes=3):
    """
    Split text into 'scenes' for auto image assignment (text cards / web
    fetch / split-layout panels). Sentence punctuation is preferred, but
    if the script has too few sentences (e.g. no periods, one long
    paragraph), falls back to fixed-size word chunks — otherwise the
    whole video would end up using just one or two images.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if len(sentences) >= min_scenes:
        return sentences

    words = text.split()
    if not words:
        return [text] if text.strip() else []
    chunks = [" ".join(words[i:i + target_chunk_words]) for i in range(0, len(words), target_chunk_words)]
    return chunks if chunks else [text]


DIALOGUE_LINE_RE = re.compile(
    r"^\s*\*{0,2}\s*(teacher|student)\b[^:\n]*:\s*\*{0,2}\s*(.*)$",
    re.IGNORECASE,
)


def _clean_dialogue_text(s):
    """Strip markdown bold markers and wrapping quote marks so TTS reads plain speech, not literal '**' or '"'."""
    s = s.strip()
    s = re.sub(r"^\*+|\*+$", "", s).strip()
    if len(s) >= 2 and s[0] in "\"“" and s[-1] in "\"”":
        s = s[1:-1].strip()
    return s


def parse_dialogue_script(script_text):
    """
    Parse a script written as alternating Teacher/Student lines into an
    ordered list of (speaker, text) segments — one per speaker turn.

    Tolerant of common formatting the person may paste in, e.g.:
        **TEACHER (clear, welcoming tone):**
        "Hello everyone! Welcome back to the channel."
    A label line's own trailing text (if any) plus any following non-label
    lines are treated as that speaker's turn, so a quote can sit on its own
    line under a '**TEACHER:**'-style header.
    """
    segments = []
    current_speaker = None
    current_lines = []

    def flush():
        if current_speaker and current_lines:
            text = " ".join(current_lines).strip()
            if text:
                segments.append((current_speaker, text))

    for raw_line in script_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = DIALOGUE_LINE_RE.match(line)
        if m:
            flush()
            current_speaker = m.group(1).lower()
            text = _clean_dialogue_text(m.group(2))
            current_lines = [text] if text else []
        elif current_speaker is not None:
            current_lines.append(_clean_dialogue_text(line))
    flush()
    return segments


@app.route("/generate", methods=["POST"])
def generate():
    user_id = get_user_id()
    video_title = request.form.get("video_title", "").strip()      # optional explicit topic
    caption_text = request.form.get("caption_text", "").strip()   # shown on screen
    script_text = request.form.get("script", "").strip()          # converted to speech
    animate = request.form.get("animate") == "true"
    try:
        speed_factor = float(request.form.get("speed", "1"))
    except ValueError:
        speed_factor = 1.0
    if speed_factor <= 0:
        speed_factor = 1.0
    # If the user told us the topic directly, trust it over guessing from the script —
    # it's short, deliberate, and free of stray words that could throw off matching.
    topic_text = video_title or script_text

    if not script_text:
        return jsonify({"error": "narration script is required"}), 400
    if not caption_text:
        caption_text = script_text  # fall back to showing the same text if left blank

    voice_count = request.form.get("voice_count", "1")   # "1" or "2"
    voice_mode = request.form.get("voice_mode")       # default | library | upload
    voice_value = request.form.get("voice_value")     # voice id, asset id, or n/a
    image_mode = request.form.get("image_mode")        # default | library | upload
    image_values = request.form.getlist("image_value")  # list of default filenames or asset ids
    music_choice = request.form.get("music_choice")    # filename in static/audio, or 'none'

    job_id = uuid.uuid4().hex
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    # --- Resolve / produce narration audio ---
    narration_path = os.path.join(job_dir, "narration.mp3")
    word_boundaries = None
    speaker_timeline = None

    if voice_count == "2":
        # --- Teacher/Student dialogue mode: each labeled line gets its own voice ---
        segments = parse_dialogue_script(script_text)
        if not segments:
            return jsonify({
                "error": "No 'Teacher:' / 'Student:' lines found. Format the script like:\n"
                         "Teacher: Welcome to today's lesson.\nStudent: What are we learning?"
            }), 400

        def resolve_speaker_narration(speaker, text, index):
            mode = request.form.get(f"{speaker}_voice_mode", "default")
            seg_path = os.path.join(job_dir, f"seg_{index}_{speaker}.mp3")
            if mode == "upload":
                uploaded = request.files.get(f"{speaker}_voice_file")
                if not uploaded:
                    raise ValueError(f"Voice sample missing for {speaker}")
                ref_ext = uploaded.filename.rsplit(".", 1)[-1].lower() if "." in uploaded.filename else "mp3"
                ref_path = os.path.join(job_dir, f"{speaker}_reference.{ref_ext}")
                if not os.path.isfile(ref_path):
                    uploaded.save(ref_path)
                clone_tts(text, ref_path, seg_path)
            else:
                value = request.form.get(f"{speaker}_voice_value") or DEFAULT_VOICES[0]["id"]
                try:
                    generate_tts(text, value, seg_path)
                except Exception as e:
                    raise RuntimeError(
                        f"System voice generation failed for {speaker} "
                        f"(the free Microsoft voice service may be temporarily blocking requests): {e}"
                    )
            return seg_path

        seg_paths = []
        speaker_timeline = []
        cursor = 0.0
        try:
            for i, (speaker, text) in enumerate(segments):
                seg_path = resolve_speaker_narration(speaker, text, i)
                seg_paths.append(seg_path)
                seg_duration = get_audio_duration(seg_path)
                speaker_timeline.append((speaker, cursor, cursor + seg_duration))
                cursor += seg_duration
        except (ValueError, RuntimeError) as e:
            return jsonify({"error": str(e)}), 400

        concat_audio_segments(seg_paths, narration_path)
        # Dialogue narration is stitched from independently-timed clips, so
        # precise per-word sync isn't available — captions fall back to
        # even-spacing across the full narration length (handled already).
        word_boundaries = None
        if not caption_text:
            caption_text = " ".join(text for _, text in segments)

    elif voice_mode == "upload":
        uploaded_voice = request.files.get("voice_file")
        if not uploaded_voice:
            return jsonify({"error": "voice file missing for upload mode"}), 400
        ref_ext = uploaded_voice.filename.rsplit(".", 1)[-1].lower() if "." in uploaded_voice.filename else "mp3"
        reference_audio_path = os.path.join(job_dir, f"voice_reference.{ref_ext}")
        uploaded_voice.save(reference_audio_path)
        try:
            clone_tts(script_text, reference_audio_path, narration_path)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 500
    elif voice_mode == "library":
        reference_audio_path = resolve_asset_path(user_id, "voice", "library", voice_value, None)
        try:
            clone_tts(script_text, reference_audio_path, narration_path)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 500
    else:  # default -> generate via TTS, capturing per-word timestamps for synced captions
        try:
            word_boundaries = generate_tts(script_text, voice_value or DEFAULT_VOICES[0]["id"], narration_path)
        except Exception as e:
            return jsonify({
                "error": f"System voice generation failed (the free Microsoft voice service may be "
                         f"temporarily blocking requests — try upgrading edge-tts): {e}"
            }), 500

    # --- Resolve images ---
    image_paths = []
    text_sources = None
    if image_mode == "textcard":
        sentences = split_into_scenes(caption_text)
        if not sentences:
            sentences = [caption_text]
        image_paths = generate_text_card_images(sentences, job_dir)
        text_sources = sentences
    elif image_mode == "webfetch":
        sentences = split_into_scenes(caption_text)
        if not sentences:
            sentences = [caption_text]
        try:
            image_paths = fetch_topic_images(sentences, job_dir, full_text=topic_text)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 500
        if not image_paths:
            return jsonify({
                "error": "Couldn't find any matching stock photos for this script. "
                         "Try different wording, or pick another image source."
            }), 400
    elif image_mode == "upload":
        files = request.files.getlist("image_files")
        if not files:
            return jsonify({"error": "image files missing for upload mode"}), 400
        for i, f in enumerate(files):
            ext = f.filename.rsplit(".", 1)[-1].lower()
            p = os.path.join(job_dir, f"img_{i}.{ext}")
            f.save(p)
            image_paths.append(p)
    elif image_mode == "library":
        for val in image_values:
            image_paths.append(resolve_asset_path(user_id, "image", "library", val, None))
    else:  # default stock images
        for val in image_values:
            image_paths.append(os.path.join(DEFAULT_IMAGES_DIR, val))

    if not image_paths:
        return jsonify({"error": "at least one image is required"}), 400

    # --- Split-layout text panels (image on one side, bold text on the other) ---
    # Only for photo-based modes — textcard images are already full text slides.
    panel_texts = None
    if image_mode in ("webfetch", "upload", "library", "default"):
        panel_sentences = split_into_scenes(caption_text)
        if panel_sentences:
            panel_texts = panel_sentences

    # --- Resolve background music ---
    music_path = None
    if music_choice and music_choice != "none":
        music_path = os.path.join(DEFAULT_MUSIC_DIR, music_choice)

    # --- Build the final video ---
    output_path = os.path.join(job_dir, "final_video.mp4")
    try:
        build_video(
            image_paths=image_paths,
            narration_path=narration_path,
            music_path=music_path,
            animate=animate,
            output_path=output_path,
            caption_text=caption_text,
            word_boundaries=word_boundaries,
            emoji_list=None,
            speaker_timeline=speaker_timeline,
            panel_texts=panel_texts,
            speed_factor=speed_factor,
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"video rendering failed: {e.stderr.decode(errors='ignore')[-500:]}"}), 500

    # --- Save job metadata so the timeline editor can regenerate later ---
    narration_duration = get_audio_duration(narration_path)
    default_seg_duration = max(narration_duration / len(image_paths), 1.0)
    meta = {
        "image_paths": image_paths,
        "narration_path": narration_path,
        "music_path": music_path,
        "animate": animate,
        "caption_text": caption_text,
        "word_boundaries": word_boundaries,
        "emoji_list": None,
        "speaker_timeline": speaker_timeline,
        "panel_texts": panel_texts,
        "speed_factor": speed_factor,
        "durations": [default_seg_duration] * len(image_paths),
        "text_sources": text_sources,
    }
    with open(os.path.join(job_dir, "meta.json"), "w") as f:
        json.dump(meta, f)

    return jsonify({"video_url": f"/download/{job_id}/final_video.mp4", "job_id": job_id})


def _load_job_meta(job_id):
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    meta_path = os.path.join(job_dir, "meta.json")
    if not os.path.isfile(meta_path):
        return None, job_dir
    with open(meta_path) as f:
        return json.load(f), job_dir


@app.route("/jobs/<job_id>/timeline")
def job_timeline(job_id):
    """Return the current per-segment timeline (image + duration) for the editor UI."""
    meta, job_dir = _load_job_meta(job_id)
    if meta is None:
        return jsonify({"error": "job not found"}), 404
    text_sources = meta.get("text_sources")
    segments = [
        {
            "index": i,
            "duration": meta["durations"][i],
            "thumb_url": f"/jobs/{job_id}/thumb/{i}",
            "text": text_sources[i] if text_sources and i < len(text_sources) else None,
        }
        for i in range(len(meta["image_paths"]))
    ]
    return jsonify({"segments": segments})


@app.route("/jobs/<job_id>/thumb/<int:index>")
def job_thumb(job_id, index):
    meta, job_dir = _load_job_meta(job_id)
    if meta is None or index < 0 or index >= len(meta["image_paths"]):
        return "not found", 404
    path = meta["image_paths"][index]
    directory, filename = os.path.split(path)
    return send_from_directory(directory, filename)


@app.route("/jobs/<job_id>/regenerate", methods=["POST"])
def job_regenerate(job_id):
    """
    Rebuild the video from an edited timeline: a reordered/resized list of
    segments, each referencing either an existing job image, a saved
    library asset, or a stock default image, plus new per-segment
    durations and/or newly appended images from the "add media" panel.
    """
    meta, job_dir = _load_job_meta(job_id)
    if meta is None:
        return jsonify({"error": "job not found"}), 404

    user_id = get_user_id()
    payload = request.get_json(silent=True) or {}
    segments = payload.get("segments", [])
    if not segments:
        return jsonify({"error": "at least one timeline segment is required"}), 400
    print(f"[regenerate] job={job_id} received segments: {segments}", flush=True)

    new_image_paths = []
    new_durations = []
    new_text_sources = []
    old_text_sources = meta.get("text_sources")
    for seg in segments:
        kind = seg.get("kind")
        duration = float(seg.get("duration", 3.0))
        duration = max(duration, 0.5)
        edited_text = seg.get("text")
        text_for_slide = None
        try:
            if kind == "job":
                idx = int(seg["index"])
                path = meta["image_paths"][idx]
                original_text = old_text_sources[idx] if old_text_sources and idx < len(old_text_sources) else None
                if edited_text is not None and original_text is not None and edited_text.strip() and edited_text != original_text:
                    # Re-render this slide's text-card image with the new wording
                    new_card_path = os.path.join(job_dir, f"card_edit_{uuid.uuid4().hex[:8]}.png")
                    make_title_card(edited_text.strip(), WIDTH, HEIGHT, new_card_path)
                    path = new_card_path
                    text_for_slide = edited_text.strip()
                else:
                    text_for_slide = original_text
            elif kind == "asset":
                path = resolve_asset_path(user_id, "image", "library", seg["id"], None)
            elif kind == "default":
                path = os.path.join(DEFAULT_IMAGES_DIR, seg["file"])
            else:
                continue
        except (KeyError, IndexError, ValueError):
            continue
        if os.path.isfile(path):
            new_image_paths.append(path)
            new_durations.append(duration)
            new_text_sources.append(text_for_slide)

    if not new_image_paths:
        return jsonify({"error": "no valid images found in the submitted timeline"}), 400
    print(f"[regenerate] job={job_id} resolved durations: {new_durations} (sum={sum(new_durations):.2f}s)", flush=True)

    output_path = os.path.join(job_dir, "final_video.mp4")
    try:
        build_video(
            image_paths=new_image_paths,
            narration_path=meta["narration_path"],
            music_path=meta["music_path"],
            animate=meta["animate"],
            output_path=output_path,
            caption_text=meta["caption_text"],
            word_boundaries=meta["word_boundaries"],
            emoji_list=None,
            speaker_timeline=meta["speaker_timeline"],
            panel_texts=meta["panel_texts"],
            speed_factor=meta["speed_factor"],
            custom_durations=new_durations,
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"video rendering failed: {e.stderr.decode(errors='ignore')[-500:]}"}), 500

    # Persist the edited timeline so further tweaks build on top of this version
    meta["image_paths"] = new_image_paths
    meta["durations"] = new_durations
    meta["text_sources"] = new_text_sources if any(t is not None for t in new_text_sources) else None
    with open(os.path.join(job_dir, "meta.json"), "w") as f:
        json.dump(meta, f)

    return jsonify({"video_url": f"/download/{job_id}/final_video.mp4?v={uuid.uuid4().hex[:8]}"})


@app.route("/download/<job_id>/<filename>")
def download(job_id, filename):
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    response = send_from_directory(job_dir, filename, as_attachment=False)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


if __name__ == "__main__":
    app.run(debug=True, port=5000)