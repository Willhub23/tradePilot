"""Monotonic comparisons are valid only within this serving process."""
from uuid import uuid4
PROCESS_ID = str(uuid4())
