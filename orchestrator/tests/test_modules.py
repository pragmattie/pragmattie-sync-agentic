from sdlc.modules import MODULE_DESCRIPTIONS
from sdlc.tables import MODULES


def test_the_descriptions_cover_exactly_the_modules_in_order():
    assert tuple(MODULE_DESCRIPTIONS) == MODULES


def test_each_description_is_one_line():
    for line in MODULE_DESCRIPTIONS.values():
        assert line and "\n" not in line
