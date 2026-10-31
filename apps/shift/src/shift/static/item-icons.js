/**
 * One picture per item, chosen by kind (G2, «آیکون برای هر قلم»).
 *
 * The server names the icon (`shift.domain.item_icons.icon_for`); this file draws it, so a tired
 * person recognises the item before reading its name and no image is stored per item. Built in and
 * offline: the PWA serves it from the shell, never from a CDN. A name this map does not know falls
 * back to the box, so a row is never left without a picture.
 */
(function (global) {
  "use strict";

  var ICONS = {
    ampoule: "🧫",
    vial: "🧪",
    bottle: "🧴",
    suppository: "💊",
    syringe: "💉",
    box: "📦",
    gauge: "🌡️",
    check: "✅",
    camera: "📷",
  };

  /** The character that stands for an icon name; the box when the name is unknown or missing. */
  function iconFor(name) {
    return ICONS[name] || ICONS.box;
  }

  global.itemIcons = { for: iconFor, all: ICONS };
})(window);
