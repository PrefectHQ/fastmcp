# Search Transforms

When a server exposes many tools, listing them all at once can overwhelm an LLM's context window. Search transforms collapse the full tool catalog behind a search interface — clients see only `search_tools` and `call_tool`, and discover the real tools on demand.

## Two search strategies

**Regex** (`RegexSearchTransform`) — clients search with regex patterns like `add|multiply` or `text.*`. Fast and precise when you know what you're looking for.

**BM25** (`BM25SearchTransform`) — clients search with natural language like `"work with numbers"`. Results are ranked by relevance using BM25 scoring. The index rebuilds automatically when tools change.

Both strategies respect the full auth pipeline: middleware, visibility transforms, and component-level auth checks all apply to search results.

## Run

```bash
# Regex
uv run python examples/search/client_regex.py

# BM25
uv run python examples/search/client_bm25.py
```
