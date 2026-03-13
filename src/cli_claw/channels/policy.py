from __future__ import annotations

from typing import Iterable


def sender_allowed(sender_id: str | None, allow_from: Iterable[str] | None) -> bool:
    allow_list = [item for item in (allow_from or []) if item]
    if not allow_list:
        return True
    if not sender_id:
        return False
    sender_str = str(sender_id)
    if sender_str in allow_list:
        return True
    if "|" in sender_str:
        for part in sender_str.split("|"):
            if part and part in allow_list:
                return True
    return False
