from http import HTTPStatus
from inspect import getsource


def test_mcp_runs_stateless_http():
    from artel.mcp.server import mcp

    assert mcp.settings.stateless_http is True


def test_sdk_returns_404_for_unknown_session():
    from mcp.server import streamable_http_manager as m

    src = getsource(m.StreamableHTTPSessionManager._handle_stateful_request)
    assert "HTTPStatus.NOT_FOUND" in src or str(HTTPStatus.NOT_FOUND.value) in src
