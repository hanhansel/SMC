# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

# Sertakan semua modul di dalam package apps
semua_modul_apps = collect_submodules('apps')

# Daftar eksplisit sebagai cadangan jika collect_submodules melewatkan modul
semua_modul_apps.extend([
    'apps.home.ai_report',
    'apps.home.hdd_checker',
    'apps.home.ai_processing',
    'apps.home.routes',
    'apps.home.models'
])

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('apps/templates', 'apps/templates'), ('apps/static', 'apps/static')],
    hiddenimports=semua_modul_apps,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='run',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
    icon=['smc.ico'], # smc.ico ada di folder yang sama dengan run.py
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='run',
)