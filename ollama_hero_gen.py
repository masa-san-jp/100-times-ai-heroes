"""
100 Times AI Heroes - ローカル完結版キャラクター生成システム

既定では、ローカルCSVとlocalhost上のOllamaだけを使用します。
画像生成は --generate-images を指定した場合だけ、localhost上のComfyUIを使用します。
クラウドLLMは --provider openai を明示した場合だけ有効になります。
"""

from __future__ import annotations

import csv
import json
import os
import random
import re
import shutil
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

import ollama
from dotenv import load_dotenv
from image_labels import label_image
from image_model_profiles import (
    build_image_prompt,
    get_image_model_profile,
)
from memory_safety import MemoryBudgetExceeded, ensure_available


# =============================================================================
# Exceptions and protocols
# =============================================================================


class OllamaConnectionError(ConnectionError):
    """Ollamaサーバーへ接続できない。"""


class OllamaModelNotFoundError(RuntimeError):
    """指定されたOllamaモデルが導入されていない。"""


class ProviderConfigError(ValueError):
    """LLMプロバイダーの設定が不正。"""


class SeedDataError(ValueError):
    """seed CSVの形式または内容が不正。"""


class ComfyUIError(RuntimeError):
    """ComfyUIとの通信または画像生成に失敗した。"""


class StageError(RuntimeError):
    """生成パイプラインの特定段階で失敗した。"""

    def __init__(self, stage: str, cause: Exception):
        self.stage = stage
        self.cause = cause
        super().__init__(str(cause))


class TextGenerator(Protocol):
    """テキスト生成プロバイダーの共通インターフェース。"""

    def generate(self, prompt: str, max_retries: int = 3) -> str:
        ...


# =============================================================================
# Configuration
# =============================================================================


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean (true/false): {value}")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer: {value}") from exc


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number: {value}") from exc


@dataclass(frozen=True)
class Config:
    """アプリケーション設定。Google Sheetsの設定は持たない。"""

    model: str
    host: str
    data_dir: str
    num_iterations: int = 100
    provider: str = "ollama"
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    generate_images: bool = False
    generate_turnaround: bool = True
    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_model_profile: str = "generic-sdxl"
    comfyui_model_profiles_path: str = "./config/comfyui/model_profiles.json"
    comfyui_workflow_path: str = "./config/comfyui/text2image_api_workflow.json"
    comfyui_checkpoint_name: str = "CHANGE_ME.safetensors"
    comfyui_timeout_seconds: float = 300.0
    comfyui_poll_interval_seconds: float = 1.0
    comfyui_width: int = 768
    comfyui_height: int = 1152
    comfyui_steps: int = 24
    comfyui_cfg: float = 7.0
    comfyui_sampler: str = "euler"
    comfyui_scheduler: str = "normal"
    comfyui_clip_skip: Optional[int] = None
    memory_guard_enabled: bool = True
    minimum_available_memory_percent: float = 15.0
    comfyui_negative_prompt: str = (
        "low quality, blurry, distorted hands, extra fingers, cropped, duplicate"
    )

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
        if provider not in {"ollama", "openai"}:
            raise ProviderConfigError(
                f"Unsupported LLM_PROVIDER: {provider}. Use ollama or openai."
            )

        ollama_model = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        selected_model = openai_model if provider == "openai" else ollama_model

        model_profile_id = os.getenv(
            "COMFYUI_MODEL_PROFILE", "qwen-image-2.1-turbo"
        ).strip()
        model_profiles_path = os.getenv("COMFYUI_MODEL_PROFILES_PATH") or str(
            Path(__file__).resolve().parent / "config/comfyui/model_profiles.json"
        )
        image_profile = get_image_model_profile(
            model_profile_id, Path(model_profiles_path)
        )
        configured_checkpoint = os.getenv("COMFYUI_CHECKPOINT_NAME", "").strip()
        checkpoint_name = (
            configured_checkpoint
            if configured_checkpoint and configured_checkpoint != "CHANGE_ME.safetensors"
            else image_profile.checkpoint_name
        )

        config = cls(
            model=selected_model,
            host=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            data_dir=os.getenv("DATA_DIR", "./data"),
            provider=provider,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=openai_model,
            generate_images=_env_bool("GENERATE_IMAGES", False),
            generate_turnaround=_env_bool("GENERATE_TURNAROUND", True),
            comfyui_url=os.getenv("COMFYUI_URL", "http://127.0.0.1:8188"),
            comfyui_model_profile=model_profile_id,
            comfyui_model_profiles_path=model_profiles_path,
            comfyui_workflow_path=os.getenv("COMFYUI_WORKFLOW_PATH")
            or image_profile.workflow_path,
            comfyui_checkpoint_name=checkpoint_name,
            comfyui_timeout_seconds=_env_float("COMFYUI_TIMEOUT_SECONDS", image_profile.timeout_seconds),
            comfyui_poll_interval_seconds=_env_float(
                "COMFYUI_POLL_INTERVAL_SECONDS", 1.0
            ),
            comfyui_width=_env_int("COMFYUI_WIDTH", image_profile.width),
            comfyui_height=_env_int("COMFYUI_HEIGHT", image_profile.height),
            comfyui_steps=_env_int("COMFYUI_STEPS", image_profile.steps),
            comfyui_cfg=_env_float("COMFYUI_CFG", image_profile.cfg),
            comfyui_sampler=os.getenv("COMFYUI_SAMPLER", image_profile.sampler),
            comfyui_scheduler=os.getenv(
                "COMFYUI_SCHEDULER", image_profile.scheduler
            ),
            comfyui_clip_skip=image_profile.clip_skip,
            memory_guard_enabled=_env_bool("MEMORY_GUARD_ENABLED", True),
            minimum_available_memory_percent=_env_float(
                "MINIMUM_AVAILABLE_MEMORY_PERCENT", 15.0
            ),
            comfyui_negative_prompt=os.getenv(
                "COMFYUI_NEGATIVE_PROMPT",
                image_profile.negative_prompt,
            ),
        )

        if config.comfyui_timeout_seconds <= 0:
            raise ValueError("COMFYUI_TIMEOUT_SECONDS must be greater than 0")
        if config.comfyui_poll_interval_seconds <= 0:
            raise ValueError("COMFYUI_POLL_INTERVAL_SECONDS must be greater than 0")
        if config.comfyui_width <= 0 or config.comfyui_height <= 0:
            raise ValueError("COMFYUI_WIDTH and COMFYUI_HEIGHT must be greater than 0")
        if config.num_iterations < 1:
            raise ValueError("num_iterations must be greater than 0")
        if config.minimum_available_memory_percent < 0 or config.minimum_available_memory_percent > 100:
            raise ValueError(
                "MINIMUM_AVAILABLE_MEMORY_PERCENT must be between 0 and 100"
            )
        return config


