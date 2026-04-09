"""Tests for polyarb.observability.logging — structured JSON logging."""

from __future__ import annotations

import json
import logging

import pytest

from polyarb.observability.context import request_id_var, scan_id_var
from polyarb.observability.logging import (
    HumanFormatter,
    JsonFormatter,
    configure_logging,
)


def _make_record(msg: str = "test message", level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="polyarb.test",
        level=level,
        pathname="test.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_json_formatter_produces_valid_json():
    fmt = JsonFormatter()
    record = _make_record()
    output = fmt.format(record)
    data = json.loads(output)
    assert "timestamp" in data
    assert data["level"] == "INFO"
    assert data["logger"] == "polyarb.test"
    assert data["message"] == "test message"


def test_correlation_ids_included():
    token_s = scan_id_var.set("scan123")
    token_r = request_id_var.set("req456")
    try:
        fmt = JsonFormatter()
        record = _make_record()
        data = json.loads(fmt.format(record))
        assert data["scan_id"] == "scan123"
        assert data["request_id"] == "req456"
    finally:
        scan_id_var.reset(token_s)
        request_id_var.reset(token_r)


def test_empty_correlation_ids():
    token_s = scan_id_var.set("")
    token_r = request_id_var.set("")
    try:
        fmt = JsonFormatter()
        record = _make_record()
        data = json.loads(fmt.format(record))
        assert data["scan_id"] == ""
        assert data["request_id"] == ""
    finally:
        scan_id_var.reset(token_s)
        request_id_var.reset(token_r)


def test_exception_info_included():
    import sys

    fmt = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="polyarb.test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="error occurred",
            args=(),
            exc_info=exc_info,
        )
    output = fmt.format(record)
    data = json.loads(output)
    assert "exception" in data
    assert "ValueError" in data["exception"]


def test_human_formatter_readable():
    fmt = HumanFormatter()
    record = _make_record()
    output = fmt.format(record)
    assert "INFO" in output
    assert "test message" in output
    assert "polyarb.test" in output
    # Should NOT be JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(output)


def test_configure_logging_sets_json_handler():
    configure_logging(json_output=True, level="INFO")
    root = logging.getLogger()
    assert len(root.handlers) >= 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    # Cleanup
    root.handlers.clear()


def test_configure_logging_level():
    configure_logging(json_output=True, level="DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    # Cleanup
    root.handlers.clear()
    root.setLevel(logging.WARNING)
