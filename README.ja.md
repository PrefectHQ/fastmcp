<div align="center">

# FastMCP 🚀

<strong>速く動いて、ものを作ろう。</strong>

[English](README.md) | 日本語

[![Docs](https://img.shields.io/badge/docs-gofastmcp.com-blue)](https://gofastmcp.com)
[![PyPI](https://img.shields.io/pypi/v/fastmcp.svg)](https://pypi.org/project/fastmcp)
</div>

---

[Model Context Protocol](https://modelcontextprotocol.io/)（MCP）はLLMをツールやデータに接続します。FastMCPはプロトタイプから本番環境まで必要なものをすべて提供します：

```python
from fastmcp import FastMCP

mcp = FastMCP("Demo 🚀")

@mcp.tool
def add(a: int, b: int) -> int:
    """2つの数値を加算する"""
    return a + b

if __name__ == "__main__":
    mcp.run()
```

## なぜFastMCPなのか

効果的なMCPアプリケーションの構築は見た目以上に難しいものです。FastMCPがすべてを処理します。Python関数でツールを宣言するだけで、スキーマ、バリデーション、ドキュメントが自動生成されます。URLでサーバーに接続するだけで、トランスポートネゴシエーション、認証、プロトコルライフサイクルが管理されます。

**FastMCPはMCPを扱うための標準フレームワークです。** FastMCP 1.0は2024年にMCP公式Python SDKに組み込まれました。現在、1日100万回以上ダウンロードされ、全言語のMCPサーバーの70%がFastMCPを使用しています。

## インストール

```bash
pip install fastmcp
```

## 3つの柱

### 🔨 サーバー
MCPサーバーを構築・デプロイ。ツール、リソース、プロンプトを公開。

### 🔗 クライアント
MCPサーバーに接続して利用。トランスポートネゴシエーション、認証を自動管理。

### 🤖 エージェント
MCPツールを呼び出す、組み込みLLMパイプライン。マルチサーバー接続、ストリーミング、ヒューマンインザループ対応。

## クイックスタート

```python
from fastmcp import FastMCP

mcp = FastMCP("天気サービス")

@mcp.tool
def get_weather(city: str) -> str:
    """都市の天気を取得する"""
    return f"{city}は晴れです！"

@mcp.resource("config://app")
def get_config() -> str:
    """アプリ設定を取得する"""
    return "設定データ"

@mcp.prompt
def review_prompt(code: str) -> str:
    """コードレビュープロンプト"""
    return f"以下のコードをレビューしてください:
{code}"
```

## ドキュメント

詳細は [gofastmcp.com](https://gofastmcp.com) をご覧ください。

## ライセンス

Apache 2.0 - 詳細は [LICENSE](LICENSE) を参照。
