# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification for the standalone granite-docling CLI."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

PROJECT_ROOT = Path(SPECPATH)

# mlx-vlm selects model implementations at runtime from the downloaded model's
# configuration.  Transformers does the same for tokenizers and processors.
# Collect these packages rather than relying on PyInstaller's static analysis.
PACKAGES_WITH_RUNTIME_IMPORTS = (
    "mlx",
    "mlx_vlm",
    "docling_core",
    "pypdfium2",
    "transformers",
    "tokenizers",
    "safetensors",
    "sentencepiece",
)

datas: list[tuple[str, str]] = []
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = []
for package in PACKAGES_WITH_RUNTIME_IMPORTS:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[],
)
pyz = PYZ(a.pure)

# Keep dependencies beside the executable. This avoids the extraction delay that
# a one-file build incurs on every invocation.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="granite-docling",
    console=True,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="granite-docling",
)
