from hotkeys import KeyEvent
from recording_channel import RecordingChannel


def test_channel_preserves_fifo_session_and_sequence_metadata():
    channel = RecordingChannel("session-1", capacity=4)
    first = KeyEvent(0x41, True)
    second = KeyEvent(0x42, False)

    assert channel.publish(first) is True
    assert channel.publish(second) is True

    records = channel.drain()

    assert [record.event for record in records] == [first, second]
    assert [record.sequence for record in records] == [0, 1]
    assert {record.session_id for record in records} == {"session-1"}


def test_channel_overflow_is_immediate_sticky_and_does_not_evict_payloads():
    channel = RecordingChannel("session-2", capacity=2)
    first = KeyEvent(0x41, True)
    second = KeyEvent(0x42, True)

    assert channel.publish(first) is True
    assert channel.publish(second) is True
    assert channel.publish(KeyEvent(0x43, True)) is False
    assert channel.status.overflowed is True
    assert channel.status.healthy is False
    assert [record.event for record in channel.drain()] == [first, second]

    assert channel.publish(KeyEvent(0x44, True)) is False
    assert channel.status.overflowed is True


def test_channel_drain_can_be_bounded_without_blocking_producer():
    channel = RecordingChannel("session-3", capacity=256)
    for vk in range(70):
        assert channel.publish(KeyEvent(vk, True)) is True

    first = channel.drain(limit=64)

    assert len(first) == 64
    assert channel.status.overflowed is False
    assert len(channel.drain()) == 6


def test_closed_session_rejects_late_publish_and_keeps_terminal_status():
    channel = RecordingChannel("session-4")

    channel.close()

    assert channel.publish(KeyEvent(0x41, True)) is False
    assert channel.status.active is False
    assert channel.status.closed is True
    assert channel.status.session_id == "session-4"
