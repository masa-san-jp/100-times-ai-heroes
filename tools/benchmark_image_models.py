#!/usr/bin/env python3
"""候補画像モデルを同じキャラクター設定とseedで比較する。"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from comfyui_image_gen import ComfyUIImageGenerator  # noqa: E402
from image_model_profiles import (  # noqa: E402
    ImageModelProfile,
    build_image_prompt,
    get_image_model_profile,
    normalize_ability_text,
)
from memory_safety import MemorySnapshot, snapshot as read_memory_snapshot  # noqa: E402


DEFAULT_PROFILE_IDS = [
    "animagine-xl-4.0-opt",
    "illustrious-xl-v2",
    "pony-v6-xl",
    "noobai-xl-1.1",
    "qwen-image-2.1",
]

SHARED_WIDTH = 832
SHARED_HEIGHT = 1216
SHARED_NEGATIVE_PROMPT = (
    "nsfw, nude, nipples, underwear, swimsuit, child, loli, shota, lowres, bad anatomy, "
    "bad hands, extra fingers, missing fingers, cropped, duplicate, multiple people, text, "
    "watermark, signature, blurry"
)

BENCHMARK_CASES = [
    {
        "id": "human-warrior",
        "age": "young adult",
        "gender": "male",
        "species": "human",
        "role": "Swordsman, disciplined guardian",
        "ability": "Can cut through digital noise with a single stroke",
        "concept": "A young human swordsman who protects people from information chaos with a calm sense of duty.",
        "title_ja": "人間の戦士",
        "setting_ja": "若い成人男性の人間の剣士。規律ある守護者として、人々を情報の混沌から守る。",
        "prompt_explanation_ja": "構図は1boy、solo、full body、standing、looking at viewer、feet visible、centered composition、white backgroundで、1人の全身像を正面から中央に配置します。設定はyoung adult、human、Swordsman, disciplined guardianで、若い成人男性の人間の剣士という人物像を指定します。能力はability to cut through digital noise with a single strokeで、情報の混沌を一閃で切り裂く力を表します。コンセプトは、人々を情報の混沌から守る冷静な使命感を英文で補足します。",
    },
    {
        "id": "aquatic-designer",
        "age": "adult",
        "gender": "female",
        "species": "half-human half-aquatic woman",
        "role": "Nostalgic Experience Designer",
        "ability": "Can reverse causality through rhythmic movement",
        "concept": "An elegant aquatic dancer who designs shared memories and dreams of a cooperative utopia.",
        "title_ja": "水棲のデザイナー",
        "setting_ja": "成人女性の半人半魚のノスタルジック・エクスペリエンス・デザイナー。",
        "prompt_explanation_ja": "構図は1girl、solo、full body、standing、looking at viewer、feet visible、centered composition、white backgroundで、1人の全身像を正面から中央に配置します。設定はadult、half-human half-aquatic woman、Nostalgic Experience Designerで、成人女性の半人半魚のデザイナーという人物像を指定します。能力はability to reverse causality through rhythmic movementで、リズミカルな動きによって因果を反転させる力を表します。コンセプトは、共有された記憶を設計し協力的なユートピアを夢見る優雅な水棲ダンサーという背景を英文で補足します。",
    },
    {
        "id": "lycanthrope-artist",
        "age": "middle-aged",
        "gender": "male",
        "species": "lycanthrope",
        "role": "Biotechnology Tattoo Artist",
        "ability": "Can absorb emotions as colors and animate tattoos",
        "concept": "A middle-aged lycanthrope artist whose living tattoos connect people through visible emotions.",
        "title_ja": "狼男のアーティスト",
        "setting_ja": "中年男性の狼男のタトゥー作家。生きたタトゥーで人々を感情的につなぐ。",
        "prompt_explanation_ja": "構図は1boy、solo、full body、standing、looking at viewer、feet visible、centered composition、white backgroundで、1人の全身像を正面から中央に配置します。設定はmiddle-aged、lycanthrope、Biotechnology Tattoo Artistで、中年男性の狼男のタトゥー作家という人物像を指定します。能力はability to absorb emotions as colors and animate tattoosで、感情を色として吸収しタトゥーを動かす力を表します。コンセプトは、生きたタトゥーを通じて目に見える感情で人々をつなぐアーティストという背景を英文で補足します。",
    },
]


def _memory_snapshot() -> MemorySnapshot:
    return read_memory_snapshot()


class MemorySampler:
    """画像生成中の利用可能メモリの最低値を記録する。"""

    def __init__(self, interval_seconds: float = 0.5):
        self.interval_seconds = interval_seconds
        self.samples: List[MemorySnapshot] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "MemorySampler":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples.append(_memory_snapshot())
            self._stop.wait(self.interval_seconds)

    @property
    def minimum_available_percent(self) -> Optional[float]:
        values = [sample.available_percent for sample in self.samples]
        values = [value for value in values if value is not None]
        return min(values) if values else None


def _parse_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _profile_generator(
    profile: ImageModelProfile,
    *,
    url: str,
    workflow_path: Optional[Path],
    output_dir: Path,
    seed: int,
    timeout_seconds: Optional[float] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    negative_prompt: Optional[str] = None,
) -> ComfyUIImageGenerator:
    selected_workflow = workflow_path or Path(profile.workflow_path)
    if not selected_workflow.is_absolute():
        selected_workflow = PROJECT_ROOT / selected_workflow
    return ComfyUIImageGenerator(
        base_url=url,
        workflow_path=selected_workflow,
        checkpoint_name=profile.checkpoint_name,
        timeout_seconds=(
            profile.timeout_seconds if timeout_seconds is None else timeout_seconds
        ),
        poll_interval_seconds=1,
        width=profile.width if width is None else width,
        height=profile.height if height is None else height,
        steps=profile.steps,
        cfg=profile.cfg,
        sampler=profile.sampler,
        scheduler=profile.scheduler,
        negative_prompt=(
            profile.negative_prompt if negative_prompt is None else negative_prompt
        ),
        clip_skip=profile.clip_skip,
        model_files=profile.model_files,
        seed_factory=lambda seed=seed: seed,
    )


def _iter_cases(case_count: int) -> Iterable[dict]:
    if case_count < 1 or case_count > len(BENCHMARK_CASES):
        raise ValueError(f"--cases must be between 1 and {len(BENCHMARK_CASES)}")
    return BENCHMARK_CASES[:case_count]


def _case_prompt(profile: ImageModelProfile, case: dict) -> str:
    values = {
        key: case[key]
        for key in ("concept", "age", "gender", "species", "ability", "role")
    }
    return build_image_prompt(profile, **values)


def build_shared_image_prompt(case: dict) -> str:
    """ケース共通のタグ構成・設定と英文コンセプトを作る。"""
    tags = [
        "1girl" if "female" in case["gender"].lower() else "1boy",
        "solo",
        "full body",
        "standing",
        "looking at viewer",
        "feet visible",
        "centered composition",
        "white background",
        case["age"],
        case["species"],
        case["role"],
        normalize_ability_text(case["ability"]),
    ]
    prompt = ", ".join(value.strip(" ,") for value in tags if value.strip(" ,"))
    concept = str(case.get("concept", "")).strip()
    return f"{prompt}, {concept}" if concept else prompt


def _prompt_for(profile: ImageModelProfile, case: dict, prompt_mode: str) -> str:
    if prompt_mode == "shared":
        return build_shared_image_prompt(case)
    return _case_prompt(profile, case)


def _settings_for(
    profile: ImageModelProfile,
    prompt_mode: str,
    timeout_override: Optional[float],
) -> dict:
    return {
        "negative_prompt": (
            SHARED_NEGATIVE_PROMPT if prompt_mode == "shared" else profile.negative_prompt
        ),
        "width": SHARED_WIDTH if prompt_mode == "shared" else profile.width,
        "height": SHARED_HEIGHT if prompt_mode == "shared" else profile.height,
        "steps": profile.steps,
        "cfg": profile.cfg,
        "sampler": profile.sampler,
        "scheduler": profile.scheduler,
        "clip_skip": profile.clip_skip,
        "timeout_seconds": (
            profile.timeout_seconds if timeout_override is None else timeout_override
        ),
    }


def run_benchmark(args: argparse.Namespace) -> dict:
    profile_ids = DEFAULT_PROFILE_IDS if args.profiles == ["all"] else args.profiles
    profiles = [
        get_image_model_profile(profile_id, args.profiles_path)
        for profile_id in profile_ids
    ]
    cases = list(_iter_cases(args.cases))
    seeds = args.seeds
    prompt_mode = getattr(args, "prompt_mode", "shared")
    timeout_override = getattr(args, "timeout", None)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "host": platform.platform(),
        "prompt_mode": prompt_mode,
        "profiles": [asdict(profile) for profile in profiles],
        "cases": cases,
        "seeds": seeds,
        "results": [],
    }

    args.output.mkdir(parents=True, exist_ok=True)
    for profile in profiles:
        profile_dir = args.output / profile.profile_id
        settings = _settings_for(profile, prompt_mode, timeout_override)
        if args.dry_run:
            for case in cases:
                for seed in seeds:
                    report["results"].append(
                        {
                            "profile": profile.profile_id,
                            "case": case["id"],
                            "seed": seed,
                            "status": "dry-run",
                            "prompt": _prompt_for(profile, case, prompt_mode),
                            **settings,
                        }
                    )
            continue

        generator = _profile_generator(
            profile,
            url=args.url,
            workflow_path=args.workflow_path,
            output_dir=profile_dir,
            seed=seeds[0],
            timeout_seconds=timeout_override,
            width=settings["width"],
            height=settings["height"],
            negative_prompt=settings["negative_prompt"],
        )
        try:
            generator.check_connection()
        except Exception as exc:
            report["results"].append(
                {
                    "profile": profile.profile_id,
                    "status": "connection-error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            continue

        for case in cases:
            for seed in seeds:
                prompt = _prompt_for(profile, case, prompt_mode)
                before = _memory_snapshot()
                started = time.monotonic()
                sampler = MemorySampler()
                result = {
                    "profile": profile.profile_id,
                    "case": case["id"],
                    "seed": seed,
                    "prompt": prompt,
                    **settings,
                    "memory_before": asdict(before),
                }
                try:
                    with sampler:
                        image = _profile_generator(
                            profile,
                            url=args.url,
                            workflow_path=args.workflow_path,
                            output_dir=profile_dir / case["id"],
                            seed=seed,
                            timeout_seconds=timeout_override,
                            width=settings["width"],
                            height=settings["height"],
                            negative_prompt=settings["negative_prompt"],
                        ).generate(
                            prompt,
                            profile_dir / case["id"],
                            f"{case['id']}_{seed}",
                        )
                    result.update(
                        {
                            "status": "success",
                            "image_path": str(image.path),
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                        }
                    )
                except Exception as exc:
                    result.update(
                        {
                            "status": "error",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                        }
                    )
                result["minimum_available_percent"] = sampler.minimum_available_percent
                result["memory_after"] = asdict(_memory_snapshot())
                report["results"].append(result)

        if not args.keep_models_loaded:
            try:
                generator.free_memory()
            except Exception as exc:
                report["results"].append(
                    {
                        "profile": profile.profile_id,
                        "status": "free-memory-warning",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )

    report["summary"] = {
        "success": sum(result.get("status") == "success" for result in report["results"]),
        "errors": sum(result.get("status") == "error" for result in report["results"]),
        "connection_errors": sum(
            result.get("status") == "connection-error" for result in report["results"]
        ),
    }
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profiles",
        type=_parse_csv,
        default=["all"],
        help="比較するprofile ID（カンマ区切り、既定: all）",
    )
    parser.add_argument("--cases", type=int, default=3, help="固定ケース数（1-3）")
    parser.add_argument(
        "--seeds",
        type=_parse_csv,
        default=["101", "202"],
        help="固定seed（カンマ区切り）",
    )
    parser.add_argument(
        "--prompt-mode",
        choices=("shared", "recommended"),
        default="shared",
        help="プロンプト方式（既定: shared）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="全profileに適用するタイムアウト秒数（省略時はprofile値）",
    )
    parser.add_argument("--url", default="http://127.0.0.1:8188")
    parser.add_argument(
        "--workflow-path",
        type=Path,
        default=None,
        help="すべてのprofileで使うworkflow（省略時はprofileのworkflow_path）",
    )
    parser.add_argument(
        "--profiles-path",
        type=Path,
        default=PROJECT_ROOT / "config/comfyui/model_profiles.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "data"
        / f"model_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ComfyUIへ接続せず、profileとpromptだけ検証する",
    )
    parser.add_argument(
        "--keep-models-loaded",
        action="store_true",
        help="profile切り替え時にComfyUIの/freeを呼ばない",
    )
    namespace = parser.parse_args()
    namespace.seeds = [int(seed) for seed in namespace.seeds]
    if not namespace.seeds:
        parser.error("--seeds must contain at least one integer")
    if namespace.timeout is not None and namespace.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return namespace


if __name__ == "__main__":
    arguments = parse_args()
    result = run_benchmark(arguments)
    if arguments.dry_run:
        print("Profiles:")
        for profile in result["profiles"]:
            print(f"- {profile['profile_id']}: {profile['prompt_style']}")
    print(json.dumps(result["summary"], ensure_ascii=False))
    print(f"Report: {arguments.output / 'report.json'}")
