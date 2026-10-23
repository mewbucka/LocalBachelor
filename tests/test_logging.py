import json
import logging

from app.core.logging import JsonFormatter, setup_logging


def _record(level, msg, *args, **extra):
    record = logging.LogRecord("app.test", level, __file__, 1, msg, args, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_basic_fields():
    line = JsonFormatter().format(_record(logging.INFO, "hello %s", "world"))
    entry = json.loads(line)
    assert entry["msg"] == "hello world"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.test"
    assert entry["ts"].endswith("+00:00")


def test_json_formatter_extras_become_top_level_fields():
    line = JsonFormatter().format(_record(logging.ERROR, "boom", correlation_id="abc123"))
    entry = json.loads(line)
    assert entry["correlation_id"] == "abc123"


def test_json_formatter_drops_third_party_noise_extras():
    # uvicorn attaches a color_message (ANSI template) to some records;
    # it must not leak into the JSON output.
    record = _record(logging.INFO, "Uvicorn running", color_message="\x1b[1m%s\x1b[0m")
    entry = json.loads(JsonFormatter().format(record))
    assert "color_message" not in entry


def test_json_formatter_includes_exception():
    try:
        raise ValueError("kaput")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "app.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
    entry = json.loads(JsonFormatter().format(record))
    assert "ValueError: kaput" in entry["exc"]


def test_setup_logging_default_level_is_info(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    setup_logging()
    assert logging.getLogger().level == logging.INFO
    root = logging.getLogger()
    assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)


def test_setup_logging_reads_env_var(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    setup_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_setup_logging_invalid_env_falls_back_to_info(monkeypatch, capsys):
    monkeypatch.setenv("LOG_LEVEL", "BOGUS")
    setup_logging()
    assert logging.getLogger().level == logging.INFO
    assert "invalid LOG_LEVEL" in capsys.readouterr().err


def test_setup_logging_explicit_level_overrides_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    setup_logging(level="ERROR")
    assert logging.getLogger().level == logging.ERROR


def test_setup_logging_is_idempotent():
    setup_logging()
    setup_logging()
    assert len(logging.getLogger().handlers) == 1
