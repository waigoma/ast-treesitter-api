# ast-treesitter-api

[Tree-sitter](https://tree-sitter.github.io/tree-sitter/) を使い、ソースコードを **JSON 構文木 (AST)** に変換する軽量 HTTP API サーバーです。
n8n などのワークフローエンジンや CI パイプラインから POST リクエスト 1 本で構文解析を呼び出せます。

## 対応言語 (22 種)

`bash` / `c` / `c_sharp` / `cpp` / `css` / `elixir` / `go` / `html` / `java` /
`javascript` / `json` / `kotlin` / `lua` / `php` / `python` / `ruby` / `rust` /
`scala` / `toml` / `tsx` / `typescript` / `yaml`

---

## Docker Compose で起動する

### ローカルビルドして起動

```bash
docker compose -f compose.yml -f compose.build.yml up --build -d
```

停止:

```bash
docker compose -f compose.yml -f compose.build.yml down
```

### GHCR イメージを使う

`compose.ghcr.yml` の `REPLACE_OWNER` を GitHub の owner 名に書き換えてください。

```yaml
# compose.ghcr.yml
image: ghcr.io/<owner>/ast-treesitter-api:latest
```

```bash
# Private の場合は先に認証
docker login ghcr.io

# 起動
docker compose -f compose.yml -f compose.ghcr.yml up -d
```

---

## API リファレンス

ベース URL: `http://127.0.0.1:8008`

### `GET /health`

サーバーの稼働確認。

```bash
curl -s http://127.0.0.1:8008/health
```

```json
{
  "status": "ok",
  "languages_loaded": 22,
  "max_tree_depth_default": 512,
  "max_nodes": 200000
}
```

### `GET /v1/languages`

利用可能な言語名の一覧。

```bash
curl -s http://127.0.0.1:8008/v1/languages
```

```json
{
  "object": "list",
  "data": ["bash", "c", "c_sharp", "cpp", "css", ...]
}
```

### `POST /v1/parse`

ソースコードを構文解析して JSON ツリーを返します。

#### リクエストボディ

| フィールド | 型 | 必須 | 既定値 | 説明 |
|---|---|:---:|---|---|
| `language` | string | ✓ | — | 言語名 (`python`, `javascript` 等) |
| `source` | string | ✓ | — | パースするソースコード |
| `include_text` | boolean | | `true` | 各ノードにソーステキストを含めるか |
| `max_depth` | integer | | `512` | JSON ツリーの最大深度 (1–4096) |
| `sexp` | boolean | | `false` | S 式文字列を追加返却するか |

#### Python の例

```bash
curl -s -X POST http://127.0.0.1:8008/v1/parse \
  -H "Content-Type: application/json" \
  -d '{
    "language": "python",
    "source": "def hello():\n    return 42",
    "sexp": true
  }'
```

#### レスポンス (抜粋)

```json
{
  "language": "python",
  "tree": {
    "root": {
      "type": "module",
      "start_byte": 0,
      "end_byte": 26,
      "start_point": { "row": 0, "column": 0 },
      "end_point": { "row": 1, "column": 13 },
      "text": "def hello():\n    return 42",
      "children": [ ... ]
    },
    "node_count": 12
  },
  "sexp": "(module (function_definition ...))"
}
```

各ノードのフィールド:

| フィールド | 説明 |
|---|---|
| `type` | ノード種別 (`function_definition`, `identifier` 等) |
| `start_byte` / `end_byte` | ソース内バイトオフセット |
| `start_point` / `end_point` | `{row, column}` 形式の位置 |
| `text` | `include_text: true` のとき対応ソース文字列 |
| `children` | 子ノードの配列 |
| `truncated` | `max_depth` で打ち切られたとき `true` |

---

## 環境変数

`.env.example` を `.env` にコピーして使用します。

```bash
cp .env.example .env
```

| 変数 | 既定値 | 説明 |
|---|---|---|
| `PORT` | `8008` | サーバーのリッスンポート |
| `HOST` | `0.0.0.0` | バインドアドレス |
| `MAX_TREE_DEPTH` | `512` | JSON ツリーの最大深度 |
| `MAX_NODES` | `200000` | 1 リクエストあたりの最大ノード数 |

---

## n8n からの使い方

**HTTP Request** ノードを以下のように設定します。

| 項目 | 値 |
|---|---|
| Method | `POST` |
| URL | `http://<ホスト>:8008/v1/parse` |
| Body Content Type | `JSON` |
| Body | `{"language": "python", "source": "{{ $json.code }}"}` |

n8n と API が同じ Docker ネットワーク内にある場合は、コンテナ名 (`ast-treesitter`) や `host.docker.internal` を URL に使用してください。

---

## ローカルでイメージをビルドする

```bash
./build.sh
```

内部では次を実行しています:

```bash
docker compose -f compose.yml -f compose.build.yml build
```

---

## GitHub Actions (GHCR への自動 publish)

`.github/workflows/publish-ghcr.yml` が以下のタイミングで `ghcr.io/<owner>/<repo>:latest` を push します。

- `main` ブランチへの push (`src/**`, `Dockerfile` の変更時)
- `v*` タグの push
- `workflow_dispatch` (手動実行)

リポジトリの **Settings > Actions > General** で "Read and write permissions" を許可してください。

---

## 依存関係

| パッケージ | バージョン | 備考 |
|---|---|---|
| `tree-sitter` | `==0.21.3` | `tree-sitter-languages` 1.10.x との互換のため固定 |
| `tree-sitter-languages` | `==1.10.2` | 22 言語バンドル |
| `fastapi` | `>=0.110` | |
| `uvicorn[standard]` | `>=0.29` | |
| `pydantic` | `>=2.6` | |
