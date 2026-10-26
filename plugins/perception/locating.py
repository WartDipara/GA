"""Conservative current-frame localization; approximate matches never authorize input."""

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from game_automation.common import digest


def load_image(path):
    with Image.open(path) as source:
        image = source.convert("RGB")
    return image


def edge_image(image):
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    return edges.point(lambda value: 255 if value >= 48 else 0)


def page_image(image, regions):
    result = image.convert("L")
    draw = ImageDraw.Draw(result)
    for bounds in regions:
        draw.rectangle(bounds, fill=0)
    return result.point(lambda value: value // 32 * 32)


def find_patch(image, patch, predicted, radius):
    matches = []
    width, height = patch.size
    if width < 6 or height < 6:
        return matches
    source = image.tobytes()
    template = patch.tobytes()
    rows = [template[offset : offset + width] for offset in range(0, len(template), width)]
    row_index = max(range(height), key=lambda index: rows[index].count(255))
    needle = rows[row_index]
    if needle.count(255) < 3 or template.count(255) < 12:
        return matches
    left = max(0, predicted[0] - radius)
    right = min(image.width - width, predicted[0] + radius)
    top = max(0, predicted[1] - radius)
    bottom = min(image.height - height, predicted[1] + radius)
    for y in range(top, bottom + 1):
        row_start = (y + row_index) * image.width
        position = source.find(needle, row_start + left, row_start + right + width)
        while position >= 0:
            x = position - row_start
            if all(
                source[(y + row) * image.width + x : (y + row) * image.width + x + width]
                == rows[row]
                for row in range(height)
            ):
                matches.append([x, y, x + width, y + height])
                if len(matches) > 1:
                    return matches
            position = source.find(needle, position + 1, row_start + right + width)
    return matches


def inside(bounds, size):
    left, top, right, bottom = bounds
    return 0 <= left < right <= size[0] and 0 <= top < bottom <= size[1]


def locate(request):
    current = load_image(request["image_path"])
    reference = load_image(request["reference_path"])
    predicted = request["bounds"]
    result = {
        "status": "unknown",
        "source": "none",
        "bounds": None,
        "predicted_bounds": predicted,
        "target_guard": None,
        "matched_anchors": [],
        "reason": "Current layout or stable visual evidence is insufficient",
        "page_match": False,
        "page_guard": None,
    }
    if current.size != reference.size or not inside(predicted, current.size):
        return result
    regions = request["noise_regions"]
    area = sum((box[2] - box[0]) * (box[3] - box[1]) for box in regions)
    if area > current.width * current.height // 4:
        regions = []
    current_page = page_image(current, regions)
    original_page = page_image(reference, regions)
    delta = ImageChops.difference(current_page, original_page)
    result["page_match"] = delta.getbbox() is None
    controls = [
        control
        for control in request["controls"]
        if control.get("enabled", True)
        and not control.get("sensitive")
        and control.get("label") == request["label"]
        and request["label"]
        and inside(control["bounds"], current.size)
    ]
    if len(controls) == 1:
        result.update(
            status="matched",
            source="control",
            bounds=controls[0]["bounds"],
            reason="Unique current accessible control with the expected label",
        )
    else:
        current_edges = edge_image(current)
        reference_edges = edge_image(reference)
        shifts = []
        matched = []
        for anchor in request["anchors"]:
            bounds = anchor["bounds"]
            if not inside(bounds, reference.size):
                continue
            matches = find_patch(
                current_edges,
                reference_edges.crop(bounds),
                bounds,
                max(current.size) // 5,
            )
            if len(matches) == 1:
                shifts.append((matches[0][0] - bounds[0], matches[0][1] - bounds[1]))
                matched.append(anchor["key"])
        result["matched_anchors"] = matched
        if len(set(matched)) < 2 or len(set(shifts)) != 1:
            return result
        offset_x, offset_y = shifts[0]
        translated = [
            predicted[0] + offset_x,
            predicted[1] + offset_y,
            predicted[2] + offset_x,
            predicted[3] + offset_y,
        ]
        matches = find_patch(current_edges, reference_edges.crop(predicted), translated, 12)
        if len(matches) != 1:
            result["status"] = "ambiguous" if matches else "unknown"
            result["reason"] = "Target patch is absent or not unique near current anchors"
            return result
        result.update(
            status="matched",
            source="visual",
            bounds=matches[0],
            reason="Two coherent stable anchors and one exact local edge match",
        )
    previous_path = request["previous_image_path"]
    if previous_path:
        previous = load_image(previous_path)
        if previous.size != current.size:
            result.update(status="unknown", reason="Layout changed between observations")
        elif edge_image(previous).crop(result["bounds"]).tobytes() != (
            edge_image(current).crop(result["bounds"]).tobytes()
        ):
            result.update(status="unknown", reason="Target is moving or changing")
    if result["status"] == "matched":
        result["target_guard"] = digest(current.crop(result["bounds"]).tobytes())
        result["page_guard"] = {
            "sha256": digest(current_page.tobytes()),
            "noise_regions": regions,
        }
    return result
