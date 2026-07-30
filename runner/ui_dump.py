"""Parses a `uiautomator dump` XML view-hierarchy into a flat, numbered list
of elements the model can act on by index instead of guessing raw pixel
coordinates."""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


class UiDumpError(ValueError):
    pass


@dataclass
class UiElement:
    index: int
    text: str
    resource_id: str
    class_name: str
    clickable: bool
    bounds: tuple[int, int, int, int]
    center: tuple[int, int]


def _short_class(class_attr: str) -> str:
    return class_attr.rsplit(".", 1)[-1] if class_attr else ""


def _parse_bounds(bounds_attr: str) -> tuple[int, int, int, int] | None:
    match = _BOUNDS_RE.match(bounds_attr or "")
    if not match:
        return None
    return tuple(int(v) for v in match.groups())


def parse_elements(xml_text: str) -> list[UiElement]:
    """Flattens every node with visible text/content-desc or that is clickable
    into a numbered list. Nodes with neither are pure layout containers and
    are skipped."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise UiDumpError(f"could not parse uiautomator dump XML: {e}") from e

    elements: list[UiElement] = []
    for node in root.iter("node"):
        text = node.get("text", "") or node.get("content-desc", "")
        clickable = node.get("clickable") == "true"
        if not text and not clickable:
            continue

        bounds = _parse_bounds(node.get("bounds", ""))
        if bounds is None:
            continue
        x1, y1, x2, y2 = bounds
        center = ((x1 + x2) // 2, (y1 + y2) // 2)

        elements.append(UiElement(
            index=len(elements) + 1,
            text=text,
            resource_id=node.get("resource-id", ""),
            class_name=_short_class(node.get("class", "")),
            clickable=clickable,
            bounds=bounds,
            center=center,
        ))

    return elements


def format_elements_for_prompt(elements: list[UiElement]) -> str:
    if not elements:
        return "(no elements found on screen)"

    lines = []
    for el in elements:
        descriptor = f'{el.class_name} "{el.text}"' if el.text else el.class_name
        tags = []
        if el.resource_id:
            tags.append(f"id: {el.resource_id}")
        tags.append("clickable" if el.clickable else "not clickable")
        lines.append(f"[{el.index}] {descriptor} ({', '.join(tags)})")
    return "\n".join(lines)


def element_by_index(elements: list[UiElement], index: int) -> UiElement | None:
    for el in elements:
        if el.index == index:
            return el
    return None
