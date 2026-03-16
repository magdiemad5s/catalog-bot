# utils/__init__.py
# Re-export frequently used helpers so imports stay short:
#   from utils import error_embed, success_embed
from .embeds import error_embed, success_embed, info_embed, warning_embed

__all__ = ["error_embed", "success_embed", "info_embed", "warning_embed"]
