"""Render the canonical operations JSON export from existing append-only storage."""
from .core import DEFAULT_EXPORT, connect, export

if __name__ == "__main__":
    print(export(connect(), DEFAULT_EXPORT))
