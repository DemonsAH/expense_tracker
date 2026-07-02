"""Local Unlimited-OCR client using llama-mtmd-cli."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Paths relative to the Unlimited-OCR portable package
OCR_PACKAGE_DIR = Path(__file__).resolve().parent.parent.parent / "Unlimited_OCR_本地部署整合包"
MTMD_CLI = str(OCR_PACKAGE_DIR / "runtime" / "llama.cpp" / "llama-mtmd-cli.exe")
MODEL = str(OCR_PACKAGE_DIR / "models" / "Unlimited-OCR-Q4_K_M.gguf")
MMPROJ = str(OCR_PACKAGE_DIR / "models" / "mmproj-Unlimited-OCR-F16.gguf")

DEFAULT_REQUEST_TIMEOUT = 600  # seconds
DEFAULT_TOKEN_BUDGET = 4096

# Regex to strip grounding tags like <|det|>...</|det|>
GROUNDING_RE = re.compile(r"<\|(ref|det)\|>.*?<\|/\1\|>", re.DOTALL)


def strip_grounding(text: str) -> str:
    return GROUNDING_RE.sub("", text)


def run_ocr(
    image_path: str | Path,
    *,
    prompt: str = "document parsing.",
    n_predict: int = DEFAULT_TOKEN_BUDGET,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    keep_grounding: bool = False,
) -> str:
    """Run Unlimited-OCR on a single image and return the OCR text."""
    image = Path(image_path).resolve()
    cmd = [
        MTMD_CLI,
        "-m", MODEL,
        "--mmproj", MMPROJ,
        "--image", str(image),
        "-p", prompt,
        "--chat-template", "deepseek-ocr",
        "--temp", "0",
        "--flash-attn", "off",
        "--no-warmup",
        "-n", str(n_predict),
        # DRY parameters to prevent repetition
        "--dry-multiplier", "0.8",
        "--dry-base", "1.75",
        "--dry-allowed-length", "2",
        "--dry-penalty-last-n", "-1",
        "--dry-sequence-breaker", "none",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=False,
            timeout=timeout,
            cwd=str(OCR_PACKAGE_DIR),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"OCR timed out after {timeout}s")

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"llama-mtmd-cli failed (code={result.returncode}): {stderr}")

    output = result.stdout.decode("utf-8", errors="replace").strip()
    if not output:
        raise RuntimeError("OCR returned empty output")

    if not keep_grounding:
        output = strip_grounding(output)

    return output