from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz, process

from .store import best_canonical_for_raw, list_canonical_names, upsert_alias


@dataclass
class CanonicalSuggestion:
    canonical: str
    score: int


def learn_from_edit(dsn: str, *, user_id: str, before_name: str, after_name: str) -> None:
    before = (before_name or "").strip()
    after = (after_name or "").strip()
    if not before or not after:
        return
    if before == after:
        return
    upsert_alias(dsn, user_id=user_id, raw_name=before, canonical_name=after)


def suggest_canonical(dsn: str, *, user_id: str, raw_name: str, min_score: int = 90) -> Optional[CanonicalSuggestion]:
    raw = (raw_name or "").strip()
    if not raw:
        return None

    exact = best_canonical_for_raw(dsn, user_id=user_id, raw_name=raw)
    if exact:
        return CanonicalSuggestion(canonical=exact, score=100)

    # 模糊匹配：在已学到的 canonical_name 里找最像的
    canonicals = list_canonical_names(dsn, user_id=user_id)
    if not canonicals:
        return None

    hit = process.extractOne(raw, canonicals, scorer=fuzz.WRatio)
    if not hit:
        return None
    canonical, score, _ = hit
    if int(score) < min_score:
        return None
    return CanonicalSuggestion(canonical=str(canonical), score=int(score))

