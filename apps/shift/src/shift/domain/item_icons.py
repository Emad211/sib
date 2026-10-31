"""One built-in icon name for each item, chosen by its kind, its unit and a few words (G2, «آیکون برای هر قلم»).

The principle is that a tired person recognises the item by a picture before reading its name. The set is
small on purpose: the phone draws the picture from the name (`static/item-icons.js`), so no image is
stored per item and a manager never has to pick one.
"""

from typing import Protocol

ICONS = ("ampoule", "vial", "bottle", "suppository", "syringe", "box", "gauge", "check", "camera")
_BY_KIND = {"yes_no": "check", "reading": "gauge", "photo": "camera"}
_BY_UNIT = {"آمپول": "ampoule", "ویال": "vial", "بطری": "bottle", "شیاف": "suppository"}
_SYRINGE_WORDS = ("سرنگ", "آنژیوکت", "سوزن", "ست سرم")


class HasIconTraits(Protocol):
    """What the choice reads from an item: its kind, its unit and its name."""

    @property
    def kind(self) -> object: ...
    @property
    def unit(self) -> str: ...
    @property
    def name(self) -> str: ...


def icon_for(item: HasIconTraits) -> str:
    """The icon name for an item; every item gets one, so no row on the phone is without a picture."""
    kind = str(item.kind)
    if kind in _BY_KIND:
        return _BY_KIND[kind]
    if any(word in item.name for word in _SYRINGE_WORDS):
        return "syringe"
    return _BY_UNIT.get(item.unit.strip(), "box")
