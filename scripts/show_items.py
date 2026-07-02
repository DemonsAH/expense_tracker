import json
import os
from collections import defaultdict

with open('ocr_test_results.json', 'r', encoding='utf-8') as f:
    results = json.load(f)

print('='*90)
print('📦 小票商品明细分析')
print('='*90)

# Group by image
grouped = defaultdict(list)
for idx, result in enumerate(results):
    image_name = os.path.basename(result['image_path'])
    grouped[image_name].append((idx, result))

for image_name in sorted(grouped.keys()):
    result_list = grouped[image_name]
    # Use the first result for this image
    result = result_list[0][1]
    
    print(f'\n{"="*90}')
    print(f'📸 {image_name}')
    print(f'{"="*90}')
    
    print(f'总金额: {result["parsed_info"]["amount"]} EUR')
    print(f'日期: {result["parsed_info"]["date"]}')
    print(f'商家: {result["parsed_info"]["description"]}')
    print(f'检测元素总数: {result["detailed_results_count"]}')
    
    items = result['parsed_info'].get('items', [])
    
    if not items:
        print(f'\n⚠️  未检测到商品清单')
    else:
        print(f'\n✅ 检测到 {len(items)} 个商品：')
        print(f'\n{"序号":<5} {"商品名称":<40} {"数量":<8} {"单价":<10} {"小计":<10}')
        print('-' * 90)
        
        total_parsed = 0
        for idx, item in enumerate(items, 1):
            name = item['name'][:37] if len(item['name']) > 37 else item['name']
            qty = item['quantity']
            unit_price = item['unit_price']
            subtotal = item['subtotal']
            
            print(f'{idx:<5} {name:<40} {qty:<8} ${unit_price:<9.2f} ${subtotal:<9.2f}')
            total_parsed += subtotal
        
        print('-' * 90)
        print(f'{"总计（解析）":<46} ${total_parsed:<9.2f}')
        print(f'{"总计（原始）":<46} ${result["parsed_info"]["amount"]:<9.2f}')
        
        diff = abs(total_parsed - result["parsed_info"]["amount"])
        if diff > 0.1:
            print(f'⚠️  差异: ${diff:.2f} (解析结果与原始金额不匹配)')
        else:
            print(f'✓ 匹配成功')
    
    # Show raw OCR text
    print(f'\n📄 OCR原始文本:')
    print('-' * 90)
    ocr_text = result['ocr_text']
    # Show first 30 lines
    lines = ocr_text.split('\n')[:30]
    for line in lines:
        if line.strip():
            print(f'  {line}')
    if len(ocr_text.split('\n')) > 30:
        print(f'  ... (共 {len(ocr_text.split(chr(10)))} 行)')

print(f'\n{"="*90}')
print(f'统计总结')
print(f'{"="*90}')

# Overall stats
unique_images = set(os.path.basename(r['image_path']) for r in results)
print(f'总共处理: {len(unique_images)} 张唯一小票')

total_items = sum(len(r['parsed_info'].get('items', [])) for r in results)
avg_items = total_items / max(len(unique_images), 1)

print(f'总共识别商品: {total_items} 个')
print(f'平均每张小票: {avg_items:.1f} 个商品')

total_amount = sum(r['parsed_info']['amount'] or 0 for r in results) / len(unique_images)
print(f'平均小票金额: ${total_amount:.2f}')

print(f'\n✨ 分析完成')
