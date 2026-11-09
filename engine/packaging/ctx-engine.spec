# PyInstaller 스펙: 엔진을 onedir로 묶어 앱 번들의 Resources/engine에 넣는다 (spikes/sidecar).
# 실행: cd engine && uv run pyinstaller packaging/ctx-engine.spec --noconfirm
# 결과: engine/dist/ctx-engine/ctx-engine
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

datas = []
for package in ("context_holder", "trafilatura", "justext", "courlan", "htmldate", "fastembed"):
    datas += collect_data_files(package)

hiddenimports = [
    "_cffi_backend",  # pygit2 (없으면 import pygit2에서 실패한다)
    *collect_submodules("context_holder"),
    *collect_submodules("uvicorn"),  # 프로토콜 · 루프를 이름으로 불러온다
    "keyring.backends.macOS",
]

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=collect_dynamic_libs("pygit2"),
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ctx-engine",
    console=True,
    target_arch="arm64",
)
coll = COLLECT(exe, a.binaries, a.datas, name="ctx-engine")
