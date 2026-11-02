from app import config, llm


def test_parametros_por_modelo(monkeypatch):
    monkeypatch.setattr(config, "MODEL", "claude-haiku-4-5")
    kw = llm.request_kwargs()
    assert "output_config" not in kw and "fallbacks" not in kw and "betas" not in kw

    monkeypatch.setattr(config, "MODEL", "claude-haiku-5-5")
    kw = llm.request_kwargs(effort="low")
    assert kw["output_config"] == {"effort": "low"} and "fallbacks" not in kw

    monkeypatch.setattr(config, "MODEL", "claude-opus-5-5")
    kw = llm.request_kwargs()
    assert kw["output_config"] == {"effort": config.EFFORT}
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
