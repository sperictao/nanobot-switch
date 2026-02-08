# nanobot-switch v0.1.0

首个可用版本，已从原 nanobot 项目中抽离为独立工具。

## 亮点

- 一键切换 Provider / Model / BaseURL / API Key（`apply`）
- Profile 管理（`save` / `save-current` / `use` / `list` / `remove`）
- cc-switch 风格可视化菜单（`menu`）
- 自动健康检查（`health once/start/stop/status`）
- 失败阈值 + fallback profile 自动切换

## 安装

```bash
uv tool install --editable /Users/erictao/temp/nanobot-switch
```

## 快速上手

```bash
nanobot-switch menu
nanobot-switch status
nanobot-switch health once
```

## 产物

- `dist/nanobot_switch-0.1.0-py3-none-any.whl`
- `dist/nanobot_switch-0.1.0.tar.gz`

## 兼容说明

- 本工具读写 `~/.nanobot/config.json`，可与现有 nanobot 配置协同使用。
