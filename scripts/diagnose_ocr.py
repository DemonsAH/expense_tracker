"""
Diagnostic script to check PaddleOCR installation and models
"""

import sys
from pathlib import Path

print("=" * 70)
print("🔍 PaddleOCR Diagnostic Script")
print("=" * 70)

# Check Python version
print(f"\n✓ Python version: {sys.version}")

# Check PaddleOCR
print("\nChecking PaddleOCR installation...")
try:
    import paddleocr
    print(f"✓ PaddleOCR imported successfully")
    print(f"  Version: {paddleocr.__version__ if hasattr(paddleocr, '__version__') else 'unknown'}")
except ImportError as e:
    print(f"✗ Failed to import PaddleOCR: {e}")
    sys.exit(1)

# Check NumPy
print("\nChecking NumPy...")
try:
    import numpy as np
    print(f"✓ NumPy version: {np.__version__}")
except ImportError as e:
    print(f"✗ Failed to import NumPy: {e}")
    sys.exit(1)

# Check OpenCV
print("\nChecking OpenCV...")
try:
    import cv2
    print(f"✓ OpenCV version: {cv2.__version__}")
except ImportError as e:
    print(f"✗ Failed to import OpenCV: {e}")
    sys.exit(1)

# Try to initialize PaddleOCR
print("\nAttempting to initialize PaddleOCR...")
try:
    from paddleocr import PaddleOCR
    
    print("  Initializing with ch_sim (Chinese)...")
    ocr = PaddleOCR(
        use_angle_cls=False,
        lang='ch_sim',
        use_gpu=False,
        show_log=True,  # Show logs for debugging
    )
    print("✓ PaddleOCR initialized successfully!")
    
    # Test on a simple image
    print("\n✓ Checking model files...")
    model_dir = Path.home() / '.paddleocr' / 'whl'
    if model_dir.exists():
        print(f"  Model directory: {model_dir}")
        models = list(model_dir.glob('*'))
        print(f"  Found {len(models)} model files")
        for m in models[:5]:
            print(f"    - {m.name}")
    else:
        print(f"  Model directory not found at {model_dir}")
    
except Exception as e:
    print(f"✗ Failed to initialize PaddleOCR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 70)
print("✨ All checks passed! OCR is ready to use.")
print("=" * 70)
