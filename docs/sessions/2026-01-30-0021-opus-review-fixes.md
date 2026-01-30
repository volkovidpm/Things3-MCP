# Session: Opus Code Review Fixes

**Date**: 2026-01-30
**Captured**: 00:21 Europe/Dublin
**Status**: Complete
**Branch**: `feature/fastmcp3-performance-optimization`

## Overview

Address all issues identified in the Claude Opus code review of the caching implementation.

## Issues to Fix

### High Priority
1. **Race condition in `_track_cache_key`** - Document single-threaded assumption
2. **O(n²) complexity in `invalidate_todos_cache`** - Use list comprehension
3. **FIFO vs LRU documentation mismatch** - Update comments

### Medium Priority
4. **Missing `invalidate_areas_cache`** after project changes
5. **Long cache keys not hashed** - Add hashing for long queries

### Minor
6. **Inconsistent null checking** - Use early return pattern consistently (reviewed: pattern is already consistent)
7. **Debug logs with sensitive data** - Reviewed: DEBUG level logs are not enabled in production; acceptable for debugging
8. **MAX_RESPONSE_CACHE_SIZE undocumented** - Add sizing rationale

## Changes Made

### Modified Files

- `src/things3_mcp/fast_server.py`:
  - Added `hashlib` import for cache key hashing
  - Moved `MAX_RESPONSE_CACHE_SIZE` and `MAX_CACHE_KEY_LENGTH` constants before `CacheKeys` class
  - Added sizing rationale comment for `MAX_RESPONSE_CACHE_SIZE = 500`
  - Added `MAX_CACHE_KEY_LENGTH = 100` constant for key hashing threshold
  - Updated `_track_cache_key()` docstring to document single-threaded assumption
  - Changed FIFO eviction comment from "LRU-style" to explicit "FIFO"
  - Fixed O(n²) in `invalidate_todos_cache()`: replaced list iteration with `.remove()` calls with O(n) list comprehension using set lookups
  - Added `invalidate_areas_cache()` calls to `add_new_project()` and `update_existing_project()`
  - Added hashing to `CacheKeys.search_todos_response()`, `CacheKeys.search_advanced_response()`, and `CacheKeys.search_raw()` for keys exceeding `MAX_CACHE_KEY_LENGTH`
  - Added hashing to `_build_cache_key()` for long serialised parameters

### Key Decisions

1. **FIFO not LRU**: The eviction strategy is simple FIFO (first-in-first-out) based on insertion order, not LRU (least recently used). LRU would require tracking access times which adds complexity for minimal benefit in this use case.

2. **Hash truncation at 16 chars**: SHA-256 produces 64 hex chars, but we only use the first 16 for cache key suffixes. This provides 64 bits of uniqueness which is more than sufficient for cache keys within a session.

3. **100 char threshold**: Keys longer than 100 characters are hashed. This balances readability (short keys are kept as-is for debugging) against memory efficiency.

4. **O(n) vs O(n²)**: Changed `invalidate_todos_cache` from O(n²) (using `.remove()` in a loop) to O(n) (using set for lookups and list comprehension for filtering).

### Update - 2026-01-30 13:23 Europe/Dublin

**Summary**: All Opus review issues fixed. Tests passing (121/121).

## Commits

1. `ea7d38a` - Address Opus code review findings for cache implementation
