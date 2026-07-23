#!/usr/bin/env python3
"""
==============================================================================
ENTERPRISE SCIENTIFIC KNOWLEDGE GRAPH PLATFORM v3.1 ULTRA
==============================================================================
An automated scientific knowledge integration & discovery engine for 
computational biology, chemistry, drug discovery, biotechnology, environmental 
science, agriculture, microbiology, toxicology, pharmacology, and life sciences.

(Truncated docstring retained from original file)
"""

from __future__ import annotations

import abc
import argparse
import asyncio
import copy
import csv
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import sys
import tempfile
import time
import zlib
from collections import defaultdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import quote, urlparse

# Third-Party Dependencies with Fallbacks
try:
    import aiohttp
    import duckdb
    import networkx as nx
    from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
except ImportError as e:
    msg = f"Missing critical dependencies: {e}" + "\nPlease run: pip install pydantic networkx duckdb aiohttp orjson pyarrow"
    sys.exit(msg)

try:
    import orjson
except ImportError:
    import json as orjson

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAS_PARQUET = True
except ImportError:
    HAS_PARQUET = False

# Optional chemdata port (lightweight ported extractor)
try:
    from chemdata_port import Document as ChemDocument
except Exception:
    ChemDocument = None

# ---------------------------
# Atomic write helper
# ---------------------------

def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    dirpath = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, prefix=".tmp-scigraph-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


# ==============================================================================
# (Remaining content unchanged except for specific patched blocks below)
# ==============================================================================

# ... (keep the original file's large body, but with the following targeted changes applied)

