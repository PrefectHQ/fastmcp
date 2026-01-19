# CuratorProvider (Search Tools)

## Problem

Servers with many tools overwhelm agent context windows:
- A server with 100 tools uses ~50KB+ just listing them
- Agents waste tokens on irrelevant tools
- Response quality degrades with too many options
- Users can't easily scale their servers

Current workarounds:
- Manual tool grouping with visibility/namespacing (requires upfront design)
- Client-side filtering (not all clients support this)
- Multiple specialized servers (operational complexity)

## Solution

A meta-provider that indexes tools and exposes search/discovery functionality. Instead of showing 100 tools, show 1 search tool that returns the relevant subset based on natural language or keywords.

## Modes

### 1. Search Mode (Keyword/Semantic)

Add a `find_tools` tool that searches over your server's tools.

**Keyword mode**: TF-IDF or simple word matching on tool names and descriptions.

**Semantic mode**: Embeddings-based search (requires `sentence-transformers` or OpenAI).

### 2. Hidden Mode (Future)

Hide the underlying tools entirely - clients only see the search tool. This drastically reduces context usage but requires clients to always search first.

## API

### Basic Usage (Keyword Search)

```python
from fastmcp import FastMCP
from fastmcp.server.providers import CuratorProvider

mcp = FastMCP("server")

# Register many tools
@mcp.tool
def search_documents(query: str) -> list[dict]: ...

@mcp.tool
def list_files(directory: str) -> list[str]: ...

@mcp.tool
def delete_file(path: str) -> bool: ...

# ... 97 more tools ...

# Add curator - indexes all tools, adds search
mcp.add_provider(CuratorProvider(
    source=mcp._provider,
    mode="keyword",
))
```

Now clients see 101 tools: the original 100 + `find_tools`.

### Semantic Search Mode

```python
from fastmcp.server.providers import CuratorProvider

mcp.add_provider(CuratorProvider(
    source=mcp._provider,
    mode="semantic",
    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
))
```

Requires `pip install sentence-transformers` (optional dependency).

### OpenAI Embeddings

```python
mcp.add_provider(CuratorProvider(
    source=mcp._provider,
    mode="semantic",
    embedding_model="openai",
    openai_api_key=os.environ["OPENAI_API_KEY"],
))
```

### Hidden Tools Mode (Future)

```python
mcp.add_provider(CuratorProvider(
    source=mcp._provider,
    mode="semantic",
    hide_tools=True,  # Only expose find_tools, hide originals
))
```

Clients only see `find_tools`. Must search before calling anything.

## Generated Tool

### find_tools

```python
def find_tools(
    query: str,
    limit: int = 10,
) -> list[ToolMatch]:
    """Search for tools relevant to your query.

    Args:
        query: Natural language description of what you want to do
        limit: Maximum number of results to return

    Returns:
        List of matching tools with relevance scores
    """
```

**Example call**:
```python
result = await client.call_tool("find_tools", {
    "query": "I need to clean up old files and free disk space",
    "limit": 5
})
```

**Example result**:
```json
[
  {
    "name": "list_files",
    "description": "List files in a directory with filters",
    "relevance": 0.92,
    "reasoning": "Useful for finding files to evaluate"
  },
  {
    "name": "get_file_age",
    "description": "Get the last modified time of a file",
    "relevance": 0.87,
    "reasoning": "Helps identify old files"
  },
  {
    "name": "delete_files",
    "description": "Delete files matching a pattern",
    "relevance": 0.85,
    "reasoning": "Performs the actual cleanup"
  },
  {
    "name": "get_disk_usage",
    "description": "Get disk usage statistics",
    "relevance": 0.78,
    "reasoning": "Verify space was freed"
  }
]
```

## Implementation

### Location

- `src/fastmcp/server/providers/curator.py` - Main implementation
- `src/fastmcp/server/providers/curator_keyword.py` - Keyword search
- `src/fastmcp/server/providers/curator_semantic.py` - Semantic search (optional)

### CuratorProvider

