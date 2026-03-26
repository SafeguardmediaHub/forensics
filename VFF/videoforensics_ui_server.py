#!/usr/bin/env python3
"""
VideoForensics — Web UI
Run: python ui/server.py
Then open: http://localhost:5050
"""
import hashlib, json, logging, os, sys, tempfile, time, uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, request, jsonify, send_file, render_template_string
import logging; logging.disable(logging.WARNING)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

UPLOAD_DIR = Path(tempfile.gettempdir()) / "vf_ui_uploads"
REPORT_DIR = Path(tempfile.gettempdir()) / "vf_ui_reports"
UPLOAD_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

_pipeline = None
_fusion   = None
_builder  = None

def get_pipeline():
    global _pipeline
    if _pipeline is None:
        from core.ingestion.pipeline import IngestionPipeline
        _pipeline = IngestionPipeline(extract_frames=False, extract_audio=False)
    return _pipeline

def get_fusion():
    global _fusion
    if _fusion is None:
        from modules.fusion.fusion_module import FusionModule
        _fusion = FusionModule()
    return _fusion

def get_builder():
    global _builder
    if _builder is None:
        from modules.reporting.report_builder import ForensicReportBuilder
        _builder = ForensicReportBuilder()
    return _builder


# ── HTML Template ─────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VideoForensics</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

  :root {
    --bg:        #0d0f12;
    --surface:   #13161b;
    --surface2:  #1a1e26;
    --border:    #252930;
    --accent:    #4f8ef7;
    --accent2:   #7c5cbf;
    --green:     #2ecc71;
    --amber:     #f39c12;
    --red:       #e74c3c;
    --text:      #e2e8f0;
    --muted:     #64748b;
    --mono:      'IBM Plex Mono', monospace;
    --sans:      'IBM Plex Sans', sans-serif;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 14px;
    min-height: 100vh;
    line-height: 1.6;
  }

  /* ── Header ── */
  header {
    border-bottom: 1px solid var(--border);
    padding: 0 32px;
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--surface);
    position: sticky; top: 0; z-index: 100;
  }
  .logo {
    font-family: var(--mono);
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.12em;
    color: var(--accent);
    text-transform: uppercase;
    display: flex; align-items: center; gap: 10px;
  }
  .logo-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent);
    animation: pulse 2s ease-in-out infinite;
  }
  @keyframes pulse {
    0%,100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(0.8); }
  }
  .version {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
    background: var(--border);
    padding: 3px 8px;
    border-radius: 4px;
  }

  /* ── Layout ── */
  .layout {
    display: grid;
    grid-template-columns: 360px 1fr;
    gap: 0;
    min-height: calc(100vh - 56px);
  }

  /* ── Sidebar ── */
  .sidebar {
    border-right: 1px solid var(--border);
    padding: 28px 24px;
    background: var(--surface);
    display: flex; flex-direction: column; gap: 24px;
  }

  .panel-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 10px;
  }

  /* ── Drop zone ── */
  .dropzone {
    border: 1.5px dashed var(--border);
    border-radius: 10px;
    padding: 32px 20px;
    text-align: center;
    cursor: pointer;
    transition: all 0.2s;
    background: var(--surface2);
    position: relative;
  }
  .dropzone:hover, .dropzone.dragover {
    border-color: var(--accent);
    background: rgba(79,142,247,0.05);
  }
  .dropzone input[type=file] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%;
  }
  .drop-icon { font-size: 32px; margin-bottom: 10px; }
  .drop-primary {
    font-weight: 500; font-size: 13px; color: var(--text); margin-bottom: 4px;
  }
  .drop-sub { font-size: 12px; color: var(--muted); }
  .drop-formats {
    margin-top: 10px; font-family: var(--mono);
    font-size: 10px; color: var(--muted);
    background: var(--border); padding: 4px 10px; border-radius: 4px; display: inline-block;
  }

  /* ── File info ── */
  .file-info {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    display: none;
  }
  .file-info.visible { display: block; }
  .file-name {
    font-family: var(--mono); font-size: 12px;
    color: var(--accent); word-break: break-all;
    margin-bottom: 4px;
  }
  .file-size { font-size: 11px; color: var(--muted); }

  /* ── Analyse button ── */
  .btn-analyse {
    background: var(--accent);
    color: #fff;
    border: none; border-radius: 8px;
    padding: 13px 20px;
    font-family: var(--mono); font-size: 12px;
    font-weight: 600; letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer; width: 100%;
    transition: all 0.2s;
    display: flex; align-items: center; justify-content: center; gap: 8px;
  }
  .btn-analyse:hover:not(:disabled) { background: #3a7de0; transform: translateY(-1px); }
  .btn-analyse:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

  /* ── Progress ── */
  .progress-wrap { display: none; }
  .progress-wrap.visible { display: block; }
  .progress-label {
    font-family: var(--mono); font-size: 11px; color: var(--muted);
    margin-bottom: 8px; display: flex; justify-content: space-between;
  }
  .progress-bar-bg {
    height: 4px; background: var(--border); border-radius: 2px; overflow: hidden;
  }
  .progress-bar-fill {
    height: 100%; background: var(--accent);
    border-radius: 2px;
    transition: width 0.4s ease;
    width: 0%;
  }
  .progress-steps { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
  .step {
    font-family: var(--mono); font-size: 11px; color: var(--muted);
    display: flex; align-items: center; gap: 8px;
    transition: color 0.3s;
  }
  .step.active { color: var(--text); }
  .step.done { color: var(--green); }
  .step-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--border); flex-shrink: 0; transition: background 0.3s;
  }
  .step.active .step-dot { background: var(--accent); animation: pulse 1s infinite; }
  .step.done .step-dot { background: var(--green); }

  /* ── Separator ── */
  .sep { border: none; border-top: 1px solid var(--border); }

  /* ── How it works ── */
  .how { display: flex; flex-direction: column; gap: 8px; }
  .how-item { display: flex; gap: 10px; align-items: flex-start; }
  .how-num {
    font-family: var(--mono); font-size: 10px; font-weight: 600;
    color: var(--accent2); background: rgba(124,92,191,0.12);
    border-radius: 4px; padding: 2px 6px; flex-shrink: 0; margin-top: 1px;
  }
  .how-text { font-size: 12px; color: var(--muted); }

  /* ── Main panel ── */
  .main {
    padding: 32px 36px;
    overflow-y: auto;
  }

  /* ── Empty state ── */
  .empty-state {
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    height: 100%; text-align: center;
    color: var(--muted); gap: 14px;
    padding: 60px 20px;
  }
  .empty-icon { font-size: 56px; opacity: 0.3; }
  .empty-title { font-size: 18px; font-weight: 500; color: var(--text); }
  .empty-sub { font-size: 13px; max-width: 340px; line-height: 1.7; }
  .empty-state .hint {
    font-family: var(--mono); font-size: 11px;
    background: var(--surface2); border: 1px solid var(--border);
    padding: 8px 14px; border-radius: 6px; color: var(--muted);
  }

  /* ── Results ── */
  #results { display: none; }
  #results.visible { display: block; }

  .result-header {
    display: flex; align-items: flex-start;
    justify-content: space-between; gap: 20px;
    margin-bottom: 28px; flex-wrap: wrap;
  }
  .result-filename {
    font-family: var(--mono); font-size: 12px; color: var(--muted); margin-bottom: 6px;
  }
  .verdict-badge {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 8px 16px; border-radius: 8px;
    font-family: var(--mono); font-size: 13px; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase;
    border: 1px solid transparent;
  }
  .verdict-badge.authentic  { background: rgba(46,204,113,0.12); color: #2ecc71; border-color: rgba(46,204,113,0.3); }
  .verdict-badge.inconclusive{ background: rgba(243,156,18,0.12); color: #f39c12; border-color: rgba(243,156,18,0.3); }
  .verdict-badge.tampered   { background: rgba(231,76,60,0.12);  color: #e74c3c; border-color: rgba(231,76,60,0.3); }
  .verdict-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }

  .result-meta { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 8px; }
  .meta-chip {
    font-family: var(--mono); font-size: 11px; color: var(--muted);
    background: var(--surface2); border: 1px solid var(--border);
    padding: 4px 10px; border-radius: 4px;
  }
  .meta-chip b { color: var(--text); }

  .btn-pdf {
    background: transparent; color: var(--accent2);
    border: 1px solid var(--accent2); border-radius: 8px;
    padding: 10px 18px; font-family: var(--mono); font-size: 11px;
    font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
    cursor: pointer; transition: all 0.2s; white-space: nowrap;
    display: flex; align-items: center; gap: 6px; text-decoration: none;
  }
  .btn-pdf:hover { background: rgba(124,92,191,0.12); }

  /* ── Score grid ── */
  .section-title {
    font-family: var(--mono); font-size: 10px;
    letter-spacing: 0.15em; text-transform: uppercase;
    color: var(--muted); margin-bottom: 14px;
    display: flex; align-items: center; gap: 10px;
  }
  .section-title::after {
    content: ''; flex: 1; height: 1px; background: var(--border);
  }

  .score-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px; margin-bottom: 28px;
  }
  .score-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px; padding: 16px;
    transition: border-color 0.2s;
  }
  .score-card.elevated { border-color: rgba(231,76,60,0.4); }
  .score-card-name {
    font-family: var(--mono); font-size: 10px;
    letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--muted); margin-bottom: 10px;
  }
  .score-value {
    font-family: var(--mono); font-size: 22px; font-weight: 600;
    line-height: 1; margin-bottom: 10px;
  }
  .score-bar-bg {
    height: 3px; background: var(--border); border-radius: 2px; overflow: hidden;
  }
  .score-bar-fill {
    height: 100%; border-radius: 2px; transition: width 0.8s ease;
  }

  /* ── Findings ── */
  .findings { display: flex; flex-direction: column; gap: 10px; margin-bottom: 28px; }
  .finding {
    background: var(--surface2); border: 1px solid var(--border);
    border-radius: 8px; padding: 14px 16px;
    border-left: 3px solid var(--border);
    display: grid; grid-template-columns: auto 1fr auto;
    gap: 12px; align-items: start;
  }
  .finding.HIGH { border-left-color: var(--red); }
  .finding.MEDIUM, .finding.MED { border-left-color: var(--amber); }
  .finding.LOW { border-left-color: var(--muted); }
  .finding-sev {
    font-family: var(--mono); font-size: 9px; font-weight: 600;
    letter-spacing: 0.1em; text-transform: uppercase;
    padding: 2px 7px; border-radius: 3px; margin-top: 2px;
  }
  .finding-sev.HIGH   { background: rgba(231,76,60,0.15);  color: var(--red); }
  .finding-sev.MEDIUM,.finding-sev.MED { background: rgba(243,156,18,0.15); color: var(--amber); }
  .finding-sev.LOW    { background: rgba(100,116,139,0.15); color: var(--muted); }
  .finding-title { font-size: 13px; font-weight: 500; color: var(--text); margin-bottom: 3px; }
  .finding-module { font-family: var(--mono); font-size: 10px; color: var(--muted); }
  .finding-desc { font-size: 12px; color: var(--muted); line-height: 1.5; }
  .finding-conf {
    font-family: var(--mono); font-size: 11px; color: var(--muted);
    background: var(--border); padding: 2px 8px; border-radius: 4px; white-space: nowrap;
  }

  /* ── Video info ── */
  .video-info-grid {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 10px; margin-bottom: 28px;
  }
  .vi-card {
    background: var(--surface2); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px 14px;
  }
  .vi-label { font-family: var(--mono); font-size: 10px; color: var(--muted); margin-bottom: 4px; }
  .vi-val { font-family: var(--mono); font-size: 13px; font-weight: 600; color: var(--text); }

  /* ── Calibration note ── */
  .calib-note {
    background: rgba(79,142,247,0.05);
    border: 1px solid rgba(79,142,247,0.2);
    border-radius: 8px; padding: 12px 16px;
    font-size: 12px; color: var(--muted); line-height: 1.6;
    margin-bottom: 28px;
  }
  .calib-note b { color: var(--accent); }


  /* ── Plain-English panel ── */
  .explain-panel {
    background: var(--surface2); border: 1px solid var(--border);
    border-left: 3px solid var(--accent2);
    border-radius: 0 10px 10px 0;
    padding: 18px 20px; margin-bottom: 28px;
  }
  .explain-header {
    font-family: var(--mono); font-size: 10px; letter-spacing: .12em;
    text-transform: uppercase; color: var(--accent2); margin-bottom: 14px;
  }
  .explain-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .explain-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px 14px;
  }
  .explain-mod {
    font-family: var(--mono); font-size: 10px; color: var(--muted);
    margin-bottom: 5px; display: flex; align-items: center;
    justify-content: space-between;
  }
  .explain-text { font-size: 12px; color: var(--text); line-height: 1.55; }

  /* ── Error ── */
  .error-box {
    background: rgba(231,76,60,0.08); border: 1px solid rgba(231,76,60,0.3);
    border-radius: 8px; padding: 16px 20px;
    font-family: var(--mono); font-size: 12px; color: var(--red);
    display: none;
  }
  .error-box.visible { display: block; }

  /* ── Scrollbar ── */
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-dot"></div>
    VideoForensics
  </div>
  <span class="version">v0.9 · pre-calibration</span>
