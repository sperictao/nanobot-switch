import pytest

from nanobot_switch.cli import (
    _build_litellm_model,
    _detect_active_provider,
    apply_profile_to_config,
    build_profile,
    parse_header_pairs,
)


def test_parse_header_pairs_success() -> None:
    headers = parse_header_pairs(["X-Token=abc", "X-Mode = fast"])
    assert headers == {"X-Token": "abc", "X-Mode": "fast"}


def test_parse_header_pairs_invalid() -> None:
    with pytest.raises(ValueError):
        parse_header_pairs(["invalid"])


def test_apply_profile_to_config_sets_fields() -> None:
    config = {
        "agents": {"defaults": {"model": "old"}},
        "providers": {},
    }
    profile = build_profile(
        provider="vllm",
        model="claude-sonnet-4-5-20250929",
        api_key="dummy",
        api_base="https://example.com/v1",
        extra_headers={"X-App": "demo"},
    )

    apply_profile_to_config(config, profile)

    assert config["agents"]["defaults"]["provider"] == "vllm"
    assert config["agents"]["defaults"]["model"] == "claude-sonnet-4-5-20250929"
    assert config["providers"]["vllm"]["apiKey"] == "dummy"
    assert config["providers"]["vllm"]["apiBase"] == "https://example.com/v1"


def test_detect_active_provider_prefers_explicit() -> None:
    config = {
        "agents": {"defaults": {"provider": "vllm", "model": "gpt-5.3-codex"}},
        "providers": {
            "vllm": {"apiKey": "dummy", "apiBase": "https://example.com/v1"},
            "openrouter": {"apiKey": "or-key"},
        },
    }
    assert _detect_active_provider(config) == "vllm"


def test_build_litellm_model_for_vllm() -> None:
    assert _build_litellm_model("vllm", "claude-sonnet-4-5") == "hosted_vllm/claude-sonnet-4-5"
