# PatosX, a multipurpose Discord bot (moderation, economy, AI, fun)
# Copyright (C) 2025 theofficialtruck
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import main


@pytest.mark.asyncio
async def test_generate_gemini_response_retries_transient_error(monkeypatch):
    """A transient outage (e.g. a Gemini 503) should be retried within the same
    request instead of immediately surfacing the 'banana peel' failure message -
    this is what made DuckGPT flaky: the very next query would succeed because
    the blip had already passed, but the failed one never got a second try."""
    monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(main, "GEMINI_API_KEYS", ["key1", "key2", "key3"])
    monkeypatch.setattr(
        main, "get_gemini_client", AsyncMock(return_value={"mode": "new", "client": None, "model": "m"})
    )

    calls = {"n": 0}

    def fake_generate_once(client_info, prompt):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("503 Service Unavailable")
        return SimpleNamespace(text="quack quack")

    monkeypatch.setattr(main, "gemini_generate_once", fake_generate_once)
    result = await main.generate_gemini_response([{"role": "user", "content": "hi"}])
    assert result == "quack quack"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_generate_gemini_response_stops_on_non_recoverable_error(monkeypatch):
    """A genuinely non-retriable error (bad request/invalid argument) should fail
    fast rather than burning through retries that can never succeed."""
    monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(main, "GEMINI_API_KEYS", ["key1"])
    monkeypatch.setattr(
        main, "get_gemini_client", AsyncMock(return_value={"mode": "new", "client": None, "model": "m"})
    )

    calls = {"n": 0}

    def fake_generate_once(client_info, prompt):
        calls["n"] += 1
        raise RuntimeError("400 Bad Request: invalid argument")

    monkeypatch.setattr(main, "gemini_generate_once", fake_generate_once)
    result = await main.generate_gemini_response([{"role": "user", "content": "hi"}])
    assert "banana peel" in result
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_generate_gemini_response_rotates_key_on_quota_error(monkeypatch):
    """A 429/quota error should rotate to the next API key and retry, since the
    request itself is fine - only that specific key is exhausted."""
    monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(main, "GEMINI_API_KEYS", ["key1", "key2"])
    monkeypatch.setattr(
        main, "get_gemini_client", AsyncMock(return_value={"mode": "new", "client": None, "model": "m"})
    )

    rotate_calls = {"n": 0}

    def fake_next_key():
        rotate_calls["n"] += 1
        return "key2"

    monkeypatch.setattr(main, "next_gemini_key", fake_next_key)
    monkeypatch.setattr(
        main, "build_gemini_client_for_key", lambda key, model: {"mode": "new", "client": None, "model": model}
    )

    calls = {"n": 0}

    def fake_generate_once(client_info, prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("429 Resource exhausted: quota")
        return SimpleNamespace(text="quack")

    monkeypatch.setattr(main, "gemini_generate_once", fake_generate_once)
    result = await main.generate_gemini_response([{"role": "user", "content": "hi"}])
    assert result == "quack"
    assert rotate_calls["n"] == 1
