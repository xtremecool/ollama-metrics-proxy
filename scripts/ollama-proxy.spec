# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src/ollama_usage_proxy/app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src/ollama_usage_proxy/default_config.toml', 'ollama_usage_proxy'),
        ('src/ollama_usage_proxy/default_prices.toml', 'ollama_usage_proxy'),
        ('src/ollama_usage_proxy/static', 'ollama_usage_proxy/static'),
    ],
    hiddenimports=[
        'ollama_usage_proxy.config',
        'ollama_usage_proxy.db',
        'ollama_usage_proxy.usage',
        'ollama_usage_proxy.models',
        'ollama_usage_proxy.pricing',
        'ollama_usage_proxy.system_telemetry',
        'tomli',
    ],
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
    name='ollama-proxy',
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
    name='ollama-proxy',
)
