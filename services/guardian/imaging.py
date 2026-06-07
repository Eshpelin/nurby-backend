"""Guardian image privacy transform.

The privacy spine: a guardian must never be able to identify any non-dependant
face. V1 enforces this the simplest correct way: every image served to a
guardian is Gaussian-blurred so no face is identifiable, while the scene
(movement, clothing colour, location) stays legible enough to reassure a
parent. The brief is explicit that over-blurring the target is acceptable but
revealing the wrong person is catastrophic, so we fail safe and blur all.

A future enhancement can leave the bound dependant's face region sharp when a
face match clears the reveal-confidence threshold; the ``reveal_box`` hook is
plumbed for that but unused in V1.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageFilter

DEFAULT_BLUR_RADIUS = 12


def blur_image_file(
    path: str,
    radius: int = DEFAULT_BLUR_RADIUS,
    reveal_box: tuple[int, int, int, int] | None = None,
) -> bytes:
    """Return JPEG bytes of the image at ``path``, Gaussian-blurred so no face
    is identifiable. ``reveal_box`` (x0,y0,x1,y1), when given, is left sharp
    (future dependant-reveal; not used in V1). Raises on unreadable input."""
    radius = max(1, int(radius))
    with Image.open(path) as src:
        img = src.convert("RGB")
        blurred = img.filter(ImageFilter.GaussianBlur(radius))
        if reveal_box is not None:
            # Paste the original (sharp) region back over the blurred base.
            x0, y0, x1, y1 = reveal_box
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(img.width, x1), min(img.height, y1)
            if x1 > x0 and y1 > y0:
                region = img.crop((x0, y0, x1, y1))
                blurred.paste(region, (x0, y0))
        buf = BytesIO()
        blurred.save(buf, format="JPEG", quality=80)
        return buf.getvalue()
