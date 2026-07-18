# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src/ollama_usage_proxy/report_main.py'],
    pathex=[],
    binaries=[],
    datas=[('src/ollama_usage_proxy/default_config.toml', 'ollama_usage_proxy'), ('src/ollama_usage_proxy/default_prices.toml', 'ollama_usage_proxy')],
    hiddenimports=['ollama_usage_proxy.pricing', 'tomli'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ollama-report',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ollama-report',
)
