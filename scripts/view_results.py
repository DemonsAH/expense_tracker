import json
import os

with open('ocr_test_results.json', 'r', encoding='utf-8') as f:
    results = json.load(f)

print('='*80)
print('OCR 测试结果总结')
print('='*80)

# Count successes
success_count = sum(1 for r in results if r['success'])

for idx, result in enumerate(results):
    file_name = os.path.basename(result['image_path'])
    print(f'\n【结果 {idx + 1}】')
    print(f'图片: {file_name}')
    print(f'状态: {"✓ 成功" if result["success"] else "✗ 失败"}')
    print(f'检测元素: {result["detailed_results_count"]} 个')
    
    print(f'\n✅ 提取的信息:')
    info = result['parsed_info']
    print(f'   金额: {info["amount"]} EUR')
    print(f'   日期: {info["date"]}')
    print(f'   商家: {info["description"]}')
    print(f'   分类: {info["category"]}')
    
    print(f'\n📄 OCR原始文本预览 (前800字符):')
    text = result['ocr_text'][:800]
    print(text)
    print('\n' + '-'*80)

print('\n' + '='*80)
print(f'总体统计: {success_count}/{len(results)} 成功处理')
print('='*80)
