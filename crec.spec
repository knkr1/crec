# PyInstaller spec for the single-file crec.exe.
#
# yt-dlp IS bundled, so a fresh install works offline straight away. It is still
# overridable at runtime: crec prepends its own writable lib directory to
# sys.path before importing yt_dlp, and a SourceFileLoader from there wins over
# the bundled copy (verified on Windows onefile builds). That is what lets an
# extractor fix reach users without shipping a new crec.
#
# ffmpeg is deliberately NOT bundled - it is fetched on first use, which keeps
# this download small and means we never redistribute a GPL binary.

# ruff: noqa
block_cipher = None

a = Analysis(
    ["entry.py"],
    pathex=["."],
    binaries=[],
    datas=[("crec/presets.toml", "crec")],
    hiddenimports=[
        "crec",
        "crec.cli",
        # yt-dlp's optional companions. Present -> more sites work; the bundle
        # grows by a couple of MB, which is a trade worth making.
        "Cryptodome",
        "mutagen",
        "brotli",
        "certifi",
        "websockets",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Nothing in crec touches these; excluding them keeps the exe lean.
        "tkinter",
        "test",
        "unittest",
        "pytest",
        "PyInstaller",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="crec",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed exes trip antivirus heuristics; not worth the MB.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
