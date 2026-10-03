"""Coordinated colors for newly imported tags and tasks."""

from collections.abc import Iterator
from colorsys import hsv_to_rgb


def tag_colors(existing: set[str]) -> Iterator[str]:
    """Yield unused opaque ARGB colors for tags."""
    return _colors(existing, initial_hue=0.58)


def task_colors(existing: set[str]) -> Iterator[str]:
    """Yield unused opaque ARGB colors for tasks."""
    return _colors(existing, initial_hue=0.08)


def _colors(existing: set[str], *, initial_hue: float) -> Iterator[str]:
    """Yield unused opaque colors with separated hues and balanced tones."""
    used = {color.upper() for color in existing}
    index = 0
    tones = ((0.60, 0.82), (0.50, 0.90), (0.70, 0.74))
    while True:
        hue = (initial_hue + index * 0.618033988749895) % 1
        saturation, value = tones[index % len(tones)]
        channels = hsv_to_rgb(hue, saturation, value)
        color = "#FF" + "".join(f"{round(channel * 255):02X}" for channel in channels)
        index += 1
        if color not in used:
            used.add(color)
            yield color
