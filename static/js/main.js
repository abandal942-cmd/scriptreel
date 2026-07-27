// --- State ---
const state = {
  voiceMode: "default",
  imageMode: "default",
  voicecountMode: "1",
  teachervoiceMode: "default",
  studentvoiceMode: "default",
  textmodeMode: "combined",
  selectedLibraryImages: new Set(),
  selectedDefaultImages: new Set(),
  selectedLibraryVoiceId: null,
};

const DEFAULT_IMAGE_FILES = window.DEFAULT_IMAGE_FILES || [];

// --- Mode toggle buttons (voice / image) ---
document.querySelectorAll(".mode-toggle").forEach((group) => {
  const groupName = group.dataset.group; // 'voice' or 'image'
  group.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      group.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const mode = btn.dataset.mode;
      state[groupName + "Mode"] = mode;

      document.querySelectorAll(`[data-panel^="${groupName}-"]`).forEach((panel) => {
        panel.classList.toggle("hidden", panel.dataset.panel !== `${groupName}-${mode}`);
      });

      if (mode === "library") {
        loadLibrary(groupName);
      }
    });
  });
});

// --- Load "my uploads" library from server ---
async function loadLibrary(kind) {
  const res = await fetch(`/assets/${kind}`);
  const items = await res.json();

  if (kind === "voice") {
    const select = document.getElementById("voiceLibrarySelect");
    select.innerHTML = "";
    if (items.length === 0) {
      select.innerHTML = `<option value="">No uploaded voices yet</option>`;
      return;
    }
    items.forEach((item) => {
      const opt = document.createElement("option");
      opt.value = item.id;
      opt.textContent = item.label;
      select.appendChild(opt);
    });
  }

  if (kind === "image") {
    const grid = document.getElementById("libraryImageGrid");
    grid.innerHTML = "";
    if (items.length === 0) {
      grid.innerHTML = `<p class="hint">No uploaded images yet</p>`;
      return;
    }
    items.forEach((item) => {
      const div = document.createElement("div");
      div.className = "asset-thumb";
      div.dataset.id = item.id;
      div.innerHTML = `<img src="/assets/thumb/${item.id}" alt="${item.label}"><span class="tick">✓</span>`;
      div.addEventListener("click", () => {
        div.classList.toggle("selected");
        if (div.classList.contains("selected")) {
          state.selectedLibraryImages.add(item.id);
        } else {
          state.selectedLibraryImages.delete(item.id);
        }
      });
      grid.appendChild(div);
    });
  }
}

// --- Default stock image grid (rendered client-side from server-provided list) ---
function renderDefaultImages() {
  const grid = document.getElementById("defaultImageGrid");
  grid.innerHTML = "";
  DEFAULT_IMAGE_FILES.forEach((filename) => {
    const div = document.createElement("div");
    div.className = "asset-thumb";
    div.dataset.file = filename;
    div.innerHTML = `<img src="/static/default_images/${filename}" alt="${filename}"><span class="tick">✓</span>`;
    div.addEventListener("click", () => {
      div.classList.toggle("selected");
      if (div.classList.contains("selected")) {
        state.selectedDefaultImages.add(filename);
      } else {
        state.selectedDefaultImages.delete(filename);
      }
    });
    grid.appendChild(div);
  });
}
renderDefaultImages();

