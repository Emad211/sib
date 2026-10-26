# -*- mode: python ; coding: utf-8 -*-

# PyInstaller spec for HesabdariSib
# هدف: تولید یک exe که کنار خودش دیتابیس و پوشه بکاپ داشته باشد.
# نکته مهم: از start.py استفاده می‌کنیم که فقط در __main__ برنامه را اجرا می‌کند

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['_cffi_backend']

# Some environments need extra hidden imports for cffi-based deps
hiddenimports += collect_submodules('cffi')


a = Analysis(
    ['start.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('src\\templates', 'src\\templates'),
        ('src\\static', 'src\\static'),
        # Needed to initialize a fresh DB in PyInstaller builds
        ('src\\adapters\\sqlite\\schema.sql', 'src\\adapters\\sqlite'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='HesabdariSib',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # No terminal window (run in background)
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
