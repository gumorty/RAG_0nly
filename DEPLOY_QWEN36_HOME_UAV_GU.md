# 在 /home/uav/gu 下本地部署 Qwen3.6 大模型

这份文档用于在没有 `sudo` 权限的服务器上部署：

```text
Qwen/Qwen3.6-35B-A3B-FP8
```

或在必要时切换到：

```text
Qwen/Qwen3.6-35B-A3B
```

所有环境、模型、缓存、日志、启动脚本、API key 都只放在：

```bash
/home/uav/gu/qwen36
```

如果需要回滚，先停止 vLLM 进程，然后删除 `/home/uav/gu/qwen36` 即可。不要修改 `/`、`/usr`、`/opt`，不要碰 `/dev/nvme0n1`，不要动 GPU 驱动、CUDA 驱动或磁盘分区。

## 0. 操作原则

只使用当前普通用户 `uav`。

不要执行这些命令：

```bash
sudo apt install ...
sudo apt remove ...
sudo apt purge ...
mkfs ...
fdisk ...
parted ...
dd ...
mount /dev/nvme0n1 ...
```

所有模型文件、Python 环境、Python 包、日志、缓存和脚本都必须留在：

```bash
/home/uav/gu/qwen36
```

这保证部署是可逆的：出问题时删除这个目录即可恢复到部署前状态。

## 1. 部署前环境检查

先执行下面的命令并保存输出。这些命令只读取信息，不会修改系统。

```bash
whoami
pwd
hostname
date
uname -a
cat /etc/os-release
id
```

```bash
df -h
lsblk -f
free -h
ulimit -n
```

```bash
nvidia-smi
nvidia-smi --query-gpu=index,name,memory.total,memory.free,driver_version --format=csv
nvcc --version || true
```

```bash
python3 --version
python3 -m pip --version || true
which python3
which pip3 || true
which curl || true
which wget || true
which git || true
which tmux || true
which screen || true
which nohup || true
```

检查 `8000` 端口是否已被占用：

```bash
ss -lntp | grep ':8000' || true
```

如果 `8000` 已经被占用，后续可以把端口改成 `8001` 或 `18000`。

## 2. 创建可回滚工作目录

```bash
mkdir -p /home/uav/gu/qwen36/{bin,envs,models,hf-cache,logs,run,tmp,apps}
cd /home/uav/gu/qwen36
```

创建环境变量文件：

```bash
cat > /home/uav/gu/qwen36/env.sh <<'EOF'
export QWEN_HOME=/home/uav/gu/qwen36
export HF_HOME=/home/uav/gu/qwen36/hf-cache
export HF_HUB_CACHE=/home/uav/gu/qwen36/hf-cache/hub
export TRANSFORMERS_CACHE=/home/uav/gu/qwen36/hf-cache/transformers
export XDG_CACHE_HOME=/home/uav/gu/qwen36/.cache
export TMPDIR=/home/uav/gu/qwen36/tmp
export VLLM_API_KEY="change-this-to-a-long-random-string"
export VLLM_HOST=127.0.0.1
export VLLM_PORT=8000
export VLLM_BASE_URL=http://127.0.0.1:8000/v1
export QWEN_MODEL=Qwen/Qwen3.6-35B-A3B-FP8
EOF
```

如果系统有 `openssl`，生成一个较强的本地 API key：

```bash
if command -v openssl >/dev/null 2>&1; then
  KEY="qwen-$(openssl rand -hex 24)"
else
  KEY="qwen-$(date +%s)-$(hostname)"
fi
sed -i "s|change-this-to-a-long-random-string|${KEY}|g" /home/uav/gu/qwen36/env.sh
```

加载环境变量，并确认 API key 和服务地址：

```bash
source /home/uav/gu/qwen36/env.sh
echo "$VLLM_API_KEY"
echo "$VLLM_BASE_URL"
```

默认监听地址是 `127.0.0.1`，也就是只允许服务器本机访问。这样更安全。后面如果需要局域网访问，再改成 `0.0.0.0`。

## 3. 安装用户级 Python 环境

推荐使用 Miniforge，因为它会完整安装在 `/home/uav/gu/qwen36` 下，不需要管理员权限，也不会污染系统 Python。

```bash
cd /home/uav/gu/qwen36
curl -L -o Miniforge3-Linux-x86_64.sh \
  https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
```

安装 Miniforge，不修改 shell 启动文件：

```bash
bash Miniforge3-Linux-x86_64.sh -b -p /home/uav/gu/qwen36/envs/miniforge3
source /home/uav/gu/qwen36/envs/miniforge3/etc/profile.d/conda.sh
conda create -y -n qwen36 python=3.11
conda activate qwen36
python --version
```

