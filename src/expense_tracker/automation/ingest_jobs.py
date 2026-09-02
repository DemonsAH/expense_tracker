"""Automation entrypoints for scheduled directory ingestion."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from expense_tracker.pipelines import ingest_receipt_with_retries
from expense_tracker.storage import (
    compute_file_sha256,
    has_processed_image,
    load_receipt_store,
    move_source_file,
    save_receipt_store,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DUPLICATE_POLICIES = {"skip-success", "retry-failed-only", "force-reprocess"}

# 中文字符范围（基本区 / 扩展 A / 兼容区 / 全角 / CJK 符号）
_CHINESE_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\uff00-\uffef\u3000-\u303f]"
)


@dataclass
class IngestJobResult:
    directory: Path
    images_found: int
    success_count: int
    failure_count: int
    skipped_count: int
    success_files: list[str] = field(default_factory=list)
    failed_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    moved_success_files: list[str] = field(default_factory=list)
    moved_failed_files: list[str] = field(default_factory=list)
    moved_skipped_files: list[str] = field(default_factory=list)
    duplicate_policy: str = "skip-success"


def _iter_job_image_paths(directory: Path, *, recursive: bool) -> list[Path]:
    iterator = directory.rglob("*") if recursive else directory.iterdir()
    return sorted(
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def _unique_image_path(target: Path) -> Path:
    """目标路径已存在时追加 _1/_2/... 避免覆盖。"""
    if not target.exists():
        return target
    counter = 1
    while True:
        candidate = target.with_name(f"{target.stem}_{counter}{target.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _strip_chinese_from_filename(path: Path) -> Path | None:
    """把文件名中的中文字符去除（就地重命名），返回新路径；无中文返回 None。

    全角字符先归一化为半角（NFKC），再去掉中文；纯中文名（去除后为空）时
    用文件哈希前 8 位兜底命名；重命名后与其他文件冲突时自动追加 _1/_2/...。
    """
    if not _CHINESE_RE.search(path.stem):
        return None
    normalized = unicodedata.normalize("NFKC", path.stem)
    new_stem = _CHINESE_RE.sub("", normalized).strip(" _-")
    if not new_stem:
        new_stem = f"img_{compute_file_sha256(path)[:8]}"
    target = _unique_image_path(path.with_name(f"{new_stem}{path.suffix}"))
    path.rename(target)
    return target


def _should_skip_processed_image(store, image_path: Path) -> bool:
    image_hash = compute_file_sha256(image_path)
    return has_processed_image(
        store,
        image_path=str(image_path),
        image_hash=image_hash,
    )


def _has_failed_ocr_record(store, image_path: Path) -> bool:
    image_hash = compute_file_sha256(image_path)
    for record in store.failed_ocr_records:
        if record.image_hash and record.image_hash == image_hash:
            return True
        if record.image_path == str(image_path):
            return True
    return False


def _validate_duplicate_policy(policy: str) -> str:
    if policy not in DUPLICATE_POLICIES:
        raise ValueError(
            f"Invalid duplicate policy '{policy}'. "
            f"Expected one of: {', '.join(sorted(DUPLICATE_POLICIES))}."
        )
    return policy


def _should_skip_by_policy(store, image_path: Path, duplicate_policy: str) -> bool:
    duplicate_policy = _validate_duplicate_policy(duplicate_policy)
    if duplicate_policy == "force-reprocess":
        return False
    if duplicate_policy == "retry-failed-only":
        return not _has_failed_ocr_record(store, image_path)
    return _should_skip_processed_image(store, image_path)


def run_ingest_directory_job(
    directory: str | Path,
    *,
    owners_path: str | Path = "owners.json",
    max_attempts: int = 3,
    artifact_output_dir: str | Path | None = None,
    failure_output_dir: str | Path = "rejected_receipts",
    processed_output_dir: str | Path = "processed_receipts",
    store_path: str | Path = "data/receipts.json",
    archive_failures: bool = True,
    duplicate_policy: str = "skip-success",
    recursive: bool = False,
    use_llm_parser: bool = False,
    use_preprocess: bool = False,
    keep_failures: bool = False,
) -> IngestJobResult:
    duplicate_policy = _validate_duplicate_policy(duplicate_policy)
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Directory not found: {root}")

    image_paths = _iter_job_image_paths(root, recursive=recursive)
    if not image_paths:
        raise ValueError(f"No supported receipt images found in: {root}")

    # 开始处理（trigger）之前，先把文件名中的中文字符去除：
    # 避免原生 OCR 程序（llama-mtmd-cli 等）对中文路径的兼容问题。
    renamed_any = False
    for image_path in image_paths:
        if _strip_chinese_from_filename(image_path) is not None:
            renamed_any = True
    if renamed_any:
        image_paths = _iter_job_image_paths(root, recursive=recursive)

    store = load_receipt_store(store_path)
    result = IngestJobResult(
        directory=root,
        images_found=len(image_paths),
        success_count=0,
        failure_count=0,
        skipped_count=0,
        duplicate_policy=duplicate_policy,
    )

    for image_path in image_paths:
        if _should_skip_by_policy(store, image_path, duplicate_policy):
            result.skipped_count += 1
            result.skipped_files.append(image_path.name)
            moved_path = move_source_file(
                image_path,
                source_root=root,
                destination_root=processed_output_dir,
            )
            result.moved_skipped_files.append(str(moved_path))
            continue

        try:
            ingestion_result = ingest_receipt_with_retries(
                image_path=image_path,
                owners_path=owners_path,
                max_attempts=max_attempts,
                save_artifacts=True,
                artifact_output_dir=artifact_output_dir,
                persist_store=True,
                store_path=store_path,
                archive_failures=archive_failures,
                failure_output_dir=failure_output_dir,
                use_llm_parser=use_llm_parser,
                use_preprocess=use_preprocess,
            )
            result.success_count += 1
            result.success_files.append(image_path.name)
            # 成功：按分配的 receipt id 重命名图片并移入"已处理"目录，
            # 同时更新 store 里的 image_path 指向新位置。
            moved_path = move_source_file(
                image_path,
                source_root=root,
                destination_root=processed_output_dir,
                dest_stem=ingestion_result.receipt_record.id,
            )
            result.moved_success_files.append(str(moved_path))
            store = load_receipt_store(store_path)
            for receipt in store.receipts:
                if receipt.id == ingestion_result.receipt_record.id:
                    receipt.image_path = str(moved_path)
                    break
            save_receipt_store(store, store_path)
        except Exception:
            result.failure_count += 1
            result.failed_files.append(image_path.name)
            # 三目录工作流（未处理→已处理→已校对）：失败文件留在源目录，
            # 便于人工查看原因后重试；否则保持原有行为移入失败目录。
            if not keep_failures and image_path.exists():
                moved_path = move_source_file(
                    image_path,
                    source_root=root,
                    destination_root=Path(failure_output_dir) / "_source",
                )
                result.moved_failed_files.append(str(moved_path))
            store = load_receipt_store(store_path)

    return result
