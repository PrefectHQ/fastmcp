# Search Transforms

When a server exposes many tools, listing them all at once can overwhelm an LLM's context window. Search transforms collapse the full tool catalog behind a search interface — clients see only `search_tools` and `call_tool`, and discover the real tools on demand.

## Two search strategies

**Regex** (`RegexSearchTransform`) — clients search with regex patterns like `add|multiply` or `text.*`. Fast and precise when you know what you're looking for.

**BM25** (`BM25SearchTransform`) — clients search with natural language like `"work with numbers"`. Results are ranked by relevance using BM25 scoring. The index rebuilds automatically when tools change.

Both strategies respect the full auth pipeline: middleware, visibility transforms, and component-level auth checks all apply to search results.

## Run

```bash
# Regex
uv run python server_regex.py   # in one terminal
uv run python client_regex.py   # in another

# BM25
uv run python server_bm25.py
uv run python client_bm25.py
```

## Example Output (Regex)

```
=== Available Tools ===
  - search_tools: Search for tools matching a regex pattern.
  - call_tool: Call a tool by name with the given arguments.

=== Search: math tools (pattern: 'add|multiply|fibonacci') ===
  - add: Add two numbers together.
  - multiply: Multiply two numbers.
  - fibonacci: Generate the first n Fibonacci numbers.

=== Search: text tools (pattern: 'text|string|word') ===
  - reverse_string: Reverse a string.
  - word_count: Count the number of words in a text.
  - to_uppercase: Convert text to uppercase.

=== Calling 'add' via call_tool ===
  Result: 42
```

## Example Output (BM25)

```
=== Available Tools ===
  - list_files: List files in a directory.
  - search_tools: Search for tools using natural language.
  - call_tool: Call a tool by name with the given arguments.

=== Search: 'work with numbers' ===
  - multiply: Multiply two numbers.
  - add: Add two numbers together.
  - fibonacci: Generate the first n Fibonacci numbers.

=== Search: 'file operations' ===
  - read_file: Read the contents of a file.

=== Calling 'word_count' via call_tool ===
  Result: 6
```

Note how `list_files` appears in the BM25 example's tool listing — it's pinned via `always_visible=["list_files"]`, keeping it visible alongside the synthetic search tools.
