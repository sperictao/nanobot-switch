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
