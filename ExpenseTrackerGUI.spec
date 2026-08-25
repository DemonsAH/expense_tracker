# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src\\expense_tracker\\gui\\app.py'],
    pathex=['src'],
    # Tcl/Tk 运行库交给 PyInstaller 的 hook-tkinter 自动收集（统一使用
    # 基础环境的同版本 Tcl/Tk）。此前手动混入 .conda 的 8.6.15 DLL，
    # 与基础环境的 8.6.12 库文件版本不匹配，导致 init.tcl 加载失败。
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 深度学习 / OCR 实验库（与本项目无关）
        'torch', 'torchvision', 'easyocr', 'paddlex', 'visualdl', 'modelscope',
        'aistudio', 'aistudio_sdk', 'bce_python_sdk', 'huggingface_hub',
        'safetensors', 'hf_xet',
        # 科学计算 / 绘图
        'pandas', 'scipy', 'skimage', 'matplotlib', 'sympy', 'networkx',
        'shapely', 'pyclipper', 'contourpy', 'cycler', 'fontTools', 'kiwisolver',
        'tifffile', 'imageio', 'imgaug', 'bidi', 'opt_einsum',
        # 另一个 GUI 框架（与 tkinter 无关）
        'PySide6', 'shiboken6',
        # Web / 爬虫
        'Flask', 'flask_babel', 'werkzeug', 'bs4', 'soupsieve', 'selenium',
        'trio', 'trio_websocket', 'wsproto', 'outcome',
        # 文档 / PDF 处理
        'pdf2docx', 'docx', 'openpyxl', 'et_xmlfile', 'reportlab', 'premailer',
        'cssselect', 'cssutils', 'fitz', 'pypdf', 'PyPDF2', 'pypdfium2',
        # 测试框架
        'pytest', '_pytest', 'pytest_cov', 'coverage', 'pluggy', 'iniconfig',
        # 其他无关库
        'lmdb', 'rarfile', 'ruamel',
    ],
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
    name='ExpenseTrackerGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
