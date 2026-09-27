# 画像生成モデルのライセンス

このリポジトリはモデルファイルを含みません。`setup_local.py` が各モデルを取得元からダウンロードし、利用者の環境に置きます。モデルの利用条件は、各モデルのライセンスに従います。

- 確認日: 2026-09-27
- この文書は原典の要約です。法的な助言ではありません。判断に迷う場合は、必ず原典を読んでください
- ライセンスは更新されることがあります。利用前に原典を再確認してください

## 一覧

| profile | モデル | ライセンス（原典） | モデルの商用利用 | 生成物の扱い | 取得元 |
|---|---|---|---|---|---|
| `animagine-xl-4.0-opt`（既定） | Animagine XL 4.0 | [CreativeML Open RAIL++-M](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md) | 可（利用制限あり） | 権利を主張しない。使い方の責任は利用者 | [公式](https://huggingface.co/cagliostrolab/animagine-xl-4.0) |
| `illustrious-xl-v2` | Illustrious XL v2.0 | [CreativeML Open RAIL-M](https://huggingface.co/spaces/CompVis/stable-diffusion-license) | 可（利用制限あり） | 権利を主張しない。使い方の責任は利用者 | [公式](https://huggingface.co/OnomaAIResearch/Illustrious-XL-v2.0) |
| `pony-v6-xl` | Pony Diffusion V6 XL | Modified Fair AI Public License 1.0-SD（[原典: Civitai のモデルページ](https://civitai.com/models/257749)） | 要許可。収益化しているサイトやアプリでの推論は禁止 | 原典で確認が必要 | [非公式の再配布](https://huggingface.co/LyliaEngine/Pony_Diffusion_V6_XL) |
| `noobai-xl-1.1` | NoobAI XL 1.1 | [Fair AI Public License 1.0-SD](https://freedevproject.org/faipl-1.0-sd/) | 可（利用制限、派生物の公開条件あり） | ライセンスの対象外（権利を主張しない） | [公式](https://huggingface.co/Laxhar/noobai-XL-1.1) |
| `qwen-image-2.1`（実験） | Qwen-Image 2.1 | [Qwen Research License Agreement（2026-09-20）](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE) | 不可（別途契約が必要）。利用目的は研究・評価に限られる | 使い道を直接制限する条項はない | [公式](https://huggingface.co/Qwen/Qwen-Image-2.1)（ComfyUI 用の再パッケージは [Comfy-Org](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)） |

## モデルごとの要点

### Animagine XL 4.0 — CreativeML Open RAIL++-M

- モデルカードは、元になった Stable Diffusion XL のライセンスを「変更も追加の制限もなく」採用し、商用利用を「可」としている
- 生成物について、ライセンス提供者は権利を主張しない。生成物の使い方の責任は利用者にある
- 付属書 A の利用制限（違法行為、他者を害する用途、未成年者の搾取、差別、虚偽情報の生成など）に従う必要がある。再配布する場合は、ライセンスの写しを含め、変更点を明記する

### Illustrious XL v2.0 — CreativeML Open RAIL-M

- モデルカードのライセンス欄は `creativeml-openrail-m`。モデルカードに追加の条件は書かれていない
- 生成物と利用制限の考え方は、上の RAIL++-M と同じ（付属書 A の利用制限に従う）

### Pony Diffusion V6 XL — Modified Fair AI Public License 1.0-SD

- 作者は PurpleSmartAI。原典は [Civitai のモデルページ](https://civitai.com/models/257749)
- このリポジトリの取得元 `LyliaEngine/Pony_Diffusion_V6_XL` は **第三者による再配布** で、Hugging Face 上のライセンス欄（`cdla-permissive-2.0`）は原典と一致しない。原典のライセンスに従う
- 原典の主な条件:
  - 収益化しているウェブサイトやアプリケーションでの推論は禁止
  - 商用利用には作者の許可が必要（contact@purplesmart.ai）
  - CivitAI と Hugging Face には商用の推論が明示的に許可されている
- 生成物の扱いは、原典のページで確認が必要（改変前の Fair AI Public License 1.0-SD は生成物を対象外としているが、改変版での扱いはこの文書では確認できていない）

### NoobAI XL 1.1 — Fair AI Public License 1.0-SD

- 生成物はライセンスの対象外で、作者は権利を主張しない
- モデル本体には条件がある:
  - 派生モデルは、同じライセンスかそれと同等以上に許容的なライセンスで提供する（コピーレフト）
  - ネットワーク越しに提供する場合は、利用者にソースを提供する
  - 禁止用途（他者を害する虚偽情報の生成、未成年者の搾取、差別的なシステム、法的権利に影響する自動判断など）がある

### Qwen-Image 2.1 — Qwen Research License Agreement

原文の該当箇所:

- 第1条 i: 「"Non-Commercial" shall mean for research or evaluation purposes only.」（非商用とは研究または評価の目的に限る）
- 第2条 a: 使用・複製・改変などの許諾は「FOR NON-COMMERCIAL PURPOSES ONLY」（非商用目的に限る）
- 第2条 b: 商用目的での利用には、別途商用ライセンスが必要（model-business@notice.qwencloud.com）
- 第3条 c: 再配布する場合は、所定の著作権表示を含む Notice ファイルを添付する
- 第4条 b: モデルや生成物を使って AI モデルを作り公開する場合は、「Built with Qwen」または「Improved using Qwen」と表示する

注意点:

- 生成した画像の使い道を直接制限する条項はない。ただし、**モデルを使う目的そのもの** が研究または評価に限られる
- 生成物を販売しない使い方でも、それが「研究または評価」に当たるかは本文からは断定できない

## このプロジェクトでの扱い

- 生成物の販売は予定していない。用途はキャラクターのアイデアを出すこと（2026-09-27 時点の作者の方針）
- 既定モデルは Animagine XL 4.0。商用利用が可能で、利用目的の制限がないため
- Qwen-Image 2.1 は、比較評価のための実験候補として残す。アイデア出しへの常用が「研究または評価」の範囲に収まるかは作者の判断による
- 付属書 A などの利用制限を守るため、すべての profile の除外語に `nsfw, nude, nipples, child, loli, shota` を入れている（`config/comfyui/model_profiles.json`）
