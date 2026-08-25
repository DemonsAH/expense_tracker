"""测试 LLM 管道测试脚本"""
from __future__ import annotations

import sys
import os
from pathlib import Path

sys.path.insert(0, "src")

from dotenv import load_dotenv
from expense_tracker.llm_parser import parse_ocr_with_llm


def main():
    # 强制加载 .env
    load_dotenv(".env")
    api_key = os.environ.get("SILICONFLOW_API_KEY")
    print(f"API Key status: {'OK' if api_key else 'MISSING'}")

    # 用已知保存的测试 OCR 结果测试 LLM parser
    test_ocr_file = Path("test_receipts/test1_ocr_result.txt")
    if not test_ocr_file.exists():
        print(f"缺少 OCR test file {test_ocr_file} 不存在！")
        return 1

    ocr_text = test_ocr_file.read_text(encoding="utf-8")
    print(f"读取 OCR 文本: {len(ocr_text)} chars")
    print("-"*60)

    try:
        receipt = parse_ocr_with_llm(ocr_text)
        print("✅ LLM 解析成功！")
        print(f"商店: {receipt.merchant}")
        print(f"日期: {receipt.purchase_date}")
        print(f"总金额: {receipt.total_amount} {receipt.currency}")
        print(f"商品数: {len(receipt.items)} 件")
        return 0
    except Exception as e:
        print(f"❌ LLM 解析失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
