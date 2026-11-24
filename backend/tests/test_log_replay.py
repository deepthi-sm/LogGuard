"""Tests for `tools.log_replay`, using fakeredis."""
from __future__ import annotations

from pathlib import Path

import fakeredis.aioredis
import pytest

from ingestion.consumer import DEFAULT_STREAM, FIELD_LINE, FIELD_SOURCE
from tools.log_replay import replay, source_from_path


@pytest.fixture
async def fake_redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


def test_source_from_path():
    assert source_from_path(Path("data/HDFS_2k.log")) == "hdfs-2k"
    assert source_from_path(Path("openstack_abnormal.log")) == "openstack-abnormal"


async def test_replay_emits_correct_xadd_schema(fake_redis, tmp_path: Path):
    """Each XADD carries exactly the fields the consumer reads."""
    log_file = tmp_path / "input.log"
    log_file.write_text("\n".join(f"INFO line {i}" for i in range(15)) + "\n", encoding="utf-8")

    stats = await replay(
        fake_redis, input_path=log_file, stream=DEFAULT_STREAM,
        rate=1000, loop=False, max_lines=None,
    )

    assert stats.lines_emitted == 15
    entries = await fake_redis.xrange(DEFAULT_STREAM)
    assert len(entries) == 15
    for _entry_id, fields in entries:
        assert FIELD_LINE in fields
        assert fields[FIELD_SOURCE] == "input"


async def test_replay_source_override(fake_redis, tmp_path: Path):
    log_file = tmp_path / "input.log"
    log_file.write_text("INFO a\nINFO b\n", encoding="utf-8")
    await replay(
        fake_redis, input_path=log_file, stream=DEFAULT_STREAM,
        rate=1000, loop=False, max_lines=None, source="web-01",
    )
    entries = await fake_redis.xrange(DEFAULT_STREAM)
    assert {fields[FIELD_SOURCE] for _id, fields in entries} == {"web-01"}


async def test_replay_max_lines_caps_emission(fake_redis, tmp_path: Path):
    log_file = tmp_path / "input.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(100)) + "\n", encoding="utf-8")
    stats = await replay(
        fake_redis, input_path=log_file, stream=DEFAULT_STREAM,
        rate=1000, loop=False, max_lines=12,
    )
    assert stats.lines_emitted == 12


async def test_replay_skips_blank_lines(fake_redis, tmp_path: Path):
    log_file = tmp_path / "input.log"
    log_file.write_text(
        "INFO real line 1\n\n   \nINFO real line 2\n\nINFO real line 3\n", encoding="utf-8",
    )
    stats = await replay(
        fake_redis, input_path=log_file, stream=DEFAULT_STREAM,
        rate=1000, loop=False, max_lines=None,
    )
    assert stats.lines_emitted == 3


async def test_replay_loop_restarts_at_eof(fake_redis, tmp_path: Path):
    log_file = tmp_path / "tiny.log"
    log_file.write_text("INFO a\nINFO b\n", encoding="utf-8")
    stats = await replay(
        fake_redis, input_path=log_file, stream=DEFAULT_STREAM,
        rate=1000, loop=True, max_lines=10,
    )
    assert stats.lines_emitted == 10


async def test_replay_invalid_rate_raises(fake_redis, tmp_path: Path):
    log_file = tmp_path / "x.log"
    log_file.write_text("line 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        await replay(
            fake_redis, input_path=log_file, stream=DEFAULT_STREAM,
            rate=0, loop=False, max_lines=1,
        )


async def test_replay_missing_input_file_raises(fake_redis, tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        await replay(
            fake_redis, input_path=tmp_path / "does_not_exist.log", stream=DEFAULT_STREAM,
            rate=10, loop=False, max_lines=None,
        )