</header>

<div class="layout">

  <!-- Sidebar -->
  <div class="sidebar">

    <div>
      <div class="panel-label">Upload Video</div>
      <div class="dropzone" id="dropzone">
        <input type="file" id="fileInput" accept="video/*,.mp4,.mov,.avi,.mkv,.mts,.m4v">
        <div class="drop-icon">🎬</div>
        <div class="drop-primary">Drop video here</div>
        <div class="drop-sub">or click to browse</div>
        <div class="drop-formats">MP4 · MOV · AVI · MKV · MTS</div>
      </div>
    </div>

    <div class="file-info" id="fileInfo">
      <div class="file-name" id="fileName">—</div>
      <div class="file-size" id="fileSize">—</div>
    </div>

    <button class="btn-analyse" id="analyseBtn" disabled onclick="runAnalysis()">
      <span>⬡</span> Analyse Video
    </button>

    <div class="progress-wrap" id="progressWrap">
      <div class="progress-label">
        <span id="progressLabel">Initialising…</span>
        <span id="progressPct">0%</span>
      </div>
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" id="progressFill"></div>
      </div>
      <div class="progress-steps">
        <div class="step" id="step-ingest"><div class="step-dot"></div>Ingest & hash video</div>
        <div class="step" id="step-meta">  <div class="step-dot"></div>Metadata analysis</div>
        <div class="step" id="step-comp">  <div class="step-dot"></div>Compression artifacts</div>
        <div class="step" id="step-noise"> <div class="step-dot"></div>Noise & camera fingerprint</div>
        <div class="step" id="step-temp">  <div class="step-dot"></div>Temporal integrity</div>
        <div class="step" id="step-light"> <div class="step-dot"></div>Lighting & physics</div>
        <div class="step" id="step-audio"> <div class="step-dot"></div>Audio forensics</div>
        <div class="step" id="step-fuse">  <div class="step-dot"></div>Fusion & verdict</div>
      </div>
    </div>

    <hr class="sep">

    <div>
      <div class="panel-label">How It Works</div>
      <div class="how">
        <div class="how-item">
          <div class="how-num">01</div>
          <div class="how-text">6 forensic modules analyse the video independently</div>
        </div>
        <div class="how-item">
          <div class="how-num">02</div>
          <div class="how-text">Fusion engine weights and combines all scores</div>
        </div>
        <div class="how-item">
          <div class="how-num">03</div>
          <div class="how-text">Verdict: Authentic → Inconclusive → Tampered</div>
        </div>
        <div class="how-item">
          <div class="how-num">04</div>
          <div class="how-text">Download PDF report for court or documentation</div>
        </div>
      </div>
    </div>

  </div>

  <!-- Main panel -->
  <div class="main" id="main">

    <div class="empty-state" id="emptyState">
      <div class="empty-icon">◈</div>
      <div class="empty-title">No video analysed yet</div>
      <div class="empty-sub">Upload a video file on the left to run a full 6-module forensic analysis.</div>
      <div class="hint">Analysis takes 60–90 seconds depending on file size</div>
    </div>

    <div class="error-box" id="errorBox"></div>

    <div id="results">

      <div class="result-header">
        <div>
          <div class="result-filename" id="rFilename">—</div>
          <div class="verdict-badge" id="verdictBadge">
            <div class="verdict-dot"></div>
            <span id="verdictLabel">—</span>
          </div>
          <div class="result-meta">
            <div class="meta-chip">Prob <b id="rProb">—</b></div>
            <div class="meta-chip">Conf <b id="rConf">—</b></div>
            <div class="meta-chip">Modules elevated <b id="rElev">—</b></div>
            <div class="meta-chip">Elapsed <b id="rElapsed">—</b></div>
          </div>
        </div>
        <a class="btn-pdf" id="pdfBtn" href="#" target="_blank">
          ⬇ PDF Report
        </a>
      </div>

      <div class="section-title">Module Scores</div>
      <div class="score-grid" id="scoreGrid"></div>


      <div class="section-title">Plain-English Summary</div>
      <div class="explain-panel">
        <div class="explain-header">🔍 What each module found</div>
        <div class="explain-grid" id="explainGrid"></div>
      </div>

      <div class="section-title">Findings</div>
      <div class="findings" id="findingsContainer"></div>

      <div class="section-title">Video Properties</div>
      <div class="video-info-grid" id="videoInfoGrid"></div>

      <div class="calib-note">
        <b>⚠ Pre-calibration notice:</b>
        <span id="calibNote">These results use default thresholds which have not been tuned to a specific video corpus.</span>
      </div>

    </div>

  </div>
