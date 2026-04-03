import modal
import yaml
import base64
import subprocess
import json
import os
from datetime import datetime
from fastapi import FastAPI, Response

app = modal.App("singbox-ss2022-manager")

# 构建镜像：下载并解压现代代理核心 Sing-box
image = (
    modal.Image.debian_slim()
    .apt_install("wget", "tar")
    .run_commands(
        "wget -O sing-box.tar.gz https://github.com/SagerNet/sing-box/releases/download/v1.9.0/sing-box-1.9.0-linux-amd64.tar.gz",
        "tar -xzf sing-box.tar.gz",
        "mv sing-box-*/sing-box /usr/local/bin/",
        "chmod +x /usr/local/bin/sing-box",
        "rm -rf sing-box*"
    )
    .pip_install("fastapi[standard]", "pyyaml")
)

# 用于在无状态函数间共享节点信息的字典
proxy_dict = modal.Dict.from_name("singbox-proxy-info", create_if_missing=True)

web_app = FastAPI()

@web_app.get("/clash")
async def clash_subscription():
    try:
        info = proxy_dict["proxy_info"]
    except KeyError:
        return {"error": "No active proxy"}
    
    clash_config = {
        "proxies": [{
            "name": info["name"],
            "type": "ss",
            "server": info["server"],
            "port": info["port"],
            "cipher": info["cipher"],
            "password": info["password"],
            "udp": True
        }]
    }
    return Response(
        content=yaml.dump(clash_config, allow_unicode=True, sort_keys=False),
        media_type="application/x-yaml",
    )

@web_app.get("/ss")
async def ss_url():
    try:
        info = proxy_dict["proxy_info"]
    except KeyError:
        return {"error": "No active proxy"}
    # SS 链接标准拼接
    auth = f"{info['cipher']}:{info['password']}"
    auth_b64 = base64.b64encode(auth.encode()).decode()
    return {"ss_url": f"ss://{auth_b64}@{info['server']}:{info['port']}#{info['name']}"}

@web_app.get("/")
async def status():
    try:
        info = proxy_dict["proxy_info"]
        return {"status": "Sing-box SS-2022 Running", "server": f"{info['server']}:{info['port']}"}
    except KeyError:
        return {"status": "No proxy running"}

# 提供订阅链接的 API 端点
@app.function(image=image)
@modal.asgi_app(label="singbox-api")
def api():
    return web_app

# 核心代理服务
@app.function(image=image, timeout=3600 * 24, region="asia-northeast1")
def run_singbox_server():
    # ⚠️ 必须填入通过 openssl rand -base64 32 生成的密钥
    password = "NdM6oU4qIJuOLuMMCXRDyrj3rgNQG2wXwGe/epQKROo="
    method = "2022-blake3-aes-256-gcm" 
    port = 8388
    config_path = "/tmp/config.json"

    # 动态生成 Sing-box 配置文件
    config = {
        "log": {"level": "info"},
        "inbounds": [
            {
                "type": "shadowsocks",
                "tag": "ss-in",
                "listen": "0.0.0.0",
                "listen_port": port,
                "method": method,
                "password": password
            }
        ],
        "outbounds": [
            {"type": "direct", "tag": "direct"}
        ]
    }

    with open(config_path, "w") as f:
        json.dump(config, f)

    # 启动 Sing-box 进程
    process = subprocess.Popen(["sing-box", "run", "-c", config_path])

    # 建立 TCP 隧道暴露端口
    with modal.forward(port, unencrypted=True) as tunnel:
        hostname, tunnel_port = tunnel.tcp_socket
        
        # 写入共享字典，供 API 读取
        proxy_dict["proxy_info"] = {
            "name": "Modal SS-2022",
            "type": "ss",
            "server": hostname,
            "port": tunnel_port,
            "cipher": method,
            "password": password,
            "updated_at": datetime.now().isoformat()
        }
        
        print(f"✅ Sing-box SS-2022 running at {hostname}:{tunnel_port}")
        process.wait() 

@app.local_entrypoint()
def main():
    run_singbox_server.remote()
