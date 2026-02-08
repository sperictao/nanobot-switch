# Release Guide

## Version

Current version: `0.1.0`

## Build artifacts

```bash
cd /Users/erictao/temp/nanobot-switch
.venv/bin/python -m build
```

Expected outputs:

- `dist/nanobot_switch-0.1.0-py3-none-any.whl`
- `dist/nanobot_switch-0.1.0.tar.gz`

## Install from local wheel

```bash
pip install dist/nanobot_switch-0.1.0-py3-none-any.whl
```

## Core commands

```bash
nanobot-switch menu
nanobot-switch status
nanobot-switch apply --provider vllm --model claude-sonnet-4-5-20250929 --api-base https://example.com/v1 --api-key sk-xxx
nanobot-switch health once
nanobot-switch health start --interval 60 --fail-threshold 3 --fallback-profile main
```

## GitHub 自动发布流程

1. 更新版本号（两处）
   - `pyproject.toml` 里的 `project.version`
   - `src/nanobot_switch/__init__.py` 里的 `__version__`
2. （可选）新增发布说明，例如 `RELEASE_NOTES_vX.Y.Z.md`
3. 提交代码并打 tag

```bash
git add .
git commit -m "chore(release): vX.Y.Z"
git tag -a vX.Y.Z -m "nanobot-switch vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

推送 `v*` 标签后，`release.yml` 会自动：

- 执行测试
- 构建 wheel/sdist
- 创建/更新 GitHub Release 并上传构建产物

## PyPI 自动发布流程

`pypi-publish.yml` 在 GitHub Release 发布后自动触发，优先使用：

1. `PYPI_API_TOKEN` Secret（如果存在）
2. Trusted Publishing（如果未设置 token）

建议先在仓库 `Settings -> Secrets and variables -> Actions` 中配置：

- `PYPI_API_TOKEN`（可选，兼容模式）

同时在 PyPI 侧配置 Trusted Publishing（推荐）后，即可无密钥发布。
