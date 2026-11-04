from __future__ import annotations

from typing import Dict, Mapping

BIN_1 = "BIN_1"
BIN_2 = "BIN_2"
BIN_3 = "BIN_3"
UNKNOWN = "UNKNOWN"

DEFAULT_MAPPING: Dict[str, str] = {
    # БАК 1 — Бумага / Картон (GPIO 12)
    "paper": BIN_1,
    "cardboard": BIN_1,

    # БАК 2 — Пластик / Тетрапак (GPIO 13)
    "plastic bottle": BIN_2,
    "plastic bag": BIN_2,
    "drink carton": BIN_2,
    "plastic bottle cap": BIN_2,

    # БАК 3 — Металл / Банки (GPIO 15)
    "can": BIN_3,
    "pop tab": BIN_3,

    # Без бака (створки закрыты)
    "battery": UNKNOWN,
    "glass bottle": UNKNOWN,
}


def resolve_bin(class_name: str, custom_mapping: Mapping[str, str] | None = None) -> str:
    key = str(class_name).strip().lower()
    mapping = custom_mapping or DEFAULT_MAPPING
    return mapping.get(key, UNKNOWN)
