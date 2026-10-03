"""Synthetic tests for the extensible tag palette."""

import re
from itertools import islice

from whx.services.colors import tag_colors, task_colors


def test_palette_supports_one_thousand_unique_argb_colors() -> None:
    colors = list(islice(tag_colors(set()), 1000))

    assert len(set(colors)) == 1000
    assert all(re.fullmatch(r"#FF[0-9A-F]{6}", color) for color in colors)
    assert colors == list(islice(tag_colors(set()), 1000))


def test_palette_avoids_existing_colors_case_insensitively() -> None:
    original = list(islice(tag_colors(set()), 10))
    colors = list(islice(tag_colors({color.lower() for color in original}), 100))

    assert not set(original).intersection(colors)


def test_task_palette_supports_one_thousand_unique_argb_colors() -> None:
    colors = list(islice(task_colors(set()), 1000))

    assert len(set(colors)) == 1000
    assert all(re.fullmatch(r"#FF[0-9A-F]{6}", color) for color in colors)
    assert colors == list(islice(task_colors(set()), 1000))
