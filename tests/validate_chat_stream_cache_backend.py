"""Validate durable chat buffering against the running selected cache backend."""

from __future__ import annotations

import sys
import time
import zlib
from uuid import uuid4

from onyx.cache.factory import get_cache_backend
from onyx.chat import stream_buffer


def main() -> None:
    expected_backend = sys.argv[1]
    if expected_backend == "postgres":
        # This validator runs as a fresh process inside the API container, so
        # it must perform the sync-engine initialization normally owned by the
        # API lifespan before exercising the PostgreSQL cache implementation.
        from onyx.db.engine.sql_engine import SqlEngine

        SqlEngine.init_engine(pool_size=1, max_overflow=0)
    cache = get_cache_backend()
    actual_backend = type(cache).__name__
    expected_type = {
        "postgres": "PostgresCacheBackend",
        "redis": "RedisCacheBackend",
    }[expected_backend]
    assert actual_backend == expected_type, (actual_backend, expected_type)

    session_id = uuid4()
    run_id = 91001
    original_live_ttl = stream_buffer.CHAT_STREAM_BUFFER_TTL_S
    original_done_ttl = stream_buffer.CHAT_STREAM_BUFFER_DONE_TTL_S
    original_cap = stream_buffer.CHAT_STREAM_BUFFER_MAX_BYTES
    capacity_session = uuid4()
    capacity_run = run_id + 1
    keys = [stream_buffer._meta_key(session_id, run_id)] + [
        stream_buffer._chunk_key(session_id, run_id, index) for index in range(4)
    ] + [stream_buffer._meta_key(capacity_session, capacity_run)] + [
        stream_buffer._chunk_key(capacity_session, capacity_run, index)
        for index in range(4)
    ]
    try:
        # Short values prove native Redis/PostgreSQL expiration behavior without
        # waiting for the production four-hour/one-hour policy windows.
        stream_buffer.CHAT_STREAM_BUFFER_TTL_S = 6
        stream_buffer.CHAT_STREAM_BUFFER_DONE_TTL_S = 5
        writer = stream_buffer.StreamBufferWriter(cache, session_id, run_id)
        writer.append_line("first backend packet\n")
        writer.flush()
        first_key = stream_buffer._chunk_key(session_id, run_id, 0)
        first_initial_ttl = cache.ttl(first_key)
        assert 4 <= first_initial_ttl <= 6, first_initial_ttl

        time.sleep(2)
        writer.append_line("second backend packet\n")
        writer.flush()
        second_key = stream_buffer._chunk_key(session_id, run_id, 1)
        first_after_write = cache.ttl(first_key)
        second_after_write = cache.ttl(second_key)
        assert 1 <= first_after_write <= 4, first_after_write
        assert 4 <= second_after_write <= 6, second_after_write
        assert second_after_write - first_after_write >= 1

        read = stream_buffer.read_stream_chunks(cache, session_id, run_id, 0)
        assert read is not None
        assert read.blocks == ["first backend packet\n", "second backend packet\n"]
        assert not read.gap

        writer.mark_done()
        for key in (
            first_key,
            second_key,
            stream_buffer._meta_key(session_id, run_id),
        ):
            ttl = cache.ttl(key)
            assert 3 <= ttl <= 5, (key, ttl)

        payload = "backend capacity fixture\n"
        stream_buffer.CHAT_STREAM_BUFFER_MAX_BYTES = len(
            zlib.compress(payload.encode("utf-8"))
        )
        bounded = stream_buffer.StreamBufferWriter(
            cache, capacity_session, capacity_run
        )
        bounded.append_line(payload)
        bounded.flush()
        accepted = stream_buffer.read_stream_chunks(
            cache, capacity_session, capacity_run, 0
        )
        assert accepted is not None and not accepted.gap
        bounded.append_line("one byte beyond the accepted compressed capacity")
        bounded.flush()
        rejected = stream_buffer.read_stream_chunks(
            cache, capacity_session, capacity_run, 0
        )
        assert rejected is not None and rejected.gap
    finally:
        stream_buffer.CHAT_STREAM_BUFFER_TTL_S = original_live_ttl
        stream_buffer.CHAT_STREAM_BUFFER_DONE_TTL_S = original_done_ttl
        stream_buffer.CHAT_STREAM_BUFFER_MAX_BYTES = original_cap
        for key in keys:
            cache.delete(key)

    print(f"CHAT_STREAM_CACHE_BACKEND_OK backend={expected_backend}")


if __name__ == "__main__":
    main()
