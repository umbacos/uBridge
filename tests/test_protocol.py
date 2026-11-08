import pytest
from tjc_hmi.protocol import TERMINATOR, FrameParser, display_text, encode


@pytest.mark.parametrize("number", [0, 255, 65535, 0xFFFFFF, 0xFFFFFFFF, 1414152961])
def test_numeric_payload_may_contain_terminator(number):
    raw = b"\x71" + number.to_bytes(4, "little") + TERMINATOR
    for split in range(len(raw) + 1):
        parser = FrameParser()
        frames = parser.feed(raw[:split]) + parser.feed(raw[split:])
        assert len(frames) == 1
        assert frames[0].number == number


def test_noise_fragmented_touch_and_many_frames():
    raw = b"\x67\x01\xff\x00\xff\x01" + TERMINATOR
    parser = FrameParser()
    frames = []
    for byte in b"noise\xff\x88\xff\xff\xff" + raw + b"\x01" + TERMINATOR:
        frames.extend(parser.feed(bytes([byte])))
    assert [f.kind for f in frames] == [0x88, 0x67, 0x01]
    assert frames[1].touch == (511, 255, True)


def test_parser_recovers_and_bounds_string_garbage():
    parser = FrameParser()
    parser.feed(b"\x70" + b"a" * 2000)
    assert len(parser.buffer) <= 1024
    assert parser.feed(b"\x71\x01\x00\x00\x00" + TERMINATOR)[0].number == 1


def test_text_cannot_escape_a_command():
    value = display_text('Caffè 20°C "\\\r\n page 99\xff')
    assert '"' not in value and "\\" not in value and "\n" not in value
    assert "Caffe" in value and "deg" in value
    assert encode(f'xstr 0,0,480,30,0,65535,0,0,0,1,"{value}"').endswith(TERMINATOR)
    for value in ["page 0\nrest", "ÿ", "a" * 241, ""]:
        with pytest.raises((ValueError, UnicodeError)):
            encode(value)
