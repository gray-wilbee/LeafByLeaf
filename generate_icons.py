#!/usr/bin/env python3
"""Generate PWA icon PNGs from static/icon.svg.

Run once after setup:
    pip install cairosvg
    python generate_icons.py
"""
import os

try:
    import cairosvg
except ImportError:
    raise SystemExit("Missing dependency — run: pip install cairosvg")

SVG = os.path.join(os.path.dirname(__file__), 'static', 'icon.svg')
os.makedirs(os.path.join(os.path.dirname(__file__), 'static', 'icons'), exist_ok=True)

icons = [
    ('static/icons/icon-192.png', 192),
    ('static/icons/icon-512.png', 512),
    ('static/apple-touch-icon.png', 180),  # iOS home screen icon
]

for path, size in icons:
    dest = os.path.join(os.path.dirname(__file__), path)
    cairosvg.svg2png(url=SVG, write_to=dest, output_width=size, output_height=size)
    print(f'  {path}  ({size}x{size})')

print('Done. Commit static/icons/ and static/apple-touch-icon.png.')