</div>

<script>
const dropzone   = document.getElementById('dropzone');
const fileInput  = document.getElementById('fileInput');
const analyseBtn = document.getElementById('analyseBtn');
const fileInfo   = document.getElementById('fileInfo');
let selectedFile = null;
let reportPath   = null;

// Drag and drop
dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', e => {
  e.preventDefault(); dropzone.classList.remove('dragover');
  const f = e.dataTransfer.files[0];
  if (f) selectFile(f);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) selectFile(fileInput.files[0]);
});

function selectFile(f) {
  selectedFile = f;
  document.getElementById('fileName').textContent = f.name;
  document.getElementById('fileSize').textContent = formatBytes(f.size);
  fileInfo.classList.add('visible');
  analyseBtn.disabled = false;
  resetResults();
}

function formatBytes(b) {
  if (b < 1024*1024) return (b/1024).toFixed(1) + ' KB';
  return (b/1024/1024).toFixed(1) + ' MB';
}

function resetResults() {
  document.getElementById('results').classList.remove('visible');
  document.getElementById('errorBox').classList.remove('visible');
  document.getElementById('emptyState').style.display = 'flex';
}

// Progress animation
const STEPS = [
  ['step-ingest', 'Ingesting & hashing…', 8],
  ['step-meta',   'Metadata analysis…',  20],
  ['step-comp',   'Compression check…',  35],
  ['step-noise',  'Noise fingerprint…',  50],
  ['step-temp',   'Temporal integrity…', 65],
  ['step-light',  'Lighting analysis…',  78],
  ['step-audio',  'Audio forensics…',    88],
  ['step-fuse',   'Fusion & verdict…',   95],
];

