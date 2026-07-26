import os
import json
import subprocess
import tempfile

from utils.captions import make_word_chunk_overlay, render_emoji_png, make_title_card, make_speaker_badge, make_text_panel

# Landscape by default - matches the reference videos (1920x1080).
# Switch to (1080, 1920) if you want vertical/reels-style output instead.
WIDTH, HEIGHT = 1280, 720
FPS = 30
TRANSITION = 0.5   # seconds of crossfade between images
WORDS_PER_CHUNK = 2


def get_audio_duration(path: str) -> float:
    """Public wrapper so callers outside this module (app.py) can measure a clip's length."""
    return _ffprobe_duration(path)


def _ffprobe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    return float(data["format"]["duration"])


def _validate_narration(path: str):
    """Fail loudly (instead of silently shipping a mute video) if narration is missing or silent."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise RuntimeError("Narration audio was not created — voice generation failed.")
    duration = _ffprobe_duration(path)
    if duration < 0.2:
        raise RuntimeError("Narration audio is essentially empty (too short) — check the script text and voice source.")
    out = subprocess.run(
        ["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    mean_db = None
    for line in out.stderr.splitlines():
        if "mean_volume" in line:
            try:
                mean_db = float(line.split(":")[1].replace("dB", "").strip())
            except ValueError:
                pass
    if mean_db is not None and mean_db < -50:
        raise RuntimeError("Narration audio came back silent — the selected voice/file may be broken.")


def _make_image_segment(image_path: str, duration: float, animate: bool, out_path: str):
    """Create a single video segment from one image, full-bleed (no letterboxing), with optional Ken Burns zoom/pan."""
    if animate:
        zoom_frames = int(duration * FPS)
        # Explicit 2x target dims (not -2) so the crop always fully covers the frame, no black bars.
        vf = (
            f"scale={WIDTH*2}:{HEIGHT*2}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={WIDTH*2}:{HEIGHT*2},"
            f"zoompan=z='min(zoom+0.0015,1.3)':d={zoom_frames}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={WIDTH}x{HEIGHT}:fps={FPS}"
        )
    else:
        vf = (
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={WIDTH}:{HEIGHT}"
        )

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", image_path,
        "-t", str(duration),
        "-vf", vf,
        "-r", str(FPS),
        "-c:v", "libx264",
        "-crf", "16",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _make_split_image_segment(image_path, duration, panel_text, animate, side, out_path, tmp_dir):
    """
    Build one segment as a split layout: the photo sits on one half of the
    frame, a bold static text panel sits on the other half. `side`
    ('left'/'right') says which half the photo is on — callers alternate
    this per image for a left/right/left/right rhythm.

    Note: unlike the full-bleed segment builder, this never applies Ken
    Burns zoompan even if animate=True — combined with the hstack of two
    streams, zoompan here was memory-hungry enough to get OOM-killed on
    smaller Render instances. A static crop keeps this stable everywhere.
    """
    half_w = WIDTH // 2
    panel_path = os.path.join(tmp_dir, f"panel_{os.path.basename(out_path)}.png")
    make_text_panel(panel_text, half_w, HEIGHT, panel_path)

    img_vf = f"scale={half_w}:{HEIGHT}:force_original_aspect_ratio=increase:flags=lanczos,crop={half_w}:{HEIGHT}"

    if side == "left":
        filter_complex = f"[0:v]{img_vf},format=yuv420p[imgv];[1:v]format=yuv420p[txtv];[imgv][txtv]hstack=inputs=2[out]"
    else:
        filter_complex = f"[0:v]{img_vf},format=yuv420p[imgv];[1:v]format=yuv420p[txtv];[txtv][imgv]hstack=inputs=2[out]"

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-loop", "1", "-i", panel_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", str(duration),
        "-r", str(FPS),
        "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-threads", "1", "-threads", "1",
        "-pix_fmt", "yuv420p",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _concat_with_crossfade(segments, durations, out_path, transition):
    """Join image segments with a smooth crossfade instead of a hard cut, for a more polished feel.
    Returns the list of transition midpoint times (seconds), used later to time emoji bursts.
    `transition` must match what was used to pad each segment's rendered duration upstream,
    otherwise the crossfade overlap silently shortens the total video below the narration
    length and everything drifts out of sync as more images/transitions accumulate."""
    if len(segments) == 1:
        subprocess.run(["ffmpeg", "-y", "-i", segments[0], "-c", "copy", out_path], check=True, capture_output=True)
        return []

    inputs = []
    for s in segments:
        inputs += ["-i", s]

    running = durations[0]
    prev_label = "[0:v]"
    filter_parts = []
    transition_times = []
    for i in range(1, len(segments)):
        offset = max(running - transition, 0)
        transition_times.append(offset + transition / 2)
        out_label = f"[vx{i}]"
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition=fade:duration={transition}:offset={offset}{out_label}"
        )
        running = running + durations[i] - transition
        prev_label = out_label

    filter_complex = ";".join(filter_parts)
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex,
           "-map", prev_label, "-r", str(FPS),
           "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-threads", "1",
           "-pix_fmt", "yuv420p", out_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return transition_times


def concat_audio_segments(segment_paths, out_path):
    """
    Concatenate multiple narration audio clips, in order, into a single track.
    Used for 2-voice (Teacher/Student) dialogue mode, where each line is
    synthesized separately (possibly by different TTS engines/voices) and
    then stitched together. ffmpeg decodes each input itself, so segments
    don't need to share a codec or sample rate going in.
    """
    if len(segment_paths) == 1:
        cmd = ["ffmpeg", "-y", "-i", segment_paths[0], "-c:a", "libmp3lame", "-b:a", "192k", out_path]
        subprocess.run(cmd, check=True, capture_output=True)
        return

    inputs = []
    for p in segment_paths:
        inputs += ["-i", p]
    n = len(segment_paths)
    filter_complex = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[aout]"
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def add_emoji_decorations(video_in, emoji_list, video_duration, transition_times, out_path, tmp_dir):
    """
    Layer topic emoji onto the video two ways:
      - a few emoji gently float in the corners for the whole clip
      - one emoji "pops" briefly at each image transition
    Renders each emoji to a PNG first; if no color-emoji font is available
    on this machine, that render is skipped, and if none render at all,
    the video passes through unchanged rather than failing.
    """
    emoji_size = int(HEIGHT * 0.09)
    burst_size = int(HEIGHT * 0.16)

    emoji_pngs = []
    for i, emoji_char in enumerate(emoji_list):
        png_path = os.path.join(tmp_dir, f"emoji_{i}.png")
        if render_emoji_png(emoji_char, max(emoji_size, burst_size), png_path):
            emoji_pngs.append(png_path)

    if not emoji_pngs:
        subprocess.run(["ffmpeg", "-y", "-i", video_in, "-c", "copy", out_path], check=True, capture_output=True)
        return

    # Safe "parking spots" in the corners so emoji don't sit over the
    # caption band (bottom area) or the middle of the image.
    corners = [(0.05, 0.06), (0.80, 0.06), (0.05, 0.60), (0.80, 0.60)]

    inputs = ["-i", video_in]
    filter_parts = []
    prev_label = "[0:v]"
    input_index = 1

    # --- Floating decorations, one per corner ---
    float_count = min(len(emoji_pngs), len(corners))
    for i in range(float_count):
        inputs += ["-loop", "1", "-i", emoji_pngs[i]]
        cx, cy = corners[i]
        speed = 0.4 + i * 0.15
        phase = i * 1.7
        x_expr = f"W*{cx}+18*sin(t*{speed}+{phase})"
        y_expr = f"H*{cy}+14*cos(t*{speed * 0.8}+{phase})"
        scaled_label = f"[es{i}]"
        filter_parts.append(f"[{input_index}:v]scale={emoji_size}:{emoji_size}{scaled_label}")
        out_label = f"[fl{i}]"
        filter_parts.append(
            f"{prev_label}{scaled_label}overlay=x='{x_expr}':y='{y_expr}':format=auto{out_label}"
        )
        prev_label = out_label
        input_index += 1

    # --- Transition "pop" bursts, cycling through available emoji ---
    for i, t_mid in enumerate(transition_times):
        emoji_path = emoji_pngs[i % len(emoji_pngs)]
        inputs += ["-loop", "1", "-i", emoji_path]
        start = max(t_mid - 0.25, 0)
        faded_label = f"[bf{i}]"
        filter_parts.append(
            f"[{input_index}:v]scale={burst_size}:{burst_size},format=rgba,"
            f"fade=t=in:st={start:.3f}:d=0.15:alpha=1,"
            f"fade=t=out:st={start + 0.35:.3f}:d=0.15:alpha=1{faded_label}"
        )
        out_label = f"[pop{i}]"
        y_expr = "(H-h)/2-60"
        filter_parts.append(
            f"{prev_label}{faded_label}overlay=x='(W-w)/2':y='{y_expr}':"
            f"enable='between(t,{start:.3f},{start + 0.5:.3f})':format=auto{out_label}"
        )
        prev_label = out_label
        input_index += 1

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", prev_label, "-t", str(video_duration),
        "-r", str(FPS), "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-threads", "1",
        "-pix_fmt", "yuv420p", out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def generate_text_card_images(sentences, tmp_dir):
    """
    Render one full-frame typographic title-card image per sentence
    (reference style: solid background + big bold centered statement).
    Used as a stand-in for photos when the user wants a text-driven look.
    """
    paths = []
    for i, sentence in enumerate(sentences):
        p = os.path.join(tmp_dir, f"card_{i}.png")
        make_title_card(sentence, WIDTH, HEIGHT, p)
        paths.append(p)
    return paths


def apply_speaker_badges(video_in, speaker_timeline, video_duration, out_path, tmp_dir):
    """
    Overlay 'TEACHER' / 'STUDENT' corner badges for 2-voice dialogue mode.
    Whichever speaker is talking at a given moment shows the bold active
    badge; the other shows a faint inactive outline. speaker_timeline is a
    list of (speaker, start, end) tuples covering the whole narration.
    """
    if not speaker_timeline:
        subprocess.run(["ffmpeg", "-y", "-i", video_in, "-c", "copy", out_path], check=True, capture_output=True)
        return

    badge_w, badge_h = int(WIDTH * 0.16), int(HEIGHT * 0.07)
    badges = {}
    for speaker in ("teacher", "student"):
        for state in ("active", "inactive"):
            p = os.path.join(tmp_dir, f"badge_{speaker}_{state}.png")
            make_speaker_badge(speaker, badge_w, badge_h, p, active=(state == "active"))
            badges[(speaker, state)] = p

    teacher_windows = [(s, e) for spk, s, e in speaker_timeline if spk == "teacher"]
    student_windows = [(s, e) for spk, s, e in speaker_timeline if spk == "student"]

    def enable_expr(windows):
        return "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in windows) if windows else "0"

    inputs = ["-i", video_in]
    filter_parts = []
    prev_label = "[0:v]"
    input_index = 1

    # Teacher badge: top-left. Active version shown during teacher windows,
    # inactive version shown the rest of the time.
    inputs += ["-i", badges[("teacher", "active")]]
    inputs += ["-i", badges[("teacher", "inactive")]]
    filter_parts.append(f"{prev_label}[{input_index}:v]overlay=x=24:y=24:enable='{enable_expr(teacher_windows)}'[t1]")
    filter_parts.append(f"[t1][{input_index+1}:v]overlay=x=24:y=24:enable='not({enable_expr(teacher_windows)})'[t2]")
    prev_label = "[t2]"
    input_index += 2

    # Student badge: top-right.
    inputs += ["-i", badges[("student", "active")]]
    inputs += ["-i", badges[("student", "inactive")]]
    x_expr = f"W-w-24"
    filter_parts.append(f"{prev_label}[{input_index}:v]overlay=x='{x_expr}':y=24:enable='{enable_expr(student_windows)}'[s1]")
    filter_parts.append(f"[s1][{input_index+1}:v]overlay=x='{x_expr}':y=24:enable='not({enable_expr(student_windows)})'[s2]")
    prev_label = "[s2]"

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", prev_label, "-t", str(video_duration),
        "-r", str(FPS), "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-threads", "1",
        "-pix_fmt", "yuv420p", out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _mix_audio(narration_path, music_path, duration, out_path):
    if music_path and os.path.isfile(music_path):
        cmd = [
            "ffmpeg", "-y",
            "-i", narration_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex",
            "[1:a]volume=0.18[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "[aout]",
            "-t", str(duration),
            out_path,
        ]
    else:
        cmd = ["ffmpeg", "-y", "-i", narration_path, "-t", str(duration), out_path]
    subprocess.run(cmd, check=True, capture_output=True)


def _build_caption_chunks(caption_text, narration_duration, word_boundaries=None, words_per_chunk=WORDS_PER_CHUNK):
    """
    Split caption text into small (1-2 word) chunks with start/end times, so
    text appears progressively in sync with speech instead of all at once.

    When real per-word timestamps are available (edge-tts default voice),
    chunks are built straight from those — using the engine's own word
    text and timing rather than re-matching against caption_text, since
    minor tokenization differences (punctuation, contractions) used to
    cause a silent, inaccurate fallback to even-spacing. Only when no
    timestamps exist at all (cloned voice, dialogue mode) do we fall back
    to spreading caption words evenly across the narration length.
    """
    if word_boundaries:
        chunks = []
        for i in range(0, len(word_boundaries), words_per_chunk):
            group = word_boundaries[i:i + words_per_chunk]
            text = " ".join(w["text"] for w in group)
            start = group[0]["start"]
            end = group[-1]["start"] + group[-1]["duration"]
            chunks.append((start, end, text))
        return chunks

    words = caption_text.split()
    if not words:
        return []

    per_chunk = narration_duration / max(len(range(0, len(words), words_per_chunk)), 1)
    chunks = []
    t = 0.0
    for i in range(0, len(words), words_per_chunk):
        text = " ".join(words[i:i + words_per_chunk])
        chunks.append((t, t + per_chunk, text))
        t += per_chunk
    return chunks


def _burn_progressive_captions(video_in, chunks, out_path):
    """Overlay each caption chunk only during its own time window (karaoke-style reveal)."""
    if not chunks:
        subprocess.run(["ffmpeg", "-y", "-i", video_in, "-c", "copy", out_path], check=True, capture_output=True)
        return

    with tempfile.TemporaryDirectory() as tmp:
        inputs = ["-i", video_in]
        filter_parts = []
        prev_label = "[0:v]"
        for i, (start, end, text) in enumerate(chunks, start=1):
            png_path = os.path.join(tmp, f"chunk_{i}.png")
            make_word_chunk_overlay(text, WIDTH, HEIGHT, png_path)
            inputs += ["-i", png_path]
            out_label = f"[c{i}]"
            filter_parts.append(
                f"{prev_label}[{i}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'{out_label}"
            )
            prev_label = out_label

        filter_complex = ";".join(filter_parts)
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex,
               "-map", prev_label, "-r", str(FPS),
               "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-threads", "1",
               "-pix_fmt", "yuv420p", out_path]
        subprocess.run(cmd, check=True, capture_output=True)


def _atempo_filter_chain(factor):
    """
    ffmpeg's atempo filter only accepts 0.5-2.0 per instance; chain multiple
    instances to reach factors outside that range (e.g. 0.3x needs two
    chained stages).
    """
    stages = []
    remaining = factor
    if remaining < 0.5:
        while remaining < 0.5:
            stages.append(0.5)
            remaining /= 0.5
        stages.append(remaining)
    elif remaining > 2.0:
        while remaining > 2.0:
            stages.append(2.0)
            remaining /= 2.0
        stages.append(remaining)
    else:
        stages.append(remaining)
    return ",".join(f"atempo={f:.4f}" for f in stages)


def apply_speed_change(video_in, speed_factor, out_path):
    """
    Retime the whole finished video (video + audio together) by
    speed_factor — e.g. 1.25 plays 25% faster, 0.75 plays 25% slower.
    Since this runs on the fully composed video (captions already
    burned in), captions and audio stay perfectly in sync at any speed.
    """
    if speed_factor == 1.0:
        subprocess.run(["ffmpeg", "-y", "-i", video_in, "-c", "copy", out_path], check=True, capture_output=True)
        return

    setpts_factor = 1.0 / speed_factor
    atempo_chain = _atempo_filter_chain(speed_factor)
    filter_complex = f"[0:v]setpts={setpts_factor:.6f}*PTS[v];[0:a]{atempo_chain}[a]"
    cmd = [
        "ffmpeg", "-y", "-i", video_in,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-r", str(FPS), "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-threads", "1",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def build_video(image_paths, narration_path, music_path, animate, output_path,
                 caption_text=None, word_boundaries=None, emoji_list=None,
                 speaker_timeline=None, panel_texts=None, speed_factor=1.0,
                 custom_durations=None):
    _validate_narration(narration_path)
    narration_duration = _ffprobe_duration(narration_path)
    per_image = max(narration_duration / len(image_paths), 1.0)

    with tempfile.TemporaryDirectory() as tmp:
        # Crossfades overlap adjacent clips, which shortens the combined
        # timeline unless we compensate by rendering each non-last segment
        # slightly longer (by exactly the transition amount) up front.
        # Without this, total video length drifts below narration_duration
        # and everything (captions, emoji, badges) falls increasingly out
        # of sync as more images/transitions accumulate.
        base_durations = custom_durations if custom_durations else [per_image] * len(image_paths)
        transition = min(TRANSITION, min(base_durations) * 0.3) if len(image_paths) > 1 else 0.0

        segments = []
        durations = []
        for i, img in enumerate(image_paths):
            is_last = (i == len(image_paths) - 1)
            seg_duration = base_durations[i] if is_last else base_durations[i] + transition
            seg_path = os.path.join(tmp, f"seg_{i}.mp4")
            if panel_texts:
                panel_text = panel_texts[i % len(panel_texts)]
                side = "left" if i % 2 == 0 else "right"
                _make_split_image_segment(img, seg_duration, panel_text, animate, side, seg_path, tmp)
            else:
                _make_image_segment(img, seg_duration, animate, seg_path)
            segments.append(seg_path)
            durations.append(seg_duration)

        base_video = os.path.join(tmp, "base.mp4")
        transition_times = _concat_with_crossfade(segments, durations, base_video, transition)

        video_with_badges = base_video
        if speaker_timeline:
            badged_video = os.path.join(tmp, "badged.mp4")
            apply_speaker_badges(base_video, speaker_timeline, narration_duration, badged_video, tmp)
            video_with_badges = badged_video

        video_with_emoji = video_with_badges
        if emoji_list:
            decorated_video = os.path.join(tmp, "decorated.mp4")
            add_emoji_decorations(video_with_badges, emoji_list, narration_duration, transition_times, decorated_video, tmp)
            video_with_emoji = decorated_video

        video_with_text = video_with_emoji
        if caption_text and caption_text.strip():
            chunks = _build_caption_chunks(caption_text, narration_duration, word_boundaries)
            captioned_video = os.path.join(tmp, "captioned.mp4")
            _burn_progressive_captions(video_with_emoji, chunks, captioned_video)
            video_with_text = captioned_video

        mixed_audio = os.path.join(tmp, "mixed.aac")
        _mix_audio(narration_path, music_path, narration_duration, mixed_audio)

        muxed_path = os.path.join(tmp, "muxed.mp4") if speed_factor != 1.0 else output_path
        cmd = [
            "ffmpeg", "-y",
            "-i", video_with_text, "-i", mixed_audio,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-threads", "1",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            muxed_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        if speed_factor != 1.0:
            apply_speed_change(muxed_path, speed_factor, output_path)

    return output_path