"""
WordManager — loads category word lists from JSON and serves random picks.

Thread-safe for concurrent room access across multiple game rooms.
"""

from __future__ import annotations

import json
import random
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


class WordManager:
    """In-memory word pool loaded once from words.json."""

    def __init__(self, words_path: Optional[Path] = None):
        if words_path is None:
            words_path = Path(__file__).resolve().parent.parent / "data" / "words.json"
        self._words_path = Path(words_path)
        self._lock = threading.RLock()
        self._categories: Dict[str, List[str]] = {}
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
            for category, words in raw.items():
                if not isinstance(words, list):
                    continue
                # Preserve order, drop blanks/duplicates (case-insensitive)
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
                if cleaned:
                    categories[str(category)] = cleaned

            if not categories:
                raise ValueError("words.json contained no valid categories")

            self._categories = categories
            print(
                f"[WORD_MANAGER] Loaded {len(self._categories)} categories "
                f"from {self._words_path}"
            )
            for name, words in self._categories.items():
                print(f"[WORD_MANAGER]   {name}: {len(words)} words")

    def get_categories(self) -> List[str]:
        """Return sorted list of available category names."""
        with self._lock:
            return sorted(self._categories.keys())

    def _require_category(self, category: str) -> List[str]:
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

    def has_category(self, category: str) -> bool:
        with self._lock:
            return category in self._categories

    def normalize_category(self, category: Optional[str], default: str = "movies") -> str:
        """
        Validate a category name; fall back to default (or first available) if invalid.
        """
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
