from __future__ import annotations
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from np3_data import (
    BASE,
    BASE_WITH_TONE_CURVE,
    OFFSET_BLACK_LEVEL,
    OFFSET_CLARITY,
    OFFSET_COLOR_BLENDER_BLUE,
    OFFSET_COLOR_BLENDER_CYAN,
    OFFSET_COLOR_BLENDER_GREEN,
    OFFSET_COLOR_BLENDER_MAGENTA,
    OFFSET_COLOR_BLENDER_ORANGE,
    OFFSET_COLOR_BLENDER_PURPLE,
    OFFSET_COLOR_BLENDER_RED,
    OFFSET_COLOR_BLENDER_YELLOW,
    OFFSET_COLOR_GRADING_BALANCE,
    OFFSET_COLOR_GRADING_BLENDING,
    OFFSET_COLOR_GRADING_HIGHLIGHTS,
    OFFSET_COLOR_GRADING_MIDTONE,
    OFFSET_COLOR_GRADING_SHADOWS,
    OFFSET_COMMENT_FLAG_A,
    OFFSET_COMMENT_FLAG_B,
    OFFSET_CONTRAST,
    OFFSET_HIGHLIGHTS,
    OFFSET_MID_RANGE_SHARPENING,
    OFFSET_NAME,
    OFFSET_SATURATION,
    OFFSET_SHADOWS,
    OFFSET_SHARPENING,
    OFFSET_TONE_CURVE_FLAG,
    OFFSET_TONE_CURVE_POINTS,
    OFFSET_TONE_CURVE_RAW,
    OFFSET_WHITE_LEVEL,
)

TEMPLATE_FILES = (
    Path(__file__).with_name('PICCON01.NP3'),
    Path(__file__).with_name('PICCON01.NCP'),
)


def find_default_template_file() -> Path | None:
    for template_file in TEMPLATE_FILES:
        if template_file.exists():
            return template_file
    return None


TEMPLATE_FILE = find_default_template_file()


def load_template_bytes() -> bytes | None:
    if TEMPLATE_FILE is None or not TEMPLATE_FILE.exists():
        return None
    data = TEMPLATE_FILE.read_bytes()
    if len(data) < len(BASE):
        return None
    if not data.startswith(b'NCP\x00'):
        return None
    return data


TEMPLATE_BYTES = load_template_bytes()
BASE_LENGTH = len(BASE)
TONE_CURVE_EXTENSION = BASE_WITH_TONE_CURVE[BASE_LENGTH:]
MAX_COMMENT_CODE_UNITS = 256
TONE_CURVE_SENTINEL_OFFSETS = (
    OFFSET_CONTRAST,
    OFFSET_HIGHLIGHTS,
    OFFSET_SHADOWS,
    OFFSET_WHITE_LEVEL,
    OFFSET_BLACK_LEVEL,
)
TONE_CURVE_MARKER = b'BI0'


def clamp(value: float, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(round(value))))


def round_degree(value: float) -> int:
    return int(((value % 360) + 360) % 360)


def load_template_bytes_from_path(template_path: Path) -> bytes:
    data = template_path.read_bytes()
    if len(data) < len(BASE):
        raise ValueError(f'Template file is too small: {template_path}')
    if not data.startswith(b'NCP\x00'):
        raise ValueError(f'Template file is not a Nikon Picture Control binary: {template_path}')
    return data


