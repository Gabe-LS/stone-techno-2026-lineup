"""Chat moderation pipeline: word filter + OpenAI omni-moderation + strike system."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

BLOCKLIST_PATH = Path(__file__).resolve().parent / "chat" / "blocklist.txt"

CHAR_SUBSTITUTIONS = {
    "@": "a",
    "0": "o",
    "1": "i",
    "3": "e",
    "$": "s",
    "5": "s",
    "!": "i",
    "4": "a",
    "7": "t",
    "+": "t",
}

DRUG_TERMS = {
    "mdma",
    "molly",
    "ecstasy",
    "ket",
    "ketamine",
    "speed",
    "amphetamine",
    "coke",
    "cocaine",
    "acid",
    "lsd",
    "pills",
    "dealer",
    "plug",
    "score",
    "stash",
    "xanax",
    "benzo",
    "meth",
    "crystal",
    "heroin",
    "fentanyl",
    "ghb",
    "poppers",
    "nitrous",
    "whippets",
    "shrooms",
    "mushrooms",
    "2cb",
    "dmt",
    "rolling",
    "tripping",
    "dosing",
    "railing",
    "snorting",
    "bumps",
    "lines",
    "baggie",
    "gram",
    "half g",
    "quarter",
    "eighth",
}


def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    text = text.lower()
    text = _strip_diacritics(text)
    for char, replacement in CHAR_SUBSTITUTIONS.items():
        text = text.replace(char, replacement)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class WordFilter:
    def __init__(self, blocklist_path: str | Path | None = None):
        self._terms: set[str] = set()
        self._drug_terms: set[str] = set()
        self._load_builtin_drugs()
        if blocklist_path:
            self._load_file(blocklist_path)

    def _load_builtin_drugs(self) -> None:
        for term in DRUG_TERMS:
            self._drug_terms.add(_normalize(term))
            self._terms.add(_normalize(term))

    def _load_file(self, path: str | Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            normalized = _normalize(line)
            if normalized:
                self._terms.add(normalized)

    def check(self, text: str) -> dict | None:
        normalized = _normalize(text)
        words = normalized.split()

        for term in self._drug_terms:
            term_words = term.split()
            for i in range(len(words) - len(term_words) + 1):
                if words[i : i + len(term_words)] == term_words:
                    return {"matched": term, "is_drug": True}
            if term in normalized:
                return {"matched": term, "is_drug": True}

        for term in self._terms - self._drug_terms:
            term_words = term.split()
            for i in range(len(words) - len(term_words) + 1):
                if words[i : i + len(term_words)] == term_words:
                    return {"matched": term, "is_drug": False}
            if term in normalized:
                return {"matched": term, "is_drug": False}

        return None

    @property
    def term_count(self) -> int:
        return len(self._terms)


# --- OpenAI Moderation ---

OPENAI_THRESHOLDS = {
    "sexual/minors": 0.50,
    "violence/graphic": 0.50,
    "sexual": 0.80,
    "hate": 0.80,
    "harassment": 0.80,
    "harassment/threatening": 0.80,
    "self-harm": 0.80,
    "self-harm/intent": 0.80,
    "self-harm/instructions": 0.80,
    "violence": 0.80,
    "illicit": 0.70,
    "illicit/violent": 0.70,
}

INSTANT_BAN_CATEGORIES = {"sexual/minors", "violence/graphic"}


async def check_openai_moderation(
    text: str, image_url: str | None = None
) -> dict | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        import openai

        client = openai.AsyncOpenAI(api_key=api_key)

        input_content: list[dict] = [{"type": "text", "text": text}]
        if image_url:
            input_content.append({"type": "image_url", "image_url": {"url": image_url}})

        response = await client.moderations.create(
            model="omni-moderation-latest",
            input=input_content,
        )

        if not response.results:
            return None

        result = response.results[0]
        scores = result.category_scores

        for category, threshold in OPENAI_THRESHOLDS.items():
            score = getattr(scores, category.replace("/", "_").replace("-", "_"), 0)
            if score and score >= threshold:
                return {
                    "category": category,
                    "score": score,
                    "instant_ban": category in INSTANT_BAN_CATEGORIES,
                }

        return None

    except Exception:
        return None


# --- Strike Logic ---


def process_strike(
    db,
    user_id: str,
    reason: str,
    detail: str | None,
    is_drug: bool = False,
) -> dict:
    from chat_db import add_strike, mute_user, ban_user, get_user

    user = get_user(db, user_id)
    if not user:
        return {"action": "none"}

    if is_drug:
        count = add_strike(db, user_id, reason, detail)
        if count >= 2:
            ban_user(
                db,
                user_id,
                user["provider"],
                user["provider_id"],
                f"Auto-ban: repeated drug-related content ({detail})",
                user["device_fingerprint"],
            )
            return {"action": "ban", "strike_count": count, "reason": reason}
        return {
            "action": "strike",
            "strike_count": count,
            "reason": reason,
            "message": "Drug-related content is not allowed. Next offense will result in a permanent ban.",
        }

    count = add_strike(db, user_id, reason, detail)

    if count >= 3:
        ban_user(
            db,
            user_id,
            user["provider"],
            user["provider_id"],
            f"Auto-ban: 3 strikes ({detail})",
            user["device_fingerprint"],
        )
        return {"action": "ban", "strike_count": count, "reason": reason}

    if count == 2:
        mute_user(db, user_id, minutes=30)
        return {
            "action": "mute",
            "strike_count": count,
            "reason": reason,
            "message": "Your message was flagged. You are muted for 30 minutes.",
        }

    return {
        "action": "strike",
        "strike_count": count,
        "reason": reason,
        "message": "Your message was flagged. Repeated violations will result in a ban.",
    }


# --- Full Pipeline ---

_word_filter: WordFilter | None = None


def get_word_filter() -> WordFilter:
    global _word_filter
    if _word_filter is None:
        _word_filter = WordFilter(BLOCKLIST_PATH if BLOCKLIST_PATH.exists() else None)
    return _word_filter


def reload_word_filter() -> None:
    global _word_filter
    _word_filter = WordFilter(BLOCKLIST_PATH if BLOCKLIST_PATH.exists() else None)


async def moderate_message(
    db, user_id: str, text: str, image_url: str | None = None
) -> dict:
    from chat_db import is_muted

    if is_muted(db, user_id):
        return {
            "allowed": False,
            "reason": "You are temporarily muted.",
            "action": "muted",
        }

    wf = get_word_filter()
    match = wf.check(text)
    if match:
        result = process_strike(
            db, user_id, "word_filter", match["matched"], is_drug=match["is_drug"]
        )
        return {
            "allowed": False,
            "reason": result.get("message", "Message blocked by content filter."),
            "action": result["action"],
            "strike_count": result.get("strike_count"),
        }

    try:
        ai_result = await check_openai_moderation(text, image_url)
    except Exception:
        ai_result = None
    if ai_result:
        if ai_result["instant_ban"]:
            from chat_db import ban_user, get_user

            user = get_user(db, user_id)
            if user:
                ban_user(
                    db,
                    user_id,
                    user["provider"],
                    user["provider_id"],
                    f"Auto-ban: {ai_result['category']} (score {ai_result['score']:.2f})",
                    user["device_fingerprint"],
                )
            return {
                "allowed": False,
                "reason": "You have been permanently banned.",
                "action": "ban",
            }

        result = process_strike(db, user_id, "ai_moderation", ai_result["category"])
        return {
            "allowed": False,
            "reason": result.get("message", "Message blocked by AI moderation."),
            "action": result["action"],
            "strike_count": result.get("strike_count"),
        }

    return {"allowed": True}
