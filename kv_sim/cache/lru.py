"""LRU victim selection. Pinned blocks are never candidates."""

def choose_lru_victim(blocks, pins):
    return next((key for key in blocks if key not in pins), None)
