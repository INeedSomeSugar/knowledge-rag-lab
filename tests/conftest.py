from __future__ import annotations

import os


# Tests must never depend on a developer's local .env or call paid model APIs.
os.environ["EMBEDDING_PROVIDER"] = "hashing"
os.environ["LLM_PROVIDER"] = "extractive"
