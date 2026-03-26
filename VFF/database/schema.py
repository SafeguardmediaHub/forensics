"""
VideoForensics System — Database Schema
Pure sqlite3 implementation (no external dependencies required).
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from config.settings import BASE_DIR

DB_PATH = BASE_DIR / "database" / "forensics.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    submitted_at TEXT,
    submitted_by TEXT,
    original_filename TEXT,
    sha256_hash TEXT,
    md5_hash TEXT,
    file_size_bytes INTEGER,
    video_profile TEXT,
    overall_tampering_probability REAL,
    overall_confidence_level TEXT,
    overall_uncertainty REAL,
    requires_human_review INTEGER DEFAULT 0,
    human_review_reason TEXT,
    verdict_summary TEXT,
    fusion_weights_used TEXT,
    completed_at TEXT,
    total_processing_seconds REAL,
    ground_truth_tampered INTEGER,
    ground_truth_source TEXT,
    ground_truth_recorded_at TEXT
);

CREATE TABLE IF NOT EXISTS module_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT,
    module_name TEXT,
    status TEXT,
    tampering_probability REAL,
    uncertainty REAL,
    confidence_level TEXT,
    summary TEXT,
    processing_time_seconds REAL,
    reliability_flag INTEGER DEFAULT 1,
    reliability_note TEXT,
    error_message TEXT,
    metadata_json TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    case_id TEXT,
    module TEXT,
    title TEXT,
    description TEXT,
    severity TEXT,
    confidence REAL,
    uncertainty REAL,
    evidence TEXT,
    raw_values TEXT,
    start_frame INTEGER,
    end_frame INTEGER,
    start_seconds REAL,
    end_seconds REAL,
    region_x REAL,
    region_y REAL,
    region_w REAL,
    region_h REAL,
    timestamp TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS audit_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT,
    entry_id TEXT UNIQUE,
    timestamp TEXT,
    event TEXT,
    detail TEXT,
    actor TEXT,
    file_hash TEXT,
    signature TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS device_fingerprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_make TEXT,
    device_model TEXT,
    firmware_ver TEXT,
    fingerprint_ref TEXT,
    frame_count INTEGER,
    quality_score REAL,
    recorded_at TEXT,
    source_case_id TEXT
);
"""

def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    conn = get_connection()
    conn.executescript(CREATE_TABLES)
    conn.commit()
    conn.close()
    return True
