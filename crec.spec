# PyInstaller spec for crec: a folder holding crec.exe, crecw.exe and _internal/.
#
# A folder (onedir), not a single self-extracting exe. Onefile unpacks itself to
# a temp directory on every run - one to two seconds before anything happens,
# more with antivirus scanning each extraction - and self-extracting exes are a
# classic antivirus false positive. A tool you run constantly cannot afford it.
#
# Two exes share one _internal/: crec.exe is the console program; crecw.exe is
# identical but windowless, for the tray, so starting it at login flashes no
# console window.
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
    datas=[("crec/presets.toml", "crec"), ("crec/assets/*.ico", "crec/assets")],
    hiddenimports=[
        "crec",
        "crec.cli",
        "crec.tray",  # imported lazily by `crec tray`
        # yt-dlp's optional companions. Present -> more sites work; the bundle
        # grows by a couple of MB, which is a trade worth making.
        "Cryptodome",
        "mutagen",
        "brotli",
        "certifi",
        "websockets",
        "requests",
        "yt_dlp_ejs",
        # Browser impersonation; without it TikTok serves a bot-challenge page.
        "curl_cffi",
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


def make_exe(name, console):
    return EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,  # UPX-packed exes trip antivirus heuristics; not worth the MB.
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon="crec/assets/crec.ico",
    )


coll = COLLECT(
    make_exe("crec", console=True),
    make_exe("crecw", console=False),
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="crec",
)
