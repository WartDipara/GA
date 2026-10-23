"""Render coordinate annotations above sanitized evidence without changing image bytes."""

import html
import math
from urllib.parse import quote

from PIL import Image

from game_automation.common import FrameworkError, within


def action_overlay(action, width, height):
    kind = action.get("type")
    if kind not in ("tap", "swipe", "long_press", "credential"):
        return ""
    x, y = action.get("x"), action.get("y")
    if not all(type(value) is int for value in (x, y)):
        return ""
    if not (0 <= x < width and 0 <= y < height):
        return '<p class="evidence-note">Recorded point is outside this image.</p>'
    radius = max(10, min(width, height) / 45)
    shapes = []
    bounds = action.get("bounds")
    if bounds and len(bounds) == 4 and all(type(value) is int for value in bounds):
        left, top, right, bottom = bounds
        if 0 <= left < right <= width and 0 <= top < bottom <= height:
            shapes.append(
                f'<rect class="target-bounds" x="{left}" y="{top}" '
                f'width="{right - left}" height="{bottom - top}"/>'
            )
    if kind == "swipe":
        end_x, end_y = action.get("end_x"), action.get("end_y")
        if type(end_x) is int and type(end_y) is int and 0 <= end_x < width and 0 <= end_y < height:
            angle = math.atan2(end_y - y, end_x - x)
            arrow_left = (
                end_x - radius * 2 * math.cos(angle - 0.5),
                end_y - radius * 2 * math.sin(angle - 0.5),
            )
            arrow_right = (
                end_x - radius * 2 * math.cos(angle + 0.5),
                end_y - radius * 2 * math.sin(angle + 0.5),
            )
            arrow_points = (
                f"{end_x},{end_y} {arrow_left[0]:.1f},{arrow_left[1]:.1f} "
                f"{arrow_right[0]:.1f},{arrow_right[1]:.1f}"
            )
            shapes.append(
                f'<path class="gesture-path" d="M {x} {y} L {end_x} {end_y}"/>'
                f'<polygon points="{arrow_points}" fill="#ff5470"/>'
            )
    shapes.append(f'<circle class="touch-ring" cx="{x}" cy="{y}" r="{radius}"/>')
    if kind == "long_press":
        shapes.append(
            f'<circle class="touch-ring hold-ring" cx="{x}" cy="{y}" r="{radius * 1.5}"/>'
        )
    shapes.append(f'<circle class="touch-point" cx="{x}" cy="{y}" r="{radius / 4}"/>')
    overlay = (
        f'<svg class="action-overlay" viewBox="0 0 {width} {height}" '
        'role="img" aria-label="Recorded action target">' + "".join(shapes) + "</svg>"
    )
    return overlay


def evidence_figure(store, report_run, image_run, reference, caption, action=None):
    if store.run(image_run)["task_id"] != store.run(report_run)["task_id"]:
        return '<p class="evidence-note">Evidence belongs to another task.</p>'
    try:
        image_path = within(store.run_dir(image_run), reference)
        with Image.open(image_path) as image:
            width, height = image.size
    except (FrameworkError, OSError, ValueError):
        return '<p class="evidence-note">Screenshot unavailable.</p>'
    prefix = "../" if image_run == report_run else f"../../{image_run}/"
    source = html.escape(prefix + quote(reference, safe="/"), quote=True)
    overlay = action_overlay(action, width, height) if action else ""
    figure = (
        '<figure class="action-evidence"><div class="evidence-frame" '
        f'style="aspect-ratio:{width}/{height}">'
        f'<img loading="lazy" src="{source}" alt="{html.escape(caption, quote=True)}">'
        f"{overlay}</div><figcaption>{html.escape(caption)}</figcaption></figure>"
    )
    return figure


def action_figures(store, run_id, action):
    proposal = action.get("proposal") or {}
    kind = proposal.get("type", "unknown")
    dispatched = action.get("dispatched")
    if dispatched is True:
        state = "Dispatched target"
    elif dispatched is False:
        state = "Proposed target · not dispatched"
    else:
        state = "Target · dispatch not recorded"
    point = ""
    if kind in ("tap", "long_press", "swipe", "credential"):
        point = f" · ({proposal.get('x')}, {proposal.get('y')})"
    caption = f"Before · {state} · {kind}{point}"
    figures = []
    if action.get("before_image"):
        figures.append(
            evidence_figure(store, run_id, run_id, action["before_image"], caption, proposal)
        )
    if action.get("after_image"):
        figures.append(
            evidence_figure(
                store,
                run_id,
                action.get("after_run", run_id),
                action["after_image"],
                "After · recorded observation; target markers apply only to the before image",
            )
        )
    state_class = "dispatched" if dispatched is True else "not-dispatched"
    rendered = f'<div class="evidence-pair {state_class}">' + "".join(figures) + "</div>"
    return rendered
