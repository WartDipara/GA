"""Local OCR removes known credentials before screenshot persistence and model submission."""

import asyncio
import io
import re
from difflib import SequenceMatcher

from PIL import Image

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


async def credential_regions(data, values):
    try:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter

        image = Image.open(io.BytesIO(data)).convert("RGBA")
        writer = DataWriter()
        writer.write_bytes(image.tobytes("raw", "BGRA"))
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, image.width, image.height
        )
        languages = OcrEngine.available_recognizer_languages
        if not languages:
            raise FrameworkError(ErrorCode.CREDENTIAL_UNAVAILABLE, "Local OCR is unavailable")
        rectangles = []
        matched_username = False
        for language in languages:
            engine = OcrEngine.try_create_from_language(language)
            async with asyncio.timeout(10):
                result = await engine.recognize_async(bitmap)
            for line in result.lines:
                text = re.sub(r"\W", "", line.text).casefold()
                sensitive = False
                for field, value in values.items():
                    value = re.sub(r"\W", "", value).casefold()
                    if not value:
                        continue
                    sensitive |= value in text
                    matched_username |= field == "username" and value in text
                    for offset in range(max(0, len(text) - len(value) + 1)):
                        part = text[offset : offset + len(value)]
                        sensitive |= SequenceMatcher(None, part, value).ratio() >= 0.8
                if not sensitive or not line.words:
                    continue
                bounds = [word.bounding_rect for word in line.words]
                rectangle = [
                    max(0, int(min(item.x for item in bounds)) - 8),
                    max(0, int(min(item.y for item in bounds)) - 8),
                    min(image.width, int(max(item.x + item.width for item in bounds)) + 8),
                    min(image.height, int(max(item.y + item.height for item in bounds)) + 8),
                ]
                rectangles.append(rectangle)
        result = rectangles, matched_username
        return result
    except (ImportError, OSError, TimeoutError) as exc:
        raise FrameworkError(
            ErrorCode.CREDENTIAL_UNAVAILABLE, "Screenshot privacy processing is unavailable"
        ) from exc