# =============================================================================
# LLM providers
# =============================================================================


class OllamaInference:
    """localhost上のOllamaを使うテキスト生成クライアント。"""

    SYSTEM_PROMPT = (
        "人間の仕事を助ける優秀なAIアシスタントとして、"
        "指示に従い、必要な情報のみを端的に出力します。"
    )

    def __init__(self, config: Config, client: Any = None):
        self.config = config
        self.client = client or ollama.Client(host=config.host, timeout=120)
        self._ensure_model_available()

    @staticmethod
    def _model_name(model_info: Any) -> str:
        if hasattr(model_info, "model"):
            return str(model_info.model)
        if isinstance(model_info, dict):
            return str(model_info.get("name") or model_info.get("model") or "")
        return str(getattr(model_info, "name", ""))

    def _ensure_model_available(self) -> None:
        """モデルを確認する。モデルの自動pullは行わない。"""
        try:
            models = self.client.list()
        except Exception as exc:
            raise OllamaConnectionError(
                f"Ollama server is not available at {self.config.host}. "
                "Start it with: ollama serve"
            ) from exc

        if isinstance(models, dict):
            model_list = models.get("models", [])
        else:
            model_list = getattr(models, "models", [])
        available = {self._model_name(item) for item in model_list}
        requested = self.config.model
        matches = requested in available or f"{requested}:latest" in available
        if not matches:
            raise OllamaModelNotFoundError(
                f"Model '{requested}' is not installed. "
                f"Run: ollama pull {requested}"
            )

    @staticmethod
    def _response_content(response: Any) -> str:
        if isinstance(response, dict):
            message = response.get("message", {})
            if isinstance(message, dict):
                return str(message.get("content", "")).strip()
            return str(getattr(message, "content", "")).strip()

        message = getattr(response, "message", None)
        return str(getattr(message, "content", "")).strip()

    def generate(self, prompt: str, max_retries: int = 3) -> str:
        """指数バックオフ付きでテキストを生成する。"""
        for attempt in range(max_retries):
            try:
                response = self.client.chat(
                    model=self.config.model,
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    options={
                        "num_predict": 2048,
                        "temperature": 0.8,
                    },
                )
                content = self._response_content(response)
                if not content:
                    raise RuntimeError("Ollama returned an empty response")
                return content
            except Exception:
                if attempt == max_retries - 1:
                    raise
                delay = 2**attempt
                print(
                    f"Retry {attempt + 1}/{max_retries} after Ollama error; "
                    f"waiting {delay}s...",
                    file=sys.stderr,
                )
                time.sleep(delay)

        raise RuntimeError("Unreachable")


class OpenAIInference:
    """明示指定時だけ利用するOpenAI Responses APIクライアント。"""

    SYSTEM_PROMPT = OllamaInference.SYSTEM_PROMPT

    def __init__(self, config: Config, client: Any = None):
        if not config.openai_api_key:
            raise ProviderConfigError(
                "OPENAI_API_KEY is required when LLM_PROVIDER=openai or "
                "--provider openai is used."
            )

        if client is not None:
            self.client = client
        else:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ProviderConfigError(
                    "OpenAI provider requires the optional dependency. "
                    "Install it with: pip install -r requirements-cloud.txt"
                ) from exc
            self.client = OpenAI(api_key=config.openai_api_key)

        self.config = config

    def generate(self, prompt: str, max_retries: int = 3) -> str:
        for attempt in range(max_retries):
            try:
                response = self.client.responses.create(
                    model=self.config.model,
                    instructions=self.SYSTEM_PROMPT,
                    input=prompt,
                    max_output_tokens=2048,
                    temperature=0.8,
                    store=False,
                )
                content = str(getattr(response, "output_text", "")).strip()
                if not content:
                    raise RuntimeError("OpenAI returned an empty response")
                return content
            except Exception:
                if attempt == max_retries - 1:
                    raise
                delay = 2**attempt
                print(
                    f"Retry {attempt + 1}/{max_retries} after OpenAI error; "
                    f"waiting {delay}s...",
                    file=sys.stderr,
                )
                time.sleep(delay)

        raise RuntimeError("Unreachable")