def serialize_np3(options: dict[str, Any], template_path: Path | None = None) -> bytes:
    name = str(options.get('name', 'Converted')).strip()[:19]
    if not name:
        name = 'Converted'

    if template_path is not None:
        template_bytes = load_template_bytes_from_path(template_path)
    else:
        template_bytes = TEMPLATE_BYTES if TEMPLATE_BYTES is not None else BASE

    buf = bytearray(template_bytes)
    protected_start = find_protected_chunk_start(buf)
    write_name(buf, name)
    if can_write_range(protected_start, OFFSET_SHARPENING, 1):
        write_sharpning(buf, float(options.get('sharpning', 2)))
    if can_write_range(protected_start, OFFSET_MID_RANGE_SHARPENING, 1):
        write_mid_range_sharpning(buf, float(options.get('midRangeSharpning', 1)))
    if can_write_range(protected_start, OFFSET_CLARITY, 1):
        write_clarity(buf, float(options.get('clarity', 0.5)))
    if options.get('contrast') is not None and can_write_range(protected_start, OFFSET_CONTRAST, 1):
        write_contrast(buf, int(options['contrast']))
    if options.get('highlights') is not None and can_write_range(protected_start, OFFSET_HIGHLIGHTS, 1):
        write_highlights(buf, int(options['highlights']))
    if options.get('shadows') is not None and can_write_range(protected_start, OFFSET_SHADOWS, 1):
        write_shadows(buf, int(options['shadows']))
    if options.get('whiteLevel') is not None and can_write_range(protected_start, OFFSET_WHITE_LEVEL, 1):
        write_white_level(buf, int(options['whiteLevel']))
    if options.get('blackLevel') is not None and can_write_range(protected_start, OFFSET_BLACK_LEVEL, 1):
        write_black_level(buf, int(options['blackLevel']))
    if options.get('saturation') is not None and can_write_range(protected_start, OFFSET_SATURATION, 1):
        write_saturation(buf, int(options['saturation']))
    if options.get('colorBlender') and can_write_range(protected_start, OFFSET_COLOR_BLENDER_RED, 24):
        write_color_blender(buf, options['colorBlender'])
    if options.get('colorGrading') and can_write_range(protected_start, OFFSET_COLOR_GRADING_HIGHLIGHTS, 19):
        write_color_grading(buf, options['colorGrading'])

    comment = options.get('comment', '')
    tone_curve = options.get('toneCurve')
    if protected_start is not None:
        return bytes(buf)

    buf = write_comment(buf, comment, 0x02 if tone_curve else 0x00)
    if tone_curve:
        buf = append_tone_curve_extension(buf)
        apply_tone_curve_header(buf, not comment)
        buf = write_tone_curve(buf, tone_curve)

    return bytes(buf)


def get_reference_template(template_path: Path | None = None) -> bytes:
    if template_path is not None and template_path.exists():
        data = template_path.read_bytes()
        if len(data) >= len(BASE) and data.startswith(b'NCP\x00'):
            return data
    return TEMPLATE_BYTES if TEMPLATE_BYTES is not None else BASE


def find_protected_chunk_start(data: bytes | bytearray) -> int | None:
    marker_offset = bytes(data).find(TONE_CURVE_MARKER)
    if marker_offset < 6:
        return None
    return marker_offset - 6


def can_write_range(protected_start: int | None, offset: int, length: int) -> bool:
    return protected_start is None or offset + length <= protected_start


REPAIR_FIELD_RANGES = [
    (OFFSET_NAME, 19),
    (OFFSET_SHARPENING, 1),
    (OFFSET_MID_RANGE_SHARPENING, 1),
    (OFFSET_CLARITY, 1),
    (OFFSET_CONTRAST, 1),
    (OFFSET_HIGHLIGHTS, 1),
    (OFFSET_SHADOWS, 1),
    (OFFSET_WHITE_LEVEL, 1),
    (OFFSET_BLACK_LEVEL, 1),
    (OFFSET_SATURATION, 1),
    (OFFSET_COLOR_BLENDER_RED, 3),
    (OFFSET_COLOR_BLENDER_ORANGE, 3),
    (OFFSET_COLOR_BLENDER_YELLOW, 3),
    (OFFSET_COLOR_BLENDER_GREEN, 3),
    (OFFSET_COLOR_BLENDER_CYAN, 3),
    (OFFSET_COLOR_BLENDER_BLUE, 3),
    (OFFSET_COLOR_BLENDER_PURPLE, 3),
    (OFFSET_COLOR_BLENDER_MAGENTA, 3),
    (OFFSET_COLOR_GRADING_HIGHLIGHTS, 4),
    (OFFSET_COLOR_GRADING_MIDTONE, 4),
    (OFFSET_COLOR_GRADING_SHADOWS, 4),
    (OFFSET_COLOR_GRADING_BLENDING, 1),
    (OFFSET_COLOR_GRADING_BALANCE, 1),
    (OFFSET_TONE_CURVE_FLAG, 1),
]

def repair_np3_bytes(src: bytes, template_bytes: bytes | None = None) -> bytes:
    reference = bytearray(template_bytes if template_bytes is not None else get_reference_template())
    source = bytearray(src)
    protected_start = find_protected_chunk_start(reference)
    for offset, length in REPAIR_FIELD_RANGES:
        if (
            can_write_range(protected_start, offset, length)
            and offset + length <= len(source)
            and offset + length <= len(reference)
        ):
            reference[offset:offset + length] = source[offset:offset + length]
    return bytes(reference)


def repair_np3_file(input_path: Path, output_path: Path, template_path: Path | None = None) -> None:
    source_bytes = input_path.read_bytes()
    template_bytes = get_reference_template(template_path)
    repaired = repair_np3_bytes(source_bytes, template_bytes)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(repaired)


