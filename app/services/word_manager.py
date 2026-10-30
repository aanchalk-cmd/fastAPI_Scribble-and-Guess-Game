"""
WordManager — loads category word lists from JSON and serves random picks.

Thread-safe for concurrent room access across multiple game rooms.
"""

from __future__ import annotations

import json
import random
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional


class CategoryNotFoundError(KeyError):
    """Raised when a requested word category does not exist."""

    def __init__(self, category: str, available: Optional[List[str]] = None):
        self.category = category
        self.available = available or []
        msg = f"Category '{category}' not found"
        if self.available:
            msg += f". Available: {', '.join(self.available)}"
        super().__init__(msg)


# Old category ids that rooms or clients may still send: the original flat ones,
# and the "<genre>_<type>" ids from when movies and characters were separate.
CATEGORY_ALIASES = {
    "movies": "hollywood",
    "characters": "hollywood",
}
LEGACY_KIND_SUFFIXES = ("_movies", "_characters")


def _clean_words(words) -> List[str]:
    """Preserve order, drop blanks/duplicates (case-insensitive)."""
    seen = set()
    cleaned: List[str] = []
    for word in words:
        if not isinstance(word, str):
            continue
        text = word.strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


class WordManager:
    """
    In-memory word pool loaded once from words.json.

    words.json is grouped as {genre: {type: [words]}}, e.g.
    {"bollywood": {"movies": [...], "characters": [...]}}. Each genre is one
    category (e.g. "bollywood") whose pool combines all of its types.
    A plain {category: [words]} entry is still accepted as a flat category.
    """

    def __init__(self, words_path: Optional[Path] = None):
        if words_path is None:
            words_path = Path(__file__).resolve().parent.parent / "data" / "words.json"
        self._words_path = Path(words_path)
        self._hints_path = self._words_path.with_name("hints.json")
        self._lock = threading.RLock()
        self._categories: Dict[str, List[str]] = {}
        # genre -> "movies"|"characters" -> UPPER title -> hint sentence
        self._hints: Dict[str, Dict[str, Dict[str, str]]] = {}
        self._hints_mtime: Optional[float] = None
        self.load()

    def load(self) -> None:
        """Load (or reload) words from disk into memory."""
        with self._lock:
            if not self._words_path.exists():
                raise FileNotFoundError(f"Words file not found: {self._words_path}")

            with self._words_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)

            if not isinstance(raw, dict):
                raise ValueError("words.json must be a JSON object of category -> word list")

            categories: Dict[str, List[str]] = {}
            for name, value in raw.items():
                if isinstance(value, dict):
                    combined = [
                        word
                        for words in value.values()
                        if isinstance(words, list)
                        for word in words
                    ]
                    cleaned = _clean_words(combined)
                    if cleaned:
                        categories[str(name)] = cleaned
                elif isinstance(value, list):
                    cleaned = _clean_words(value)
                    if cleaned:
                        categories[str(name)] = cleaned

            if not categories:
                raise ValueError("words.json contained no valid categories")

            self._categories = categories
            print(
                f"[WORD_MANAGER] Loaded {len(self._categories)} categories "
                f"from {self._words_path}"
            )
            for name, words in self._categories.items():
                print(f"[WORD_MANAGER]   {name}: {len(words)} words")
            self._load_hints()

    def _load_hints(self) -> None:
        """Load famous-line hints. A missing file just means no hints yet."""
        self._hints = {}
        if not self._hints_path.exists():
            print(f"[WORD_MANAGER] No hints file at {self._hints_path}")
            return
        with self._hints_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return
        loaded = 0
        for genre, kinds in raw.items():
            if not isinstance(kinds, dict):
                continue
            bucket: Dict[str, Dict[str, str]] = {}
            for kind, pairs in kinds.items():
                if not isinstance(pairs, dict):
                    continue
                kind_map: Dict[str, str] = {}
                for title, hint in pairs.items():
                    if not isinstance(title, str) or not isinstance(hint, str):
                        continue
                    text = " ".join(hint.split())
                    key = title.strip().upper()
                    if not key or not text:
                        continue
                    kind_map[key] = text
                if kind_map:
                    bucket[str(kind)] = kind_map
                    loaded += len(kind_map)
            if bucket:
                self._hints[str(genre)] = bucket
        print(f"[WORD_MANAGER] Loaded {loaded} hints from {self._hints_path}")

    def _ensure_hints_loaded(self) -> None:
        """Pick up edits to hints.json without restarting the server."""
        try:
            mtime = self._hints_path.stat().st_mtime
        except OSError:
            self._hints = {}
            self._hints_mtime = None
            return
        if self._hints_mtime == mtime:
            return
        self._load_hints()
        self._hints_mtime = mtime

    @staticmethod
    def _hint_key(title: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", (title or "").upper())

    def _find_hint(self, names: List[str], key: str, loose: str) -> Optional[str]:
        for name in names:
            kinds = self._hints.get(name) or {}
            for kind in ("movies", "characters"):
                bucket = kinds.get(kind) or {}
                hint = bucket.get(key)
                if hint:
                    return hint
                if loose:
                    for stored, text in bucket.items():
                        if self._hint_key(stored) == loose:
                            return text
        return None

    def get_hint(self, title: str, categories: Optional[List[str]] = None) -> Optional[str]:
        """
        Famous line or scene for this title.

        The room's categories are checked first. If that title's line is stored
        under another genre, that line is used too. Movies are checked before
        characters when the same name exists in both. Case and punctuation
        are ignored, because a round stores the title in uppercase.
        """
        key = " ".join((title or "").strip().upper().split())
        if not key:
            return None
        loose = self._hint_key(key)
        with self._lock:
            self._ensure_hints_loaded()
            names: List[str] = []
            for raw in categories or []:
                resolved = self.resolve_alias(raw)
                if resolved and resolved not in names:
                    names.append(resolved)
            if not names:
                names = list(self._hints.keys())
            found = self._find_hint(names, key, loose)
            if found:
                return found
            others = [name for name in self._hints if name not in names]
            return self._find_hint(others, key, loose)

    def get_categories(self) -> List[str]:
        """Return sorted list of available category names."""
        with self._lock:
            return sorted(self._categories.keys())

    @staticmethod
    def resolve_alias(category: Optional[str]) -> Optional[str]:
        if not category:
            return category
        if category in CATEGORY_ALIASES:
            return CATEGORY_ALIASES[category]
        for suffix in LEGACY_KIND_SUFFIXES:
            if category.endswith(suffix):
                return category[: -len(suffix)]
        return category

    def _require_category(self, category: str) -> List[str]:
        category = self.resolve_alias(category)
        with self._lock:
            if category not in self._categories:
                raise CategoryNotFoundError(category, self.get_categories())
            # Return a shallow copy so callers cannot mutate the pool
            return list(self._categories[category])

    def get_random_word(self, category: str) -> str:
        """Return one random word from the given category (uppercase for game matching)."""
        pool = self._require_category(category)
        word = random.choice(pool)
        return word.strip().upper()

    def get_random_words(self, category: str, count: int = 3) -> List[str]:
        """
        Return up to `count` unique random words from the category.
        If fewer words exist than requested, returns all available words.
        """
        if count < 0:
            raise ValueError("count must be >= 0")
        pool = self._require_category(category)
        sample_size = min(count, len(pool))
        if sample_size == 0:
            return []
        picked = random.sample(pool, sample_size)
        return [w.strip().upper() for w in picked]

    def get_random_words_from(self, categories: List[str], count: int = 3) -> List[str]:
        """
        Like get_random_words, but samples from the combined pool of several
        categories (a room may pick more than one). Unknown ids are skipped.
        """
        if count < 0:
            raise ValueError("count must be >= 0")
        pool: List[str] = []
        seen = set()
        for category in categories:
            if not self.has_category(category):
                continue
            for word in self._require_category(category):
                key = word.strip().upper()
                if key not in seen:
                    seen.add(key)
                    pool.append(word)
        if not pool:
            return self.get_random_words(self.normalize_category(None), count=count)
        # The drawer is offered titles that have a stored hint first, so the
        # Hint button has a line to show. Titles without one fill any gap.
        hinted = [word for word in pool if self.get_hint(word, categories)]
        hinted_keys = {word.strip().upper() for word in hinted}
        rest = [word for word in pool if word.strip().upper() not in hinted_keys]
        picked: List[str] = []
        if hinted:
            picked.extend(random.sample(hinted, min(count, len(hinted))))
        if len(picked) < count and rest:
            picked.extend(random.sample(rest, min(count - len(picked), len(rest))))
        return [word.strip().upper() for word in picked]

    def parse_categories(self, value: Optional[str]) -> List[str]:
        """
        Parse a comma-separated category list (e.g. "bollywood,anime")
        into valid, de-duplicated ids. Falls back to [default] if none are valid.
        """
        found: List[str] = []
        for raw in str(value or "").split(","):
            category = self.resolve_alias(raw.strip())
            if category and self.has_category(category) and category not in found:
                found.append(category)
        return found or [self.normalize_category(None)]

    def has_category(self, category: str) -> bool:
        with self._lock:
            return self.resolve_alias(category) in self._categories

    def normalize_category(self, category: Optional[str], default: str = "hollywood") -> str:
        """
        Validate a category name; fall back to default (or first available) if invalid.
        """
        category = self.resolve_alias(category)
        default = self.resolve_alias(default)
        with self._lock:
            if category and category in self._categories:
                return category
            if default in self._categories:
                return default
            if self._categories:
                return next(iter(self._categories.keys()))
            raise CategoryNotFoundError(category or default, [])


# Module-level singleton; FastAPI startup calls load() / ensures it exists.
word_manager = WordManager()
