import pytest

from sdlc.epics import epic_label


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("M5 Delivery forecasting", "Delivery forecasting (M5)"),
        ("M12 Platform and tooling", "Platform and tooling (M12)"),
        ("  M0 Bootstrap  ", "Bootstrap (M0)"),
        ("Scoring", "Scoring"),
        ("M5", "M5"),
        ("Milestone five", "Milestone five"),
        ("m5 lower case", "m5 lower case"),
        ("MX Not a milestone code", "MX Not a milestone code"),
        ("", ""),
    ],
)
def test_epic_label(name, label):
    assert epic_label(name) == label
