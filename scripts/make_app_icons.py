"""生成桌面应用图标：favicon.svg → packaging/icons/Xinxicha.{icns,ico}

macOS 上运行（qlmanage 渲染 SVG + iconutil 生成 icns）。
产物提交进仓库，Windows 构建机无需再生成。

用法: python scripts/make_app_icons.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC_SVG = ROOT / "src/market_lens/web/static/favicon.svg"
OUT_DIR = ROOT / "packaging/icons"

ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]  # 含 @2x 变体由下方组合展开
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def render_svg(size: int, out: Path) -> None:
    subprocess.run(
        ["qlmanage", "-t", "-s", str(size), "-o", str(out.parent), str(SRC_SVG)],
        check=True, capture_output=True,
    )
    produced = out.parent / "favicon.svg.png"
    produced.replace(out)


def build_icns(png1024: Path) -> Path:
    iconset = OUT_DIR / "Xinxicha.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    img = Image.open(png1024).convert("RGBA")
    for s in ICNS_SIZES:
        img.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
        if s <= 512:  # @2x 变体（最大 512@2x=1024）
            img.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
    target = OUT_DIR / "Xinxicha.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)], check=True)
    shutil.rmtree(iconset)
    return target


def build_ico(png1024: Path) -> Path:
    target = OUT_DIR / "Xinxicha.ico"
    img = Image.open(png1024).convert("RGBA")
    img.save(target, sizes=ICO_SIZES)
    return target


def main() -> None:
    if sys.platform != "darwin":
        sys.exit("请在 macOS 上运行（依赖 qlmanage/iconutil）")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        png1024 = Path(tmp) / "1024.png"
        render_svg(1024, png1024)
        icns = build_icns(png1024)
        ico = build_ico(png1024)
    print(f"✔ {icns}")
    print(f"✔ {ico}")


if __name__ == "__main__":
    main()
