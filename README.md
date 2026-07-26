# ScriptReel

Turn a script into a narrated video: pick a voice (system voice, one you've
uploaded before, or a fresh upload), pick images the same way (stock,
previously uploaded, or fresh upload), optionally animate them with a Ken
Burns pan/zoom, add background music, and get an .mp4 back.

## 1. Local setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

You also need **ffmpeg** installed and on your PATH:
- Windows: download from ffmpeg.org, add the `bin` folder to PATH
- Mac: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

Run it:
```bash
python app.py
```
Open http://localhost:5000

## 2. Add your own defaults

- Put a few stock photos in `static/default_images/` (jpg/png) — these
  show up as the "Stock images" option.
- Put a few royalty-free mp3 tracks in `static/audio/` — these show up
  in the background-music dropdown.
- Add/remove default TTS voices in `utils/tts_utils.py` (`DEFAULT_VOICES`).
  Run `edge-tts --list-voices` to see all available voices.

## 3. How the "upload / library / default" picker works (voice + images)

Every asset (voice or image) a user uploads is saved to `app.db` (SQLite)
under their session, in the `user_assets` table. Next time they visit,
"My uploads" shows everything they've saved before, so they don't have to
re-upload. This logic lives in `app.py` (`/assets/upload`, `/assets/<type>`)
and is shared by both voice and image pickers.

Note: an uploaded "voice" is used as-is as the narration track (no
text-to-speech, no voice cloning) — so it should be a recording of someone
actually reading the script. True AI voice cloning would need a paid API
(e.g. ElevenLabs voice cloning) — not included here.

## 4. Deploying to Render

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- ffmpeg isn't preinstalled on Render's default image — add a file named
  `Aptfile` containing `ffmpeg`, or switch to a Docker deploy with an
  `apt-get install -y ffmpeg` step in the Dockerfile.
- `app.db` and `/uploads` live on local disk, which is **ephemeral on
  Render** (wiped on redeploy). For real persistence, point `UPLOAD_DIR`
  and `DB_PATH` at a Render Disk, or swap SQLite for a hosted DB and
  uploads for S3/Cloudinary.

## 5. About animation quality

The Ken Burns pan/zoom here is a real-but-simple effect — it moves a
static image, it doesn't generate new motion. If you want the
cinematic/AI-generated look (characters moving, camera depth, etc.),
that requires calling a paid AI video API (Runway, Kling, Luma) from
`utils/video_builder.py` instead of the ffmpeg zoompan filter — a bigger
next step, not included in this MVP.