// --- Generate button ---
document.getElementById("generateBtn").addEventListener("click", async () => {
  let script, caption;
  if (state.textmodeMode === "combined") {
    script = document.getElementById("combinedTextInput").value.trim();
    caption = script; // same text drives both — guarantees word-for-word sync
  } else {
    script = document.getElementById("scriptInput").value.trim();
    caption = document.getElementById("captionInput").value.trim();
  }
  if (!script) {
    alert("Please enter the script (the text to be spoken).");
    return;
  }

  const formData = new FormData();
  formData.append("video_title", document.getElementById("videoTitleInput").value.trim());
  formData.append("speed", document.getElementById("speedSelect").value);
  formData.append("script", script);
  formData.append("caption_text", caption); // may be blank -> backend falls back to script
  formData.append("animate", document.getElementById("animateToggle").checked);
  formData.append("music_choice", document.getElementById("musicSelect").value);

  // Voice
  formData.append("voice_count", state.voicecountMode);
  if (state.voicecountMode === "2") {
    if (!/^\s*\*{0,2}\s*(teacher|student)\b[^:\n]*:/im.test(script)) {
      alert("2-voice mode needs the script formatted with 'Teacher:' / 'Student:' at the start of lines.");
      return;
    }
    formData.append("teacher_voice_mode", state.teachervoiceMode);
    if (state.teachervoiceMode === "default") {
      formData.append("teacher_voice_value", document.getElementById("teacherVoiceSelect").value);
    } else {
      const tFile = document.getElementById("teacherVoiceFileInput").files[0];
      if (!tFile) { alert("Please choose a Teacher voice sample to upload."); return; }
      formData.append("teacher_voice_file", tFile);
    }
    formData.append("student_voice_mode", state.studentvoiceMode);
    if (state.studentvoiceMode === "default") {
      formData.append("student_voice_value", document.getElementById("studentVoiceSelect").value);
    } else {
      const sFile = document.getElementById("studentVoiceFileInput").files[0];
      if (!sFile) { alert("Please choose a Student voice sample to upload."); return; }
      formData.append("student_voice_file", sFile);
    }
  } else {
    formData.append("voice_mode", state.voiceMode);
    if (state.voiceMode === "default") {
      formData.append("voice_value", document.getElementById("voiceSelect").value);
    } else if (state.voiceMode === "library") {
      formData.append("voice_value", document.getElementById("voiceLibrarySelect").value);
    } else if (state.voiceMode === "upload") {
      const file = document.getElementById("voiceFileInput").files[0];
      if (!file) { alert("Please choose a voice file to upload."); return; }
      formData.append("voice_file", file);
    }
  }

  // Images
  formData.append("image_mode", state.imageMode);
  if (state.imageMode === "default") {
    if (state.selectedDefaultImages.size === 0) { alert("Select at least one stock image."); return; }
    state.selectedDefaultImages.forEach((f) => formData.append("image_value", f));
  } else if (state.imageMode === "library") {
    if (state.selectedLibraryImages.size === 0) { alert("Select at least one uploaded image."); return; }
    state.selectedLibraryImages.forEach((id) => formData.append("image_value", id));
  } else if (state.imageMode === "upload") {
    const files = document.getElementById("imageFileInput").files;
    if (files.length === 0) { alert("Please choose images to upload."); return; }
    Array.from(files).forEach((f) => formData.append("image_files", f));
  }

  toggleLoading(true);
  try {
    const res = await fetch("/generate", { method: "POST", body: formData });
    const data = await res.json();
    if (data.error) {
      alert("Error: " + data.error);
      return;
    }
    document.getElementById("resultCard").classList.remove("hidden");
    document.getElementById("resultVideo").src = data.video_url;
    document.getElementById("downloadLink").href = data.video_url;
    document.getElementById("resultCard").scrollIntoView({ behavior: "smooth" });
    if (data.job_id) {
      await initTimelineEditor(data.job_id);
    }
  } catch (err) {
    alert("Something went wrong generating the video.");
    console.error(err);
  } finally {
    toggleLoading(false);
  }
});

function toggleLoading(show) {
  document.getElementById("loadingOverlay").classList.toggle("hidden", !show);
  document.getElementById("generateBtn").disabled = show;
}

// --- Timeline Editor ---
const PX_PER_SEC = 40;
const timelineState = {
  jobId: null,
  segments: [], // { kind: 'job'|'asset', ref: index|id, duration, thumbUrl }
};

async function initTimelineEditor(jobId) {
  timelineState.jobId = jobId;
  const res = await fetch(`/jobs/${jobId}/timeline`);
  const data = await res.json();
  if (data.error) return;

  timelineState.segments = data.segments.map((s) => ({
    kind: "job",
    ref: s.index,
    duration: s.duration,
    thumbUrl: s.thumb_url,
    text: s.text, // null unless this slide is a text-card with editable wording
  }));

  document.getElementById("timelineEditorCard").classList.remove("hidden");
  renderTimeline();
  loadMediaPanel();
}

function addSegmentToTimeline(seg, atIndex) {
  if (typeof atIndex === "number") {
    timelineState.segments.splice(atIndex, 0, seg);
  } else {
    timelineState.segments.push(seg);
  }
  renderTimeline();
}

