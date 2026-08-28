# 作例

このディレクトリは、ローカルComfyUIで生成した作例を保存する場所です。

モデルファイルや個人データは置かず、選別した画像と生成条件だけを管理します。

## モデル比較作例

`model-comparison/` 以下に、モデルごとの作例を保存します。

```text
model-comparison/
├── animagine-xl-4.0-opt/
│   ├── human-warrior_101.png
│   ├── aquatic-designer_101.png
│   └── ...png
├── illustrious-xl-v2/
├── pony-v6-xl/
└── noobai-xl-1.1/
```

ファイル名は `<case_id>_<seed>.png` とします。比較時に生成される`report.json`には、使用したprofile、checkpoint、prompt、seed、解像度、steps、CFG、samplerが記録されます。

## 作例を作る

まずはComfyUIに比較対象のcheckpointを1つだけ配置し、次のように少量で実行します。

```bash
python tools/benchmark_image_models.py \
  --profiles animagine-xl-4.0-opt \
  --cases 1 \
  --seeds 101 \
  --output examples/model-comparison
```

比較時は同じcaseとseedを各モデルへ渡します。完成した作例をリポジトリへ追加する場合は、画像の品質とモデルの利用条件を確認してください。
