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

## CI/CD 自动化

仓库已内置 3 个工作流：

- `.github/workflows/ci.yml`
  - `push` / `pull_request` 到 `main` 时自动执行测试与打包校验
- `.github/workflows/release.yml`
  - `push` 标签 `v*`（如 `v0.1.1`）时自动构建并发布 GitHub Release，上传 wheel/sdist
- `.github/workflows/pypi-publish.yml`
  - 当 GitHub Release `published` 时自动发布到 PyPI

## PyPI 发布配置

支持两种方式，推荐第 1 种（更安全）：

1. Trusted Publishing（推荐）
   - 在 PyPI 项目里配置 GitHub OIDC Publisher：
     - Owner: `sperictao`
     - Repository: `nanobot-switch`
     - Workflow: `pypi-publish.yml`
     - Environment: `pypi`
2. API Token（兼容方式）
   - 在 GitHub 仓库设置 Secret：`PYPI_API_TOKEN`
   - 可用命令：

```bash
gh secret set PYPI_API_TOKEN --repo sperictao/nanobot-switch
```