function moveSegment(i, direction) {
  const j = i + direction;
  if (j < 0 || j >= timelineState.segments.length) return;
  const tmp = timelineState.segments[i];
  timelineState.segments[i] = timelineState.segments[j];
  timelineState.segments[j] = tmp;
  renderTimeline();
}

function renderTimeline() {
  const track = document.getElementById("timelineTrack");
  const ruler = document.getElementById("timelineRuler");
  track.innerHTML = "";
  ruler.innerHTML = "";
  track.classList.toggle("empty", timelineState.segments.length === 0);

  const totalDuration = timelineState.segments.reduce((sum, s) => sum + s.duration, 0);
  const totalWidth = Math.max(totalDuration * PX_PER_SEC, 320);
  ruler.style.minWidth = `${totalWidth}px`;
  track.style.minWidth = `${totalWidth}px`;

  const tickInterval = totalDuration > 120 ? 20 : totalDuration > 60 ? 10 : 5;
  for (let t = 0; t <= totalDuration + tickInterval; t += tickInterval) {
    const tick = document.createElement("div");
    tick.className = "tick";
    tick.style.left = `${t * PX_PER_SEC}px`;
    tick.textContent = `${t}s`;
    ruler.appendChild(tick);
  }

  timelineState.segments.forEach((seg, i) => {
    const clip = document.createElement("div");
    clip.className = "timeline-clip";
    const baseWidth = Math.max(seg.duration * PX_PER_SEC, 50);
    clip.style.width = `${seg.text != null ? Math.max(baseWidth, 170) : baseWidth}px`;
    clip.style.backgroundImage = `url(${seg.thumbUrl})`;
    clip.innerHTML = `
      <button class="seg-remove" title="Remove">✕</button>
      <div class="clip-move">
        <button class="clip-move-btn" data-dir="-1" title="Move left">‹</button>
        <button class="clip-move-btn" data-dir="1" title="Move right">›</button>
      </div>
      ${seg.text != null ? `<textarea class="clip-text-edit" placeholder="On-screen text for this slide…">${seg.text}</textarea>` : ""}
      <div class="clip-overlay">
        <input type="number" min="0.5" step="0.5" value="${seg.duration.toFixed(1)}">
        <span>sec</span>
      </div>
    `;
    if (seg.text != null) {
      clip.querySelector(".clip-text-edit").addEventListener("change", (e) => {
        seg.text = e.target.value;
      });
    }
    clip.querySelector("input").addEventListener("change", (e) => {
      const val = parseFloat(e.target.value);
      seg.duration = isNaN(val) || val < 0.5 ? 0.5 : val;
      renderTimeline();
    });
    clip.querySelector(".seg-remove").addEventListener("click", () => {
      timelineState.segments.splice(i, 1);
      renderTimeline();
    });
    clip.querySelectorAll(".clip-move-btn").forEach((btn) => {
      btn.addEventListener("click", () => moveSegment(i, parseInt(btn.dataset.dir, 10)));
    });

    // Dropping directly on this clip inserts the new image right before it,
    // instead of always appending at the end of the timeline.
    clip.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.stopPropagation();
      clip.classList.add("drag-over-clip");
    });
    clip.addEventListener("dragleave", () => clip.classList.remove("drag-over-clip"));
    clip.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      clip.classList.remove("drag-over-clip");
      const raw = e.dataTransfer.getData("text/plain");
      if (!raw) return;
      try {
        const dropped = JSON.parse(raw);
        addSegmentToTimeline({ kind: dropped.kind, ref: dropped.id, duration: 3.0, thumbUrl: dropped.thumbUrl }, i);
      } catch (err) {
        console.error("drop parse failed", err);
      }
    });

    track.appendChild(clip);
  });
}

