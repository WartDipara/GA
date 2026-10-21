import struct

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


def png_dimensions(header):
    if len(header) < 24 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise FrameworkError(ErrorCode.INVALID_SCREENSHOT)
    dimensions = struct.unpack(">II", header[16:24])
    if not all(0 < value <= 16384 for value in dimensions):
        raise FrameworkError(ErrorCode.INVALID_SCREENSHOT)
    return dimensions
