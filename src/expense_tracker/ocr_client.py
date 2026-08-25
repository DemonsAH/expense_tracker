"""Local GLM-OCR client using llama-mtmd-cli.

Runs the GLM-OCR multimodal model (llama.cpp mtmd project) on a single image
via the llama-mtmd-cli subprocess. Deployment layout and tuned parameters are
documented in GLM-OCR本地部署指南.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Marker used to identify the GLM-OCR deployment directory.
_OCR_PACKAGE_NAME = "ocr_service"
_OCR_CLI_RELATIVE = Path("llama") / "llama-mtmd-cli.exe"


def _find_ocr_package_dir() -> Path:
    """Locate the GLM-OCR deployment directory (ocr_service).

    The deployment bundles model weights and native binaries, so it is
    intentionally kept out of the PyInstaller archive. In a frozen build
    ``__file__`` points into the extracted bundle rather than the source tree,
    so we look next to the executable first, then relative to the source tree
    and the current directory.
    """
    env_dir = os.environ.get("EXPENSE_TRACKER_OCR_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    anchors: list[Path] = []
    if getattr(sys, "frozen", False):
        anchors.append(Path(sys.executable).resolve().parent)
    anchors.append(Path(__file__).resolve().parent.parent.parent)
    anchors.append(Path.cwd().resolve())

    for anchor in anchors:
        current = anchor if anchor.is_dir() else anchor.parent
        for _ in range(6):
            candidate = current / _OCR_PACKAGE_NAME
            if (candidate / _OCR_CLI_RELATIVE).exists():
                return candidate
            if current.parent == current:
                break
            current = current.parent

    return Path(__file__).resolve().parent.parent.parent / _OCR_PACKAGE_NAME


# Paths relative to the GLM-OCR deployment directory
OCR_PACKAGE_DIR = _find_ocr_package_dir()
LLAMA_DIR = OCR_PACKAGE_DIR / "llama"
MTMD_CLI = str(LLAMA_DIR / "llama-mtmd-cli.exe")
MODEL = str(LLAMA_DIR / "models" / "GLM-OCR-Q8_0.gguf")
MMPROJ = str(LLAMA_DIR / "models" / "mmproj-GLM-OCR-Q8_0.gguf")

DEFAULT_REQUEST_TIMEOUT = 180  # seconds
DEFAULT_TOKEN_BUDGET = 800  # GLM-OCR 输出预算上限（指南 §4：不能超过 800）

# Regex to strip grounding tags like <|det|>...</|det|> (kept for compatibility;
# GLM-OCR itself does not emit grounding tags).
GROUNDING_RE = re.compile(r"<\|(ref|det)\|>.*?<\|/\1\|>", re.DOTALL)


def strip_grounding(text: str) -> str:
    return GROUNDING_RE.sub("", text)


def _prepare_image_for_cli(image: Path) -> Path:
    """llama-mtmd-cli 的原生解码器（stb_image）只认标准格式，且 Windows 上
    读不了含非 ASCII（中文）字符的路径。微信导出的 JPEG 常是非标准编码
    （CMYK/渐进式等），cv2 能解码但 stb_image 会报 failed to decode。

    统一用 cv2 解码后重编码为标准 PNG 写到 ASCII 临时路径（调用方负责
    清理），彻底规避路径编码与图片格式两类问题。
    """
    import cv2
    from expense_tracker.preprocess import load_image

    image_array = load_image(image)
    fd, tmp_path = tempfile.mkstemp(prefix="ocr_input_", suffix=".png")
    os.close(fd)
    tmp = Path(tmp_path)
    if not cv2.imwrite(str(tmp), image_array):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to write OCR temp image: {tmp}")
    return tmp


def _normalize_ocr_output(content: str) -> str:
    """Convert GLM-OCR JSON-lines output to text; pass plain text / HTML through.

    GLM-OCR 的输出不稳定（指南 §5），可能是纯文本、JSON 或 HTML <table>。
    纯文本与 HTML 直接透传（HTML 表格由 ocr_parser.py 处理）；JSON
    （``{"lines": [{"text", "bbox"}]}``）则按行重建：块按行 (y1) 后列 (x1)
    排序，同一行的块用空格拼接，保证 名称/价格/归属人标记 保持在同一行。
    坐标已统一归一化到 0..1000 网格。
    """
    content = content.strip()
    if not content:
        return content

    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        return content
    try:
        data = json.loads(content[start:end + 1])
    except json.JSONDecodeError:
        return content

    lines = data.get("lines")
    if not isinstance(lines, list) or not lines:
        return content

    blocks: list[tuple[float, float, str]] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        text = line.get("text")
        bbox = line.get("bbox")
        if not text or not bbox:
            continue
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            x1, y1 = float(bbox[0]), float(bbox[1])
        elif isinstance(bbox, dict):
            x1 = float(bbox.get("x1", bbox.get("x", 0)))
            y1 = float(bbox.get("y1", bbox.get("y", 0)))
        else:
            x1, y1 = 0.0, 0.0
        blocks.append((y1, x1, str(text).strip()))

    if not blocks:
        return content

    # 同一行的块（y 差 ≤ 25，基于 0..1000 归一化网格）拼接成一行
    blocks.sort(key=lambda b: (b[0], b[1]))
    rows: list[list[str]] = []
    current_row: list[str] = []
    current_y: float | None = None
    for y1, _, text in blocks:
        if current_y is not None and abs(y1 - current_y) <= 25:
            current_row.append(text)
        else:
            if current_row:
                rows.append(current_row)
            current_row = [text]
            current_y = y1
    if current_row:
        rows.append(current_row)

    return "\n".join(" ".join(row) for row in rows)


def run_ocr(
    image_path: str | Path,
    *,
    prompt: str = "OCR",
    n_predict: int = DEFAULT_TOKEN_BUDGET,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    keep_grounding: bool = False,
) -> str:
    """Run GLM-OCR on a single image and return the OCR text."""
    image = Path(image_path).resolve()
    if not Path(MTMD_CLI).is_file():
        raise FileNotFoundError(
            f"GLM-OCR CLI not found at {MTMD_CLI}. "
            "Set EXPENSE_TRACKER_OCR_DIR to the folder containing the ocr_service deployment."
        )

    # llama-mtmd-cli 读不了中文路径和非标准图片格式：先统一重编码为标准
    # PNG 写到 ASCII 临时文件（用完删除）
    temp_copy: Path | None = None
    try:
        image_arg = _prepare_image_for_cli(image)
        if image_arg is not image:
            temp_copy = image_arg
        cmd = [
            MTMD_CLI,
            "-m", MODEL,
            "--mmproj", MMPROJ,
            "--image", str(image_arg),
            "-p", prompt,
            "-c", "8192",
            "--temp", "0",
            "--top-k", "1",
            "--seed", "0",
            # 防复读：默认 repeat-penalty 1.0 会循环输出（指南 §3 实测调优）
            "--repeat-penalty", "1.3",
            "--repeat-last-n", "256",
            "-n", str(n_predict),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=False,
                timeout=timeout,
                cwd=str(LLAMA_DIR),
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"OCR timed out after {timeout}s")
    finally:
        if temp_copy is not None:
            temp_copy.unlink(missing_ok=True)

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"llama-mtmd-cli failed (code={result.returncode}): {stderr}")

    output = result.stdout.decode("utf-8", errors="replace").strip()
    if not output:
        raise RuntimeError("OCR returned empty output")

    output = _normalize_ocr_output(output)

    if not keep_grounding:
        output = strip_grounding(output)

    return output
