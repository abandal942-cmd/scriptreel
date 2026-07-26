import os
import re
from collections import Counter
import requests

PEXELS_API_URL = "https://api.pexels.com/v1/search"

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "and", "or", "but", "to",
    "of", "in", "on", "for", "with", "this", "that", "it", "you", "your",
    "we", "i", "today", "lets", "let's", "our", "so", "do", "does", "did",
    "what", "how", "why", "when", "where", "will", "can", "could", "would",
}


def _extract_query(sentence, max_words=4):
    """Turn a sentence into a short, image-search-friendly keyword phrase."""
    words = re.findall(r"[A-Za-z']+", sentence.lower())
    keywords = [w for w in words if w not in STOPWORDS and len(w) > 2]
    if not keywords:
        keywords = words
    return " ".join(keywords[:max_words]) or sentence[:40]


def _extract_topic(full_text, max_words=2):
    """
    Find the 1-2 most-repeated meaningful words across the whole script —
    this becomes a context anchor appended to every per-sentence search so
    ambiguous words get disambiguated (e.g. a script about the Python
    *language* keeps returning coding photos instead of snake photos).
    """
    words = re.findall(r"[A-Za-z']+", (full_text or "").lower())
    keywords = [w for w in words if w not in STOPWORDS and len(w) > 2]
    if not keywords:
        return ""
    counts = Counter(keywords)
    top = [w for w, _ in counts.most_common(max_words)]
    return " ".join(top)


def fetch_topic_images(sentences, out_dir, api_key=None, per_page=6, full_text=None):
    """
    For each sentence, search Pexels for a matching photo and download a
    result. Every query is anchored with the script's overall topic
    (derived from full_text) so ambiguous single words get disambiguated —
    e.g. "python" alone could mean the snake, but "python programming"
    reliably returns coding-related photos. Requests several candidates
    per search and skips any photo already used elsewhere in this video,
    so the same picture doesn't repeat across every scene. Returns a list
    of local file paths — sentences with no search results are simply
    skipped (not a hard failure), so the caller may get fewer images than
    sentences.
    """
    api_key = api_key or os.environ.get("PEXELS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No Pexels API key configured. Set the PEXELS_API_KEY environment variable "
            "(get a free key at https://www.pexels.com/api/)."
        )

    topic = _extract_topic(full_text or " ".join(sentences))

    headers = {"Authorization": api_key}
    image_paths = []
    used_photo_ids = set()
    for i, sentence in enumerate(sentences):
        query = _extract_query(sentence)
        if topic and topic not in query:
            query = f"{query} {topic}".strip()
        try:
            resp = requests.get(
                PEXELS_API_URL,
                headers=headers,
                params={"query": query, "per_page": per_page, "orientation": "landscape"},
                timeout=15,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
            if not photos:
                continue

            # Prefer a photo we haven't used yet in this video; only repeat
            # one as a last resort if every candidate is already used.
            chosen = next((p for p in photos if p["id"] not in used_photo_ids), photos[0])
            used_photo_ids.add(chosen["id"])

            img_url = chosen["src"]["large2x"]
            img_resp = requests.get(img_url, timeout=20)
            img_resp.raise_for_status()
            path = os.path.join(out_dir, f"webimg_{i}.jpg")
            with open(path, "wb") as f:
                f.write(img_resp.content)
            image_paths.append(path)
        except requests.RequestException:
            continue  # skip this one image rather than failing the whole video

    return image_paths