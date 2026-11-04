from __future__ import annotations

from typing import Dict, Mapping

BIN_1 = "BIN_1"
BIN_2 = "BIN_2"
BIN_3 = "BIN_3"
UNKNOWN = "UNKNOWN"

DEFAULT_MAPPING: Dict[str, str] = {
    "plastic bottle": BIN_3,
    "plastic bag": BIN_3,
    "drink carton": BIN_3,
    "plastic bottle cap": BIN_3,
    "can": BIN_2,
    "pop tab": BIN_2,
    "cardboard": BIN_1,
    "paper": BIN_1,
    "battery": UNKNOWN,
    "glass bottle": UNKNOWN,
}


def resolve_bin(class_name: str, custom_mapping: Mapping[str, str] | None = None) -> str:
    key = str(class_name).strip().lower()
    mapping = custom_mapping or DEFAULT_MAPPING
    return mapping.get(key, UNKNOWN)
