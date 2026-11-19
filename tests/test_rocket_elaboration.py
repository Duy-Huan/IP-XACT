"""Protect functional FIRRTL semantics while preserving descriptive metadata."""

import json

import pytest

from socsim.domain import ContractError
from socsim.rocket_elaboration import DESCRIPTIVE, lower_annotations, split_annotations


def circuit(annotations):
    return (
        "FIRRTL version 3.3.0\ncircuit Example :%["
        + json.dumps(annotations)
        + "]\n  module Example :\n"
    )


def test_descriptive_annotations_are_preserved_separately_and_only_those_are_removed(tmp_path):
    metadata = [
        {"class": name, "target": "~Example|Core", "data_width": 64} for name in DESCRIPTIVE
    ]
    functional = [
        {"class": "firrtl.transforms.DontTouchAnnotation", "target": "~Example|Core>data"}
    ]
    original = circuit(metadata + functional)
    source = tmp_path / "source.fir"
    source.write_text(original)
    record = lower_annotations(source, tmp_path / "lowered.fir", tmp_path / "metadata.json")
    assert list(record["annotations"]) == metadata
    assert record["hardware_changes"] is False
    lowered = (tmp_path / "lowered.fir").read_text()
    assert "DontTouchAnnotation" in lowered and all(name not in lowered for name in DESCRIPTIVE)
    assert original.split("]\n  module")[1] == lowered.split("]\n  module")[1]
    assert json.loads((tmp_path / "metadata.json").read_text())["annotations"] == metadata
    with pytest.raises(ContractError, match="preserve existing"):
        lower_annotations(source, tmp_path / "lowered.fir", tmp_path / "metadata.json")


def test_unknown_potentially_functional_annotation_is_never_ignored():
    unknown = {"class": "third.party.ChangeHardwareTiming", "target": "~Example|Core"}
    lowered, metadata = split_annotations(circuit([unknown]))
    assert "third.party.ChangeHardwareTiming" in lowered and not metadata


@pytest.mark.parametrize(
    "text",
    [
        "",
        "circuit Example :",
        "circuit Example :%[{}]",
        "circuit Example :%[[{}]]",
        'circuit Example :%[[{"class":"A","class":"B"}]]',
        'circuit Example :%[[{"class":"A"}]',
        'circuit Example :%[[{"class":3}]]',
    ],
)
def test_malformed_annotations_fail_explicitly(text):
    with pytest.raises(ContractError):
        split_annotations(text)
