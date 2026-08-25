"""Receipt image preprocessing: crop from black background + perspective correction.

实现方式严格遵循《图片预处理指南.md》：
  decode_image() / load_image() -> preprocess() -> OCR

假设小票平放在黑色背景上拍摄。用 Otsu 阈值按明暗分离出最亮连通域作为小票区域，
用 minAreaRect 找最小外接矩形角点，再做透视变换矫正成标准长方形，最后统一缩放 + 锐化。
找不到可信小票区域时走 passthrough（仅缩放 + 锐化，不矫正）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# 小票最小面积占比：亮区必须 >= 3% 画面，否则忽略（可能只是噪点）
MIN_RECEIPT_AREA_RATIO = 0.03
# 全亮图判定阈值：亮区 > 85% 说明整图都是亮的，没有黑底，跳过抠图
MAX_BRIGHT_RATIO = 0.85
# 长边目标宽度（1024px 实测明显掉精度，不要乱改）
TARGET_WIDTH = 1600
# 透视变换输出边保护：任一边 < 50px 视为无效四边形
MIN_OUTPUT_SIDE = 50


@dataclass
class PreprocessResult:
    image: np.ndarray
    found_receipt: bool
    method: str
    corners: list[tuple[float, float]] | None = None
    info: dict[str, Any] = field(default_factory=dict)


def decode_image(data: bytes) -> np.ndarray:
    """网络上传的原始字节流解码成 BGR ndarray。"""
    buf = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Failed to decode image bytes")
    return image


def load_image(path: str | Path) -> np.ndarray:
    """本地文件读取，返回 BGR ndarray。

    cv2.imread 在 Windows 上对含非 ASCII（中文）字符的路径会返回 None，
    这里改为按字节读取文件再 imdecode，保证中文文件名也能正常加载。
    """
    with open(path, "rb") as handle:
        data = handle.read()
    buf = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Failed to load image: {path}")
    return image


def _order_corners(box: np.ndarray) -> list[tuple[float, float]]:
    """把 minAreaRect 的 4 个角点规整成 [TL, TR, BR, BL]。

    - s = x + y：左上角 s 最小，右下角 s 最大；
    - d = x - y：右上角 d 最大，左下角 d 最小。
    """
    points = box.tolist()
    s = [p[0] + p[1] for p in points]
    d = [p[0] - p[1] for p in points]
    tl = points[s.index(min(s))]
    br = points[s.index(max(s))]
    tr = points[d.index(max(d))]
    bl = points[d.index(min(d))]
    return [tl, tr, br, bl]


def _fit_to_width(image: np.ndarray, target_width: int) -> np.ndarray:
    """长边压到 target_width，只缩不放；已 <= target_width 则原样。"""
    h, w = image.shape[:2]
    if w <= target_width:
        return image
    out_w = target_width
    out_h = max(1, int(round(h * target_width / w)))
    # 缩小专用插值 INTER_AREA，换成其他插值小票文字会糊
    return cv2.resize(image, (out_w, out_h), interpolation=cv2.INTER_AREA)


def _sharpen(image: np.ndarray) -> np.ndarray:
    """unsharp mask 轻度锐化：out = 1.6*img - 0.6*GaussianBlur(sigma=1.5)。"""
    blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=1.5)
    return cv2.addWeighted(image, 1.6, blurred, -0.6, 0)


def _passthrough(image: np.ndarray, reason: str, target_width: int) -> PreprocessResult:
    """兜底：找不到可信小票区域时不报错，仅缩放 + 锐化。"""
    img = _fit_to_width(image, target_width)
    img = _sharpen(img)
    return PreprocessResult(
        image=img,
        found_receipt=False,
        method="passthrough",
        corners=None,
        info={"reason": reason},
    )


def preprocess(image: np.ndarray, target_width: int = TARGET_WIDTH) -> PreprocessResult:
    """对 BGR 图像做抠图 + 透视矫正，返回 PreprocessResult（含新图、标志与诊断信息）。"""
    # 步骤 1：灰度 + 高斯模糊（5x5 降噪）
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # 步骤 2：Otsu 自动阈值，把图像分成"亮（小票纸面）"和"暗（黑色背景）"
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 步骤 3：找最大连通域（只取最外层轮廓，取面积最大者当作小票）
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return _passthrough(image, "no_contours", target_width)
    largest = max(contours, key=cv2.contourArea)

    # 步骤 4：面积占比合理性检查（防误判）
    h, w = image.shape[:2]
    area_ratio = cv2.contourArea(largest) / (h * w)
    if area_ratio < MIN_RECEIPT_AREA_RATIO:
        return _passthrough(image, f"area_too_small: {area_ratio:.4f}", target_width)
    if area_ratio > MAX_BRIGHT_RATIO:
        return _passthrough(image, f"all_bright: {area_ratio:.4f}", target_width)

    # 步骤 5：最小外接矩形 + 角点排序
    rect = cv2.minAreaRect(largest)
    box = cv2.boxPoints(rect)
    corners = _order_corners(box)

    # 步骤 6：透视变换矫正成矩形（取上下/左右宽度较大者，抗透视误差）
    tl, tr, br, bl = corners
    w_top = np.linalg.norm(np.array(tr) - np.array(tl))
    w_bot = np.linalg.norm(np.array(br) - np.array(bl))
    h_left = np.linalg.norm(np.array(bl) - np.array(tl))
    h_right = np.linalg.norm(np.array(br) - np.array(tr))
    out_w = round(max(w_top, w_bot))
    out_h = round(max(h_left, h_right))
    # 退化保护：任一边 < 50px 视为无效
    if out_w < MIN_OUTPUT_SIDE or out_h < MIN_OUTPUT_SIDE:
        return _passthrough(image, f"degenerate_quad: {out_w}x{out_h}", target_width)

    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    src = np.array(corners, dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(image, matrix, (out_w, out_h))

    # 步骤 7：统一缩放 + 锐化
    warped = _fit_to_width(warped, target_width)
    warped = _sharpen(warped)

    return PreprocessResult(
        image=warped,
        found_receipt=True,
        method="perspective",
        corners=corners,
        info={
            "area_ratio": round(area_ratio, 4),
            "output_size": [warped.shape[1], warped.shape[0]],
        },
    )