```python
from fastmcp.server.providers import Provider
from fastmcp.tools import Tool

class CuratorProvider(Provider):
    """Meta-provider that adds tool search capabilities."""

    def __init__(
        self,
        source: Provider,
        mode: Literal["keyword", "semantic"] = "keyword",
        hide_tools: bool = False,
        embedding_model: str | None = None,
        openai_api_key: str | None = None,
    ):
        self.source = source
        self.mode = mode
        self.hide_tools = hide_tools

        # Build search index
        if mode == "keyword":
            self.searcher = KeywordSearcher()
        else:
            self.searcher = SemanticSearcher(
                model=embedding_model,
                openai_api_key=openai_api_key,
            )

        self._index_built = False

    async def list_tools(self) -> Sequence[Tool]:
        """Return source tools + find_tools."""
        source_tools = await self.source.list_tools()

        if not self._index_built:
            await self._build_index(source_tools)
            self._index_built = True

        # Add find_tools
        find_tool = self._create_find_tools_tool()

        if self.hide_tools:
            return [find_tool]
        else:
            return [*source_tools, find_tool]

    async def get_tool(self, name: str) -> Tool | None:
        if name == "find_tools":
            return self._create_find_tools_tool()

        if self.hide_tools:
            return None

        return await self.source.get_tool(name)

    async def _build_index(self, tools: Sequence[Tool]) -> None:
        """Build search index from tools."""
        for tool in tools:
            self.searcher.add(
                name=tool.name,
                description=tool.description or "",
                tags=" ".join(tool.tags) if tool.tags else "",
            )

    def _create_find_tools_tool(self) -> Tool:
        """Create the find_tools tool."""

        async def find_tools_handler(query: str, limit: int = 10) -> list[dict]:
            results = await self.searcher.search(query, limit=limit)

            # Fetch full tool info for each result
            tools = []
            for match in results:
                tool = await self.source.get_tool(match.name)
                if tool:
                    tools.append({
                        "name": tool.name,
                        "description": tool.description,
                        "relevance": match.score,
                        "reasoning": match.reasoning,
                    })

            return tools

        return Tool.from_function(
            find_tools_handler,
            name="find_tools",
            description="Search for tools relevant to your query",
        )
```

### KeywordSearcher

```python
from collections import Counter
import math

class KeywordSearcher:
    """TF-IDF based keyword search."""

    def __init__(self):
        self.documents: dict[str, str] = {}  # name -> text
        self.vocab: set[str] = set()
        self.idf: dict[str, float] = {}

    def add(self, name: str, description: str, tags: str = "") -> None:
        """Add a tool to the index."""
        text = f"{name} {description} {tags}".lower()
        self.documents[name] = text
        self.vocab.update(self._tokenize(text))

    async def search(self, query: str, limit: int = 10) -> list[ToolMatch]:
        """Search for relevant tools."""
        # Build IDF if not built
        if not self.idf:
            self._build_idf()

        query_tokens = self._tokenize(query.lower())
        scores = {}

        for name, doc_text in self.documents.items():
            doc_tokens = self._tokenize(doc_text)
            score = self._compute_score(query_tokens, doc_tokens)
            scores[name] = score

        # Sort by score descending
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        return [
            ToolMatch(
                name=name,
                score=score,
                reasoning=self._generate_reasoning(name, query),
            )
            for name, score in ranked[:limit]
            if score > 0
        ]

    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenization."""
        return text.split()

    def _build_idf(self) -> None:
        """Build IDF scores for vocabulary."""
        n_docs = len(self.documents)

        for word in self.vocab:
            df = sum(1 for doc in self.documents.values() if word in doc)
            self.idf[word] = math.log(n_docs / (1 + df))

    def _compute_score(self, query_tokens: list[str], doc_tokens: list[str]) -> float:
        """Compute TF-IDF similarity."""
        doc_counts = Counter(doc_tokens)
        score = 0.0

        for token in query_tokens:
            if token in doc_counts:
                tf = doc_counts[token] / len(doc_tokens)
                idf = self.idf.get(token, 0)
                score += tf * idf

        return score

    def _generate_reasoning(self, name: str, query: str) -> str:
        """Generate simple reasoning for why tool matched."""
        # Find common words
        query_words = set(self._tokenize(query.lower()))
        doc_words = set(self._tokenize(self.documents[name]))
        common = query_words & doc_words

        if common:
            return f"Matches keywords: {', '.join(sorted(common)[:3])}"
        return "Relevant based on semantic similarity"
```

### SemanticSearcher

