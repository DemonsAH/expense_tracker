# OCR Testing

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Recommended: store the API key in a local `.env` file in the project root:

```env
SILICONFLOW_API_KEY=your_api_key_here
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_OCR_MODEL=deepseek-ai/DeepSeek-OCR
SILICONFLOW_OCR_PROMPT=<image>\nFree OCR.
```

3. You can also set it only for the current PowerShell session:

```powershell
$env:SILICONFLOW_API_KEY = "your_api_key_here"
$env:SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
$env:SILICONFLOW_OCR_MODEL = "Qwen/Qwen3.6-27B"
$env:SILICONFLOW_OCR_PROMPT = "<image>`nThis is a shopping receipt, not a table. Please output the receipt text as OCR."
```

4. Put receipt images in `test_receipts/`.

Supported formats: `.jpg`, `.jpeg`, `.png`, `.bmp`, `.gif`

## Run

```bash
python test_ocr.py
```

## Notes

- The OCR engine now auto-loads a local `.env` file if one exists.
- `.env` is ignored by git so your key stays local.
- The OCR engine calls SiliconFlow's OpenAI-compatible `/chat/completions` API.
- The parser layer is unchanged, so after you confirm the OCR text quality we can keep tuning the receipt extraction rules.
