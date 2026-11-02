from __future__ import annotations

from typing import Dict, Mapping

BIN_1 = "BIN_1"
BIN_2 = "BIN_2"
BIN_3 = "BIN_3"
UNKNOWN = "UNKNOWN"

DEFAULT_MAPPING: Dict[str, str] = {
    "plastic bottle": BIN_1,
    "plastic bag": BIN_1,
    "drink carton": BIN_1,
    "plastic bottle cap": BIN_1,
    "can": BIN_2,
    "pop tab": BIN_2,
    "cardboard": BIN_3,
    "paper": BIN_3,
    "battery": UNKNOWN,
    "glass bottle": UNKNOWN,
}


def resolve_bin(class_name: str, custom_mapping: Mapping[str, str] | None = None) -> str:
    key = str(class_name).strip().lower()
    mapping = custom_mapping or DEFAULT_MAPPING
    return mapping.get(key, UNKNOWN)