```python
from sentence_transformers import SentenceTransformer
import numpy as np

class SemanticSearcher:
    """Embeddings-based semantic search."""

    def __init__(
        self,
        model: str = "sentence-transformers/all-MiniLM-L6-v2",
        openai_api_key: str | None = None,
    ):
        if model == "openai":
            self.use_openai = True
            self.openai_api_key = openai_api_key
        else:
            self.use_openai = False
            self.model = SentenceTransformer(model)

        self.documents: dict[str, str] = {}
        self.embeddings: dict[str, np.ndarray] = {}

    def add(self, name: str, description: str, tags: str = "") -> None:
        """Add a tool to the index."""
        text = f"{name}. {description}"
        self.documents[name] = text

        # Embed immediately (could defer to first search)
        if self.use_openai:
            embedding = self._embed_openai(text)
        else:
            embedding = self.model.encode(text)

        self.embeddings[name] = embedding

    async def search(self, query: str, limit: int = 10) -> list[ToolMatch]:
        """Search for relevant tools using embeddings."""
        # Embed query
        if self.use_openai:
            query_embedding = self._embed_openai(query)
        else:
            query_embedding = self.model.encode(query)

        # Compute cosine similarity
        scores = {}
        for name, doc_embedding in self.embeddings.items():
            similarity = np.dot(query_embedding, doc_embedding) / (
                np.linalg.norm(query_embedding) * np.linalg.norm(doc_embedding)
            )
            scores[name] = float(similarity)

        # Sort by similarity
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        return [
            ToolMatch(
                name=name,
                score=score,
                reasoning=self._generate_reasoning(name, query, score),
            )
            for name, score in ranked[:limit]
            if score > 0.3  # Threshold
        ]

    def _embed_openai(self, text: str) -> np.ndarray:
        """Get embedding from OpenAI."""
        import openai

        client = openai.OpenAI(api_key=self.openai_api_key)
        response = client.embeddings.create(
            input=text,
            model="text-embedding-3-small"
        )
        return np.array(response.data[0].embedding)

    def _generate_reasoning(self, name: str, query: str, score: float) -> str:
        if score > 0.8:
            return "Highly relevant to your request"
        elif score > 0.6:
            return "Likely useful for this task"
        else:
            return "May be relevant"
```

### ToolMatch

```python
from dataclasses import dataclass

@dataclass
class ToolMatch:
    name: str
    score: float
    reasoning: str
```

## Edge Cases

1. **Empty index** - If source has no tools, find_tools returns empty list.

2. **Index rebuild** - If tools change (hot reload), should we rebuild index? For now, no - index is static after first build.

3. **Large servers** - Embedding 1000 tools at startup could be slow. Consider lazy indexing.

4. **Query quality** - Vague queries ("do something") will return poor results. Document best practices.

5. **Hide tools mode** - If tools are hidden, agents MUST use find_tools first. Calling a tool by name directly fails.

6. **Concurrent searches** - Index is read-only after building, safe for concurrent access.

## Testing

Add `tests/server/providers/test_curator.py`:

```python
async def test_curator_keyword_search():
    provider = LocalProvider()

    @provider.tool
    def search_documents(query: str) -> list:
        """Search through documents."""
        return []

    @provider.tool
    def delete_file(path: str) -> bool:
        """Delete a file."""
        return True

    curator = CuratorProvider(provider, mode="keyword")

    tools = await curator.list_tools()
    assert len(tools) == 3  # original 2 + find_tools

    # Call find_tools
    find_tool = await curator.get_tool("find_tools")
    result = await find_tool.fn(query="search for documents", limit=5)

    assert len(result) > 0
    assert result[0]["name"] == "search_documents"
    assert result[0]["relevance"] > 0

async def test_curator_semantic_search():
    pytest.importorskip("sentence_transformers")

    provider = LocalProvider()

    @provider.tool
    def find_files(pattern: str) -> list:
        """Locate files matching a pattern."""
        return []

    @provider.tool
    def delete_data(id: str) -> bool:
        """Remove data by ID."""
        return True

    curator = CuratorProvider(provider, mode="semantic")

    find_tool = await curator.get_tool("find_tools")
    result = await find_tool.fn(query="search for files", limit=5)

    # Semantic search should match "find_files" even though words differ
    assert any(r["name"] == "find_files" for r in result)
```

## Documentation

Add to `docs/servers/providers/curator.mdx`:

- Why search over tools matters
- Keyword vs semantic modes
- Setting up embeddings
- Best practices for queries
- Performance considerations
- Comparison with manual grouping

## Dependencies

Make `sentence-transformers` an optional dependency:

```toml
[project.optional-dependencies]
semantic-search = [
    "sentence-transformers>=2.0.0",
]
```

For OpenAI mode, require `openai` (already a dependency).

## Future Enhancements

1. **Agent mode** - Full conversational agent instead of just search
2. **Caching** - Cache query results
3. **Multi-modal search** - Search across tools + resources + prompts
4. **Relevance feedback** - Learn from which tools users actually call
5. **Custom scoring** - Allow users to provide scoring functions