def create_text_generator(config: Config) -> TextGenerator:
    if config.provider == "ollama":
        return OllamaInference(config)
    if config.provider == "openai":
        return OpenAIInference(config)
    raise ProviderConfigError(
        f"Unsupported provider: {config.provider}. Use ollama or openai."
    )


# =============================================================================
# Local CSV storage
# =============================================================================


PROJECT_SEEDS_DIR = Path(__file__).resolve().parent / "config" / "seeds"


class LocalStorage:
    """ローカルCSVストレージ。Google Sheetsは使用しない。"""

    SEED_HEADERS = {
        "age": "age",
        "gender": "gender",
        "species": "species",
        "ability": "ability",
        "wants": "wants",
        "role": "role",
    }
    OUTPUT_HEADERS = [
        "name",
        "profile",
        "catchphrase",
        "image_prompt",
        "concept",
        "age",
        "gender",
        "species",
        "ability",
        "wants",
        "role",
        "image_path",
        "image_seed",
        "turnaround_path",
        "height_cm",
        "character_dir",
    ]

    DEFAULT_SEEDS = {
        "age": [
            "Child",
            "Teenager",
            "Young Adult",
            "Middle-aged",
            "Elder",
            "Ageless",
        ],
        "gender": [
            "Male",
            "Female",
            "Non-binary",
            "Genderfluid",
            "Androgynous",
        ],
        "species": [
            "Human",
            "Elf",
            "Dwarf",
            "Demon",
            "Angel",
            "Dragon-kin",
            "Beastfolk",
            "Undead",
            "Cyborg",
            "Alien",
        ],
        "ability": [
            "Can manipulate fire at will",
            "Has the power to read minds",
            "Possesses superhuman strength",
            "Can turn invisible",
            "Has the ability to heal others",
            "Can control time for brief moments",
            "Possesses perfect memory",
            "Can communicate with animals",
        ],
        "wants": [
            "I want to find my lost family",
            "I want to become the strongest warrior",
            "I want to discover the truth about my past",
            "I want to protect the innocent",
            "I want to achieve immortality",
            "I want to find true love",
            "I want to conquer the world",
            "I want to bring peace to all nations",
        ],
        "role": [
            "Warrior. A skilled fighter dedicated to protecting others",
            "Mage. A wielder of arcane arts seeking forbidden knowledge",
            "Healer. A compassionate soul devoted to saving lives",
            "Assassin. A shadow operative with deadly precision",
            "Scholar. A seeker of ancient wisdom and lost lore",
            "Merchant. A cunning trader with connections everywhere",
            "Noble. A person of high birth with political influence",
            "Wanderer. A mysterious traveler with no fixed home",
        ],
    }

    def __init__(self, config: Config):
        self.data_dir = Path(config.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.seed_files = {
            key: self.data_dir / f"seed_{key}.csv" for key in self.SEED_HEADERS
        }

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.run_dir = self.data_dir / f"run_{timestamp}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.characters_dir = self.run_dir / "characters"
        self.output_file = self.run_dir / "output.csv"
        self.errors_file = self.run_dir / "errors.jsonl"

        self._ensure_seed_data()
        self._seed_values: Dict[str, List[str]] = {
            key: self._read_seed_values(path)
            for key, path in self.seed_files.items()
        }
        self._init_output_file()

    def _ensure_seed_data(self) -> None:
        for key, seed_file in self.seed_files.items():
            if seed_file.exists():
                continue
            bundled = PROJECT_SEEDS_DIR / f"seed_{key}.csv"
            if bundled.exists():
                seed_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(bundled, seed_file)
                continue
            with open(seed_file, "w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow([self.SEED_HEADERS[key]])
                writer.writerows([[value] for value in self.DEFAULT_SEEDS[key]])

    def _read_seed_values(self, seed_file: Path) -> List[str]:
        key = seed_file.stem.removeprefix("seed_")
        expected_header = self.SEED_HEADERS.get(key)
        if expected_header is None:
            raise SeedDataError(f"Unknown seed file: {seed_file}")

        try:
            with open(seed_file, "r", newline="", encoding="utf-8-sig") as file:
                rows = list(csv.reader(file))
        except OSError as exc:
            raise SeedDataError(f"Cannot read seed file: {seed_file}: {exc}") from exc

        if not rows:
            raise SeedDataError(
                f"Seed file is empty: {seed_file}. "
                f"The first row must be '{expected_header}'."
            )

        if rows[0] != [expected_header]:
            raise SeedDataError(
                f"Invalid header in {seed_file}: expected one column named "
                f"'{expected_header}'."
            )

        values: List[str] = []
        for row_number, row in enumerate(rows[1:], start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != 1:
                raise SeedDataError(
                    f"Invalid row {row_number} in {seed_file}: "
                    "each data row must contain exactly one column."
                )
            value = row[0].strip()
            if value:
                values.append(value)

        if not values:
            raise SeedDataError(
                f"Seed file has no usable values: {seed_file}. "
                "Add at least one non-empty value below the header."
            )
        return values

    def get_random_attribute(self, attr_type: str) -> str:
        if attr_type not in self._seed_values:
            raise SeedDataError(f"Unknown seed attribute: {attr_type}")
        values = self._seed_values[attr_type]
        if not values:
            raise SeedDataError(f"Seed attribute has no usable values: {attr_type}")
        return random.choice(values)

    def _init_output_file(self) -> None:
        with open(self.output_file, "w", newline="", encoding="utf-8") as file:
            csv.writer(file).writerow(self.OUTPUT_HEADERS)

    def append_output(self, row: List[Any]) -> None:
        if len(row) != len(self.OUTPUT_HEADERS):
            raise ValueError(
                f"Output row has {len(row)} columns; "
                f"expected {len(self.OUTPUT_HEADERS)}."
            )
        with open(self.output_file, "a", newline="", encoding="utf-8") as file:
            csv.writer(file).writerow(row)

    def append_seed(self, attr_type: str, value: str) -> None:
        if attr_type not in self.seed_files:
            raise SeedDataError(f"Unknown seed attribute: {attr_type}")
        normalized = " ".join(value.replace("**", "").split()).strip(" \"'")
        if not normalized:
            raise SeedDataError(
                f"Cannot append an empty value to seed_{attr_type}.csv"
            )
        with open(self.seed_files[attr_type], "a", newline="", encoding="utf-8") as file:
            csv.writer(file).writerow([normalized])
        self._seed_values[attr_type].append(normalized)

    def commit_character(
        self,
        row: List[Any],
        seed_updates: Dict[str, str],
        character_data: Optional[Dict[str, Any]] = None,
        temporary_dir: Optional[Path] = None,
    ) -> None:
        """生成済みの1キャラクターを保存する。生成途中では呼び出さない。"""
        rollback_targets = [
            (self.output_file, self.output_file.stat().st_size),
            *(
                (self.seed_files[attr_type], self.seed_files[attr_type].stat().st_size)
                for attr_type in ("ability", "wants", "role")
            ),
        ]
        seed_lengths = {
            attr_type: len(self._seed_values[attr_type])
            for attr_type in ("ability", "wants", "role")
        }

        character_id = (
            str(character_data["id"])
            if character_data is not None
            else (str(row[-1]).split("/")[-1] or "legacy_character")
        )
        final_dir = self.characters_dir / character_id
        temp_dir = Path(temporary_dir) if temporary_dir is not None else (
            self.characters_dir / f".tmp_{character_id}"
        )

        try:
            self.characters_dir.mkdir(parents=True, exist_ok=True)
            temp_dir.mkdir(parents=True, exist_ok=True)
            if character_data is not None:
                self._write_character_files(temp_dir, character_data)
            temp_dir.replace(final_dir)
            self.append_output(row)
            for attr_type in ("ability", "wants", "role"):
                self.append_seed(attr_type, seed_updates[attr_type])
        except BaseException as original_error:
            # BaseException: Ctrl+C during the write must not leave partial rows.
            rollback_failures = []
            for path, size in rollback_targets:
                try:
                    with path.open("r+b") as file:
                        file.truncate(size)
                except Exception:
                    rollback_failures.append(str(path))

            for attr_type, length in seed_lengths.items():
                del self._seed_values[attr_type][length:]

            cleanup_failures = []
            for path in (temp_dir, final_dir):
                try:
                    if path.exists():
                        shutil.rmtree(path)
                except BaseException:
                    cleanup_failures.append(str(path))

            if rollback_failures or cleanup_failures:
                paths = ", ".join(rollback_failures + cleanup_failures)
                raise RuntimeError(
                    f"Commit rollback failed; manually check: {paths}"
                ) from original_error
            raise

    def _write_character_files(
        self, directory: Path, character_data: Dict[str, Any]
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "character.json").open("w", encoding="utf-8") as file:
            json.dump(character_data, file, ensure_ascii=False, indent=2)
            file.write("\n")

        images = character_data["images"]
        full_body = images.get("full_body") if images is not None else None
        turnaround = images.get("turnaround") if images is not None else None
        lines = [f"# {character_data['name']}", ""]
        if full_body is not None:
            lines.extend([f"![{character_data['name']}](full_body.png)", ""])
        if turnaround is not None:
            lines.extend(
                [
                    "## 3面図",
                    "",
                    f"![{character_data['name']} 3面図](turnaround.png)",
                    "",
                ]
            )
        lines.extend(
            [
                f"> {character_data['catchphrase']}",
                "",
                "## プロフィール",
                character_data["profile"],
                "",
                "## 設定",
                f"- 身長: {character_data['height_cm']}cm",
                f"- 年齢: {character_data['attributes']['age']}",
                f"- 性別: {character_data['attributes']['gender']}",
                f"- 種族: {character_data['attributes']['species']}",
                f"- 役割: {character_data['attributes']['role']}",
                f"- 能力: {character_data['attributes']['ability']}",
                f"- 願い: {character_data['attributes']['wants']}",
                "",
                "## コンセプト",
                character_data["concept"],
                "",
                "## 生成条件",
            ]
        )
        if full_body is not None:
            lines.extend(
                [
                    f"- 画像モデル: {full_body['model_profile']}（seed {full_body['seed']}）",
                    f"- LLM: {character_data['generation']['llm_provider']} / "
                    f"{character_data['generation']['llm_model']}",
                    f"- 画像プロンプト: {full_body['prompt']}",
                ]
            )
        else:
            lines.append(
                f"- LLM: {character_data['generation']['llm_provider']} / "
                f"{character_data['generation']['llm_model']}"
            )
        if turnaround is not None:
            lines.append(f"- 3面図 seed: {turnaround['seed']}")
        (directory / "character.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def record_error(
        self,
        iteration: int,
        stage: str,
        error: Exception,
    ) -> None:
        payload = {
            "iteration": iteration,
            "stage": stage,
            "error_type": type(error).__name__,
            "message": str(error),
            "timestamp": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        with open(self.errors_file, "a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def relative_path(self, path: Path) -> str:
        return path.relative_to(self.data_dir).as_posix()

    def relative_run_path(self, path: Path) -> str:
        return path.relative_to(self.run_dir).as_posix()

    def character_paths(self, iteration: int, name: str) -> tuple[Path, Path]:
        character_id = f"{iteration:03d}_{_safe_filename(name)}"
        return (
            self.characters_dir / f".tmp_{character_id}",
            self.characters_dir / character_id,
        )


# =============================================================================
# Prompts
# =============================================================================


class Prompts:
    """キャラクター生成用プロンプト。"""

    TURNAROUND = (
        "Character turnaround reference sheet of the character in image 1. The same "
        "character is shown three times side by side in a T-pose with both arms "
        "stretched straight out horizontally: front view on the left, side view in "
        "the middle, back view on the right. Each view is full body from head to "
        "feet. The face, hairstyle, outfit, colors, materials and accessories are "
        "identical to image 1, with the same body proportions and the same height in "
        "all three views. Plain white background, clean anime illustration style, "
        "evenly spaced, no text, no labels, no watermark."
    )

    @staticmethod
    def character_concept(physical: str, role: str, ability: str, wants: str) -> str:
        return f"""以下のキャラクター属性を1人の人物として統合し、英文1段落で描写してください。

## 属性
- 身体的特徴: {physical}
- 役割: {role}
- 能力: {ability}
- 願望: {wants}

## ルール
- 英語で出力
- 1段落のみ（3〜4文）
- 属性に含まれる固有の言葉や具体的な描写は省略せずに残す
- 属性にない出来事・場所・過去・人間関係・心情は加えない
- "hero", "destiny", "darkness", "protect the innocent" のような決まり文句を使わない
- 説明や補足は不要

## 出力"""

    @staticmethod
    def name(concept: str) -> str:
        return f"""以下のキャラクター設定にふさわしい人名を1つ生成してください。

## キャラクター設定
{concept}

## ルール
- 名前のみを出力（説明不要）
- 英語表記
- 国籍・文化・架空言語の名前も可
- 1行のみ

## 出力例
Kain Astralion
Yuichi Aihara

## 出力"""

    @staticmethod
    def profile(concept: str) -> str:
        return f"""以下のキャラクター設定をもとに、この人物のプロフィールを日本語で書いてください。

## キャラクター設定
{concept}

## ルール
- 日本語で出力
- 性別不明・Theyの場合は「彼は」を使用
- 1段落のみ（3〜4文）
- 書くのは人物像だけ: 年齢・性別・種族、役割、能力、願望
- 役割・能力・願望は、設定に含まれる固有の言葉や条件を省略せず、具体的に書く
- 設定にない出来事・場所・過去・人間関係・今していること・考えていること・心情は書かない
- 「運命」「闇」「守るべきもの」「強い意志」のような決まり文句を使わない
- 英語をそのまま残さず、自然な日本語にする（役割名などの固有の名称はカタカナ表記にしてよい）

## 出力例
彼はプリティーンのノンバイナリー半人半神で、役割はデジタル栄養コンサルタント。人の頭に詰め込まれた情報を献立のように仕分け、一日に摂ってよい量を処方する。能力は、写真の中に入り込み、雨の日に撮られた写真に限って細部を書き換えられること。願望は、祖母が最後に残したレシピを、この冬に港が閉ざされる前に祖母の初恋の相手へ届けることだ。

## 出力"""

    @staticmethod
    def catchphrase(concept: str) -> str:
        return f"""以下のキャラクターの意思を表す印象的な決め台詞を生成してください。

## キャラクター設定
{concept}

## ルール
- 日本語で出力
- キャラクターにふさわしい口調
- 一人称から始める
- 1文のみ

## 出力例
私は、歴史の断片を手に取り、宇宙の隅々に宿る感情を感じ取るよ。

## 出力"""

    @staticmethod
    def new_ability(concept: str) -> str:
        return f"""以下のキャラクターと対になるキャラクターが持つ特殊能力を1つ生成してください。

## 元キャラクター
{concept}

## ルール
- 英語で出力
- 能力名と説明を1文で
- 1つのみ
- 元キャラクターと同じ能力や、よくある超能力（火・透明化・怪力・読心・治癒など）は避け、意外な能力にする
- 条件や制約は1つまで。30語以内
- 元キャラクターの名前・役割・持ち物を参照せず、単独で意味が通る文にする
- 記号や装飾（** など）、改行を使わない

## 出力例
Can walk into photographs and change small details, but only in pictures taken on rainy days.

## 出力"""

    @staticmethod
    def new_wants(concept: str) -> str:
        return f"""以下のキャラクターと対になるキャラクターの切実な願望を1つ生成してください。

## 元キャラクター
{concept}

## ルール
- 英語で出力
- "I want to..." の形式
- 1文のみ
- 「守りたい」「平和」「最強になりたい」「真実の愛」のような漠然とした願望は避け、具体的な相手・場所・物のどれか1つを含める
- 日付・年・実在の地名は入れない。25語以内
- 元キャラクターの名前・役割・持ち物を参照せず、単独で意味が通る文にする
- 記号や装飾（** など）、改行を使わない

## 出力例
I want to deliver my grandmother's last recipe to her first love.

## 出力"""

    @staticmethod
    def new_role(concept: str) -> str:
        return f"""以下のキャラクターと対になるキャラクターのユニークな役割を1つ生成してください。

## 元キャラクター
{concept}

## ルール
- 英語で出力
- 役割名と説明
- 1つのみ
- 戦士・魔法使い・治癒師・暗殺者のような定番の職業は避け、現代や近未来の職業と幻想を掛け合わせた具体的な役割にする
- 「役割名. 説明1文」の形で、全体で20語以内
- 元キャラクターの名前・役割・持ち物を参照せず、単独で意味が通る文にする
- 記号や装飾（** など）、改行を使わない

## 出力例
Umbrella Archivist. Catalogs every umbrella left behind on the city's trains.

## 出力"""

    @staticmethod
    def height(concept: str, age: str, species: str) -> str:
        return f"""以下のキャラクターの身長を、コンセプト・年齢・種族から判断して決めてください。

## キャラクター設定
{concept}
- 年齢: {age}
- 種族: {species}

## ルール
- 身長をセンチメートル単位の整数だけで出力
- 数字以外の説明、単位、文章は不要
- 例: 142

## 出力"""


# =============================================================================
# Image prompt and generation orchestration
# =============================================================================


def generate_image_prompt(
    concept: str,
    profile_id: str = "generic-sdxl",
    age: str = "",
    gender: str = "",
    species: str = "",
    ability: str = "",
    role: str = "",
    profiles_path: str = "./config/comfyui/model_profiles.json",
) -> str:
    """画像モデルのプロファイルに合わせてpromptを作る。"""
    profile = get_image_model_profile(profile_id, Path(profiles_path))
    return build_image_prompt(
        profile,
        concept=concept,
        age=age,
        gender=gender,
        species=species,
        ability=ability,
        role=role,
    )


def _stage(stage: str, function: Callable[[], Any]) -> Any:
    try:
        return function()
    except StageError:
        raise
    except Exception as exc:
        raise StageError(stage, exc) from exc


def _parse_height(response: str) -> int:
    match = re.search(r"\d+", str(response))
    if match is None:
        raise ValueError("Height response did not contain an integer")
    height = int(match.group(0))
    if not 10 <= height <= 2000:
        raise ValueError(f"Height must be between 10 and 2000 cm: {height}")
    return height


def _generate_height(llm: TextGenerator, concept: str, age: str, species: str) -> int:
    prompt = Prompts.height(concept, age, species)
    last_error: Optional[Exception] = None
    for _ in range(3):
        try:
            return _parse_height(llm.generate(prompt))
        except ValueError as exc:
            last_error = exc
    raise ValueError(f"Could not obtain a valid height after 3 attempts: {last_error}")


def _workflow_uses_negative_prompt(workflow_path: str) -> bool:
    from comfyui_image_gen import SDXL_INJECTIONS, WORKFLOW_CONFIGS

    config = WORKFLOW_CONFIGS.get(
        Path(workflow_path).name, {"injections": SDXL_INJECTIONS}
    )
    return "negative_prompt" in config["injections"]


def _safe_filename(value: str) -> str:
    normalized = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in value.strip()
    )
    return normalized.strip("._") or "character"


def _create_image_generator(config: Config) -> Any:
    from comfyui_image_gen import ComfyUIImageGenerator

    profile = get_image_model_profile(
        config.comfyui_model_profile,
        Path(config.comfyui_model_profiles_path),
    )
    return ComfyUIImageGenerator(
        base_url=config.comfyui_url,
        workflow_path=Path(config.comfyui_workflow_path),
        checkpoint_name=config.comfyui_checkpoint_name,
        timeout_seconds=config.comfyui_timeout_seconds,
        poll_interval_seconds=config.comfyui_poll_interval_seconds,
        width=config.comfyui_width,
        height=config.comfyui_height,
        steps=config.comfyui_steps,
        cfg=config.comfyui_cfg,
        sampler=config.comfyui_sampler,
        scheduler=config.comfyui_scheduler,
        negative_prompt=config.comfyui_negative_prompt,
        clip_skip=config.comfyui_clip_skip,
        model_files=profile.model_files,
        turnaround_workflow_path=(
            Path(profile.turnaround_workflow_path)
            if profile.turnaround_workflow_path
            else None
        ),
        turnaround_width=profile.turnaround_width,
        turnaround_height=profile.turnaround_height,
        turnaround_timeout_seconds=profile.turnaround_timeout_seconds,
    )


def _turnaround_profile(config: Config) -> Any:
    return get_image_model_profile(
        config.comfyui_model_profile,
        Path(config.comfyui_model_profiles_path),
    )


def generate_characters(
    config: Config,
    text_generator: Optional[TextGenerator] = None,
    storage: Optional[LocalStorage] = None,
    image_generator: Any = None,
) -> Path:
    """指定回数のキャラクターを生成し、output.csvのパスを返す。"""
    llm = text_generator or create_text_generator(config)
    local_storage = storage or LocalStorage(config)

    turnaround_supported = bool(
        config.generate_images
        and config.generate_turnaround
        and _turnaround_profile(config).turnaround_workflow_path
    )
    if config.generate_images:
        image_generator = image_generator or _create_image_generator(config)
        _stage("image", image_generator.check_connection)
        if config.generate_turnaround and not turnaround_supported:
            print("このモデルは3面図に対応していません")

    print(f"Starting generation with provider: {config.provider}")
    print(f"Model: {config.model}")
    print(f"Iterations: {config.num_iterations}")
    print(f"Data directory: {config.data_dir}")
    print(f"Run directory: {local_storage.run_dir}")
    print(f"Output file: {local_storage.output_file}")

    for index in range(config.num_iterations):
        iteration = index + 1
        print(f"\n[{iteration}/{config.num_iterations}] Generating character...")
        temporary_dir: Optional[Path] = None
        try:
            age = local_storage.get_random_attribute("age")
            gender = local_storage.get_random_attribute("gender")
            species = local_storage.get_random_attribute("species")
            physical = f"{age} {gender} {species}"
            ability = local_storage.get_random_attribute("ability")
            wants = local_storage.get_random_attribute("wants")
            role = local_storage.get_random_attribute("role")

            concept = _stage(
                "character_concept",
                lambda: llm.generate(
                    Prompts.character_concept(physical, role, ability, wants)
                ),
            )
            name = _stage("name", lambda: llm.generate(Prompts.name(concept)))
            profile = _stage(
                "profile", lambda: llm.generate(Prompts.profile(concept))
            )
            catchphrase = _stage(
                "catchphrase", lambda: llm.generate(Prompts.catchphrase(concept))
            )
            image_prompt = generate_image_prompt(
                concept,
                profile_id=config.comfyui_model_profile,
                age=age,
                gender=gender,
                species=species,
                ability=ability,
                role=role,
                profiles_path=config.comfyui_model_profiles_path,
            )

            new_ability = _stage(
                "new_ability", lambda: llm.generate(Prompts.new_ability(concept))
            )
            new_wants = _stage(
                "new_wants", lambda: llm.generate(Prompts.new_wants(concept))
            )
            new_role = _stage(
                "new_role", lambda: llm.generate(Prompts.new_role(concept))
            )
            height_cm = _stage(
                "height", lambda: _generate_height(llm, concept, age, species)
            )

            character_id = f"{iteration:03d}_{_safe_filename(name)}"
            temporary_dir, final_character_dir = local_storage.character_paths(
                iteration, name
            )
            local_storage.characters_dir.mkdir(parents=True, exist_ok=True)
            temporary_dir.mkdir(parents=True, exist_ok=True)
            image_path = ""
            image_seed = ""
            turnaround_path = ""
            images_data: Optional[Dict[str, Any]] = None
            if config.generate_images:
                raw_dir = temporary_dir / "raw"
                raw_dir.mkdir(parents=True, exist_ok=True)

                def move_generated(result: Any, destination: Path) -> Any:
                    generated_path = Path(result.path)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if generated_path != destination:
                        generated_path.replace(destination)
                    return result

                def generate_image() -> Any:
                    if config.memory_guard_enabled:
                        try:
                            memory = ensure_available(
                                config.minimum_available_memory_percent
                            )
                        except MemoryBudgetExceeded as exc:
                            raise ComfyUIError(str(exc)) from exc
                        if memory.available_percent is not None:
                            print(
                                "  Available memory before image: "
                                f"{memory.available_percent:.1f}%"
                            )
                    result = image_generator.generate(
                        image_prompt,
                        raw_dir,
                        "full_body",
                    )
                    return move_generated(result, raw_dir / "full_body.png")

                image_result = _stage(
                    "image",
                    generate_image,
                )
                labeled_full_body = temporary_dir / "full_body.png"
                _stage(
                    "label",
                    lambda: label_image(
                        raw_dir / "full_body.png",
                        labeled_full_body,
                        name,
                        height_cm,
                    ),
                )
                image_path = local_storage.relative_run_path(final_character_dir / "full_body.png")
                image_seed = str(image_result.seed)
                full_body_data = {
                    "file": "full_body.png",
                    "raw_file": "raw/full_body.png",
                    "prompt": image_prompt,
                    "negative_prompt": (
                        config.comfyui_negative_prompt
                        if _workflow_uses_negative_prompt(config.comfyui_workflow_path)
                        else None
                    ),
                    "seed": image_result.seed,
                    "model_profile": config.comfyui_model_profile,
                    "width": config.comfyui_width,
                    "height": config.comfyui_height,
                }
                turnaround_data = None
                if turnaround_supported:
                    def generate_turnaround() -> Any:
                        result = image_generator.generate_turnaround(
                            Prompts.TURNAROUND,
                            raw_dir / "full_body.png",
                            raw_dir,
                            "turnaround",
                        )
                        return move_generated(result, raw_dir / "turnaround.png")

                    turnaround_result = _stage("turnaround", generate_turnaround)
                    labeled_turnaround = temporary_dir / "turnaround.png"
                    _stage(
                        "label",
                        lambda: label_image(
                            raw_dir / "turnaround.png",
                            labeled_turnaround,
                            name,
                            height_cm,
                        ),
                    )
                    turnaround_path = local_storage.relative_run_path(
                        final_character_dir / "turnaround.png"
                    )
                    turnaround_data = {
                        "file": "turnaround.png",
                        "raw_file": "raw/turnaround.png",
                        "prompt": Prompts.TURNAROUND,
                        "seed": turnaround_result.seed,
                        "model_profile": config.comfyui_model_profile,
                        "width": _turnaround_profile(config).turnaround_width,
                        "height": _turnaround_profile(config).turnaround_height,
                    }
                images_data = {"full_body": full_body_data, "turnaround": turnaround_data}

            character_data = {
                "schema_version": 2,
                "id": character_id,
                "iteration": iteration,
                "created_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "name": name,
                "height_cm": height_cm,
                "profile": profile,
                "catchphrase": catchphrase,
                "concept": concept,
                "attributes": {
                    "age": age,
                    "gender": gender,
                    "species": species,
                    "ability": ability,
                    "wants": wants,
                    "role": role,
                },
                "new_seeds": {
                    "ability": new_ability,
                    "wants": new_wants,
                    "role": new_role,
                },
                "images": images_data,
                "generation": {
                    "llm_provider": config.provider,
                    "llm_model": config.model,
                },
            }

            row = [
                name,
                profile,
                catchphrase,
                image_prompt,
                concept,
                age,
                gender,
                species,
                ability,
                wants,
                role,
                image_path,
                image_seed,
                turnaround_path,
                height_cm,
                local_storage.relative_run_path(final_character_dir),
            ]
            _stage(
                "persistence",
                lambda: local_storage.commit_character(
                    row,
                    {
                        "ability": new_ability,
                        "wants": new_wants,
                        "role": new_role,
                    },
                    character_data=character_data,
                    temporary_dir=temporary_dir,
                ),
            )
            print(f"  Name: {name}")
        except StageError as exc:
            local_storage.record_error(iteration, exc.stage, exc.cause)
            print(
                f"ERROR iteration={iteration} stage={exc.stage}: {exc.cause}\n"
                f"Results kept at: {local_storage.run_dir}",
                file=sys.stderr,
            )
            raise
        except Exception as exc:
            local_storage.record_error(iteration, "input", exc)
            print(
                f"ERROR iteration={iteration} stage=input: {exc}\n"
                f"Results kept at: {local_storage.run_dir}",
                file=sys.stderr,
            )
            raise StageError("input", exc) from exc
        finally:
            if temporary_dir is not None and temporary_dir.exists():
                shutil.rmtree(temporary_dir, ignore_errors=True)

    print("\n処理が完了しました。")
    print(f"実行ディレクトリ: {local_storage.run_dir}")
    print(f"出力ファイル: {local_storage.output_file}")
    return local_storage.output_file


def main(
    iterations: Optional[int] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    generate_images: Optional[bool] = None,
    generate_turnaround: Optional[bool] = None,
) -> int:
    try:
        config = Config.from_env()
        overrides: Dict[str, Any] = {}
        if iterations is not None:
            overrides["num_iterations"] = iterations
        if model is not None:
            overrides["model"] = model
        if provider is not None:
            overrides["provider"] = provider
            if provider == "openai" and model is None:
                overrides["model"] = config.openai_model
            if provider == "ollama" and model is None:
                overrides["model"] = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
        if generate_images is not None:
            overrides["generate_images"] = generate_images
        if generate_turnaround is not None:
            overrides["generate_turnaround"] = generate_turnaround
        config = replace(config, **overrides)
        if config.num_iterations < 1:
            raise ValueError("iterations must be greater than 0")

        generate_characters(config)
        return 0
    except (
        OllamaConnectionError,
        OllamaModelNotFoundError,
        ProviderConfigError,
        SeedDataError,
        ComfyUIError,
        StageError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="100 Times AI Heroes - local character generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
既定の実行:
  python ollama_hero_gen.py --iterations 1

ローカル画像生成:
  python ollama_hero_gen.py --iterations 1 --generate-images

任意のクラウドLLM:
  python ollama_hero_gen.py --provider openai --iterations 1
""",
    )
    parser.add_argument(
        "--iterations", "-n", type=int, default=None, help="生成するキャラクター数"
    )
    parser.add_argument(
        "--model",
        "-m",
        type=str,
        default=None,
        metavar="MODEL",
        help="選択したLLMプロバイダーで使うモデル名",
    )
    parser.add_argument(
        "--provider",
        choices=["ollama", "openai"],
        default=None,
        help="LLMプロバイダー（既定: .envのLLM_PROVIDER、未設定時ollama）",
    )
    parser.add_argument(
        "--generate-images",
        action="store_true",
        help="localhost上のComfyUIで画像も生成する",
    )
    parser.add_argument(
        "--no-turnaround",
        action="store_true",
        help="3面図を生成しない",
    )
    args = parser.parse_args()

    raise SystemExit(
        main(
            iterations=args.iterations,
            model=args.model,
            provider=args.provider,
            generate_images=True if args.generate_images else None,
            generate_turnaround=False if args.no_turnaround else None,
        )
    )