let stepInterval = null;
let stepIdx = 0;

function startProgress() {
  document.getElementById('progressWrap').classList.add('visible');
  document.getElementById('progressFill').style.width = '0%';
  STEPS.forEach(([id]) => {
    const el = document.getElementById(id);
    el.classList.remove('active','done');
  });
  stepIdx = 0;
  advanceStep();
  stepInterval = setInterval(advanceStep, 8500);
}

function advanceStep() {
  if (stepIdx > 0) {
    document.getElementById(STEPS[stepIdx-1][0]).classList.remove('active');
    document.getElementById(STEPS[stepIdx-1][0]).classList.add('done');
  }
  if (stepIdx < STEPS.length) {
    const [id, label, pct] = STEPS[stepIdx];
    document.getElementById(id).classList.add('active');
    document.getElementById('progressLabel').textContent = label;
    document.getElementById('progressPct').textContent = pct + '%';
    document.getElementById('progressFill').style.width = pct + '%';
    stepIdx++;
  }
}

function stopProgress() {
  clearInterval(stepInterval);
  STEPS.forEach(([id]) => {
    const el = document.getElementById(id);
    el.classList.remove('active'); el.classList.add('done');
  });
  document.getElementById('progressFill').style.width = '100%';
  document.getElementById('progressPct').textContent = '100%';
  document.getElementById('progressLabel').textContent = 'Complete';
}

