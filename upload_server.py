"""手机照片上传服务：浏览器上传直达「未处理」目录。

纯 Python 标准库实现（零第三方依赖），手机与电脑同一局域网时，
手机浏览器访问打印出的地址（如 http://192.168.x.x:8765/）即可批量上传，
照片直接保存到目标目录（默认 receipt_input/未处理），随后由定时任务自动处理。

用法：
  python upload_server.py [--host 0.0.0.0] [--port 8765] [--dir "receipt_input/未处理"]
"""

from __future__ import annotations

import argparse
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_DIR = Path("receipt_input") / "未处理"
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_BODY_BYTES = 50 * 1024 * 1024  # 单次请求上限 50MB

UPLOAD_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>小票上传</title>
<style>
  body { font-family: -apple-system, "Segoe UI", sans-serif; margin: 0; background: #f5f1e8; }
  .card { max-width: 480px; margin: 8vh auto; background: #fffdf8; border-radius: 18px;
          padding: 24px; box-shadow: 0 10px 24px rgba(0,0,0,.08); }
  h1 { font-size: 20px; margin: 0 0 6px; }
  p { color: #6b746f; font-size: 13px; margin: 4px 0 16px; }
  input[type=file] { width: 100%; box-sizing: border-box; padding: 10px; border: 1px dashed #c96f3b;
                     border-radius: 10px; background: #fff; margin-bottom: 14px; }
  button { width: 100%; padding: 12px; border: 0; border-radius: 10px; background: #c96f3b;
           color: #fff; font-size: 16px; cursor: pointer; }
  #msg { margin-top: 12px; font-size: 13px; }
</style>
</head>
<body>
<div class="card">
  <h1>上传小票照片</h1>
  <p>支持 JPG / PNG / WEBP / BMP，可多选。上传后自动进入「未处理」，由定时任务识别。</p>
  <form action="/upload" method="post" enctype="multipart/form-data">
    <input type="file" name="photos" accept="image/*" multiple required>
    <button type="submit">上传</button>
  </form>
  <div id="msg"></div>
</div>
<script>
const form = document.querySelector('form');
const msg = document.getElementById('msg');
form.addEventListener('submit', () => { msg.textContent = '上传中…'; });
</script>
</body>
</html>
"""


def _is_private_ipv4(address: str) -> bool:
    """判断是否为私网地址（10/8、172.16/12、192.168/16）。"""
    try:
        octets = [int(part) for part in address.split(".")]
    except ValueError:
        return False
    if len(octets) != 4:
        return False
    a, b, *_ = octets
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def _local_ipv4_addresses() -> list[str]:
    """返回最可能被手机访问的真实局域网 IPv4 地址。

    优先用 UDP 探测系统访问公网所用的出口网卡（通常即 Wi-Fi/以太网）；
    失败时枚举所有私网地址，并过滤掉 VPN / 虚拟网卡段（Tailscale CGNAT、
    Docker/WSL 等），避免手机访问不到的内部地址误导用户。
    """
    # 1) UDP connect：系统按路由表选择真实网卡，返回其局域网 IP
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            primary = sock.getsockname()[0]
        if primary.count(".") == 3 and not primary.startswith("100.64.") and not primary.startswith("169.254."):
            return [primary]
    except OSError:
        pass

    # 2) 兜底：枚举所有地址并启发式过滤
    candidates: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            address = info[4][0]
            if address.count(".") == 3 and _is_private_ipv4(address):
                candidates.add(address)
    except OSError:
        pass

    def looks_virtual(address: str) -> bool:
        # 100.64/10 = Tailscale/运营商 CGNAT；169.254 = 无 DHCP 的自分配；
        # 虚拟网卡（VMware/Docker/WSL/Hyper-V）常落在 x.x.x.1
        if address.startswith("100.64.") or address.startswith("169.254."):
            return True
        if address.endswith(".1"):
            return True
        return False

    real = sorted(a for a in candidates if not looks_virtual(a))
    return real or sorted(candidates)


def _unique_target(directory: Path, filename: str) -> Path:
    """目标已存在时追加 _1/_2/... 避免覆盖。"""
    name = Path(filename).name
    target = directory / name
    if not target.exists():
        return target
    stem = Path(name).stem
    suffix = Path(name).suffix
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


class UploadHandler(BaseHTTPRequestHandler):
    upload_dir: Path = DEFAULT_DIR

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            body = UPLOAD_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/upload":
            self._respond(404, "Not Found")
            return
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY_BYTES:
            self._respond(413, "文件过大（上限 50MB）")
            return

        content_type = self.headers.get("Content-Type", "")
        match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
        if not match:
            self._respond(400, "缺少 multipart boundary")
            return
        boundary = (match.group(1) or match.group(2)).encode("utf-8")
        body = self.rfile.read(length)

        saved: list[str] = []
        skipped: list[str] = []
        # 按 boundary 切分（手工解析 multipart/form-data）
        for raw in body.split(b"--" + boundary):
            chunk = raw.lstrip(b"\r\n")
            if chunk in (b"--\r\n", b"--", b""):
                continue
            header_end = chunk.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            headers_block = chunk[:header_end].decode("utf-8", "replace")
            content = chunk[header_end + 4:]
            if content.endswith(b"\r\n"):
                content = content[:-2]
            filename_match = re.search(r'filename="([^"]*)"', headers_block)
            if not filename_match:
                continue  # 非文件字段
            filename = Path(filename_match.group(1)).name
            suffix = Path(filename).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                skipped.append(filename or "(未知文件)")
                continue
            if not filename:
                skipped.append("(空文件名)")
                continue
            target = _unique_target(self.upload_dir, filename)
            target.write_bytes(content)
            saved.append(target.name)

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        if saved:
            message = f"已保存 {len(saved)} 张：{'、'.join(saved[:5])}"
            if len(saved) > 5:
                message += f" 等 {len(saved)} 张"
        else:
            message = "未保存任何文件（仅支持图片格式）"
        if skipped:
            message += f"；已跳过不支持的文件：{'、'.join(skipped[:3])}"
        self._respond(200, message)

    def _respond(self, code: int, text: str) -> None:
        body = f"<meta charset='utf-8'><body style='font-family:sans-serif;padding:24px'>{text}</body>".encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="小票照片局域网上传服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    parser.add_argument("--dir", default=str(DEFAULT_DIR), help=f"保存目录（默认 {DEFAULT_DIR}）")
    args = parser.parse_args()

    upload_dir = Path(args.dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    UploadHandler.upload_dir = upload_dir

    print("=" * 56)
    print("小票照片上传服务已启动")
    print(f"保存目录 : {upload_dir.resolve()}")
    print("手机访问（与本机同一 Wi-Fi）:")
    for address in _local_ipv4_addresses():
        print(f"  http://{address}:{args.port}/")
    print("按 Ctrl+C 停止服务")
    print("=" * 56)

    server = ThreadingHTTPServer((args.host, args.port), UploadHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
