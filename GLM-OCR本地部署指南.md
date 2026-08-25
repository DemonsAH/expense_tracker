# GLM-OCR 本地部署指南（Windows + CUDA，llama.cpp）

> 用途：本文档给其他 Agent 使用。任何涉及小票 OCR / 图片文字识别的任务，
> 先读本文档，按"快速启动"即可复用本机已就绪的 GLM-OCR 服务，不要重复造轮子。

## 1. 一句话总结

GLM-OCR 通过 **llama.cpp 的 `llama-server.exe`** 在本机跑成一个 **OpenAI 兼容 API 服务**，
监听 `http://127.0.0.1:8080/v1`，模型名 `glm-ocr`。业务代码用 openai SDK 直接调用，
无需改任何模型部署逻辑。

## 2. 目录结构（已就绪，勿删除）

```
ocr_service/
└── llama/
    ├── llama-server.exe          # llama.cpp b10453，CUDA 12.4 版
    ├── llama.dll / llama.exe / ggml*.dll / mtmd.dll
    ├── cudart64_12.dll           (0.5MB)   ← 必须有！
    ├── cublas64_12.dll           (95MB)    ← 必须有！
    ├── cublasLt64_12.dll         (452MB)   ← 必须有！
    └── models/
        ├── GLM-OCR-Q8_0.gguf     (906MB)   # 主模型
        └── mmproj-GLM-OCR-Q8_0.gguf (462MB) # 视觉投影器，OCR 必须
```

- 引擎来源：[llama.cpp Releases](https://github.com/ggml-org/llama.cpp/releases)
  `llama-*-bin-win-cuda-12.4-x64.zip`
- CUDA 运行时 DLL 来源：同名版本的 `cudart-llama-bin-win-cuda-12.4-x64.zip`
  （普通 CUDA 发布包**不带**运行时 DLL，需单独下载解出上面 3 个 DLL）
- 模型来源：[ggml-org/GLM-OCR-GGUF](https://huggingface.co/ggml-org/GLM-OCR-GGUF)

## 3. 快速启动（推荐）

双击 `ocr_service\start-llama.bat`（启动前会自动检查 exe / 模型 / CUDA DLL 是否存在）。

等价命令（PowerShell）：

```powershell
.\llama\llama-server.exe -m .\llama\models\GLM-OCR-Q8_0.gguf `
  --mmproj .\llama\models\mmproj-GLM-OCR-Q8_0.gguf `
  --host 127.0.0.1 --port 8080 -ngl 99 --temp 0 --top-k 1 --seed 0 `
  --ctx-size 8192 --repeat-penalty 1.3 --repeat-last-n 256 `
  --alias glm-ocr --no-webui
```

参数说明（实测调优过的，别乱改）：
| 参数 | 值 | 原因 |
|------|-----|------|
| `-ngl` | 99 | 全部层上 GPU |
| `--temp 0 --top-k 1 --seed 0` | — | 确定性输出 |
| `--repeat-penalty 1.3 --repeat-last-n 256` | — | 默认 1.0 会复读循环 |
| `--ctx-size 8192` | — | 足够容纳图片 token |
| `--no-webui` | — | 8080 只留 API |
| `--flash-attn` | **不要加** | 新语法要显式 `on/off/auto`，写 `-fa` 报错；本模型无收益 |

## 4. 调用方式（业务代码）

OpenAI 兼容接口，模型名 `glm-ocr`（对应 `--alias glm-ocr`）：

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="local")  # 本地无需 key

resp = client.chat.completions.create(
    model="glm-ocr",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "OCR"},                      # ★ 提示词只能是 OCR
            {"type": "image_url", "image_url": {"url": data_url}}, # ★ 文本在前、图片在后
        ],
    }],
    temperature=0,
    max_tokens=800,   # ★ 不能超过 800
)
content = resp.choices[0].message.content
```

对应 .env 配置（参考 `小票V1.2\.env.example`）：

```ini
OCR_BACKEND=auto
OCR_BASE_URL=http://127.0.0.1:8080/v1
OCR_API_KEY=            # 本地服务不需要 key，留空
OCR_MODEL=glm-ocr
OCR_PROMPT_STYLE=glm-ocr
OCR_TIMEOUT=180
OCR_MAX_TOKENS=800
```

自动路由逻辑（config.py 的 `resolved_ocr_backend`）：`auto` 模式下
`OCR_BASE_URL` 填了就真调 OCR，没填走离线 mock。

## 5. 结果解析（注意）

GLM-OCR 输出**不稳定**，可能是：
1. 纯文本（每行一个文字块）
2. JSON（`{"lines": [{"text","bbox"}]}`）
3. **HTML `<table>`**（`OCR markdown` 模式下常见，单元格=商品名/价格）

本项目解析器在 `src\expense_tracker\ocr_client.py` 的 `_parse_ocr_content`，
三种都兼容：HTML 表格自动转成 `名称   价格` 的行。新代码调用时直接复用该解析逻辑。
坐标已统一归一化到 0..1000 网格。

## 6. 验证服务是否可用

```powershell
# 1) 服务健康检查
curl.exe http://127.0.0.1:8080/v1/models

