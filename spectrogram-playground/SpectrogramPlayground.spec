from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("librosa")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas + [("src/resources/styles.qss", "src/resources")],
    hiddenimports=hiddenimports + ["sounddevice", "soundfile", "pyqtgraph"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SpectrogramPlayground", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="SpectrogramPlayground")
