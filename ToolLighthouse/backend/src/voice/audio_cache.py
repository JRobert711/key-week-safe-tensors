"""Cache LRU acotada en memoria. Las frases son deterministas: clave = (modelo, voz, texto)."""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional, Tuple

from .provider import AudioResult

CacheKey = Tuple[str, str, str]


def make_key(model: str, reference_id: Optional[str], text: str) -> CacheKey:
    return (model, reference_id or "", text)


class AudioCache:
    def __init__(self, max_entries: int):
        self._max = max(1, max_entries)
        self._data: "OrderedDict[CacheKey, AudioResult]" = OrderedDict()

    def get(self, key: CacheKey) -> Optional[AudioResult]:
        item = self._data.get(key)
        if item is not None:
            self._data.move_to_end(key)
        return item

    def put(self, key: CacheKey, value: AudioResult) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
