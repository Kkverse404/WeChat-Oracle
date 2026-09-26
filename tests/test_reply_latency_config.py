from wechat_oracle.config import Settings


def test_reply_first_defaults_bound_interactive_work(monkeypatch):
    for name in (
        "WO_AGENT_MAX_STEPS",
        "WO_AGENT_REFLECTION_ENABLED",
        "WO_AGENT_RECENT_CONTEXT_CHAT",
        "WO_AGENT_MEMORY_MAX_CHARS",
        "WO_AGENT_MAX_TOOL_CALLS_PER_RUN",
    ):
        monkeypatch.delenv(name, raising=False)

    current = Settings(_env_file=None)

    assert current.agent_max_steps == 4
    assert current.agent_reflection_enabled is False
    assert current.agent_recent_context_chat == 40
    assert current.agent_memory_max_chars == 12_000
    assert current.agent_max_tool_calls_per_run == 8
