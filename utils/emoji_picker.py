"""
Small keyword -> emoji lookup used to auto-decorate a video with emoji that
match the script's topic, when the user hasn't picked their own. Not
exhaustive — easy to extend by adding entries to KEYWORD_EMOJI.
"""

KEYWORD_EMOJI = {
    # tech / programming
    "python": "🐍", "code": "💻", "coding": "💻", "programming": "💻",
    "software": "🖥️", "computer": "🖥️", "ai": "🤖", "robot": "🤖",
    "data": "📊", "internet": "🌐", "app": "📱", "phone": "📱",
    "cloud": "☁️", "security": "🔒", "cyber": "🛡️", "bug": "🐛",
    "network": "🌐", "startup": "🚀", "launch": "🚀", "website": "🌐",
    # business / money
    "money": "💰", "business": "💼", "finance": "💵", "market": "📈",
    "stock": "📈", "growth": "📈", "sale": "🏷️", "profit": "💹",
    "bank": "🏦", "invest": "💹",
    # education
    "school": "🏫", "teacher": "🍎", "student": "🎓", "learn": "📚",
    "book": "📚", "study": "📖", "exam": "📝", "science": "🔬",
    "math": "➗", "history": "📜", "lesson": "📚",
    # food / travel
    "food": "🍽️", "recipe": "🍳", "cooking": "👨‍🍳", "travel": "✈️",
    "trip": "🧳", "beach": "🏖️", "mountain": "⛰️",
    # nature / health
    "nature": "🌿", "tree": "🌳", "animal": "🐾", "health": "🩺",
    "fitness": "💪", "yoga": "🧘", "doctor": "🩺", "medicine": "💊",
    # music / entertainment
    "music": "🎵", "movie": "🎬", "game": "🎮", "sport": "⚽",
    "football": "⚽", "cricket": "🏏",
    # general positive
    "win": "🏆", "success": "🏆", "idea": "💡", "love": "❤️",
    "happy": "😊", "celebrate": "🎉",
}

FALLBACK_EMOJI = ["✨", "🎬", "🔥"]


def _extract_manual_emoji(raw):
    """Pull out just the emoji characters a user typed, ignoring stray text/spaces."""
    seen = []
    for ch in raw:
        if ord(ch) > 0x2100 and ch not in seen:  # rough emoji/symbol range filter
            seen.append(ch)
    return seen


def pick_emojis(script_text, manual_emojis=None, max_count=6):
    """
    Decide which emoji to decorate the video with:
    - If the user typed emoji manually, use those (in the order typed).
    - Otherwise scan the script for known topic keywords and collect
      matching emoji, in order of first appearance.
    - If nothing matches either way, fall back to a small generic set so
      the video still gets some decoration.
    """
    if manual_emojis:
        manual = _extract_manual_emoji(manual_emojis)
        if manual:
            return manual[:max_count]

    text = (script_text or "").lower()
    found = []
    for keyword, emoji in KEYWORD_EMOJI.items():
        if keyword in text and emoji not in found:
            found.append(emoji)
        if len(found) >= max_count:
            break

    return found if found else FALLBACK_EMOJI[:max_count]