def write_name(buf: bytearray, name: str) -> None:
    if len(name) > 19:
        raise ValueError('name must be less than 19 characters')
    for i in range(19):
        buf[OFFSET_NAME + i] = 0
    for i, ch in enumerate(name):
        buf[OFFSET_NAME + i] = ord(ch)


def write_sharpning(buf: bytearray, sharpning: float = 2.0) -> None:
    buf[OFFSET_SHARPENING] = 0x80 + clamp(sharpning, -3, 9) * 4


def write_mid_range_sharpning(buf: bytearray, mid_range_sharpning: float = 1.0) -> None:
    buf[OFFSET_MID_RANGE_SHARPENING] = 0x80 + clamp(mid_range_sharpning, -5, 5) * 4


def write_clarity(buf: bytearray, clarity: float = 0.5) -> None:
    buf[OFFSET_CLARITY] = 0x80 + clamp(clarity, -5, 5) * 4


def write_contrast(buf: bytearray, contrast: int = 0) -> None:
    buf[OFFSET_CONTRAST] = 0x80 + clamp(contrast, -100, 100)


def write_highlights(buf: bytearray, highlights: int = 0) -> None:
    buf[OFFSET_HIGHLIGHTS] = 0x80 + clamp(highlights, -100, 100)


def write_shadows(buf: bytearray, shadows: int = 0) -> None:
    buf[OFFSET_SHADOWS] = 0x80 + clamp(shadows, -100, 100)


def write_white_level(buf: bytearray, white_level: int = 0) -> None:
    buf[OFFSET_WHITE_LEVEL] = 0x80 + clamp(white_level, -100, 100)


def write_black_level(buf: bytearray, black_level: int = 0) -> None:
    buf[OFFSET_BLACK_LEVEL] = 0x80 + clamp(black_level, -100, 100)


def write_saturation(buf: bytearray, saturation: int = 0) -> None:
    buf[OFFSET_SATURATION] = 0x80 + clamp(saturation, -100, 100)


def write_color_blender(buf: bytearray, color_blender: dict[str, Any] = {}) -> None:
    write_color_blender_values(buf, OFFSET_COLOR_BLENDER_RED, color_blender.get('red', {}))
    write_color_blender_values(buf, OFFSET_COLOR_BLENDER_ORANGE, color_blender.get('orange', {}))
    write_color_blender_values(buf, OFFSET_COLOR_BLENDER_YELLOW, color_blender.get('yellow', {}))
    write_color_blender_values(buf, OFFSET_COLOR_BLENDER_GREEN, color_blender.get('green', {}))
    write_color_blender_values(buf, OFFSET_COLOR_BLENDER_CYAN, color_blender.get('cyan', {}))
    write_color_blender_values(buf, OFFSET_COLOR_BLENDER_BLUE, color_blender.get('blue', {}))
    write_color_blender_values(buf, OFFSET_COLOR_BLENDER_PURPLE, color_blender.get('purple', {}))
    write_color_blender_values(buf, OFFSET_COLOR_BLENDER_MAGENTA, color_blender.get('magenta', {}))


def write_color_blender_values(buf: bytearray, offset: int, color: dict[str, Any]) -> None:
    hue = color.get('hue', 0)
    chroma = color.get('chroma', 0)
    brightness = color.get('brightness', 0)
    buf[offset] = 0x80 + clamp(hue, -100, 100)
    buf[offset + 1] = 0x80 + clamp(chroma, -100, 100)
    buf[offset + 2] = 0x80 + clamp(brightness, -100, 100)


def write_color_grading(buf: bytearray, color_grading: dict[str, Any] = {}) -> None:
    write_color_grading_values(buf, OFFSET_COLOR_GRADING_HIGHLIGHTS, color_grading.get('highlights', {}))
    write_color_grading_values(buf, OFFSET_COLOR_GRADING_MIDTONE, color_grading.get('midTone', {}))
    write_color_grading_values(buf, OFFSET_COLOR_GRADING_SHADOWS, color_grading.get('shadows', {}))
    blending = color_grading.get('blending', 50)
    balance = color_grading.get('balance', 0)
    buf[OFFSET_COLOR_GRADING_BLENDING] = 0x80 + clamp(blending, -100, 100)
    buf[OFFSET_COLOR_GRADING_BALANCE] = 0x80 + clamp(balance, -100, 100)


