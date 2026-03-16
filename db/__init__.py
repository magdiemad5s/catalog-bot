"""
db/__init__.py — Re-export the two functions every cog will need.

Usage:
    from db import get_db, init_db
"""
from .client import get_db, init_db

__all__ = ["get_db", "init_db"]
