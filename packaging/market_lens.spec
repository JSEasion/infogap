# -*- mode: python ; coding: utf-8 -*-
"""行业透镜桌面版打包配置（macOS .app + Windows exe 共用）。

构建：见 packaging/build_mac.sh / packaging/build_windows.ps1
"""
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)
ROOT = HERE.parent
SRC = ROOT / "src"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]

datas = collect_data_files("market_lens", includes=["web/templates/*", "web/static/*"])
datas += collect_data_files("jieba")  # 分词词典
if (ROOT / "config.yaml").exists():
    datas.append((str(ROOT / "config.yaml"), "."))  # 首启复制到用户目录的默认配置

hiddenimports = (
    collect_submodules("uvicorn")   # uvicorn 运行期动态导入 loop/protocol 实现
    + collect_submodules("webview")  # pywebview 按平台动态选择后端（cocoa/winforms）
    + ["market_lens.desktop"]
)

a = Analysis(
    [str(HERE / "entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "watchfiles", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MarketLens",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(HERE / "icons/MarketLens.ico") if sys.platform == "win32" else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="market_lens",
)

if sys.platform == "darwin":
    BUNDLE(
        coll,
        name="行业透镜.app",
        icon=str(HERE / "icons/MarketLens.icns"),
        bundle_identifier="com.marketlens.desktop",
        info_plist={
            "CFBundleName": "行业透镜",
            "CFBundleDisplayName": "行业透镜",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        },
    )
