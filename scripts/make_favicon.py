"""生成 favicon 位图（PNG），与 favicon.svg 同一几何形状。

不依赖 Pillow：手写 PNG 编码（zlib + struct），逐像素超采样抗锯齿。
用法：.venv/bin/python scripts/make_favicon.py
"""
import struct
import zlib
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "market_lens" / "web" / "static"

# 与 favicon.svg 保持一致的几何（64x64 坐标系）
BG_TOP, BG_BOT = (0x4A, 0x7B, 0xFF), (0x24, 0x47, 0xB8)
LAYERS = [  # (x0, y0, x1, y1, r, rgb, alpha)
    (13, 17.5, 51, 26.5, 4.5, (255, 255, 255), 1.0),     # 上条：完整信息
    (34, 37.5, 51, 46.5, 4.5, (255, 176, 32), 1.0),      # 高亮：信息差
    (13, 37.5, 34.5, 46.5, 4.5, (255, 255, 255), 0.78),  # 下条：部分信息
]
BG_RECT = (0, 0, 64, 64, 14)
SS = 3  # 每像素 3x3 超采样


def inside_rrect(x, y, x0, y0, x1, y1, r):
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def render(size):
    s = size / 64.0
    off = [(i + 0.5) / SS for i in range(SS)]
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            r = g = b = a = 0.0
            for oy in off:
                for ox in off:
                    x, y = (px + ox) * s, (py + oy) * s
                    # 该采样点独立合成，再参与像素平均
                    sr = sg = sb = sa = 0.0
                    if inside_rrect(x, y, *BG_RECT):
                        t = y / 64.0
                        sr = BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t
                        sg = BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t
                        sb = BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t
                        sa = 1.0
                    for (x0, y0, x1, y1, rad, rgb, alpha) in LAYERS:
                        if inside_rrect(x, y, x0, y0, x1, y1, rad):
                            sr = sr + (rgb[0] - sr) * alpha
                            sg = sg + (rgb[1] - sg) * alpha
                            sb = sb + (rgb[2] - sb) * alpha
                            sa = sa + (1 - sa) * alpha
                    r += sr
                    g += sg
                    b += sb
                    a += sa
            n = SS * SS
            row.append((round(r / n), round(g / n), round(b / n), round(a * 255 / n)))
        rows.append(row)
    return rows


def write_png(path, rows):
    h, w = len(rows), len(rows[0])
    raw = b"".join(b"\x00" + b"".join(struct.pack("4B", *px) for px in row) for row in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


if __name__ == "__main__":
    for size in (16, 32, 180):
        out = STATIC / f"favicon-{size}.png"
        write_png(out, render(size))
        print(f"wrote {out} ({out.stat().st_size} bytes)")