async function runAnalysis() {
  if (!selectedFile) return;
  analyseBtn.disabled = true;
  document.getElementById('emptyState').style.display = 'none';
  document.getElementById('errorBox').classList.remove('visible');
  document.getElementById('results').classList.remove('visible');
  startProgress();

  const fd = new FormData();
  fd.append('video', selectedFile);

  try {
    const r = await fetch('/analyse', { method: 'POST', body: fd });
    const data = await r.json();
    stopProgress();
    if (!r.ok || data.error) {
      showError(data.error || 'Analysis failed');
    } else {
      showResults(data);
    }
  } catch(e) {
    stopProgress();
    showError('Request failed: ' + e.message);
  } finally {
    analyseBtn.disabled = false;
    document.getElementById('progressWrap').classList.remove('visible');
  }
}

function showError(msg) {
  const eb = document.getElementById('errorBox');
  eb.textContent = '⚠ ' + msg;
  eb.classList.add('visible');
}


function scoreColor(s) {
  if (s < 0.20) return '#2ecc71';
  if (s < 0.30) return '#f39c12';
  return '#e74c3c';
}

function videoPlainEnglish(mod, score) {
  const ok = score < 0.20, mild = score < 0.30;
  const map = {
    metadata: ok
      ? 'The file\'s information tags (creation date, camera model, encoder software) are all consistent with a genuine unedited recording.'
      : mild
        ? 'Some tags look slightly unusual — possibly from a file transfer or messaging app re-encoding on send.'
        : 'Tags contradict each other or contain suspicious timestamps, suggesting the file was processed by editing software.',

    compression: ok
      ? 'No signs of re-encoding. The video has not been compressed more than once — no double-compression fingerprints detected.'
      : mild
        ? 'Minor compression artefacts found — could be from a messaging app or screen recorder re-encoding the file.'
        : 'Double-compression marks detected. The video has been compressed, possibly edited, then compressed again.',

    noise: ok
      ? 'The camera\'s unique sensor fingerprint (PRNU) stays consistent throughout — all frames appear to come from the same device.'
      : mild
        ? 'Slight variation in the camera fingerprint — could be from strong compression or a low-light scene.'
        : 'The camera fingerprint changes or disappears in parts of the video — a strong sign that frames from a different source were inserted.',

    temporal: ok
      ? 'Frames flow naturally with no duplications, jumps, or motion inconsistencies. The video plays as one continuous recording.'
      : mild
        ? 'Very minor motion irregularities detected — could be a dropped frame from the recording device.'
        : 'Frame sequence anomalies found — duplicate frames, motion discontinuities, or timestamp issues that suggest the video was re-assembled.',

    lighting: ok
      ? 'Light and shadows behave consistently throughout. No physically impossible lighting changes that would suggest compositing.'
      : mild
        ? 'Minor lighting variation detected — could be natural changes like clouds passing or a flickering light source.'
        : 'Lighting or shadows change in a way that violates physics, suggesting parts of the scene were replaced or overlaid.',

    audio: ok
      ? 'The audio track is consistent throughout — no sudden changes in background noise or acoustic environment.'
      : mild
        ? 'Minor audio variation detected — could be natural movement of the microphone or brief background noise change.'
        : 'The audio shows signs of editing — the acoustic environment changes suddenly, or the audio and video appear to be out of sync.',
  };
  return map[mod] || '—';
}

