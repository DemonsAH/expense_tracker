# Expense Tracker — 小票智能处理系统

Expense Tracker 是一套面向个人与多人共同消费场景的小票智能处理系统。系统从本地目录读取小票图片，调用多模态模型直接输出结构化消费数据，经 LangChain 工作流完成校验、重试、归档、人工审核、落库和报表生成。

## 环境配置

### Python 环境

推荐使用项目自带的 conda 环境：

```powershell
conda activate .\.conda
```

以可编辑模式安装项目：

```powershell
pip install -e .
```

### API Key 配置

创建或编辑项目根目录下的 `.env` 文件：

```env
SILICONFLOW_API_KEY=你的API密钥
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1

LANGSMITH_API_KEY=你的LangSmith密钥
LANGSMITH_PROJECT=expense-tracker
EXPENSE_TRACKER_ENABLE_LANGSMITH=true
```

- `EXPENSE_TRACKER_ENABLE_LANGSMITH=true` 开启 LangSmith 追踪；不需要可设为 `false`
- `SILICONFLOW_API_KEY` 是 SiliconFlow 多模态模型调用的必要配置

### 归属人配置

项目通过 `owners.json` 配置归属人。示例：

```json
{
  "owners": [
    { "id": "me",     "name": "我",   "marker": "M", "is_me": true  },
    { "id": "alice",  "name": "Alice", "marker": "A", "is_me": false },
    { "id": "bob",    "name": "Bob",   "marker": "B", "is_me": false }
  ]
}
```

规则：

- `id` 不可重复
- `marker` 不可重复
- 必须有且仅有一人 `is_me: true`

---

## 图形界面 (GUI) 启动

### 方式一：`python -m` 启动（推荐）

```powershell
python -m expense_tracker.gui.app
```

### 方式二：pip 命令行入口

安装后可执行：

```powershell
expense-tracker-gui
```

### 方式三：打包后的 exe

```powershell
.\dist\ExpenseTrackerGUI.exe
```

启动后会自动加载 `data/receipts.json` 中的小票数据和 `owners.json` 中的归属人配置。

---

## GUI 使用指南

### 主界面概览

启动后窗口顶部显示如下区域：

```
[Refresh All] [New Receipt] [Save Receipt] [Delete Receipt] [Open Image] | [Trigger Ingestion]

Total Receipts: 10   Success: 8   Failed OCR: 1   Pending Review: 1

Store: C:\...\data\receipts.json | Reports: C:\...\reports
```

- **统计栏**：实时显示已入库小票总数、成功数、失败 OCR 数、待审核数
- **状态栏**：显示上一次操作结果

下方有三个标签页：

| 标签页 | 功能 |
|--------|------|
| **Receipts** | 查看、新建、编辑、删除小票 |
| **Failed OCR** | 查看识别失败的归档、重新提交处理 |
| **Reports** | 生成、查看月度报表 |

---

### Receipts 标签页 — 管理小票

界面分为左右两栏：

- **左侧**：小票列表，按日期倒序排列，显示商家、日期、金额、OCR 状态
- **右侧**：选中小票的详情表单 + 商品列表 + 已移除审计项

#### 浏览小票

点击左侧列表中任意小票，右侧自动加载其全部字段和商品明细。

#### 手动新增小票

1. 点击工具栏 **「New Receipt」** 按钮
2. 右侧表单清空，填入：
   - **Merchant**：商家名称（如 REWE、ALDI）
   - **Purchase Date**：购买日期，格式 `YYYY-MM-DD`
   - **Currency**：货币，固定 `EUR`
   - **Total Amount**：总金额（数字）
   - **Payment Method**：支付方式（card / cash 等，选填）
   - **Default Owner**：整单默认归属人，从下拉列表选择
   - **Owner Mode**：归属模式
     - `normal`：不识别标记，全部归 Me
     - `receipt_owner`：识别整单@标记，整单归该归属人
     - `item_owner`：识别商品行标记，行级优先
   - **Receipt Marker**：整单归属标记字母
   - **OCR Status**：`pending`（新录入）/ `success`（模型识别成功）/ `verified`（人工确认）
   - **Verified**：勾选表示人工确认过
3. 点击 **「Add Item」** 逐条添加商品：

   | 字段 | 说明 |
   |------|------|
   | Name | 商品名称 |
   | Normalized | 标准化名称（用于价格对比） |
   | Category | 品类：`SNACKS / PERSONAL_CARE / HOUSEHOLD / DRINK / MEAT / VEGGIE / FRUIT / OTHER / DINING` |
   | Quantity | 数量（称重商品填重量） |
   | Unit Price | 单价 |
   | Total Price | 总价（取消项/押金项可为负数） |
   | Owner | 归属人 |
   | Marker | 归属人标记字母 |

4. 点击 **「Save Receipt」** 保存，数据自动写入 `data/receipts.json`