def write_color_grading_values(buf: bytearray, offset: int, color: dict[str, Any]) -> None:
    hue = round_degree(color.get('hue', 0))
    chroma = color.get('chroma', 0)
    brightness = color.get('brightness', 0)
    buf[offset] = 0x80 + (hue >> 8)
    buf[offset + 1] = hue & 0xFF
    buf[offset + 2] = 0x80 + clamp(chroma, -100, 100)
    buf[offset + 3] = 0x80 + clamp(brightness, -100, 100)


def write_comment(buf: bytearray, comment: str = '', next_chunk_type: int = 0x00) -> bytearray:
    if not comment:
        return buf
    payload = encode_comment(comment)
    ret = bytearray(len(buf) + 4 + len(payload) + 4)
    ret[: len(buf)] = buf
    ret[OFFSET_COMMENT_FLAG_A] = 1
    ret[OFFSET_COMMENT_FLAG_B] = 1
    ret[len(buf) : len(buf) + 4] = len(payload).to_bytes(4, 'big')
    ret[len(buf) + 4 : len(buf) + 4 + len(payload)] = payload
    ret[len(buf) + 4 + len(payload) : len(buf) + 8 + len(payload)] = next_chunk_type.to_bytes(4, 'big')
    return ret


def write_tone_curve(buf: bytearray, tone_curve: dict[str, Any]) -> bytearray:
    tone_curve_start = get_tone_curve_start(buf)
    write_tone_curve_raw(buf, tone_curve.get('raw', []), tone_curve_start)
    write_tone_curve_points(buf, tone_curve.get('points', []), tone_curve_start)
    return buf


def write_tone_curve_raw(buf: bytearray, raw: list[int], tone_curve_start: int) -> None:
    offset_delta = tone_curve_start - BASE_LENGTH
    for i, value in enumerate(raw):
        buf[OFFSET_TONE_CURVE_RAW + offset_delta + i * 2 : OFFSET_TONE_CURVE_RAW + offset_delta + i * 2 + 2] = int(clamp(value, 0, 32767)).to_bytes(2, 'big')


def write_tone_curve_points(buf: bytearray, points: list[dict[str, Any]], tone_curve_start: int) -> None:
    max_points = 20
    point_count = min(len(points), max_points)
    offset_delta = tone_curve_start - BASE_LENGTH
    buf[OFFSET_TONE_CURVE_POINTS + offset_delta] = point_count
    for i in range(point_count):
        point = points[i]
        offset = OFFSET_TONE_CURVE_POINTS + offset_delta + 1 + i * 2
        buf[offset] = clamp(point.get('x', 0), 0, 255)
        buf[offset + 1] = clamp(point.get('y', 0), 0, 255)


def append_tone_curve_extension(buf: bytearray) -> bytearray:
    return bytearray(buf + TONE_CURVE_EXTENSION)


def apply_tone_curve_header(buf: bytearray, has_no_comment: bool) -> None:
    for offset in TONE_CURVE_SENTINEL_OFFSETS:
        buf[offset] = 0x01
    if has_no_comment:
        buf[OFFSET_TONE_CURVE_FLAG] = 0x02


def get_tone_curve_start(buf: bytearray) -> int:
    comment_payload_length = read_comment_payload_length(buf)
    return BASE_LENGTH if comment_payload_length is None else BASE_LENGTH + 4 + comment_payload_length + 4


def read_comment_payload_length(buf: bytearray) -> int | None:
    if buf[OFFSET_COMMENT_FLAG_A] != 1 or buf[OFFSET_COMMENT_FLAG_B] != 1:
        return None
    if len(buf) < BASE_LENGTH + 8:
        return None
    payload_length = int.from_bytes(buf[BASE_LENGTH : BASE_LENGTH + 4], 'big')
    if payload_length == 0 or payload_length % 2 != 0:
        return None
    return payload_length


def encode_comment(comment: str) -> bytes:
    if '\0' in comment:
        raise ValueError('comment must not contain NUL characters')
    encoded = comment.encode('utf-8')
    if len(comment) > MAX_COMMENT_CODE_UNITS:
        encoded = trim_comment(comment).encode('utf-8')
    if len(encoded) > MAX_COMMENT_CODE_UNITS * 4:
        encoded = encoded[: MAX_COMMENT_CODE_UNITS * 4]
    if len(encoded) % 2 != 0:
        encoded += b'\x00'
    return encoded


def trim_comment(comment: str) -> str:
    if len(comment) <= MAX_COMMENT_CODE_UNITS:
        return comment
    trimmed = comment[:MAX_COMMENT_CODE_UNITS]
    if trimmed and 0xD800 <= ord(trimmed[-1]) <= 0xDBFF:
        trimmed = trimmed[:-1]
    return trimmed
