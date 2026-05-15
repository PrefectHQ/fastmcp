# Re-export for backwards compatibility
# The canonical location is now fastmcp.client.sampling.handlers
from fastmcp.client.sampling.handlers.openai import OpenAISamplingHandler

__all__ = ["OpenAISamplingHandler"]