安装运行所需 Python 包：

```bash
python -m pip install -U pip setuptools wheel
python -m pip install -U uv
uv pip install vllm --torch-backend=auto
python -m pip install -U openai huggingface_hub qwen-agent
```

如果下面这条命令失败：

```bash
uv pip install vllm --torch-backend=auto
```

就改用普通 pip 安装：

```bash
python -m pip install -U vllm
```

安装完成后验证 PyTorch、CUDA 和 vLLM：

```bash
python - <<'PY'
import sys
print(sys.version)
import torch
print("torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available())
for i in range(torch.cuda.device_count()):
    print(i, torch.cuda.get_device_name(i), torch.cuda.get_device_properties(i).total_memory / 1024**3, "GiB")
import vllm
print("vllm", vllm.__version__)
PY
```

如果这里显示 `torch.cuda.is_available()` 是 `False`，先不要继续启动模型，要先排查 CUDA/PyTorch 环境。

## 4. 可选：提前下载模型

这一步不是必须的，但建议执行。提前下载模型可以把“下载失败”和“启动失败”分开排查。

```bash
source /home/uav/gu/qwen36/env.sh
source /home/uav/gu/qwen36/envs/miniforge3/etc/profile.d/conda.sh
conda activate qwen36
```

下载模型到 `/home/uav/gu/qwen36/models`：

```bash
huggingface-cli download "$QWEN_MODEL" \
  --local-dir /home/uav/gu/qwen36/models/Qwen3.6-35B-A3B-FP8 \
  --local-dir-use-symlinks False \
  --resume-download
```

如果 Hugging Face 下载很慢或被阻断，不要随便换第三方模型。可以改用 ModelScope，但要先确认对应的是 Qwen 官方模型路径。

## 5. 保守启动 vLLM

先创建启动脚本：

```bash
cat > /home/uav/gu/qwen36/bin/start_vllm.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

source /home/uav/gu/qwen36/env.sh
source /home/uav/gu/qwen36/envs/miniforge3/etc/profile.d/conda.sh
conda activate qwen36

mkdir -p "$QWEN_HOME/logs" "$QWEN_HOME/run" "$QWEN_HOME/tmp"

MODEL_PATH="$QWEN_MODEL"
if [ -d "$QWEN_HOME/models/Qwen3.6-35B-A3B-FP8" ]; then
  MODEL_PATH="$QWEN_HOME/models/Qwen3.6-35B-A3B-FP8"
fi

exec vllm serve "$MODEL_PATH" \
  --served-model-name qwen3.6-35b-a3b-fp8 \
  --host "$VLLM_HOST" \
  --port "$VLLM_PORT" \
  --api-key "$VLLM_API_KEY" \
  --tensor-parallel-size 2 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.85 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --language-model-only
EOF

chmod +x /home/uav/gu/qwen36/bin/start_vllm.sh
```

说明：

- `--tensor-parallel-size 2` 表示使用两张 GPU。
- `--max-model-len 32768` 是保守上下文长度，先保证稳定，不要一开始直接上 262K。
- `--gpu-memory-utilization 0.85` 限制 vLLM 使用显存的比例，给系统留余量。
- `--language-model-only` 表示先只启用文本能力，不加载视觉部分，降低显存压力。
- `--enable-auto-tool-choice` 和 `--tool-call-parser qwen3_coder` 用于工具调用。

用 `nohup` 后台启动，不依赖 `tmux` 或 `systemd`：

```bash
source /home/uav/gu/qwen36/env.sh
nohup /home/uav/gu/qwen36/bin/start_vllm.sh \
  > /home/uav/gu/qwen36/logs/vllm.out \
  2> /home/uav/gu/qwen36/logs/vllm.err &
echo $! > /home/uav/gu/qwen36/run/vllm.pid
```

查看日志：

```bash
tail -f /home/uav/gu/qwen36/logs/vllm.out /home/uav/gu/qwen36/logs/vllm.err
```

查看 GPU 占用：

```bash
nvidia-smi
```

## 6. 验证本地 API

先检查模型列表：

```bash
source /home/uav/gu/qwen36/env.sh
curl http://127.0.0.1:${VLLM_PORT}/v1/models \
  -H "Authorization: Bearer ${VLLM_API_KEY}"
```

测试中文办公能力：

```bash
curl http://127.0.0.1:${VLLM_PORT}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${VLLM_API_KEY}" \
  -d '{
    "model": "qwen3.6-35b-a3b-fp8",
    "messages": [
      {"role": "user", "content": "请用中文简要说明你能做哪些办公和编程任务。"}
    ],
    "temperature": 0.7,
    "max_tokens": 1024
  }'
```

测试 OpenAI Python SDK 调用：