#### 编辑已有小票

点击左侧列表选中一个小票 → 修改右侧任意字段 → 点击 **「Save Receipt」** 保存。

可直接双击商品行 → **「Edit Item」** 修改商品详情。

#### 删除小票

选中左侧小票 → 点击 **「Delete Receipt」** → 确认后从存储中移除。

#### 删除单个商品

在商品列表中选择一行 → 点击 **「Delete Item」** 移除。

#### 查看原始图片

选中带有 `image_path` 的小票 → 点击 **「Open Image」** → 在系统默认图片查看器中打开。

---

### Trigger Ingestion — 自动识别小票（核心功能）

这是系统的核心自动化入口，将图片提交给多模态模型进行识别。

1. 点击工具栏 **「Trigger Ingestion」** 按钮
2. 在文件选择对话框中选中一张小票图片（支持 JPG、JPEG、PNG、WEBP）
3. 系统自动执行以下流程：

   ```
   图片读取 → 模型调用 → JSON 解析 → 结构校验 → 业务校验
   → 未通过则自动重试（最多 3 次）
   → 通过则写入 data/receipts.json
   → 3 次全失败则归档到 rejected_receipts/
   ```

4. 成功后会弹出提示，左侧列表刷新显示新小票
5. 失败后会弹出错误提示，可在 **Failed OCR** 标签页查看详情

**使用前提**：`.env` 中已配置 `SILICONFLOW_API_KEY`。

---

### Failed OCR 标签页 — 处理识别失败

列表显示每一条失败记录：

| 列 | 说明 |
|----|------|
| Image | 原始图片路径 |
| Attempts | 已重试次数 |
| Failure Reason | 失败原因（金额不匹配 / JSON 解析失败 / 归属人不存在等） |
| Created | 归档时间 |

#### 查看失败图片

选中一条记录 → 点击 **「Open Archived Image」** 查看归档中保存的原始图片。

#### 查看原始路径

选中一条记录 → 点击 **「Open Original Path」** 在资源管理器中打开原始图片所在的目录。

#### 重新提交处理

选中一条失败记录 → 点击 **「Review & Resubmit」**：

1. 弹窗显示该小票的失败原因和重试次数
2. 确认后系统将归档图片移回输入目录 `receipt_input/`
3. 文件名冲突时自动添加后缀避免覆盖
4. 移回后可以再次点击 **「Trigger Ingestion」** 选择该图片重新处理

---

### Reports 标签页 — 生成月报

#### 设置报表月份

在 **Report Month** 输入框中填入月份，格式 `YYYY-MM`（默认值为上个月）。

#### 生成月报

1. 输入月份
2. 勾选 **Write Schema**（可选，会同时输出 JSON Schema 文件）
3. 点击 **「Generate Report」**

系统会读取 `data/receipts.json` 中该月及历史数据，生成包含以下内容的报表：

| 模块 | 内容 |
|------|------|
| 概览 | 月总支出、季度总支出、我的支出、MoM 环比变化、YoY 同比增长、Top 品类 |
| 归属人支出 | 各归属人支出金额、占比、环比/同比变化 |
| 品类支出 | 各品类支出金额、占比 |
| 价格涨跌排行 | Top 5 涨价商品、Top 5 降价商品 |
| 月度摘要 | 自然语言高亮总结 |

输出文件：

- `reports/YYYY-MM/report.json` — 结构化 JSON 报表
- `reports/YYYY-MM/report.html` — 可浏览器打开的 HTML 网页报表
- `reports/_schema/monthly_report.schema.json` — JSON Schema（勾选 Write Schema 时）

#### 查看报表

- 选中一条月报 → **「Open HTML」** 在浏览器中打开
- 选中一条月报 → **「Open JSON」** 用默认程序打开 JSON 数据

#### 刷新列表

点击 **「Refresh Reports」** 重新扫描 `reports/` 目录，列出所有已生成的月报。

---

## CLI 命令行使用

CLI 是开发测试阶段的主要入口，也能在自动化定时任务中使用。

### 环境变量（pip 未安装时）

```powershell
$env:PYTHONPATH="src"
```

以下示例默认已执行 `pip install -e .`，命令以 `expense-tracker` 开头。

### 查看帮助

```powershell
expense-tracker --help
```

### 处理单张小票

```powershell
expense-tracker ingest test_receipts/test1.jpg --print-json
```

可选参数：

| 参数 | 说明 |
|------|------|
| `--owners` | 归属人配置文件路径 |
| `--model` | 模型名，默认 `Qwen/Qwen3.6-27B` |
| `--max-attempts` | 最大重试次数，默认 3 |
| `--artifact-dir` | 成功产物输出目录 |
| `--failure-dir` | 失败归档目录 |
| `--store-path` | JSON 存储文件路径 |
| `--no-store` | 不写入 JSON store |
| `--no-archive` | 不归档失败记录 |
| `--print-json` | 打印最终 receipt_record |

