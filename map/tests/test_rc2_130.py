"""Map 1.3.0-rc2 (team 5.7.0-rc2): the helpers behind the tester's findings T1-3, T1-4 and T1-5."""
from __future__ import annotations

import pytest

from app.domain.consolidation import level_key
from app.domain.reports import place_note_box


@pytest.mark.parametrize("a, b", [("LEVEL 02", "2"), ("Level 2", "L2"), ("LEVEL 01M", "1M"),
                                  ("LEVEL B1", "B01"), ("LVL-3", "level 03"),
                                  ("Mezzanine", "MEZZANINE")])
def test_one_level_written_several_ways_is_one_level(a, b):
    assert level_key(a) == level_key(b)


@pytest.mark.parametrize("a, b", [("LEVEL 02", "LEVEL 20"), ("LEVEL 1", "LEVEL 1M"),
                                  ("B1", "1"), ("LOBBY", "OBBY")])
def test_different_levels_stay_different(a, b):
    assert level_key(a) != level_key(b)


def test_a_note_box_stays_on_the_sheet():
    # wanted below a marker at the bottom-left corner: pushed in, put above
    x, y = place_note_box(-40, 590, 150, 60, 800, 600, above=560)
    assert x >= 2 and y + 60 <= 600 - 2 and y == 500
    # no room above either: pushed up to fit
    x, y = place_note_box(700, 590, 150, 60, 800, 600, above=30)
    assert x + 150 <= 798 and y + 60 <= 598
    # where it fits, it is left alone
    assert place_note_box(100, 100, 150, 60, 800, 600, above=80) == (100, 100)


def test_a_locked_workbook_is_said_in_words(monkeypatch, tmp_path):
    from app.api import routes as R
    from app import services as S

    def locked(*a, **k):
        raise PermissionError(13, "Permission denied", str(tmp_path / "x_analysis.xlsx"))
    with pytest.raises(S.ValidationError) as e:
        R._saving(tmp_path / "x_analysis.xlsx", locked)
    assert "still open" in str(e.value) and "damaged" not in str(e.value)
    # another failure is not dressed up as a locked file
    with pytest.raises(RuntimeError):
        R._saving(tmp_path / "x.pdf", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
