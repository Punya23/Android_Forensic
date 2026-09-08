# SNAGR app icon — regenerate the icon.icns/.ico/.png set from scratch.
#
# Usage (from app/):
#   ../engine/.venv/bin/python build/icons/generate.py
#   iconutil -c icns build/icons/icon.iconset -o build/icons/icon.icns
#   rm -rf build/icons/icon.iconset   # intermediate, not tracked
#
# Colors are pinned to the dark-theme tokens in src/index.css
# (--color-panel-2 / --color-accent) so the icon matches the in-app theme.

from PIL import Image, ImageDraw
import math, os

OUT = "build/icons"
os.makedirs(OUT, exist_ok=True)

# Draw at high res, supersampled, then downscale for crisp anti-aliasing.
S = 4096
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

BG = (13, 13, 14, 255)      # matches dark-theme panel-2
ACCENT = (242, 165, 74, 255)  # matches dark-theme accent (amber)

# Rounded-square badge background.
radius = int(S * 0.225)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=BG)

# Magnifying glass: thick ring + handle, amber, centered-ish (slightly up/left)
cx, cy = int(S * 0.42), int(S * 0.42)
r = int(S * 0.20)
ring_w = int(S * 0.052)

bbox = [cx - r, cy - r, cx + r, cy + r]
d.ellipse(bbox, outline=ACCENT, width=ring_w)

# Handle: line from ring edge (lower-right, 45deg) out to badge corner.
ang = math.radians(45)
start_r = r + ring_w * 0.15
end_r = r + int(S * 0.235)
x1 = cx + start_r * math.cos(ang)
y1 = cy + start_r * math.sin(ang)
x2 = cx + end_r * math.cos(ang)
y2 = cy + end_r * math.sin(ang)
handle_w = int(S * 0.062)
d.line([x1, y1, x2, y2], fill=ACCENT, width=handle_w)
# Round the handle's far end (line() draws flat caps).
cap_r = handle_w / 2
d.ellipse([x2 - cap_r, y2 - cap_r, x2 + cap_r, y2 + cap_r], fill=ACCENT)

# Downsample for anti-aliasing.
FULL = img.resize((1024, 1024), Image.LANCZOS)
FULL.save(f"{OUT}/icon_1024.png")

# --- macOS iconset ---
iconset = f"{OUT}/icon.iconset"
os.makedirs(iconset, exist_ok=True)
mac_sizes = [16, 32, 64, 128, 256, 512, 1024]
for sz in mac_sizes:
    im = FULL.resize((sz, sz), Image.LANCZOS)
    im.save(f"{iconset}/icon_{sz}x{sz}.png")
    if sz <= 512:
        im2 = FULL.resize((sz * 2, sz * 2), Image.LANCZOS)
        im2.save(f"{iconset}/icon_{sz}x{sz}@2x.png")

# --- Windows .ico ---
FULL.save(f"{OUT}/icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

# --- Linux png ---
FULL.resize((512, 512), Image.LANCZOS).save(f"{OUT}/icon_512.png")
FULL.resize((256, 256), Image.LANCZOS).save(f"{OUT}/icon_256.png")

print("done")