function renderExplain(scores) {
  const g = document.getElementById('explainGrid');
  const MODULE_LABELS = {
    metadata:'Metadata', compression:'Compression', noise:'Noise / PRNU',
    temporal:'Temporal', lighting:'Lighting', audio:'Audio',
  };
  g.innerHTML = '';
  for (const [mod, score] of Object.entries(scores)) {
    const col = scoreColor(score);
    const pct = Math.round(score * 100);
    g.innerHTML += `
      <div class="explain-card">
        <div class="explain-mod">
          <span>${MODULE_LABELS[mod] || mod}</span>
          <span style="color:${col};font-weight:600">${pct}%</span>
        </div>
        <div class="explain-text">${videoPlainEnglish(mod, score)}</div>
      </div>`;
  }
}

function showResults(d) {
  document.getElementById('emptyState').style.display = 'none';
  document.getElementById('results').classList.add('visible');

  // Header
  document.getElementById('rFilename').textContent = d.filename;
  document.getElementById('rProb').textContent    = (d.fused_probability * 100).toFixed(1) + '%';
  document.getElementById('rConf').textContent    = (d.confidence * 100).toFixed(0) + '%';
  document.getElementById('rElev').textContent    = d.n_elevated_modules + '/6';
  document.getElementById('rElapsed').textContent = d.elapsed_s + 's';

  // Verdict badge
  const badge = document.getElementById('verdictBadge');
  const v = d.verdict;
  badge.className = 'verdict-badge';
  if (v === 'likely_authentic' || v === 'authentic') badge.classList.add('authentic');
  else if (v === 'tampered' || v === 'likely_tampered') badge.classList.add('tampered');
  else badge.classList.add('inconclusive');
  document.getElementById('verdictLabel').textContent = d.verdict_label;

  // PDF button
  document.getElementById('pdfBtn').href = '/report/' + d.report_id;

  // Module scores
  const MODULE_NAMES = {
    metadata:    'Metadata', compression: 'Compression',
    noise:       'Noise',    temporal:    'Temporal',
    lighting:    'Lighting', audio:       'Audio',
  };
  const scoreGrid = document.getElementById('scoreGrid');
  scoreGrid.innerHTML = '';
  const THRESHOLD = 0.30;
  for (const [mod, score] of Object.entries(d.module_scores)) {
    const pct  = Math.round(score * 100);
    const elevated = score >= THRESHOLD;
    const color = score < 0.20 ? '#2ecc71'
                : score < THRESHOLD ? '#f39c12'
                : '#e74c3c';
    scoreGrid.innerHTML += `
      <div class="score-card ${elevated ? 'elevated' : ''}">
        <div class="score-card-name">${MODULE_NAMES[mod] || mod}</div>
        <div class="score-value" style="color:${color}">${pct}<span style="font-size:13px;opacity:0.6">%</span></div>
        <div class="score-bar-bg">
          <div class="score-bar-fill" style="width:${pct}%;background:${color}"></div>
        </div>
      </div>`;
  }

  // Plain-English summary
  renderExplain(d.module_scores);

  // Findings
  const fc = document.getElementById('findingsContainer');
  fc.innerHTML = '';
  if (!d.findings || d.findings.length === 0) {
    fc.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">No significant findings.</div>';
  } else {
    const sorted = [...d.findings].sort((a,b) => {
      const rank = {HIGH:0,MEDIUM:1,MED:1,LOW:2};
      return (rank[a.severity]||2) - (rank[b.severity]||2);
    });
    for (const f of sorted) {
      const sev = f.severity.toUpperCase();
      fc.innerHTML += `
        <div class="finding ${sev}">
          <span class="finding-sev ${sev}">${sev}</span>
          <div>
            <div class="finding-title">${f.title}</div>
            <div class="finding-module">${f.module}</div>
            <div class="finding-desc" style="margin-top:4px">${f.description}</div>
          </div>
          <span class="finding-conf">${(f.confidence*100).toFixed(0)}%</span>
        </div>`;
    }
  }

  // Video info
  const vig = document.getElementById('videoInfoGrid');
  const v2  = d.video;
  vig.innerHTML = '';
  const props = [
    ['Codec',     v2.codec || '—'],
    ['Resolution',v2.width && v2.height ? `${v2.width}×${v2.height}` : '—'],
    ['Frame Rate',v2.frame_rate ? v2.frame_rate.toFixed(2) + ' fps' : '—'],
    ['Duration',  v2.duration_s ? v2.duration_s.toFixed(1) + 's' : '—'],
    ['File Size', v2.file_size_bytes ? (v2.file_size_bytes/1024/1024).toFixed(1)+' MB' : '—'],
    ['Audio',     v2.has_audio ? (v2.audio_codec || 'Yes') : 'No'],
    ['Encoder',   v2.encoder || '—'],
    ['SHA-256',   v2.sha256 ? v2.sha256.slice(0,12)+'…' : '—'],
  ];
  for (const [label, val] of props) {
    vig.innerHTML += `<div class="vi-card"><div class="vi-label">${label}</div><div class="vi-val">${val}</div></div>`;
  }

  // Calib note
  document.getElementById('calibNote').textContent =
    d.calibration_note || 'Pre-calibration thresholds in use.';
}
</script>
</body>
</html>
"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/analyse", methods=["POST"])
def analyse():
    if "video" not in request.files:
        return jsonify({"error": "No video file uploaded"}), 400

    file = request.files["video"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    filename  = Path(file.filename).name
    tmp_path  = UPLOAD_DIR / f"{uuid.uuid4().hex}{Path(filename).suffix or '.mp4'}"
    pdf_path  = None
    report_id = uuid.uuid4().hex

    try:
        file.save(str(tmp_path))
        t0 = time.time()

        case   = get_pipeline().ingest(tmp_path)
        result = get_fusion().run(case)
        m      = result.metadata
        vp     = case.video_profile

        # Generate PDF
        pdf_path = REPORT_DIR / f"vf_{report_id}.pdf"
        try:
            get_builder().build(
                output_path        = pdf_path,
                case_id            = case.case_id,
                filename           = filename,
                video_profile      = vp,
                fusion_result      = result,
                analysis_timestamp = datetime.now(timezone.utc),
            )
        except Exception:
            pass  # PDF failure doesn't block JSON response

        elapsed  = round(time.time() - t0, 1)
        duration = (vp.total_frames / vp.frame_rate) if vp.frame_rate else 0

        findings = []
        for f in result.findings:
            fd = {
                "module":      f.module,
                "title":       f.title,
                "severity":    f.severity.value,
                "confidence":  round(f.confidence, 4),
                "description": f.description,
            }
            findings.append(fd)

        return jsonify({
            "filename":           filename,
            "verdict":            m["verdict"],
            "verdict_label":      m["verdict_label"],
            "fused_probability":  m["fused_probability"],
            "confidence":         m["confidence"],
            "n_elevated_modules": m["n_elevated_modules"],
            "elapsed_s":          elapsed,
            "module_scores":      {k: round(float(v), 4) for k, v in m["module_scores"].items()},
            "findings":           findings,
            "report_id":          report_id,
            "video": {
                "codec":          vp.codec,
                "width":          vp.width,
                "height":         vp.height,
                "frame_rate":     vp.frame_rate,
                "total_frames":   vp.total_frames,
                "duration_s":     round(duration, 1),
                "has_audio":      vp.has_audio,
                "audio_codec":    vp.audio_codec,
                "file_size_bytes":vp.file_size_bytes,
                "sha256":         vp.sha256_hash,
                "encoder":        vp.encoder_software,
            },
            "calibration_note": m.get("calibration_note", ""),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if tmp_path.exists():
            try: tmp_path.unlink()
            except: pass


@app.route("/report/<report_id>")
def report(report_id):
    # Sanitise
    report_id = "".join(c for c in report_id if c.isalnum())
    pdf_path  = REPORT_DIR / f"vf_{report_id}.pdf"
    if not pdf_path.exists():
        return "Report not found", 404
    return send_file(str(pdf_path), mimetype="application/pdf",
                     as_attachment=True, download_name="vf_report.pdf")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()

    print(f"\nVideoForensics — Web UI")
    print(f"{'─'*40}")
    print(f"  Open:  http://{args.host}:{args.port}")
    print(f"  Stop:  Ctrl+C")
    print()
    app.run(host=args.host, port=args.port, debug=False, threaded=False)
