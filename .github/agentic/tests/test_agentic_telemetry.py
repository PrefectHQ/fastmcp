from prefect.blocks.system import Secret

from flows import agentic_telemetry


def test_no_token_outside_a_flow_run_means_no_telemetry(monkeypatch):
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    monkeypatch.delenv("PREFECT__FLOW_RUN_ID", raising=False)
    assert agentic_telemetry.token() is None
    assert agentic_telemetry.configure() is False


def test_token_comes_from_env_first(monkeypatch):
    monkeypatch.setenv("LOGFIRE_TOKEN", "from-env")
    assert agentic_telemetry.token() == "from-env"


def test_a_missing_secret_never_stops_a_run(monkeypatch):
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    monkeypatch.setenv("PREFECT__FLOW_RUN_ID", "run")

    def missing(*_, **__):
        raise ValueError("Unable to find block document")

    monkeypatch.setattr(Secret, "load", missing)
    assert agentic_telemetry.token() is None
