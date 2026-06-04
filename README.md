# ast-treesitter-api

[Tree-sitter](https://tree-sitter.github.io/tree-sitter/) を使い、ソースコードを **JSON 構文木 (AST)** に変換する軽量 HTTP API サーバーです。
n8n などのワークフローエンジンや CI パイプラインから POST リクエスト 1 本で構文解析を呼び出せます。

## 対応言語 (27 種)

`bash` / `c` / `c_sharp` / `cpp` / `css` / `dockerfile` / `elixir` / `go` /
`hcl` / `html` / `java` / `javascript` / `json` / `julia` / `kotlin` / `lua` /
`php` / `python` / `r` / `ruby` / `rust` / `scala` / `sql` / `toml` / `tsx` /
`typescript` / `yaml`

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
  "languages_loaded": 27,
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
| `source` | string | ✓ | — | パースするソースコード |
| `filename` | string | | `null` | ファイル名。拡張子から文法を自動判定し `language` より優先される |
| `language` | string | | `null` | 言語名 (`python`, `javascript` 等)。`filename` で判定できない場合のフォールバック |
| `include_text` | boolean | | `true` | 各ノードにソーステキストを含めるか |
| `max_depth` | integer | | `512` | JSON ツリーの最大深度 (1–4096) |
| `sexp` | boolean | | `false` | S 式文字列を追加返却するか |

> `filename` と `language` はどちらか一方が必要です。両方指定した場合は `filename` の拡張子が優先され、拡張子が不明な場合のみ `language` にフォールバックします。

#### Python の例

```bash
# filename で言語を指定する場合
curl -s -X POST http://127.0.0.1:8008/v1/parse \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "hello.py",
    "source": "def hello():\n    return 42",
    "sexp": true
  }'

# language で言語を指定する場合
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

### `POST /v1/chunk`

ソースコードやテキストを意味単位で分割した **チャンク配列** を返します。
ファイル名の拡張子を渡すだけで AST / markdown / text の各戦略を自動選択します。

#### リクエストボディ

| パラメータ | 型 | 既定 | 説明 |
|---|---|---|---|
| `source` | string | (必須) | 本文 |
| `filename` | string | — | 例: `"README.md"`。拡張子で戦略を自動選択 |
| `language` | string | — | grammar 名 / `markdown` / `text` / `auto`。省略可 |
| `mode` | string | `auto` | `auto` / `ast` / `markdown` / `text` |
| `max_chunk_size` | int | `500` | 1 チャンクの最大文字数。`0` で無効 |
| `chunk_overlap` | int | `50` | 分割片の重複文字数 |
| `split_definitions` | bool | `false` | 定義も上限超過時に分割するか |
| `include_context` | bool | `true` | 先頭コメント/デコレータを次の定義へ付与 |

#### 自動戦略選択の優先順位

`filename` があれば拡張子で判定 → 無ければ `language` → どれも該当しなければ `text` にフォールバック (`400` を返さない)。

| 拡張子の例 | 適用戦略 |
|---|---|
| `.md` / `.markdown` / `.mdx` | markdown (見出し単位で分割) |
| `.txt` / `.log` / `.rst` / `.csv` | text (文字数単位で分割) |
| `.py` / `.ts` / `.go` / `.rs` 等 | ast (関数・クラス単位で分割) |
| `.sql` | ast (SELECT / CREATE / INSERT 等のステートメント単位で分割) |
| `.r` | ast (トップレベルの関数・変数代入ごとに分割) |
| `.jl` | ast (関数・struct・module 単位で分割) |
| `.tf` / `.hcl` | ast (ファイル全体を 1 チャンク、サイズ分割は有効) |
| `.dockerfile` | ast (ファイル全体を 1 チャンク、サイズ分割は有効) |
| 不明な拡張子 | text にフォールバック |

#### Markdown ファイルの例

```bash
curl -s http://127.0.0.1:8008/v1/chunk \
  -H 'Content-Type: application/json' \
  -d '{"filename": "README.md", "source": "# Title\n\n## Usage\n...\n\n## Install\n..."}'
```

レスポンス (抜粋):

```json
{
  "language": "markdown",
  "chunk_count": 3,
  "chunks": [
    {
      "chunk_type": "section",
      "node_type": "section",
      "text": "# Title\n\n",
      "heading_path": "Title",
      "part": null,
      "start_byte": 0,
      "end_byte": 9,
      "start_point": { "row": 0, "column": 0 },
      "end_point": { "row": 2, "column": 0 }
    },
    {
      "chunk_type": "section",
      "node_type": "section",
      "text": "## Usage\n...\n\n",
      "heading_path": "Title > Usage",
      "part": null,
      ...
    }
  ]
}
```

長い節は `max_chunk_size` を超えた場合に `part: 1`, `part: 2`, … 付きで分割されます。

#### テキストファイルの例

```bash
curl -s http://127.0.0.1:8008/v1/chunk \
  -H 'Content-Type: application/json' \
  -d '{"filename": "notes.txt", "source": "..."}'
```

`language: "text"` で返り、500 文字ごとに `chunk_overlap: 50` 文字のオーバーラップを付けて分割されます。

#### Python コードの例 (従来どおり)

```bash
curl -s http://127.0.0.1:8008/v1/chunk \
  -H 'Content-Type: application/json' \
  -d '{
    "language": "python",
    "source": "def hello():\n    return 42\n",
    "include_context": true
  }'
```

#### レスポンスフィールド

チャンクオブジェクトには以下のフィールドが含まれます。

| フィールド | 説明 |
|---|---|
| `chunk_type` | `preamble` / `definition` / `other` (AST) ・ `section` (Markdown) ・ `text` (テキスト) |
| `node_type` | AST ノード種別 (AST) または `"section"` / `"text"` |
| `text` | チャンク本文 |
| `start_byte` / `end_byte` | ソース内バイトオフセット |
| `start_point` / `end_point` | `{row, column}` 形式の位置 |
| `heading_path` | Markdown の見出し階層 (例: `"Usage > Install"`)。Markdown 以外は `null` |
| `part` | サイズ分割で生成された場合の 1 始まりの連番。分割なしは `null` |

#### 後方互換

従来の `{language, source, include_context}` 形式はそのまま動作します。
`definition` チャンクは `split_definitions: true` を指定しない限り、サイズ上限を超えても分割されません。

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
| Body | `{"filename": "script.py", "source": "{{ $json.code }}"}` |

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
| `tree-sitter-languages` | `==1.10.2` | 27 言語バンドル |
| `fastapi` | `>=0.110` | |
| `uvicorn[standard]` | `>=0.29` | |
| `pydantic` | `>=2.6` | |