```bash
cat > /home/uav/gu/qwen36/bin/test_openai_client.py <<'EOF'
import os
from openai import OpenAI

client = OpenAI(
    base_url=f"http://127.0.0.1:{os.environ.get('VLLM_PORT', '8000')}/v1",
    api_key=os.environ["VLLM_API_KEY"],
)

resp = client.chat.completions.create(
    model="qwen3.6-35b-a3b-fp8",
    messages=[
        {"role": "user", "content": "写一个 Python 函数，读取 Excel 文件并按指定列分组统计。"}
    ],
    temperature=0.6,
    max_tokens=2048,
)

print(resp.choices[0].message.content)
EOF

source /home/uav/gu/qwen36/env.sh
source /home/uav/gu/qwen36/envs/miniforge3/etc/profile.d/conda.sh
conda activate qwen36
python /home/uav/gu/qwen36/bin/test_openai_client.py
```

## 7. 配置其他程序调用本地大模型

如果是在服务器本机上的其他程序调用，使用：

```bash
source /home/uav/gu/qwen36/env.sh
export OPENAI_API_KEY="$VLLM_API_KEY"
export OPENAI_BASE_URL="http://127.0.0.1:${VLLM_PORT}/v1"
export OPENAI_MODEL="qwen3.6-35b-a3b-fp8"
```

如果要让同一可信局域网里的其他机器访问，先把监听地址从 `127.0.0.1` 改成 `0.0.0.0`：

```bash
sed -i 's|export VLLM_HOST=127.0.0.1|export VLLM_HOST=0.0.0.0|' /home/uav/gu/qwen36/env.sh
```

然后重启 vLLM，并在客户端机器上配置：

```bash
export OPENAI_API_KEY="从 /home/uav/gu/qwen36/env.sh 里复制出来的 VLLM_API_KEY"
export OPENAI_BASE_URL="http://SERVER_IP:8000/v1"
export OPENAI_MODEL="qwen3.6-35b-a3b-fp8"
```

不要把这个端口直接暴露到公网。如果需要远程访问，优先使用 SSH 隧道：

```bash
ssh -L 8000:127.0.0.1:8000 uav@SERVER_IP
```

然后在本地电脑使用：

```bash
export OPENAI_BASE_URL="http://127.0.0.1:8000/v1"
```

## 8. 停止服务

优先用 PID 文件停止：

```bash
if [ -f /home/uav/gu/qwen36/run/vllm.pid ]; then
  kill "$(cat /home/uav/gu/qwen36/run/vllm.pid)" || true
  rm -f /home/uav/gu/qwen36/run/vllm.pid
fi
```

如果 PID 文件失效，先查找自己的 vLLM 进程：

```bash
ps -fu uav | grep vllm | grep -v grep
```

然后只停止自己的 vLLM 进程：

```bash
kill PID
```

不要杀其他用户的进程。

## 9. 完整回滚

先停止服务：

```bash
if [ -f /home/uav/gu/qwen36/run/vllm.pid ]; then
  kill "$(cat /home/uav/gu/qwen36/run/vllm.pid)" || true
fi
```

确认没有自己的 vLLM 进程残留：

```bash
ps -fu uav | grep vllm | grep -v grep || true
```

然后只删除部署目录：

```bash
rm -rf /home/uav/gu/qwen36
```

这会删除本次部署创建的 Python 环境、模型权重、缓存、日志、API key 文件、启动脚本和临时文件。

## 10. 如果 Qwen3.6-FP8 显存不够

先降低上下文长度：

```bash
--max-model-len 16384
```

如果仍然 OOM，再切换到更稳的文本模型：

```bash
export QWEN_MODEL=Qwen/Qwen3-30B-A3B-Instruct-2507-FP8
```

同时修改 `/home/uav/gu/qwen36/env.sh`，然后重启 vLLM。

这个备选模型是文本模型，非 thinking 模式，对办公助手和普通工具调用通常更容易稳定。

## 11. 服务稳定后的下一步

只有在 vLLM 稳定运行之后，再安装和学习 Qwen-Agent：

```bash
python -m pip install -U "qwen-agent[gui,rag,mcp]"
```

初期不要安装或启用 `code_interpreter`，因为它可能依赖 Docker，而这台服务器当前没有 Docker，也没有 `sudo` 权限。

建议后续按这个顺序扩展：

```text
1. vLLM 本地 API 稳定运行
2. OpenAI SDK 能正常调用
3. 接 Open WebUI 或自写 FastAPI 前端
4. 接 Qwen-Agent 的工具调用
5. 接 RAG 知识库
6. 接 MCP 工具
7. 再考虑 LangChain / LangGraph 和更复杂的办公智能体
```