# 2) 命令行 OCR 测试（如果装了 expense-tracker CLI）
python -m expense_tracker.cli test-ocr 小票.jpg --base-url http://127.0.0.1:8080/v1 --model glm-ocr --prompt-style glm-ocr

# 3) 确认 GPU 真的生效（关键！见第 7 节）
.\llama\llama-bench.exe -m .\llama\models\GLM-OCR-Q8_0.gguf -ngl 99 -p 0 -n 64
#   正常: backend = CUDA, tg64 ≈ 300+ t/s
#   异常: backend = CPU, ~18 t/s
```

## 7. ⚠️ 最大坑：GPU 静默回退 CPU

**症状**：服务能跑但每张 40-65 秒（正常 GPU 只要 3-4 秒）。

**根因**：llama.cpp 的 `ggml-cuda.dll` 加载失败（Win32 error 126，缺依赖）时
**不报错，静默用 CPU**。原因通常是缺 CUDA 运行时 DLL。

**修复**：确保 `ocr_service\llama\` 下存在 `cudart64_12.dll`、
`cublas64_12.dll`、`cublasLt64_12.dll`（缺哪个补哪个，从 `cudart-llama-bin-win-cuda-12.4-x64.zip` 解出）。

**排查顺序**：看 `llama-bench` 输出 backend 是 CUDA 还是 CPU → 查 3 个 DLL 是否齐全
→ 用 `dumpbin /dependents ggml-cuda.dll` 查缺哪个依赖。

## 8. 其他踩坑清单（实测）

1. **提示词必须是 `OCR`**：写 `OCR markdown` 会丢店名只出表格；长提示词会退化复读。
2. **`max_tokens` ≤ 800**：模型训练输出预算有限，给大反而退化。
3. **消息顺序**：文本在前、图片在后（两代引擎都要求）。
4. **图片预处理**：保持 1600px 长边，别降到 1024（实测明显掉精度）；prefill 只占 ~0.2s，大图几乎不增耗时。
5. **`--flash-attn`**：新版必须写 `on|off|auto`，`-fa` 会被当标志位吃掉下一参数报错。
6. **llama-server 图片缓存**：相同图片重复请求命中缓存跳过视觉编码（基准测试注意区分）。
7. **选型原因**：DeepSeek-OCR 在密集两列超市小票上漏价格列/复读循环，GLM-OCR 6/6 张一次通过。Q4_K_M 量化只快 ~15% 且掉精度风险，坚持用 Q8_0。

## 9. 性能参考（RTX 4070 SUPER 实测）

| 指标 | 数值 |
|------|------|
| GPU 单张耗时 | ~3-4 s（大头是解码 800 token 输出，~3s） |
| prefill | ~2500 tok/s（~0.2s） |
| decode | ~260 tok/s |
| CPU 回退时 | 40-65 s/张 |
| 并发 | 3 路约 2.2x 吞吐（`ingest-dir --workers 3`） |
