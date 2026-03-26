"""
VideoForensics System — Audit Trail
Every action taken on every video is logged here with cryptographic integrity.
This is the chain of custody mechanism. It is built before everything else
because it must be active from the moment a file enters the system.

Every log entry is HMAC-signed. Any tampering with the log is detectable.
"""

from __future__ import annotations
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vf.audit")


class AuditEntry:
    """A single immutable audit log entry."""

    def __init__(
        self,
        case_id: str,
        event: str,
        detail: str = "",
        actor: str = "system",
        file_hash: Optional[str] = None,
    ):
        self.case_id    = case_id
        self.event      = event
        self.detail     = detail
        self.actor      = actor
        self.file_hash  = file_hash
        self.timestamp  = datetime.utcnow().isoformat()
        self.entry_id   = self._generate_id()
        self.signature  = None   # Set by AuditTrail after chaining

    def _generate_id(self) -> str:
        """Deterministic entry ID from content."""
        raw = f"{self.case_id}:{self.event}:{self.timestamp}:{self.detail}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id":  self.entry_id,
            "case_id":   self.case_id,
            "timestamp": self.timestamp,
            "event":     self.event,
            "detail":    self.detail,
            "actor":     self.actor,
            "file_hash": self.file_hash,
            "signature": self.signature,
        }

    def __repr__(self) -> str:
        return f"AuditEntry({self.event} @ {self.timestamp})"


class AuditTrail:
    """
    Append-only audit trail with HMAC chain integrity.

    Each entry is signed using:
        HMAC(key, previous_signature + current_entry_content)

    This creates a chain where any modification to any entry
    or any deletion of an entry breaks all subsequent signatures.
    Tampering with the audit log is detectable.
    """

    def __init__(self, case_id: str, log_dir: Optional[Path] = None):
        self.case_id    = case_id
        self.entries: List[AuditEntry] = []
        self._last_signature = case_id   # Chain seed is the case_id
        self._hmac_key = self._load_hmac_key()
        self._log_dir = log_dir

        if self._log_dir:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = self._log_dir / f"{case_id}.audit.jsonl"
        else:
            self._log_path = None

        logger.debug(f"AuditTrail initialized for case {case_id}")

    def _load_hmac_key(self) -> bytes:
        """
        Load HMAC key from environment. In production this must be set.
        Falls back to a deterministic development key with a warning.
        """
        key_str = os.environ.get("VF_AUDIT_HMAC_KEY")
        if not key_str:
            logger.warning(
                "VF_AUDIT_HMAC_KEY not set. Using development key. "
                "DO NOT USE IN PRODUCTION."
            )
            key_str = "vf-dev-hmac-key-not-for-production"
        return key_str.encode()

    def _sign(self, entry: AuditEntry) -> str:
        """
        Compute HMAC signature chaining this entry to the previous one.
        Signs only stable fields — signature field excluded (it is None at sign time).
        """
        stable = {
            "entry_id":  entry.entry_id,
            "case_id":   entry.case_id,
            "timestamp": entry.timestamp,
            "event":     entry.event,
            "detail":    entry.detail,
            "actor":     entry.actor,
            "file_hash": entry.file_hash,
        }
        content = json.dumps(stable, sort_keys=True, default=str)
        message = f"{self._last_signature}:{content}".encode()
        return hmac.new(self._hmac_key, message, hashlib.sha256).hexdigest()

    def log(
        self,
        event: str,
        detail: str = "",
        actor: str = "system",
        file_hash: Optional[str] = None,
    ) -> AuditEntry:
        """
        Append a signed entry to the audit trail.
        This is the only way to add entries — the trail is append-only.
        """
        entry = AuditEntry(
            case_id=self.case_id,
            event=event,
            detail=detail,
            actor=actor,
            file_hash=file_hash,
        )
        entry.signature = self._sign(entry)
        self._last_signature = entry.signature
        self.entries.append(entry)

        if self._log_path:
            self._persist_entry(entry)

        logger.debug(f"Audit [{self.case_id}] {event}: {detail[:80]}")
        return entry

    def _persist_entry(self, entry: AuditEntry) -> None:
        """Write entry to the append-only JSONL log file."""
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), default=str) + "\n")
        except Exception as e:
            logger.error(f"Failed to persist audit entry: {e}")

    def verify_integrity(self) -> Dict[str, Any]:
        """
        Verify the cryptographic integrity of the entire chain.
        Returns a report of any integrity violations found.
        """
        violations = []
        expected_prev = self.case_id

        for i, entry in enumerate(self.entries):
            stable = {
                "entry_id":  entry.entry_id,
                "case_id":   entry.case_id,
                "timestamp": entry.timestamp,
                "event":     entry.event,
                "detail":    entry.detail,
                "actor":     entry.actor,
                "file_hash": entry.file_hash,
            }
            content = json.dumps(stable, sort_keys=True, default=str)
            message = f"{expected_prev}:{content}".encode()
            expected_sig = hmac.new(self._hmac_key, message, hashlib.sha256).hexdigest()

            if not hmac.compare_digest(entry.signature or "", expected_sig):
                violations.append({
                    "entry_index": i,
                    "entry_id": entry.entry_id,
                    "event": entry.event,
                    "timestamp": entry.timestamp,
                    "issue": "Signature mismatch — entry may have been tampered with",
                })

            expected_prev = entry.signature

        return {
            "case_id": self.case_id,
            "total_entries": len(self.entries),
            "integrity_valid": len(violations) == 0,
            "violations": violations,
            "verified_at": datetime.utcnow().isoformat(),
        }

    def export(self) -> List[Dict[str, Any]]:
        """Export the full audit trail as a list of dicts."""
        return [e.to_dict() for e in self.entries]

    def __len__(self) -> int:
        return len(self.entries)

    def __repr__(self) -> str:
        return f"AuditTrail(case={self.case_id}, entries={len(self.entries)})"
