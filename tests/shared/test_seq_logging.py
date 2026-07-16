"""Regression tests for the seqlog request-timeout hardening."""

import logging
import time

import pytest

from functions.shared import seq_logging as sl


def _make_record() -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=None,
        exc_info=None,
    )


@pytest.fixture
def seq_handler():
    seqlog_internal = pytest.importorskip("seqlog.structured_logging")
    sl._patch_seqlog_request_timeout()
    handler = seqlog_internal.SeqLogHandler(server_url="http://127.0.0.1:1/")
    try:
        yield handler
    finally:
        handler.consumer.stop()
        handler.session.close()


def test_patch_adds_timeout_to_seq_post(seq_handler, monkeypatch):
    """The patched publish_log_batch must pass a bounded timeout to session.post."""
    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

    def _fake_post(url, data=None, stream=None, timeout=None):
        calls.append(timeout)
        return _FakeResponse()

    monkeypatch.setattr(seq_handler.session, "post", _fake_post)

    seq_handler.publish_log_batch([_make_record()])

    assert calls, "session.post was never called"
    assert calls[0] == sl._SEQ_REQUEST_TIMEOUT


def test_dead_seq_server_does_not_hang_indefinitely():
    """An unreachable Seq host must return within the bounded timeout window."""
    seqlog_internal = pytest.importorskip("seqlog.structured_logging")
    sl._patch_seqlog_request_timeout()

    handler = seqlog_internal.SeqLogHandler(server_url="http://192.0.2.1:5341/")
    try:
        started = time.monotonic()
        handler.publish_log_batch([_make_record()])
        elapsed = time.monotonic() - started

        assert elapsed < 30, (
            f"publish_log_batch took {elapsed:.1f}s against an unreachable host - "
            "timeout patch was not applied"
        )
    finally:
        handler.consumer.stop()
        handler.session.close()


def test_patch_failure_degrades_gracefully(monkeypatch, caplog):
    """If seqlog internals change, patching must warn rather than crash startup."""
    import seqlog.structured_logging as seqlog_internal

    monkeypatch.delattr(seqlog_internal, "SeqLogHandler", raising=True)

    with caplog.at_level(logging.WARNING):
        sl._patch_seqlog_request_timeout()

    assert any("Could not patch seqlog request timeout" in r.message for r in caplog.records)
