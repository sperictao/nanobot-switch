"""nanobot-switch CLI."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import typer
from litellm import acompletion
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from nanobot_switch import __version__

app = typer.Typer(help="Standalone switcher for nanobot provider/model/baseurl with health checks")
health_app = typer.Typer(help="Health check controls")
app.add_typer(health_app, name="health")

console = Console()

NANOBOT_DIR = Path.home() / ".nanobot"
CONFIG_PATH = NANOBOT_DIR / "config.json"
PROFILES_PATH = NANOBOT_DIR / "switch_profiles.json"
HEALTH_STATE_PATH = NANOBOT_DIR / "healthcheck_state.json"
HEALTH_RUNTIME_PATH = NANOBOT_DIR / "healthcheck_runtime.json"
HEALTH_LOG_PATH = NANOBOT_DIR / "healthcheck.log"

ALLOWED_PROVIDERS = (
    "anthropic",
    "openai",
    "openrouter",
    "deepseek",
    "groq",
    "zhipu",
    "dashscope",
    "vllm",
    "gemini",
    "moonshot",
    "aihubmix",
)

PROVIDER_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("aihubmix", ("aihubmix",)),
    ("openrouter", ("openrouter",)),
    ("deepseek", ("deepseek",)),
    ("anthropic", ("anthropic", "claude")),
    ("openai", ("openai", "gpt", "o1", "o3", "o4")),
    ("gemini", ("gemini",)),
    ("zhipu", ("zhipu", "glm", "zai")),
    ("dashscope", ("dashscope", "qwen")),
    ("groq", ("groq",)),
    ("moonshot", ("moonshot", "kimi")),
    ("vllm", ("vllm",)),
)

GATEWAY_DEFAULTS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "aihubmix": "https://aihubmix.com/v1",
}

FALLBACK_ORDER = [
    "openrouter",
    "aihubmix",
    "anthropic",
    "openai",
    "deepseek",
    "gemini",
    "zhipu",
    "dashscope",
    "moonshot",
    "vllm",
    "groq",
]


def version_callback(value: bool):
    if value:
        console.print(f"nanobot-switch v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
    ),
):
    """nanobot-switch."""


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_dict(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        value = {}
        container[key] = value
    return value


def _normalize_provider(provider: str) -> str:
    name = provider.strip().lower()
    if name not in ALLOWED_PROVIDERS:
        allowed = ", ".join(ALLOWED_PROVIDERS)
        raise typer.BadParameter(f"Unsupported provider '{provider}'. Allowed: {allowed}")
    return name


def _provider_configured(provider_name: str, provider_cfg: dict[str, Any]) -> bool:
    api_key = str(provider_cfg.get("apiKey") or "")
    api_base = str(provider_cfg.get("apiBase") or "")
    if provider_name == "vllm":
        return bool(api_key or api_base)
    return bool(api_key)


def _resolve_api_base(provider_name: str, api_base: str | None) -> str | None:
    if api_base:
        return api_base
    return GATEWAY_DEFAULTS.get(provider_name)


def _mask_secret(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        console.print(f"[red]Config not found:[/red] {CONFIG_PATH}")
        console.print("Run `nanobot onboard` first.")
        raise typer.Exit(1)

    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        console.print(f"[red]Failed to read config:[/red] {exc}")
        raise typer.Exit(1)


def _save_config(config: dict[str, Any]) -> None:
    _write_json(CONFIG_PATH, config)


def _load_profiles_data() -> dict[str, Any]:
    data = _read_json(PROFILES_PATH, {"profiles": {}, "lastUsed": None})
    if not isinstance(data.get("profiles"), dict):
        data["profiles"] = {}
    if "lastUsed" not in data:
        data["lastUsed"] = None
    return data


def _save_profiles_data(data: dict[str, Any]) -> None:
    _write_json(PROFILES_PATH, data)


def parse_header_pairs(header_values: list[str]) -> dict[str, str] | None:
    if not header_values:
        return None
    headers: dict[str, str] = {}
    for item in header_values:
        if "=" not in item:
            raise ValueError(f"Invalid header '{item}', expected KEY=VALUE")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid header '{item}', key cannot be empty")
        headers[key] = value.strip()
    return headers or None


def build_profile(
    provider: str,
    model: str,
    api_key: str,
    api_base: str | None,
    extra_headers: dict[str, str] | None,
    created_at: int | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    return {
        "provider": provider,
        "model": model,
        "apiKey": api_key,
        "apiBase": api_base,
        "extraHeaders": extra_headers,
        "createdAt": created_at or now,
        "updatedAt": now,
    }


def _guess_provider_from_model(model: str, providers: dict[str, Any]) -> str | None:
    model_lower = model.lower()
    for provider_name, keywords in PROVIDER_KEYWORDS:
        cfg = providers.get(provider_name, {})
        if not isinstance(cfg, dict):
            continue
        if not _provider_configured(provider_name, cfg):
            continue
        if any(keyword in model_lower for keyword in keywords):
            return provider_name
    return None


def _detect_active_provider(config: dict[str, Any]) -> str | None:
    agents = config.get("agents", {}) if isinstance(config.get("agents"), dict) else {}
    defaults = agents.get("defaults", {}) if isinstance(agents.get("defaults"), dict) else {}
    providers = config.get("providers", {}) if isinstance(config.get("providers"), dict) else {}

    explicit = str(defaults.get("provider") or "").strip().lower()
    if explicit in ALLOWED_PROVIDERS:
        cfg = providers.get(explicit, {})
        if isinstance(cfg, dict) and _provider_configured(explicit, cfg):
            return explicit

    model = str(defaults.get("model") or "")
    guessed = _guess_provider_from_model(model, providers)
    if guessed:
        return guessed

    for provider_name in FALLBACK_ORDER:
        cfg = providers.get(provider_name, {})
        if isinstance(cfg, dict) and _provider_configured(provider_name, cfg):
            return provider_name

    return None


def _profile_from_config(config: dict[str, Any]) -> dict[str, Any]:
    provider_name = _detect_active_provider(config)
    if not provider_name:
        raise typer.BadParameter("No available provider in ~/.nanobot/config.json")

    agents = config.get("agents", {}) if isinstance(config.get("agents"), dict) else {}
    defaults = agents.get("defaults", {}) if isinstance(agents.get("defaults"), dict) else {}
    model = str(defaults.get("model") or "").strip()
    if not model:
        raise typer.BadParameter("Model is empty in ~/.nanobot/config.json")

    providers = config.get("providers", {}) if isinstance(config.get("providers"), dict) else {}
    provider_cfg = providers.get(provider_name, {}) if isinstance(providers.get(provider_name), dict) else {}

    return build_profile(
        provider=provider_name,
        model=model,
        api_key=str(provider_cfg.get("apiKey") or ""),
        api_base=provider_cfg.get("apiBase"),
        extra_headers=provider_cfg.get("extraHeaders") if isinstance(provider_cfg.get("extraHeaders"), dict) else None,
    )


def apply_profile_to_config(config: dict[str, Any], profile: dict[str, Any]) -> None:
    provider_name = _normalize_provider(str(profile.get("provider") or ""))

    model = str(profile.get("model") or "").strip()
    if not model:
        raise ValueError("Profile model cannot be empty")

    agents = _ensure_dict(config, "agents")
    defaults = _ensure_dict(agents, "defaults")
    providers = _ensure_dict(config, "providers")
    provider_cfg = _ensure_dict(providers, provider_name)

    provider_cfg["apiKey"] = str(profile.get("apiKey") or "")
    provider_cfg["apiBase"] = profile.get("apiBase")
    provider_cfg["extraHeaders"] = profile.get("extraHeaders")

    defaults["model"] = model
    defaults["provider"] = provider_name


def _build_litellm_model(provider_name: str, model: str) -> str:
    normalized = model

    if provider_name == "openrouter" and not normalized.startswith("openrouter/"):
        return f"openrouter/{normalized}"
    if provider_name == "aihubmix":
        return f"openai/{normalized.split('/')[-1]}"
    if provider_name == "vllm" and not normalized.startswith("hosted_vllm/"):
        return f"hosted_vllm/{normalized}"

    prefix_map = {
        "anthropic": "anthropic",
        "openai": "openai",
        "deepseek": "deepseek",
        "groq": "groq",
        "zhipu": "zai",
        "dashscope": "dashscope",
        "gemini": "gemini",
        "moonshot": "moonshot",
    }
    prefix = prefix_map.get(provider_name)
    if prefix and not normalized.startswith(f"{prefix}/"):
        return f"{prefix}/{normalized}"

    return normalized


def _is_health_response_ok(content: str, finish_reason: str, expected_text: str) -> bool:
    if finish_reason == "error":
        return False
    text = (content or "").strip()
    if not text:
        return False
    if text.lower().startswith("error"):
        return False
    if expected_text and expected_text.lower() not in text.lower():
        return False
    return True


async def _run_health_check_for_profile_async(
    profile: dict[str, Any],
    timeout_s: int,
    prompt: str,
    expected_text: str,
) -> tuple[bool, str, float, str, str]:
    provider_name = _normalize_provider(str(profile.get("provider") or ""))
    model = str(profile.get("model") or "")
    api_key = str(profile.get("apiKey") or "")
    api_base = _resolve_api_base(provider_name, profile.get("apiBase"))
    extra_headers = profile.get("extraHeaders") if isinstance(profile.get("extraHeaders"), dict) else None

    litellm_model = _build_litellm_model(provider_name, model)

    kwargs: dict[str, Any] = {
        "model": litellm_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 64,
        "temperature": 0.0,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base
    if extra_headers:
        kwargs["extra_headers"] = extra_headers

    start = time.perf_counter()
    try:
        response = await asyncio.wait_for(acompletion(**kwargs), timeout=timeout_s)
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return False, f"Exception: {exc}", elapsed, provider_name, model

    choice = response.choices[0]
    message = choice.message
    content = (getattr(message, "content", None) or "").strip()
    finish_reason = choice.finish_reason or "stop"

    elapsed = (time.perf_counter() - start) * 1000
    ok = _is_health_response_ok(content, finish_reason, expected_text)
    detail = content or f"finish_reason={finish_reason}"

    return ok, detail, elapsed, provider_name, model


def run_health_check(
    timeout_s: int,
    prompt: str,
    expected_text: str,
) -> tuple[bool, str, float, str, str]:
    config = _load_config()
    profile = _profile_from_config(config)
    return asyncio.run(_run_health_check_for_profile_async(profile, timeout_s, prompt, expected_text))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _current_summary() -> dict[str, str]:
    config = _load_config()
    profile = _profile_from_config(config)
    provider_name = _normalize_provider(str(profile.get("provider") or ""))
    api_base = _resolve_api_base(provider_name, profile.get("apiBase")) or "(default)"

    return {
        "provider": provider_name,
        "model": str(profile.get("model") or ""),
        "apiBase": api_base,
        "apiKey": _mask_secret(str(profile.get("apiKey") or "")),
    }


def _print_switch_success(profile: dict[str, Any]) -> None:
    provider_name = _normalize_provider(str(profile.get("provider") or ""))
    api_base = _resolve_api_base(provider_name, profile.get("apiBase")) or "(default)"
    console.print(f"[green]✓[/green] Switched to provider: [cyan]{provider_name}[/cyan]")
    console.print(f"Model: [cyan]{profile['model']}[/cyan]")
    console.print(f"API Base: [cyan]{api_base}[/cyan]")


def _run_optional_health_check(should_check: bool) -> None:
    if not should_check:
        return

    ok, detail, elapsed_ms, provider_name, model = run_health_check(
        timeout_s=20,
        prompt="Reply with exactly: HEALTHY",
        expected_text="HEALTHY",
    )
    latency = f"{elapsed_ms:.0f}ms"
    if ok:
        console.print(f"[green]✓[/green] Health check passed ({latency}) provider={provider_name} model={model}")
        return

    console.print(f"[red]✗[/red] Health check failed ({latency}) provider={provider_name} model={model}")
    console.print(f"Reason: {detail}")
    raise typer.Exit(1)


def _print_profiles_table() -> None:
    data = _load_profiles_data()
    profiles = data.get("profiles", {})
    if not profiles:
        console.print("No saved profiles.")
        return

    table = Table(title="Switch Profiles")
    table.add_column("Name", style="cyan")
    table.add_column("Provider", style="green")
    table.add_column("Model", style="yellow")
    table.add_column("API Base", style="magenta")
    table.add_column("API Key", style="dim")

    for name in sorted(profiles.keys()):
        profile = profiles[name]
        provider_name = str(profile.get("provider") or "")
        resolved_api_base = _resolve_api_base(provider_name, profile.get("apiBase")) or "(default)"
        table.add_row(
            name,
            provider_name,
            str(profile.get("model") or ""),
            resolved_api_base,
            _mask_secret(str(profile.get("apiKey") or "")),
        )

    console.print(table)
    if data.get("lastUsed"):
        console.print(f"Last used: [cyan]{data['lastUsed']}[/cyan]")


@app.command("status")
def status():
    """Show active provider/model summary."""
    summary = _current_summary()
    state = _read_json(HEALTH_STATE_PATH, {})
    pid = int(state.get("pid", 0) or 0)
    watcher = f"running (PID {pid})" if pid and _pid_alive(pid) else "stopped"

    body = (
        f"Provider: [cyan]{summary['provider']}[/cyan]\n"
        f"Model: [cyan]{summary['model']}[/cyan]\n"
        f"API Base: [cyan]{summary['apiBase']}[/cyan]\n"
        f"API Key: [cyan]{summary['apiKey']}[/cyan]\n"
        f"Health Watcher: [cyan]{watcher}[/cyan]"
    )
    console.print(Panel(body, title="nanobot-switch Status", border_style="cyan"))


@app.command("apply")
def apply(
    provider: str = typer.Option(..., "--provider", "-p", help="Provider name"),
    model: str = typer.Option(..., "--model", "-m", help="Model name"),
    api_key: str = typer.Option("", "--api-key", help="API key"),
    api_base: str = typer.Option(None, "--api-base", help="Custom API base URL"),
    header: list[str] = typer.Option(None, "--header", "-H", help="Extra header KEY=VALUE"),
    save_as: str = typer.Option(None, "--save-as", help="Save as profile name"),
    check: bool = typer.Option(True, "--check/--no-check", help="Run health check"),
):
    """One-click switch provider/model/baseurl/key."""
    provider_name = _normalize_provider(provider)
    try:
        headers = parse_header_pairs(header or [])
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    profile = build_profile(
        provider=provider_name,
        model=model,
        api_key=api_key,
        api_base=api_base,
        extra_headers=headers,
    )

    config = _load_config()
    apply_profile_to_config(config, profile)
    _save_config(config)

    if save_as:
        data = _load_profiles_data()
        created_at = data["profiles"].get(save_as, {}).get("createdAt")
        profile["createdAt"] = created_at or profile["createdAt"]
        profile["updatedAt"] = int(time.time())
        data["profiles"][save_as] = profile
        data["lastUsed"] = save_as
        _save_profiles_data(data)
        console.print(f"[green]✓[/green] Profile saved: [cyan]{save_as}[/cyan]")

    _print_switch_success(profile)
    _run_optional_health_check(check)


@app.command("save")
def save(
    name: str = typer.Argument(..., help="Profile name"),
    provider: str = typer.Option(..., "--provider", "-p", help="Provider name"),
    model: str = typer.Option(..., "--model", "-m", help="Model name"),
    api_key: str = typer.Option("", "--api-key", help="API key"),
    api_base: str = typer.Option(None, "--api-base", help="Custom API base URL"),
    header: list[str] = typer.Option(None, "--header", "-H", help="Extra header KEY=VALUE"),
):
    """Save a profile without applying."""
    provider_name = _normalize_provider(provider)
    try:
        headers = parse_header_pairs(header or [])
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    data = _load_profiles_data()
    created_at = data["profiles"].get(name, {}).get("createdAt")
    data["profiles"][name] = build_profile(
        provider=provider_name,
        model=model,
        api_key=api_key,
        api_base=api_base,
        extra_headers=headers,
        created_at=created_at,
    )
    _save_profiles_data(data)
    console.print(f"[green]✓[/green] Profile saved: [cyan]{name}[/cyan]")


@app.command("save-current")
def save_current(
    name: str = typer.Argument(..., help="Profile name"),
):
    """Save current ~/.nanobot/config.json provider settings as a profile."""
    config = _load_config()
    profile = _profile_from_config(config)

    data = _load_profiles_data()
    created_at = data["profiles"].get(name, {}).get("createdAt")
    profile["createdAt"] = created_at or profile["createdAt"]
    profile["updatedAt"] = int(time.time())
    data["profiles"][name] = profile
    _save_profiles_data(data)
    console.print(f"[green]✓[/green] Current config saved as profile: [cyan]{name}[/cyan]")


@app.command("use")
def use(
    name: str = typer.Argument(..., help="Profile name"),
    check: bool = typer.Option(True, "--check/--no-check", help="Run health check"),
):
    """Switch to a saved profile."""
    data = _load_profiles_data()
    profile = data["profiles"].get(name)
    if not profile:
        console.print(f"[red]Profile not found:[/red] {name}")
        raise typer.Exit(1)

    config = _load_config()
    apply_profile_to_config(config, profile)
    _save_config(config)

    data["lastUsed"] = name
    _save_profiles_data(data)

    _print_switch_success(profile)
    _run_optional_health_check(check)


@app.command("list")
def list_profiles():
    """List saved profiles."""
    _print_profiles_table()


@app.command("remove")
def remove(
    name: str = typer.Argument(..., help="Profile name"),
):
    """Remove a profile."""
    data = _load_profiles_data()
    if name not in data.get("profiles", {}):
        console.print(f"[red]Profile not found:[/red] {name}")
        raise typer.Exit(1)

    del data["profiles"][name]
    if data.get("lastUsed") == name:
        data["lastUsed"] = None
    _save_profiles_data(data)
    console.print(f"[green]✓[/green] Profile removed: [cyan]{name}[/cyan]")


@health_app.command("once")
def health_once(
    timeout: int = typer.Option(20, "--timeout", help="Request timeout (seconds)"),
    prompt: str = typer.Option("Reply with exactly: HEALTHY", "--prompt", help="Health prompt"),
    expected_text: str = typer.Option("HEALTHY", "--expected", help="Expected response substring"),
):
    """Run one health check now."""
    ok, detail, elapsed_ms, provider_name, model = run_health_check(timeout, prompt, expected_text)
    latency = f"{elapsed_ms:.0f}ms"
    if ok:
        console.print(f"[green]✓[/green] Healthy ({latency}) provider={provider_name} model={model}")
        return

    console.print(f"[red]✗[/red] Unhealthy ({latency}) provider={provider_name} model={model}")
    console.print(f"Reason: {detail}")
    raise typer.Exit(1)


@health_app.command("start")
def health_start(
    interval: int = typer.Option(60, "--interval", help="Check interval seconds"),
    timeout: int = typer.Option(20, "--timeout", help="Request timeout seconds"),
    fail_threshold: int = typer.Option(3, "--fail-threshold", help="Consecutive failures before failover"),
    fallback_profile: str = typer.Option(None, "--fallback-profile", help="Profile to switch to after repeated failures"),
    prompt: str = typer.Option("Reply with exactly: HEALTHY", "--prompt", help="Health prompt"),
    expected_text: str = typer.Option("HEALTHY", "--expected", help="Expected response substring"),
):
    """Start background health watcher."""
    if interval < 5:
        raise typer.BadParameter("--interval must be >= 5")
    if timeout < 3:
        raise typer.BadParameter("--timeout must be >= 3")
    if fail_threshold < 1:
        raise typer.BadParameter("--fail-threshold must be >= 1")

    if fallback_profile:
        data = _load_profiles_data()
        if fallback_profile not in data.get("profiles", {}):
            console.print(f"[red]Fallback profile not found:[/red] {fallback_profile}")
            raise typer.Exit(1)

    state = _read_json(HEALTH_STATE_PATH, {})
    pid = int(state.get("pid", 0) or 0)
    if pid and _pid_alive(pid):
        console.print(f"[yellow]Health watcher already running (PID {pid})[/yellow]")
        return

    cmd = [
        sys.executable,
        "-m",
        "nanobot_switch",
        "health",
        "_worker",
        "--interval",
        str(interval),
        "--timeout",
        str(timeout),
        "--fail-threshold",
        str(fail_threshold),
        "--prompt",
        prompt,
        "--expected",
        expected_text,
    ]
    if fallback_profile:
        cmd.extend(["--fallback-profile", fallback_profile])

    HEALTH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HEALTH_LOG_PATH, "a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    time.sleep(0.5)
    if not _pid_alive(proc.pid):
        console.print("[red]Failed to start health watcher, check log file.[/red]")
        console.print(f"Log: [cyan]{HEALTH_LOG_PATH}[/cyan]")
        raise typer.Exit(1)

    _write_json(
        HEALTH_STATE_PATH,
        {
            "pid": proc.pid,
            "startedAt": int(time.time()),
            "interval": interval,
            "timeout": timeout,
            "failThreshold": fail_threshold,
            "fallbackProfile": fallback_profile,
            "prompt": prompt,
            "expectedText": expected_text,
            "logPath": str(HEALTH_LOG_PATH),
        },
    )

    console.print(f"[green]✓[/green] Health watcher started (PID {proc.pid})")
    console.print(f"Log: [cyan]{HEALTH_LOG_PATH}[/cyan]")


@health_app.command("stop")
def health_stop():
    """Stop background health watcher."""
    state = _read_json(HEALTH_STATE_PATH, {})
    pid = int(state.get("pid", 0) or 0)

    if not pid:
        console.print("[yellow]Health watcher is not running.[/yellow]")
        return

    if not _pid_alive(pid):
        console.print("[yellow]Health watcher was already stopped.[/yellow]")
        HEALTH_STATE_PATH.unlink(missing_ok=True)
        return

    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        os.kill(pid, signal.SIGTERM)

    for _ in range(20):
        if not _pid_alive(pid):
            break
        time.sleep(0.1)

    if _pid_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            os.kill(pid, signal.SIGKILL)

    HEALTH_STATE_PATH.unlink(missing_ok=True)
    console.print(f"[green]✓[/green] Health watcher stopped (PID {pid})")


@health_app.command("status")
def health_status():
    """Show health watcher status and latest check result."""
    state = _read_json(HEALTH_STATE_PATH, {})
    runtime = _read_json(HEALTH_RUNTIME_PATH, {})

    pid = int(state.get("pid", 0) or 0)
    running = bool(pid and _pid_alive(pid))

    console.print("nanobot-switch Health Watcher\n")
    console.print(f"Running: {'[green]yes[/green]' if running else '[red]no[/red]'}")
    if pid:
        console.print(f"PID: {pid}")
    if state.get("interval"):
        console.print(f"Interval: {state['interval']}s")
    if state.get("failThreshold"):
        console.print(f"Fail threshold: {state['failThreshold']}")
    if state.get("fallbackProfile"):
        console.print(f"Fallback profile: {state['fallbackProfile']}")

    if runtime:
        console.print("")
        console.print("Last check:")
        if runtime.get("checkedAt"):
            checked_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(runtime["checkedAt"]))
            console.print(f"  Time: {checked_at}")
        if runtime.get("provider"):
            console.print(f"  Provider: {runtime['provider']}")
        if runtime.get("model"):
            console.print(f"  Model: {runtime['model']}")
        if "ok" in runtime:
            console.print(f"  Healthy: {'yes' if runtime['ok'] else 'no'}")
        if runtime.get("latencyMs") is not None:
            console.print(f"  Latency: {runtime['latencyMs']:.0f}ms")
        if runtime.get("consecutiveFailures") is not None:
            console.print(f"  Consecutive failures: {runtime['consecutiveFailures']}")
        if runtime.get("message"):
            console.print(f"  Message: {runtime['message']}")
        if runtime.get("switchedToProfile"):
            console.print(f"  Failover: switched to {runtime['switchedToProfile']}")

    if state.get("logPath"):
        console.print(f"\nLog: [cyan]{state['logPath']}[/cyan]")


def _sleep_interruptible(seconds: int, running_flag: dict[str, bool]) -> None:
    remaining = max(1, seconds)
    while remaining > 0 and running_flag["value"]:
        time.sleep(1)
        remaining -= 1


@health_app.command("_worker", hidden=True)
def health_worker(
    interval: int = typer.Option(..., "--interval"),
    timeout: int = typer.Option(..., "--timeout"),
    fail_threshold: int = typer.Option(..., "--fail-threshold"),
    fallback_profile: str = typer.Option(None, "--fallback-profile"),
    prompt: str = typer.Option(..., "--prompt"),
    expected_text: str = typer.Option(..., "--expected"),
):
    """Internal background health-check worker."""
    running_flag = {"value": True}

    def _stop_handler(_signum, _frame):
        running_flag["value"] = False

    signal.signal(signal.SIGTERM, _stop_handler)
    signal.signal(signal.SIGINT, _stop_handler)

    consecutive_failures = 0
    total_checks = 0

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] health worker started")

    while running_flag["value"]:
        config = _load_config()
        profile = _profile_from_config(config)

        ok, detail, latency_ms, provider_name, model = asyncio.run(
            _run_health_check_for_profile_async(profile, timeout, prompt, expected_text)
        )
        total_checks += 1
        switched_to = None

        if ok:
            consecutive_failures = 0
        else:
            consecutive_failures += 1

        if (not ok) and fallback_profile and consecutive_failures >= fail_threshold:
            profile_data = _load_profiles_data().get("profiles", {})
            fallback = profile_data.get(fallback_profile)
            if fallback:
                config = _load_config()
                apply_profile_to_config(config, fallback)
                _save_config(config)
                switched_to = fallback_profile
                consecutive_failures = 0

        runtime = {
            "checkedAt": int(time.time()),
            "ok": ok,
            "provider": provider_name,
            "model": model,
            "latencyMs": latency_ms,
            "consecutiveFailures": consecutive_failures,
            "message": detail[:500],
            "switchedToProfile": switched_to,
            "totalChecks": total_checks,
        }
        _write_json(HEALTH_RUNTIME_PATH, runtime)

        status = "OK" if ok else "FAIL"
        line = (
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {status} "
            f"provider={provider_name} model={model} latency={latency_ms:.0f}ms "
            f"fails={consecutive_failures}"
        )
        if switched_to:
            line += f" failover={switched_to}"
        line += f" detail={detail[:180]}"
        print(line)

        _sleep_interruptible(interval, running_flag)

    state = _read_json(HEALTH_STATE_PATH, {})
    if int(state.get("pid", 0) or 0) == os.getpid():
        HEALTH_STATE_PATH.unlink(missing_ok=True)

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] health worker stopped")


def _prompt_provider(default_provider: str | None = None) -> str:
    providers = list(ALLOWED_PROVIDERS)
    if default_provider not in providers:
        default_provider = providers[0]

    table = Table(title="Providers")
    table.add_column("#", style="cyan")
    table.add_column("Provider", style="green")
    for idx, provider_name in enumerate(providers, start=1):
        table.add_row(str(idx), provider_name)
    console.print(table)

    default_idx = str(providers.index(default_provider) + 1)
    choice = Prompt.ask(
        "Choose provider",
        choices=[str(i) for i in range(1, len(providers) + 1)],
        default=default_idx,
    )
    return providers[int(choice) - 1]


def _prompt_headers(default_headers: dict[str, str] | None) -> dict[str, str] | None:
    default_text = ""
    if default_headers:
        default_text = ",".join(f"{k}={v}" for k, v in default_headers.items())

    while True:
        raw = Prompt.ask("Extra headers KEY=VALUE (comma separated, optional)", default=default_text)
        values = [value.strip() for value in raw.split(",") if value.strip()]
        try:
            return parse_header_pairs(values)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")


def _prompt_profile(default_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = default_profile or {}

    provider_name = _prompt_provider(str(defaults.get("provider") or "vllm"))

    model_default = str(defaults.get("model") or "")
    while True:
        model = Prompt.ask("Model", default=model_default)
        if model.strip():
            break
        console.print("[red]Model cannot be empty.[/red]")

    api_base = Prompt.ask("API Base (optional)", default=str(defaults.get("apiBase") or ""))

    current_key = str(defaults.get("apiKey") or "")
    if current_key:
        entered = Prompt.ask(f"API Key (blank to keep {_mask_secret(current_key)})", password=True, default="")
        api_key = entered if entered else current_key
    else:
        api_key = Prompt.ask("API Key (optional for some local endpoints)", password=True, default="")

    headers = _prompt_headers(defaults.get("extraHeaders") if isinstance(defaults.get("extraHeaders"), dict) else None)

    return build_profile(
        provider=provider_name,
        model=model,
        api_key=api_key,
        api_base=api_base or None,
        extra_headers=headers,
        created_at=defaults.get("createdAt"),
    )


def _select_profile(optional: bool = False) -> str | None:
    data = _load_profiles_data()
    profiles = data.get("profiles", {})
    if not profiles:
        console.print("[yellow]No saved profiles.[/yellow]")
        return None

    names = sorted(profiles.keys())
    table = Table(title="Saved Profiles")
    table.add_column("#", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Provider", style="yellow")
    table.add_column("Model", style="magenta")
    for idx, name in enumerate(names, start=1):
        profile = profiles[name]
        table.add_row(str(idx), name, str(profile.get("provider") or ""), str(profile.get("model") or ""))
    console.print(table)

    choices = [str(i) for i in range(1, len(names) + 1)]
    if optional:
        choices = ["0", *choices]

    default_choice = "0" if optional else "1"
    choice = Prompt.ask("Choose profile", choices=choices, default=default_choice)
    if optional and choice == "0":
        return None

    return names[int(choice) - 1]


def _pause_menu() -> None:
    try:
        Prompt.ask("[dim]Press Enter to continue[/dim]", default="")
    except KeyboardInterrupt:
        pass


def _interactive_switch_profile() -> None:
    name = _select_profile(optional=False)
    if not name:
        return

    run_check = Confirm.ask("Run health check after switch?", default=True)
    use(name=name, check=run_check)


def _interactive_save_profile() -> None:
    data = _load_profiles_data()
    name = Prompt.ask("Profile name")
    existing = data.get("profiles", {}).get(name)

    profile = _prompt_profile(existing)

    data["profiles"][name] = profile
    _save_profiles_data(data)
    console.print(f"[green]✓[/green] Profile saved: [cyan]{name}[/cyan]")

    if Confirm.ask("Switch to this profile now?", default=True):
        config = _load_config()
        apply_profile_to_config(config, profile)
        _save_config(config)
        data["lastUsed"] = name
        _save_profiles_data(data)
        _print_switch_success(profile)
        if Confirm.ask("Run health check now?", default=True):
            health_once()


def _interactive_quick_apply() -> None:
    profile = _prompt_profile()
    config = _load_config()
    apply_profile_to_config(config, profile)
    _save_config(config)
    _print_switch_success(profile)

    if Confirm.ask("Save as profile?", default=True):
        name = Prompt.ask("Profile name")
        data = _load_profiles_data()
        created_at = data["profiles"].get(name, {}).get("createdAt")
        profile["createdAt"] = created_at or profile["createdAt"]
        profile["updatedAt"] = int(time.time())
        data["profiles"][name] = profile
        data["lastUsed"] = name
        _save_profiles_data(data)
        console.print(f"[green]✓[/green] Profile saved: [cyan]{name}[/cyan]")

    if Confirm.ask("Run health check now?", default=True):
        health_once()


def _interactive_start_health_watcher() -> None:
    interval = IntPrompt.ask("Interval seconds", default=60)
    timeout = IntPrompt.ask("Request timeout seconds", default=20)
    fail_threshold = IntPrompt.ask("Consecutive failures before failover", default=3)
    fallback_profile = _select_profile(optional=True)

    health_start(
        interval=interval,
        timeout=timeout,
        fail_threshold=fail_threshold,
        fallback_profile=fallback_profile,
        prompt="Reply with exactly: HEALTHY",
        expected_text="HEALTHY",
    )


@app.command("menu")
def menu():
    """Open visual interactive menu (cc-switch style)."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        console.print("[red]Interactive menu requires a TTY terminal.[/red]")
        raise typer.Exit(1)

    while True:
        try:
            summary = _current_summary()
            state = _read_json(HEALTH_STATE_PATH, {})
            pid = int(state.get("pid", 0) or 0)
            watcher = f"running (PID {pid})" if pid and _pid_alive(pid) else "stopped"

            console.clear()
            body = (
                f"Provider: [cyan]{summary['provider']}[/cyan]\n"
                f"Model: [cyan]{summary['model']}[/cyan]\n"
                f"API Base: [cyan]{summary['apiBase']}[/cyan]\n"
                f"API Key: [cyan]{summary['apiKey']}[/cyan]\n"
                f"Health Watcher: [cyan]{watcher}[/cyan]"
            )
            console.print(Panel(body, title="nanobot-switch Menu", border_style="cyan"))

            console.print("[cyan]1[/cyan]. Show current status")
            console.print("[cyan]2[/cyan]. List profiles")
            console.print("[cyan]3[/cyan]. Switch to profile (one click)")
            console.print("[cyan]4[/cyan]. Create or update profile")
            console.print("[cyan]5[/cyan]. Quick apply (without profile)")
            console.print("[cyan]6[/cyan]. Save current config as profile")
            console.print("[cyan]7[/cyan]. Run health check once")
            console.print("[cyan]8[/cyan]. Start auto health watcher")
            console.print("[cyan]9[/cyan]. Stop auto health watcher")
            console.print("[cyan]10[/cyan]. Health watcher status")
            console.print("[cyan]0[/cyan]. Exit")

            choice = Prompt.ask(
                "Choose an option",
                choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
                default="1",
            )
        except KeyboardInterrupt:
            console.print("\n[green]Bye.[/green]")
            return

        try:
            if choice == "0":
                console.print("[green]Bye.[/green]")
                return
            if choice == "1":
                console.clear()
                status()
            elif choice == "2":
                console.clear()
                list_profiles()
            elif choice == "3":
                console.clear()
                _interactive_switch_profile()
            elif choice == "4":
                console.clear()
                _interactive_save_profile()
            elif choice == "5":
                console.clear()
                _interactive_quick_apply()
            elif choice == "6":
                console.clear()
                name = Prompt.ask("Profile name")
                save_current(name=name)
            elif choice == "7":
                console.clear()
                health_once()
            elif choice == "8":
                console.clear()
                _interactive_start_health_watcher()
            elif choice == "9":
                console.clear()
                health_stop()
            elif choice == "10":
                console.clear()
                health_status()
        except (typer.BadParameter, typer.Exit) as exc:
            if isinstance(exc, typer.BadParameter):
                console.print(f"[red]{exc}[/red]")

        _pause_menu()


if __name__ == "__main__":
    app()
