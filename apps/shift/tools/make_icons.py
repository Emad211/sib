"""Generate clean clinic PWA icons for Sib Shift (192, 512, maskable 512).

Supports pure Python stdlib (zlib + struct) with automatic Pillow fallback if installed.
No external dependencies required.
"""

import struct
import zlib
from collections.abc import Callable
from pathlib import Path

try:
    from PIL import Image, ImageDraw

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


def _write_png_stdlib(path: Path, size: int, pixel_fn: Callable[[int, int], tuple[int, int, int, int]]) -> None:
    """Encode an RGBA image using pure Python stdlib (PNG format)."""
    raw_rows: list[bytes] = []
    for y in range(size):
        row = bytearray([0])  # filter type 0 (None)
        for x in range(size):
            r, g, b, a = pixel_fn(x, y)
            row.extend((r, g, b, a))
        raw_rows.append(bytes(row))

    raw_data = b"".join(raw_rows)
    compressed = zlib.compress(raw_data, level=9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        length = len(data)
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", length) + tag + data + struct.pack(">I", crc)

    png_header = b"\x89PNG\r\n\x1a\n"
    # IHDR: width (4), height (4), bit depth (1=8), color type (1=6 RGBA), compression (1=0), filter (1=0), interlace (1=0)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    ihdr_chunk = chunk(b"IHDR", ihdr)
    idat_chunk = chunk(b"IDAT", compressed)
    iend_chunk = chunk(b"IEND", b"")

    with open(path, "wb") as f:
        f.write(png_header + ihdr_chunk + idat_chunk + iend_chunk)


def generate_png_pure(path: Path, size: int, is_maskable: bool = False) -> None:
    """Generate clinic icon via stdlib."""
    bg_r, bg_g, bg_b = 15, 118, 110  # #0f766e
    cx, cy = size / 2.0, size / 2.0
    scale = 0.32 if is_maskable else 0.38
    emblem_radius = size * scale
    bar_width = max(4.0, emblem_radius * 0.44)
    bar_len = emblem_radius
    corner_radius = size * 0.22

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        px = x + 0.5
        py = y + 0.5

        if not is_maskable:
            # Check rounded corner squircle
            dx = max(0.0, abs(px - cx) - (cx - corner_radius))
            dy = max(0.0, abs(py - cy) - (cy - corner_radius))
            if dx * dx + dy * dy > corner_radius * corner_radius:
                return (0, 0, 0, 0)

        # Cross geometry
        in_vbar = (abs(px - cx) <= bar_width / 2.0) and (abs(py - cy) <= bar_len)
        in_hbar = (abs(py - cy) <= bar_width / 2.0) and (abs(px - cx) <= bar_len)

        # Center dot
        dot_r = bar_width * 0.28
        in_dot = ((px - cx) ** 2 + (py - cy) ** 2) <= (dot_r**2)

        if in_dot:
            return (bg_r, bg_g, bg_b, 255)
        if in_vbar or in_hbar:
            return (255, 255, 255, 255)
        return (bg_r, bg_g, bg_b, 255)

    _write_png_stdlib(path, size, pixel)


def make_icons(target_dir: Path) -> list[Path]:
    """Generate 192.png, 512.png, and maskable-512.png in target_dir."""
    target_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    specs = [
        ("192.png", 192, False),
        ("512.png", 512, False),
        ("maskable-512.png", 512, True),
    ]

    for filename, size, maskable in specs:
        out_path = target_dir / filename
        if HAS_PILLOW:
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            bg = (15, 118, 110, 255)
            white = (255, 255, 255, 255)
            if maskable:
                draw.rectangle([0, 0, size, size], fill=bg)
            else:
                rad = int(size * 0.22)
                draw.rounded_rectangle([0, 0, size, size], radius=rad, fill=bg)
            cx, cy = size / 2, size / 2
            scale = 0.32 if maskable else 0.38
            emblem_radius = int(size * scale)
            bar_width = max(4, int(emblem_radius * 0.44))
            draw.rounded_rectangle(
                [cx - bar_width / 2, cy - emblem_radius, cx + bar_width / 2, cy + emblem_radius],
                radius=bar_width // 2,
                fill=white,
            )
            draw.rounded_rectangle(
                [cx - emblem_radius, cy - bar_width / 2, cx + emblem_radius, cy + bar_width / 2],
                radius=bar_width // 2,
                fill=white,
            )
            dot_r = int(bar_width * 0.28)
            draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=bg)
            img.save(out_path, format="PNG")
        else:
            generate_png_pure(out_path, size, is_maskable=maskable)
        generated.append(out_path)

    return generated


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent.parent
    static_icons = base_dir / "src" / "shift" / "static" / "icons"
    outputs = make_icons(static_icons)
    for p in outputs:
        print(f"Generated: {p.relative_to(base_dir)}")
