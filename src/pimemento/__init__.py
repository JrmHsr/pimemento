"""Pimemento - Shared memory layer for AI teams.

Multi-tenant. Cross-MCP. Schema-less.
"""

from pimemento.config import PimementoConfig
from pimemento.embedded import register_tools

__version__ = "1.0.0"
__all__ = ["register_tools", "PimementoConfig", "__version__"]
