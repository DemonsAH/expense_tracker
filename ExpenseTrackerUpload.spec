# -*- mode: python ; coding: utf-8 -*-
# 手机照片局域网上传服务（纯标准库，无第三方依赖）


a = Analysis(
    ['upload_server.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 打包工具运行时
        'setuptools', 'pkg_resources', '_distutils_hack', 'distutils',
        # 深度学习 / OCR / 图像
        'torch', 'torchvision', 'cv2', 'numpy', 'PIL', 'easyocr', 'paddlex',
        'modelscope', 'huggingface_hub', 'safetensors', 'scipy', 'skimage',
        'matplotlib', 'pandas',
        # 框架 / Web 服务器（服务用标准库 http.server）
        'Flask', 'Django', 'FastAPI', 'uvicorn', 'aiohttp',
        # 其他无关库
        'pydantic', 'openai', 'httpx', 'pytest', '_pytest', 'lxml',
        'PySide6', 'shiboken6', 'tkinter',
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
    name='ExpenseTrackerUpload',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