### 批量处理目录

```powershell
expense-tracker ingest-dir test_receipts
```

默认跳过已在 store 中存在的图片（按 `image_path` + 文件 `sha256` 哈希判断）。

强制重新处理所有图片：

```powershell
expense-tracker ingest-dir test_receipts --no-skip-processed
```

### 生成月度报表

```powershell
# 生成上一个自然月的报表
expense-tracker generate-report --write-schema

# 生成指定月份的报表
expense-tracker generate-report 2026-05 --write-schema
```

### 定时报表任务

由 Task Scheduler / cron 自动触发，目标为上个月。若 `report.json` 和 `report.html` 均已存在则跳过。

```powershell
expense-tracker run-report-job --write-schema

# 强制重建
expense-tracker run-report-job --write-schema --force
```

### 定时入库任务

扫描指定目录中的图片，处理后移走源文件：
- 成功 → `processed_receipts/`
- 跳过重复 → `processed_receipts/`
- 失败 → `rejected_receipts/_source/`

```powershell
expense-tracker run-ingest-job incoming_receipts --recursive
```

---

## 处理流程

单张图片的完整处理链路：

1. 读取图片文件
2. 调用多模态模型获取 JSON
3. 解析结构化 JSON
4. 结构校验（字段完整、类型合法）
5. 业务校验（owner_id 存在、金额一致、品类枚举合法）
6. 失败时自动重试，最多 3 次
7. 后处理（保留取消项，不自动移除）
8. 通过后写入 `data/receipts.json`
9. 每次调用模型原始文本均保存留档
10. 3 次全失败后归档到 `rejected_receipts/`

---

## 业务规则摘要

### 取消项与负数项

- `Storno`、`Sofortstorno`、`Rücknahme` 等取消项保留在正式商品列表中
- `Leergut`、`Pfand` 等押金负数项也保留
- 负数 `total_price` 参与月度总支出，自然抵消
- 旧版规则中自动移除逻辑已废弃

### 价格排行排除

以下不进入涨跌排行：
- `DINING` 品类
- `Leergut / Pfand` 商品（按名称检测）
- 所有 `total_price < 0` 的负数项

### 归属人识别

三种模式：
- `normal`：不识别标记，全部归 Me
- `receipt_owner`：识别小票顶部 `@A`、`@B` 整单标记
- `item_owner`：识别商品行末尾 `A`、`B` 行级标记；行级优先，无标记回退整单，整单未标记回退 Me

归属人识别可通过将 `marker_to_id` 映射置空来关闭（关闭后统一进入 `normal` 模式）。

---

## 项目结构

```
src/expense_tracker/
├── cli.py              # CLI 命令行入口
├── config.py           # 系统配置管理
├── ocr_client.py       # 多模态模型调用客户端
├── ocr_parser.py       # OCR 文本解析器（归属人标记检测等）
├── llm_client.py       # LLM 客户端抽象
├── tracing.py          # LangSmith 追踪
├── pipelines/          # 处理管线
│   ├── receipt_ingestion.py    # 主入库管线
│   ├── receipt_validation.py   # 业务校验
│   ├── receipt_postprocess.py  # 后处理
│   └── retry_policy.py         # 重试策略
├── schemas/            # 数据模型
│   ├── enums.py        # 枚举（ItemCategory, OwnerMode, OcrStatus）
│   ├── extraction.py   # 模型输出结构
│   ├── domain.py       # 持久化存储结构
│   ├── owners.py       # 归属人配置
│   └── converters.py   # 模型输出 → 存储结构转换
├── storage/            # 持久化存储
│   ├── json_store.py   # JSON store 读写
│   ├── file_index.py   # 文件哈希
│   ├── artifacts.py    # 成功/失败产物保存
│   └── directory_flow.py  # 文件移动与去重
├── reports/            # 报表模块
│   └── monthly.py      # 月度报表生成、HTML 渲染
├── automation/         # 自动化任务
│   ├── ingest_jobs.py  # 定时入库任务
│   └── report_jobs.py  # 定时报表任务
├── prompts/            # Prompt 模板
│   └── receipt_prompt.py  # 小票识别 Prompt 构建
└── gui/                # 图形界面
    ├── app.py          # Tkinter 主界面
    └── services.py     # GUI 业务服务层

tests/                  # 231 个单元测试
├── test_schemas.py
├── test_storage.py
├── test_pipelines_unit.py
├── test_owner_recognition.py
├── test_business_rules_phase2.py
├── test_reports_unit.py
├── test_reports.py
├── test_prompts.py
├── test_gui_services.py
├── test_cli_reports.py
├── test_ingest_jobs.py
├── test_report_jobs.py
├── test_receipt_pipeline.py
└── conftest.py
```