"""
PlayerNameGenerator — funny fallback names for players who leave the name blank.

Names live in app/data/player_names.json, grouped by word category id (the same
ids as words.json, e.g. "bollywood_movies"), so a room's random names match the
categories it is playing.
"""
import json
import random
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional


class PlayerNameGenerator:
    def __init__(self, names_path: Optional[Path] = None):
        if names_path is None:
            names_path = Path(__file__).resolve().parent.parent / "data" / "player_names.json"
        self._names_path = Path(names_path)
        self._lock = threading.RLock()
        self._names: Dict[str, List[str]] = {}
        self.load()

    def load(self) -> None:
        """Load (or reload) the name lists from disk into memory."""
        with self._lock:
            if not self._names_path.exists():
                raise FileNotFoundError(f"Player names file not found: {self._names_path}")

            with self._names_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)

            if not isinstance(raw, dict):
                raise ValueError("player_names.json must be a JSON object of category -> name list")

            names: Dict[str, List[str]] = {}
            for category, values in raw.items():
                if isinstance(values, list):
                    cleaned = [" ".join(str(v).split()) for v in values if str(v).strip()]
                    if cleaned:
                        names[str(category)] = cleaned

            if not names:
                raise ValueError("player_names.json contained no names")

            self._names = names
            print(f"[PLAYER_NAMES] Loaded names for {len(self._names)} categories from {self._names_path}")

    def random_name(self, categories: Iterable[str], taken: Iterable[str] = (), max_length: int = 10) -> str:
        """
        A random name themed on `categories` that nobody in `taken` is using
        (case-insensitive). Unknown categories fall back to every name. If all
        themed names are taken, a number is added while staying within max_length.
        """
        with self._lock:
            pool: List[str] = []
            for category in categories or []:
                for name in self._names.get(category, []):
                    if name not in pool:
                        pool.append(name)
            if not pool:
                pool = [name for names in self._names.values() for name in names]

        used = {str(name).casefold() for name in taken}
        free = [name for name in pool if name.casefold() not in used]
        if free:
            return random.choice(free)

        base = random.choice(pool)
        for number in range(2, 1000):
            suffix = f" {number}"
            candidate = base[: max_length - len(suffix)].rstrip() + suffix
            if candidate.casefold() not in used:
                return candidate
        raise RuntimeError("Could not find a free player name")


# Module-level singleton, loaded at import like the word list.
player_names = PlayerNameGenerator()
