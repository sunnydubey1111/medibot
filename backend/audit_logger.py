import os
import json
import logging
from pathlib import Path
from datetime import datetime

BACKEND_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
AUDIT_LOG_PATH = BACKEND_DIR / "audit.log"

_handler = logging.FileHandler(str(AUDIT_LOG_PATH), encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(message)s"))

_logger = logging.getLogger("medibot.audit")
_logger.setLevel(logging.INFO)
_logger.addHandler(_handler)
_logger.propagate = False


def log_query(
    username: str,
    role: str,
    question: str,
    retrieval_type: str,
    confidence: float = None,
    blocked: bool = False,
):
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "username": username,
        "role": role,
        "question": question[:300],
        "retrieval_type": retrieval_type,
        "confidence": round(confidence, 4) if confidence is not None else None,
        "blocked": blocked,
    }
    _logger.info(json.dumps(entry))
