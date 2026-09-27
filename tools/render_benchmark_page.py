#!/usr/bin/env python3
"""Render a self-contained comparison page for a benchmark report."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path
from typing import Any, Optional


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _relative_asset(result_dir: Path, path_value: str) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        path = result_dir / path
    try:
        return os.path.relpath(path, result_dir).replace(os.sep, "/")
    except ValueError:
        return path.as_uri()


def _make_thumbnail(result_dir: Path, result: dict, thumbs_dir: Path) -> str:
    original = str(result.get("image_path", ""))
    original_href = _relative_asset(result_dir, original)
    if not original:
        return original_href

    source = Path(original)
    if not source.is_absolute():
        source = result_dir / source
    try:
        from PIL import Image
    except ImportError:
        return original_href

    if not source.exists():
        return original_href
    thumbnail_name = (
        f"{_slug(result.get('profile', 'profile'))}_"
        f"{_slug(result.get('case', 'case'))}_"
        f"{_slug(str(result.get('seed', 'seed')))}.jpg"
    )
    destination = thumbs_dir / thumbnail_name
    try:
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((360, 360))
            image.save(destination, format="JPEG", quality=88, optimize=True)
        return _relative_asset(result_dir, str(destination))
    except Exception:
        return original_href


def _case_metadata(report: dict) -> list[dict]:
    cases = report.get("cases", [])
    return [case if isinstance(case, dict) else {"id": case} for case in cases]


def _result_index(report: dict) -> tuple[dict[tuple[str, str, str], dict], dict[str, str]]:
    results: dict[tuple[str, str, str], dict] = {}
    profile_errors: dict[str, str] = {}
    for result in report.get("results", []):
        profile = str(result.get("profile", ""))
        case = result.get("case")
        seed = result.get("seed")
        if case is not None and seed is not None:
            results[(profile, str(case), str(seed))] = result
        elif result.get("status") == "connection-error":
            profile_errors[profile] = (
                f"{result.get('error_type', 'connection-error')}: "
                f"{result.get('message', 'connection failed')}"
            )
    return results, profile_errors


def _average_time(results: dict[tuple[str, str, str], dict], profile: str) -> str:
    values = [
        float(result["elapsed_seconds"])
        for key, result in results.items()
        if key[0] == profile
        and result.get("status") == "success"
        and isinstance(result.get("elapsed_seconds"), (int, float))
    ]
    return f"{sum(values) / len(values):.2f}s" if values else "n/a"


def _note_html(note: Optional[dict]) -> str:
    if not note:
        return ""
    flag = str(note.get("flag", "none"))
    text = _escape(note.get("note", ""))
    badge = f'<span class="badge { _slug(flag) }">{_escape(flag)}</span>'
    return f'<div class="note">{badge} {text}</div>'


def _image_cell(
    result_dir: Path,
    result: Optional[dict],
    profile: str,
    case_id: str,
    seed: str,
    thumbs_dir: Path,
    notes: dict,
    profile_error: Optional[str],
) -> str:
    if result is None:
        message = profile_error or "No result recorded"
        return f'<div class="image-cell error"><div>{_escape(message)}</div></div>'
    status = result.get("status")
    if status != "success" or not result.get("image_path"):
        message = result.get("message") or result.get("error_type") or status or "error"
        body = f'<div class="error">{_escape(message)}</div>'
    else:
        href = _relative_asset(result_dir, str(result["image_path"]))
        thumbnail = _make_thumbnail(result_dir, result, thumbs_dir)
        body = (
            f'<a href="{_escape(href)}" target="_blank" rel="noopener">'
            f'<img src="{_escape(thumbnail)}" alt="{_escape(profile)} { _escape(seed) }">'
            "</a>"
        )
    if isinstance(result.get("elapsed_seconds"), (int, float)):
        body += f'<div class="time">{float(result["elapsed_seconds"]):.2f}s</div>'
    note = notes.get(f"{profile}/{case_id}/{seed}")
    return f'<div class="image-cell">{body}{_note_html(note)}</div>'


def _settings_table(profiles: list[dict], results: dict) -> str:
    rows = []
    for profile in profiles:
        profile_id = str(profile.get("profile_id", ""))
        rows.append(
            "<tr>"
            f"<th>{_escape(profile_id)}</th>"
            f"<td>{_escape(profile.get('steps'))}</td>"
            f"<td>{_escape(profile.get('cfg'))}</td>"
            f"<td>{_escape(profile.get('sampler'))}</td>"
            f"<td>{_escape(profile.get('scheduler'))}</td>"
            f"<td>{_escape(profile.get('clip_skip'))}</td>"
            f"<td>{_average_time(results, profile_id)}</td>"
            "</tr>"
        )
    return (
        "<table class=\"settings\"><thead><tr>"
        "<th>Model</th><th>Steps</th><th>CFG</th><th>Sampler</th>"
        "<th>Scheduler</th><th>Clip skip</th><th>Average time</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_benchmark_page(result_dir: Path) -> Path:
    result_dir = result_dir.resolve()
    report = json.loads((result_dir / "report.json").read_text(encoding="utf-8"))
    notes_path = result_dir / "notes.json"
    notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else {}
    thumbs_dir = result_dir / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    profiles = report.get("profiles", [])
    cases = _case_metadata(report)
    seeds = [str(seed) for seed in report.get("seeds", [])]
    results, profile_errors = _result_index(report)
    prompt_mode = report.get("prompt_mode", "recommended")
    profile_ids = [str(profile.get("profile_id", "")) for profile in profiles]

    constant_prompt = ""
    constant_negative = ""
    for result in report.get("results", []):
        if result.get("prompt") and not constant_prompt:
            constant_prompt = str(result["prompt"])
        if result.get("negative_prompt") and not constant_negative:
            constant_negative = str(result["negative_prompt"])

    shared_conditions = ""
    if prompt_mode == "shared":
        shared_conditions = (
            "<p><strong>Held constant:</strong> prompt, negative prompt, size, and seed.</p>"
            "<p><strong>Per model:</strong> steps, CFG, sampler, scheduler, and clip skip.</p>"
        )
    else:
        shared_conditions = (
            "<p><strong>Held constant:</strong> case content and seed.</p>"
            "<p><strong>Per model:</strong> prompt, negative prompt, size, steps, CFG, sampler, scheduler, and clip skip.</p>"
        )

    sections = []
    for case in cases:
        case_id = str(case.get("id", ""))
        prompt_html = ""
        negative_html = ""
        if prompt_mode == "shared":
            prompt = constant_prompt
            for result in report.get("results", []):
                if result.get("case") == case_id and result.get("prompt"):
                    prompt = str(result["prompt"])
                    break
            prompt_html = f'<pre class="prompt">{_escape(prompt)}</pre>'
            negative_html = f'<pre class="prompt">{_escape(constant_negative)}</pre>'
        else:
            prompt_html = "".join(
                f'<details><summary>{_escape(profile_id)} prompt</summary>'
                f'<pre class="prompt">{_escape(next((r.get("prompt", "") for r in report.get("results", []) if r.get("profile") == profile_id and r.get("case") == case_id), ""))}</pre></details>'
                for profile_id in profile_ids
            )
            negative_html = "".join(
                f'<details><summary>{_escape(profile_id)} negative prompt</summary>'
                f'<pre class="prompt">{_escape(next((r.get("negative_prompt", "") for r in report.get("results", []) if r.get("profile") == profile_id and r.get("case") == case_id), ""))}</pre></details>'
                for profile_id in profile_ids
            )

        header_cells = "<div class=\"corner\"></div>" + "".join(
            f'<div class="model-header">{_escape(profile_id)}</div>' for profile_id in profile_ids
        )
        image_rows = []
        for seed in seeds:
            cells = [f'<div class="seed-label">{_escape(seed)}</div>']
            for profile_id in profile_ids:
                result = results.get((profile_id, case_id, seed))
                cells.append(
                    _image_cell(
                        result_dir,
                        result,
                        profile_id,
                        case_id,
                        seed,
                        thumbs_dir,
                        notes,
                        profile_errors.get(profile_id),
                    )
                )
            image_rows.append("".join(cells))
        grid = (
            f'<div class="grid-wrap"><div class="grid" style="--model-count:{len(profile_ids)}">'
            + header_cells
            + "".join(image_rows)
            + "</div></div>"
        )
        sections.append(
            f'<section><h2>{_escape(case.get("title_ja", case_id))}</h2>'
            f'<p class="setting">{_escape(case.get("setting_ja", ""))}</p>'
            f'<h3>Prompt</h3>{prompt_html}'
            f'<p class="explanation">{_escape(case.get("prompt_explanation_ja", ""))}</p>'
            f'<h3>Negative prompt</h3>{negative_html}{grid}</section>'
        )

    document = f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Benchmark comparison</title>
<style>
:root {{ color-scheme: light dark; font-family: system-ui, -apple-system, sans-serif; }}
body {{ max-width: 1500px; margin: 0 auto; padding: 24px; line-height: 1.5; background: Canvas; color: CanvasText; }}
h1, h2, h3 {{ line-height: 1.2; }} section {{ margin-top: 40px; }}
.settings {{ border-collapse: collapse; width: 100%; }} th, td {{ border: 1px solid GrayText; padding: 7px; text-align: left; }}
.prompt {{ white-space: pre-wrap; overflow-wrap: anywhere; padding: 12px; border: 1px solid GrayText; border-radius: 6px; }}
.explanation, .setting {{ padding: 10px 12px; border-left: 4px solid #6a8; }}
.grid-wrap {{ overflow-x: auto; }}
.grid {{ display: grid; grid-template-columns: 80px repeat(var(--model-count), minmax(180px, 1fr)); min-width: max(100%, calc(80px + var(--model-count) * 190px)); gap: 8px; }}
.model-header, .seed-label {{ font-weight: 700; padding: 8px; position: sticky; left: 0; }}
.model-header {{ background: #64748b; color: white; border-radius: 5px; }}
.seed-label {{ align-self: start; border: 1px solid GrayText; border-radius: 5px; }}
.image-cell {{ min-height: 130px; padding: 6px; border: 1px solid GrayText; border-radius: 6px; text-align: center; }}
.image-cell img {{ display: block; width: 100%; height: auto; max-height: 360px; object-fit: contain; margin: auto; }}
.time {{ margin-top: 4px; font-variant-numeric: tabular-nums; }} .error {{ color: #d66; overflow-wrap: anywhere; }}
.note {{ margin-top: 5px; font-size: .9em; text-align: left; overflow-wrap: anywhere; }}
.badge {{ border-radius: 4px; padding: 1px 5px; background: #777; color: white; }} .fatal {{ background: #b33; }} .caution {{ background: #a70; }}
@media (prefers-color-scheme: dark) {{ .model-header {{ background: #334155; }} }}
@media (max-width: 800px) {{ body {{ padding: 12px; }} .settings {{ display: block; overflow-x: auto; }} section {{ margin-top: 28px; }} }}
</style></head><body>
<h1>Benchmark comparison</h1><p>Prompt mode: <strong>{_escape(prompt_mode)}</strong></p>
{shared_conditions}{_settings_table(profiles, results)}{"".join(sections)}
</body></html>'''
    output = result_dir / "comparison.html"
    output.write_text(document, encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()
    print(render_benchmark_page(args.result_dir))


if __name__ == "__main__":
    main()
