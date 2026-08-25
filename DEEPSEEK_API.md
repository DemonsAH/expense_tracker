# DeepSeek 官方 API 调用说明（供 Agent 参考）

## 基本配置

- Base URL：`https://api.deepseek.com`（OpenAI 兼容，也可用 `https://api.deepseek.com/v1`）
- 认证：HTTP Header `Authorization: Bearer $DEEPSEEK_API_KEY`
- 密钥来源：https://platform.deepseek.com
- 建议用 `openai` Python SDK：

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",                       # 或从环境变量 DEEPSEEK_API_KEY 读取
    base_url="https://api.deepseek.com",
)
```

## 模型名

| 用途 | model 值 |
|------|----------|
| 多模态（图片+文本） | `deepseek-v4-flash-vision-exp`（实验模型） |
| 纯文本 | `deepseek-v4-flash` |

## 纯文本调用

```python
resp = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[
        {"role": "system", "content": "你是结构化抽取助手，只输出 JSON。"},
        {"role": "user", "content": "将以下文本转为 JSON：..."},
    ],
)
print(resp.choices[0].message.content)
```

## 图片输入（vision 模型，三种方式）

### 方式一：外部 URL

```python
resp = client.chat.completions.create(
    model="deepseek-v4-flash-vision-exp",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "识别这张小票并输出 JSON"},
        {"type": "image_url", "image_url": {"url": "https://example.com/receipt.jpg"}},
    ]}],
)
```

### 方式二：base64 内联（本地图片）

```python
import base64

with open("receipt.jpg", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

resp = client.chat.completions.create(
    model="deepseek-v4-flash-vision-exp",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "识别这张小票"},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]}],
)
```

### 方式三：Files API（先上传拿 file_id，可复用省带宽）

```python
# 1) 上传（免费）
upload = client.files.create(file=open("receipt.jpg", "rb"), purpose="vision")

# 2) 请求中用 file_id 引用
resp = client.chat.completions.create(
    model="deepseek-v4-flash-vision-exp",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "识别这张小票"},
        {"type": "file", "file_id": upload.id},
    ]}],
)
```

## 计费与限制

- 图片按 token 计费：一张图最多 **384 tokens**，价格与 V4-Flash 相同
- 支持格式：JPEG / PNG / GIF / WebP
- 上下文 1M，最大输出 384K
- 支持 Chat Completions、Messages、Responses 三种格式；支持 JSON Output、Tool Calls
- 若需关闭思考（reasoning），在请求参数中按官方文档设置（Exp 模型 API 默认按官方当前行为）

## 常用排查

- `400 expected 'text'`：传图格式不被当前 API 接受，改用 base64 或 Files API 方式
- 图片过大：优先用 Files API，或先压缩