async function loadMediaPanel() {
  const grid = document.getElementById("mediaPanelGrid");
  grid.innerHTML = "";
  const res = await fetch("/assets/image");
  const items = await res.json();
  items.forEach((item) => {
    const thumbUrl = `/assets/thumb/${item.id}`;
    const wrap = document.createElement("div");
    wrap.className = "media-item";
    wrap.draggable = true;
    wrap.innerHTML = `<img src="${thumbUrl}" alt="${item.label}"><button class="media-add-btn" title="Add to timeline">+</button>`;
    wrap.addEventListener("dragstart", (e) => {
      e.dataTransfer.effectAllowed = "copy";
      e.dataTransfer.setData("text/plain", JSON.stringify({ kind: "asset", id: item.id, thumbUrl }));
    });
    wrap.querySelector(".media-add-btn").addEventListener("click", () => {
      addSegmentToTimeline({ kind: "asset", ref: item.id, duration: 3.0, thumbUrl });
    });
    grid.appendChild(wrap);
  });
}

document.getElementById("mediaUploadInput").addEventListener("change", async (e) => {
  const files = Array.from(e.target.files);
  for (const file of files) {
    const fd = new FormData();
    fd.append("type", "image");
    fd.append("file", file);
    fd.append("label", file.name);
    await fetch("/assets/upload", { method: "POST", body: fd });
  }
  e.target.value = "";
  loadMediaPanel();
});

const timelineTrackEl = document.getElementById("timelineTrack");
timelineTrackEl.addEventListener("dragover", (e) => {
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
  timelineTrackEl.classList.add("drag-over");
});
timelineTrackEl.addEventListener("dragleave", () => {
  timelineTrackEl.classList.remove("drag-over");
});
timelineTrackEl.addEventListener("drop", (e) => {
  e.preventDefault();
  timelineTrackEl.classList.remove("drag-over");
  const raw = e.dataTransfer.getData("text/plain");
  if (!raw) return;
  try {
    const dropped = JSON.parse(raw);
    addSegmentToTimeline({ kind: dropped.kind, ref: dropped.id, duration: 3.0, thumbUrl: dropped.thumbUrl });
  } catch (err) {
    console.error("drop parse failed", err);
  }
});

document.getElementById("regenerateBtn").addEventListener("click", async () => {
  if (timelineState.segments.length === 0) {
    alert("Add at least one image to the timeline.");
    return;
  }
  const payload = {
    segments: timelineState.segments.map((s) => ({
      kind: s.kind,
      index: s.kind === "job" ? s.ref : undefined,
      id: s.kind === "asset" ? s.ref : undefined,
      duration: s.duration,
      text: s.text != null ? s.text : undefined,
    })),
  };
  document.getElementById("loadingText").textContent = "Regenerating your video…";
  toggleLoading(true);
  try {
    const res = await fetch(`/jobs/${timelineState.jobId}/regenerate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) {
      alert("Error: " + data.error);
      return;
    }
    const video = document.getElementById("timelineResultVideo");
    document.getElementById("timelineResultBox").classList.remove("hidden");
    video.pause();
    video.removeAttribute("src");
    video.load();
    video.src = data.video_url;
    video.load();
    document.getElementById("timelineDownloadLink").href = data.video_url;

    // keep the main Result section in sync too
    const mainVideo = document.getElementById("resultVideo");
    mainVideo.pause();
    mainVideo.removeAttribute("src");
    mainVideo.load();
    mainVideo.src = data.video_url;
    mainVideo.load();
    document.getElementById("downloadLink").href = data.video_url;

    document.getElementById("timelineResultBox").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    alert("Something went wrong regenerating the video.");
    console.error(err);
  } finally {
    toggleLoading(false);
    document.getElementById("loadingText").textContent = "Rendering your video…";
  }
});

// --- Sidebar: smooth scroll + active link highlight ---
document.querySelectorAll(".sidebar-link").forEach((link) => {
  link.addEventListener("click", (e) => {
    const targetId = link.getAttribute("href").slice(1);
    const target = document.getElementById(targetId);
    if (target) {
      e.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
});

const sidebarLinks = Array.from(document.querySelectorAll(".sidebar-link"));
const sidebarSections = sidebarLinks
  .map((link) => document.getElementById(link.getAttribute("href").slice(1)))
  .filter(Boolean);

function updateActiveSidebarLink() {
  let currentIndex = 0;
  sidebarSections.forEach((section, i) => {
    if (section.getBoundingClientRect().top <= 120) currentIndex = i;
  });
  sidebarLinks.forEach((link, i) => link.classList.toggle("active-link", i === currentIndex));
}
window.addEventListener("scroll", updateActiveSidebarLink);
updateActiveSidebarLink();