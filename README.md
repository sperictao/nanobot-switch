# nanobot-switch

一个独立于 `nanobot` 仓库的切换工具，提供：

- 一键切换 Provider / Model / BaseURL / API Key
- Profile 保存与复用
- 自动健康检查（后台守护）
- 失败阈值触发自动切换到 fallback profile
- 类似 cc-switch 的可视化交互菜单

## 安装

```bash
cd /Users/erictao/temp/nanobot-switch
uv tool install --editable .
```

## 快速开始

```bash
# 打开可视化菜单
nanobot-switch menu

# 命令式一键切换
nanobot-switch apply \
  --provider vllm \
  --model claude-sonnet-4-5-20250929 \
  --api-base https://example.com/v1 \
  --api-key sk-xxx \
  --save-as main

# 健康检查一次
nanobot-switch health once

# 启动自动健康检查（连续失败 3 次后切到 fallback）
nanobot-switch health start \
  --interval 60 \
  --fail-threshold 3 \
  --fallback-profile main
```

## 文件

工具使用以下文件：

- `~/.nanobot/config.json`
- `~/.nanobot/switch_profiles.json`
- `~/.nanobot/healthcheck_state.json`
- `~/.nanobot/healthcheck_runtime.json`
- `~/.nanobot/healthcheck.log`

