"""Strict lowering of pinned Rocket descriptive annotations, without hardware edits."""

import json
from collections import Counter
from pathlib import Path

from .domain import ContractError
from .ipxact import digest
from .runner import write_json

DESCRIPTIVE = frozenset(
    {
        "freechips.rocketchip.util.ParamsAnnotation",
        "freechips.rocketchip.util.SRAMAnnotation",
    }
)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate Rocket annotation JSON key")
        result[key] = value
    return result


def split_annotations(text):
    """Preserve all functional annotations for strict firtool handling.

    ParamsAnnotation/SRAMAnnotation are records of parameters and RAM geometry,
    as defined by the pinned upstream util/Annotations.scala. Their complete
    contents are retained separately. No global 'ignore unknown' option is used.
    """
    if not isinstance(text, str) or not 0 < len(text) <= 256 * 1024**2:
        raise ContractError("invalid Rocket FIRRTL size")
    start = text.find("%[")
    if start < 0:
        raise ContractError("Rocket FIRRTL lacks its annotation block")
    try:
        annotations, end = json.JSONDecoder(object_pairs_hook=_object).raw_decode(text, start + 2)
    except (ValueError, RecursionError) as exc:
        raise ContractError("invalid Rocket FIRRTL annotation JSON") from exc
    if not isinstance(annotations, list) or len(annotations) > 100000 or text[end : end + 1] != "]":
        raise ContractError("invalid Rocket annotation block")
    kept, metadata = [], []
    for annotation in annotations:
        if (
            not isinstance(annotation, dict)
            or not isinstance(annotation.get("class"), str)
            or not annotation["class"]
        ):
            raise ContractError("classless/invalid Rocket annotation")
        if annotation["class"] in DESCRIPTIVE:
            metadata.append(annotation)
        else:
            kept.append(annotation)
    lowered = text[:start] + "%[" + json.dumps(kept, separators=(",", ":")) + text[end:]
    return lowered, tuple(metadata)


def lower_annotations(source: Path, output: Path, metadata: Path):
    if output.exists() or metadata.exists():
        raise ContractError("preserve existing Rocket lowering outputs")
    if source.stat().st_size > 256 * 1024**2:
        raise ContractError("oversized Rocket FIRRTL")
    lowered, annotations = split_annotations(source.read_text())
    output.write_text(lowered)
    record = {
        "contract": "rocket-descriptive-annotations-v1",
        "source_sha256": digest(source),
        "lowered_sha256": digest(output),
        "descriptive_classes": dict(Counter(a["class"] for a in annotations)),
        "annotations": annotations,
        "hardware_changes": False,
        "unknown_annotations": "retained; strict firtool must accept or fail",
    }
    write_json(metadata, record)
    return record
