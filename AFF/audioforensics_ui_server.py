#!/usr/bin/env python3
"""
AudioForensics — Standalone Web UI
Run:  python ui/server.py
Open: http://localhost:5051
"""
import logging, sys, tempfile, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
logging.disable(logging.WARNING)

from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

UPLOAD_DIR = Path(tempfile.gettempdir()) / "af_ui_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AudioForensics</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

:root {
  --bg:       #0b0d10;
  --surface:  #111418;
  --surface2: #171c23;
  --border:   #1f252e;
  --accent:   #00c9a7;
  --accent2:  #0e8a74;
  --glow:     rgba(0,201,167,.12);
  --green:    #27ae60;
  --amber:    #e67e22;
  --red:      #e74c3c;
  --text:     #dce8f0;
  --muted:    #4e6070;
  --mono:     'IBM Plex Mono', monospace;
  --sans:     'IBM Plex Sans', sans-serif;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: var(--sans); font-size: 14px; min-height: 100vh; line-height: 1.6; }

/* ── Header ── */
header {
  border-bottom: 1px solid var(--border);
  padding: 0 32px; height: 56px;
  display: flex; align-items: center; justify-content: space-between;
  background: var(--surface); position: sticky; top: 0; z-index: 100;
}
.logo { font-family: var(--mono); font-size: 13px; font-weight: 600; letter-spacing: .14em; color: var(--accent); text-transform: uppercase; display: flex; align-items: center; gap: 12px; }
.logo-wave {
  display: flex; align-items: center; gap: 2px; height: 20px;
}
.logo-wave span {
  display: block; width: 3px; border-radius: 2px; background: var(--accent);
  animation: wave 1.2s ease-in-out infinite;
}
.logo-wave span:nth-child(1) { height: 6px;  animation-delay: 0s; }
.logo-wave span:nth-child(2) { height: 14px; animation-delay: .15s; }
.logo-wave span:nth-child(3) { height: 10px; animation-delay: .3s; }
.logo-wave span:nth-child(4) { height: 18px; animation-delay: .45s; }
.logo-wave span:nth-child(5) { height: 8px;  animation-delay: .6s; }
.logo-wave span:nth-child(6) { height: 14px; animation-delay: .75s; }
.logo-wave span:nth-child(7) { height: 6px;  animation-delay: .9s; }
@keyframes wave { 0%,100%{transform:scaleY(1)} 50%{transform:scaleY(.4)} }

.header-right { display: flex; align-items: center; gap: 12px; }
.version { font-family: var(--mono); font-size: 11px; color: var(--muted); background: var(--border); padding: 3px 8px; border-radius: 4px; }
.api-link { font-family: var(--mono); font-size: 11px; color: var(--accent2); text-decoration: none; border: 1px solid var(--accent2); padding: 3px 10px; border-radius: 4px; transition: all .2s; }
.api-link:hover { background: rgba(14,138,116,.15); }

/* ── Layout ── */
.layout { display: grid; grid-template-columns: 360px 1fr; min-height: calc(100vh - 56px); }

/* ── Sidebar ── */
.sidebar { border-right: 1px solid var(--border); padding: 28px 24px; background: var(--surface); display: flex; flex-direction: column; gap: 20px; }
.panel-label { font-family: var(--mono); font-size: 10px; letter-spacing: .15em; text-transform: uppercase; color: var(--muted); margin-bottom: 10px; }

/* ── Dropzone ── */
.dropzone { border: 1.5px dashed var(--border); border-radius: 10px; padding: 30px 20px; text-align: center; cursor: pointer; transition: all .2s; background: var(--surface2); position: relative; }
.dropzone:hover, .dropzone.dragover { border-color: var(--accent); background: var(--glow); }
.dropzone input[type=file] { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; }
.drop-icon { font-size: 32px; margin-bottom: 10px; }
.drop-primary { font-weight: 500; font-size: 13px; margin-bottom: 4px; }
.drop-sub { font-size: 12px; color: var(--muted); }
.drop-formats { margin-top: 10px; font-family: var(--mono); font-size: 10px; color: var(--muted); background: var(--border); padding: 4px 10px; border-radius: 4px; display: inline-block; }

/* ── File info ── */
.file-info { background: var(--surface2); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; display: none; }
.file-info.visible { display: block; }
.file-name { font-family: var(--mono); font-size: 12px; color: var(--accent); word-break: break-all; margin-bottom: 3px; }
.file-size { font-size: 11px; color: var(--muted); }

/* ── Button ── */
.btn-analyse { background: var(--accent); color: #0b0d10; border: none; border-radius: 8px; padding: 13px 20px; font-family: var(--mono); font-size: 12px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; cursor: pointer; width: 100%; transition: all .2s; display: flex; align-items: center; justify-content: center; gap: 8px; }
.btn-analyse:hover:not(:disabled) { background: #00e8c0; transform: translateY(-1px); box-shadow: 0 4px 20px var(--glow); }
.btn-analyse:disabled { opacity: .35; cursor: not-allowed; transform: none; }

/* ── Progress ── */
.progress-wrap { display: none; }
.progress-wrap.visible { display: block; }
.progress-label { font-family: var(--mono); font-size: 11px; color: var(--muted); margin-bottom: 8px; display: flex; justify-content: space-between; }
.progress-bar-bg { height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; }
.progress-bar-fill { height: 100%; background: var(--accent); border-radius: 2px; transition: width .4s ease; width: 0%; box-shadow: 0 0 8px var(--accent); }
.progress-steps { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
.step { font-family: var(--mono); font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 8px; transition: color .3s; }
.step.active { color: var(--text); }
.step.done   { color: var(--green); }
.step-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--border); flex-shrink: 0; transition: background .3s; }
.step.active .step-dot { background: var(--accent); box-shadow: 0 0 6px var(--accent); animation: pulse 1s infinite; }
.step.done   .step-dot { background: var(--green); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

/* ── How it works ── */
.sep { border: none; border-top: 1px solid var(--border); }
.how { display: flex; flex-direction: column; gap: 8px; }
.how-item { display: flex; gap: 10px; align-items: flex-start; }
.how-num { font-family: var(--mono); font-size: 10px; font-weight: 600; color: var(--accent); background: rgba(0,201,167,.1); border-radius: 4px; padding: 2px 6px; flex-shrink: 0; margin-top: 1px; }
.how-text { font-size: 12px; color: var(--muted); line-height: 1.5; }

/* ── Main ── */
.main { padding: 32px 36px; overflow-y: auto; }

/* ── Empty state ── */
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 65vh; text-align: center; color: var(--muted); gap: 16px; }
.empty-waveform { display: flex; align-items: center; gap: 3px; opacity: .15; margin-bottom: 8px; }
.empty-waveform span { display: block; width: 4px; border-radius: 2px; background: var(--accent); }
.empty-title { font-size: 17px; font-weight: 500; color: var(--text); }
.empty-sub { font-size: 13px; max-width: 320px; line-height: 1.7; }
.hint { font-family: var(--mono); font-size: 11px; background: var(--surface2); border: 1px solid var(--border); padding: 8px 14px; border-radius: 6px; }

/* ── Results ── */
#results { display: none; }
#results.visible { display: block; }

/* ── Result header ── */
.result-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 28px; flex-wrap: wrap; }
.result-filename { font-family: var(--mono); font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.verdict-badge { display: inline-flex; align-items: center; gap: 8px; padding: 9px 18px; border-radius: 8px; font-family: var(--mono); font-size: 13px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; border: 1px solid transparent; }
.verdict-badge.authentic   { background: rgba(39,174,96,.12);  color: #27ae60; border-color: rgba(39,174,96,.3); }
.verdict-badge.inconclusive{ background: rgba(230,126,34,.12); color: #e67e22; border-color: rgba(230,126,34,.3); }
.verdict-badge.tampered    { background: rgba(231,76,60,.12);  color: #e74c3c; border-color: rgba(231,76,60,.3); }
.verdict-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }

.result-meta { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
.meta-chip { font-family: var(--mono); font-size: 11px; color: var(--muted); background: var(--surface2); border: 1px solid var(--border); padding: 4px 10px; border-radius: 4px; }
.meta-chip b { color: var(--text); }

/* ── Section titles ── */
.section-title { font-family: var(--mono); font-size: 10px; letter-spacing: .15em; text-transform: uppercase; color: var(--muted); margin-bottom: 14px; display: flex; align-items: center; gap: 10px; }
.section-title::after { content:''; flex:1; height:1px; background: var(--border); }

/* ── Score grid ── */
.score-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; margin-bottom: 28px; }
.score-card { background: var(--surface2); border: 1px solid var(--border); border-radius: 10px; padding: 16px; transition: border-color .2s; position: relative; overflow: hidden; }
.score-card.elevated { border-color: rgba(231,76,60,.45); }
.score-card::before { content:''; position:absolute; top:0; left:0; right:0; height:2px; background: transparent; transition: background .3s; }
.score-card.elevated::before { background: linear-gradient(90deg, transparent, rgba(231,76,60,.6), transparent); }
.score-card-name { font-family: var(--mono); font-size: 10px; letter-spacing: .1em; text-transform: uppercase; color: var(--muted); margin-bottom: 10px; }
.score-value { font-family: var(--mono); font-size: 24px; font-weight: 600; line-height: 1; margin-bottom: 10px; }
.score-bar-bg { height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; }
.score-bar-fill { height: 100%; border-radius: 2px; transition: width .9s ease; }
.score-desc { font-size: 10px; color: var(--muted); margin-top: 8px; line-height: 1.4; }

/* ── Plain-English panel ── */
.explain-panel { background: var(--surface2); border: 1px solid var(--border); border-left: 3px solid var(--accent2); border-radius: 0 10px 10px 0; padding: 18px 20px; margin-bottom: 28px; }
.explain-header { font-family: var(--mono); font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: var(--accent); margin-bottom: 14px; }
.explain-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.explain-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
.explain-mod { font-family: var(--mono); font-size: 10px; color: var(--muted); margin-bottom: 5px; display: flex; align-items: center; justify-content: space-between; }
.explain-text { font-size: 12px; color: var(--text); line-height: 1.55; }

/* ── Findings ── */
.findings { display: flex; flex-direction: column; gap: 10px; margin-bottom: 28px; }
.finding { background: var(--surface2); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; border-left: 3px solid var(--border); display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: start; }
.finding.HIGH   { border-left-color: var(--red); }
.finding.MEDIUM { border-left-color: var(--amber); }
.finding.LOW    { border-left-color: var(--muted); }
.finding-sev { font-family: var(--mono); font-size: 9px; font-weight: 600; letter-spacing: .1em; text-transform: uppercase; padding: 2px 7px; border-radius: 3px; margin-top: 2px; white-space: nowrap; }
.finding-sev.HIGH   { background: rgba(231,76,60,.15);  color: var(--red); }
.finding-sev.MEDIUM { background: rgba(230,126,34,.15); color: var(--amber); }
.finding-sev.LOW    { background: rgba(78,96,112,.15);  color: var(--muted); }
.finding-title  { font-size: 13px; font-weight: 500; margin-bottom: 3px; }
.finding-module { font-family: var(--mono); font-size: 10px; color: var(--muted); }
.finding-desc   { font-size: 12px; color: var(--muted); line-height: 1.5; margin-top: 4px; }
.finding-conf   { font-family: var(--mono); font-size: 11px; color: var(--muted); background: var(--border); padding: 2px 8px; border-radius: 4px; white-space: nowrap; }

/* ── Temporal location tag ── */
.finding-ts { font-family: var(--mono); font-size: 10px; color: var(--accent); background: rgba(0,201,167,.08); border: 1px solid rgba(0,201,167,.2); padding: 2px 7px; border-radius: 4px; display: inline-block; margin-top: 5px; }

/* ── Audio properties grid ── */
.info-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin-bottom: 28px; }
.vi-card { background: var(--surface2); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
.vi-label { font-family: var(--mono); font-size: 10px; color: var(--muted); margin-bottom: 4px; }
.vi-val   { font-family: var(--mono); font-size: 13px; font-weight: 600; color: var(--text); }

/* ── Calibration note ── */
.calib-note { background: rgba(0,201,167,.04); border: 1px solid rgba(0,201,167,.18); border-radius: 8px; padding: 12px 16px; font-size: 12px; color: var(--muted); line-height: 1.6; margin-bottom: 28px; }
.calib-note b { color: var(--accent); }

/* ── Error ── */
.error-box { background: rgba(231,76,60,.08); border: 1px solid rgba(231,76,60,.3); border-radius: 8px; padding: 14px 18px; font-family: var(--mono); font-size: 12px; color: var(--red); display: none; margin-bottom: 16px; }
.error-box.visible { display: block; }

::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-wave">
      <span></span><span></span><span></span><span></span>
      <span></span><span></span><span></span>
    </div>
    AudioForensics
  </div>
  <div class="header-right">
    <a class="api-link" href="/api/v1/docs" target="_blank">API Docs</a>
    <span class="version">v0.9 · pre-calibration</span>
  </div>
</header>

<div class="layout">

  <!-- ── Sidebar ── -->
  <div class="sidebar">

    <div>
      <div class="panel-label">Upload Audio File</div>
      <div class="dropzone" id="dropzone">
        <input type="file" id="fileInput"
          accept="audio/*,.wav,.mp3,.m4a,.aac,.flac,.ogg,.opus,.amr,.wma">
        <div class="drop-icon">🎙</div>
        <div class="drop-primary">Drop audio here</div>
        <div class="drop-sub">or click to browse</div>
        <div class="drop-formats">WAV · MP3 · M4A · FLAC · AAC · OGG · OPUS · AMR</div>
      </div>
    </div>

    <div class="file-info" id="fileInfo">
      <div class="file-name" id="fileName">—</div>
      <div class="file-size" id="fileSize">—</div>
    </div>

    <button class="btn-analyse" id="analyseBtn" disabled onclick="runAnalysis()">
      ▶ Analyse Audio
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
        <div class="step" id="s-ingest"><div class="step-dot"></div>Ingest &amp; decode audio</div>
        <div class="step" id="s-meta">  <div class="step-dot"></div>Metadata analysis</div>
        <div class="step" id="s-enf">   <div class="step-dot"></div>ENF frequency check</div>
        <div class="step" id="s-noise"> <div class="step-dot"></div>Noise floor analysis</div>
        <div class="step" id="s-comp">  <div class="step-dot"></div>Compression artifacts</div>
        <div class="step" id="s-verb">  <div class="step-dot"></div>Reverberation analysis</div>
        <div class="step" id="s-voice"> <div class="step-dot"></div>Voice forensics</div>
        <div class="step" id="s-fuse">  <div class="step-dot"></div>Fusion &amp; verdict</div>
      </div>
    </div>

    <hr class="sep">

    <div>
      <div class="panel-label">How It Works</div>
      <div class="how">
        <div class="how-item">
          <div class="how-num">01</div>
          <div class="how-text">6 independent modules each examine a different physical property of the audio</div>
        </div>
        <div class="how-item">
          <div class="how-num">02</div>
          <div class="how-text">ENF · Noise · Compression · Reverberation · Voice · Metadata</div>
        </div>
        <div class="how-item">
          <div class="how-num">03</div>
          <div class="how-text">Fusion engine weighs all evidence — metadata alone cannot convict</div>
        </div>
        <div class="how-item">
          <div class="how-num">04</div>
          <div class="how-text">Verdict: Likely Authentic → Inconclusive → Likely Tampered → Tampered</div>
        </div>
      </div>
    </div>

  </div>

  <!-- ── Main panel ── -->
  <div class="main">

    <div class="empty-state" id="emptyState">
      <div class="empty-waveform">
        <span style="height:12px"></span><span style="height:28px"></span>
        <span style="height:20px"></span><span style="height:40px"></span>
        <span style="height:16px"></span><span style="height:36px"></span>
        <span style="height:24px"></span><span style="height:44px"></span>
        <span style="height:18px"></span><span style="height:30px"></span>
        <span style="height:22px"></span><span style="height:38px"></span>
        <span style="height:14px"></span><span style="height:26px"></span>
        <span style="height:10px"></span>
      </div>
      <div class="empty-title">No audio analysed yet</div>
      <div class="empty-sub">Upload any audio file to run a full 6-module forensic integrity analysis.</div>
      <div class="hint">Analysis typically takes 10–30 seconds</div>
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
            <div class="meta-chip">Probability <b id="rProb">—</b></div>
            <div class="meta-chip">Confidence <b id="rConf">—</b></div>
            <div class="meta-chip">Elevated <b id="rElev">—</b></div>
            <div class="meta-chip">Elapsed <b id="rTime">—</b></div>
          </div>
        </div>
      </div>

      <div class="section-title">Module Scores</div>
      <div class="score-grid" id="scoreGrid"></div>

      <div class="explain-panel">
        <div class="explain-header">🔍 Plain-English Summary</div>
        <div class="explain-grid" id="explainGrid"></div>
      </div>

      <div class="section-title">Findings</div>
      <div class="findings" id="findingsContainer"></div>

      <div class="section-title">Audio Properties</div>
      <div class="info-grid" id="infoGrid"></div>

      <div class="calib-note">
        <b>⚠ Pre-calibration notice:</b>
        <span id="calibNote">Default thresholds in use — results should be treated as indicative.</span>
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

dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', e => {
  e.preventDefault(); dropzone.classList.remove('dragover');
  if (e.dataTransfer.files[0]) selectFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) selectFile(fileInput.files[0]);
});

function selectFile(f) {
  selectedFile = f;
  document.getElementById('fileName').textContent = f.name;
  document.getElementById('fileSize').textContent = fmtBytes(f.size);
  fileInfo.classList.add('visible');
  analyseBtn.disabled = false;
  document.getElementById('results').classList.remove('visible');
  document.getElementById('errorBox').classList.remove('visible');
  document.getElementById('emptyState').style.display = 'flex';
}

function fmtBytes(b) {
  return b < 1048576 ? (b/1024).toFixed(1)+' KB' : (b/1048576).toFixed(1)+' MB';
}

// ── Progress ──────────────────────────────────────────────────────────────────
const STEPS = [
  ['s-ingest','Ingesting & decoding…',  10],
  ['s-meta',  'Metadata analysis…',     22],
  ['s-enf',   'ENF frequency check…',   36],
  ['s-noise', 'Noise floor analysis…',  50],
  ['s-comp',  'Compression check…',     63],
  ['s-verb',  'Reverberation check…',   76],
  ['s-voice', 'Voice forensics…',       88],
  ['s-fuse',  'Fusion & verdict…',      95],
];
let stepIdx = 0, stepTimer = null;

function startProgress() {
  document.getElementById('progressWrap').classList.add('visible');
  document.getElementById('progressFill').style.width = '0%';
  STEPS.forEach(([id]) => {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active','done');
  });
  stepIdx = 0;
  advanceStep();
  stepTimer = setInterval(advanceStep, 6000);
}
function advanceStep() {
  if (stepIdx > 0) {
    const el = document.getElementById(STEPS[stepIdx-1][0]);
    if (el) { el.classList.remove('active'); el.classList.add('done'); }
  }
  if (stepIdx < STEPS.length) {
    const [id, label, pct] = STEPS[stepIdx];
    const el = document.getElementById(id);
    if (el) el.classList.add('active');
    document.getElementById('progressLabel').textContent = label;
    document.getElementById('progressPct').textContent   = pct + '%';
    document.getElementById('progressFill').style.width  = pct + '%';
    stepIdx++;
  }
}
function stopProgress() {
  clearInterval(stepTimer);
  STEPS.forEach(([id]) => {
    const el = document.getElementById(id);
    if (el) { el.classList.remove('active'); el.classList.add('done'); }
  });
  document.getElementById('progressFill').style.width = '100%';
  document.getElementById('progressPct').textContent  = '100%';
  document.getElementById('progressLabel').textContent= 'Complete';
}

// ── Analysis ──────────────────────────────────────────────────────────────────
async function runAnalysis() {
  if (!selectedFile) return;
  analyseBtn.disabled = true;
  document.getElementById('emptyState').style.display = 'none';
  document.getElementById('errorBox').classList.remove('visible');
  document.getElementById('results').classList.remove('visible');
  startProgress();

  const fd = new FormData();
  fd.append('audio', selectedFile);

  try {
    const r = await fetch('/api/v1/analyze', { method: 'POST', body: fd });
    const d = await r.json();
    stopProgress();
    if (!r.ok || d.error) {
      showError(d.error || 'Analysis failed');
    } else {
      showResults(d);
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

// ── Render results ────────────────────────────────────────────────────────────
const MODULE_NAMES = {
  metadata:'Metadata', enf:'ENF', noise:'Noise Floor',
  compression:'Compression', reverberation:'Reverberation', voice:'Voice',
};
const MODULE_DESC = {
  metadata:    'File tags, timestamps & container',
  enf:         'Mains hum continuity (50/60 Hz)',
  noise:       'Background noise floor consistency',
  compression: 'Lossy codec double-encoding marks',
  reverberation:'Room acoustic fingerprint',
  voice:       'Speaker pitch & spectral stability',
};

function scoreColor(s) {
  if (s < 0.20) return 'var(--green)';
  if (s < 0.35) return 'var(--amber)';
  return 'var(--red)';
}

function plainEnglish(mod, score) {
  const ok = score < 0.20, mild = score < 0.35;
  const map = {
    metadata: ok
      ? 'File tags and timestamps are consistent with a genuine recording.'
      : mild
        ? 'Some tags look slightly unusual — possibly from a file transfer or messaging app.'
        : 'Tags contradict each other or contain suspicious timestamps, suggesting the file was processed by editing software.',
    enf: ok
      ? 'The electrical mains hum (where present) flows smoothly with no breaks or jumps.'
      : mild
        ? 'The ENF signal is faint or slightly uneven — possibly recorded outdoors or with a battery device.'
        : 'The mains frequency shows a discontinuity — a strong indicator of a splice point in the recording.',
    noise: ok
      ? 'Background noise is stable throughout — consistent with one recording environment.'
      : mild
        ? 'Very minor background noise variation detected — likely innocent (AGC or mic movement).'
        : 'The noise floor changes abruptly, like two recordings from different environments were joined.',
    compression: ok
      ? 'No signs of re-encoding. The audio has not passed through a lossy codec more than once.'
      : mild
        ? 'Minor compression artefacts present — could be from a messaging app re-encoding on send.'
        : 'Double-compression marks detected. This audio has been compressed, possibly edited, and compressed again.',
    reverberation: ok
      ? 'Room acoustics stay consistent throughout — the recording sounds like one place.'
      : mild
        ? 'Slight variation in room echo — possibly a small room with reflective surfaces or mic movement.'
        : 'Room echo changes noticeably mid-recording, suggesting the audio came from two different environments.',
    voice: ok
      ? 'Speaker pitch and vocal character stay natural and consistent across the recording.'
      : mild
        ? 'Minor pitch variation detected — within the range of natural speech but worth noting.'
        : 'The speaker\'s voice shows an unusual discontinuity — inconsistent with natural continuous speech.',
  };
  return map[mod] || '—';
}

function showResults(d) {
  document.getElementById('emptyState').style.display = 'none';
  document.getElementById('results').classList.add('visible');

  // Header
  document.getElementById('rFilename').textContent = d.filename;
  document.getElementById('rProb').textContent     = (d.fused_probability * 100).toFixed(1) + '%';
  document.getElementById('rConf').textContent     = (d.confidence * 100).toFixed(0) + '%';
  document.getElementById('rElev').textContent     = d.elevated_modules.length + ' / 6';
  document.getElementById('rTime').textContent     = d.elapsed_s + 's';

  // Verdict badge
  const badge = document.getElementById('verdictBadge');
  badge.className = 'verdict-badge';
  const v = d.verdict;
  if      (v === 'likely_authentic') badge.classList.add('authentic');
  else if (v === 'tampered' || v === 'likely_tampered') badge.classList.add('tampered');
  else badge.classList.add('inconclusive');
  document.getElementById('verdictLabel').textContent = d.verdict_label;

  // Score cards
  const sg = document.getElementById('scoreGrid');
  sg.innerHTML = '';
  for (const [mod, score] of Object.entries(d.module_scores)) {
    const pct = Math.round(score * 100);
    const col = scoreColor(score);
    const elv = score >= 0.35;
    const skipped = d.skipped_modules && d.skipped_modules.includes(mod);
    sg.innerHTML += `
      <div class="score-card ${elv ? 'elevated' : ''}">
        <div class="score-card-name">${MODULE_NAMES[mod] || mod}${skipped ? ' <span style="font-size:9px;color:var(--muted)">[skipped]</span>' : ''}</div>
        <div class="score-value" style="color:${skipped ? 'var(--muted)' : col}">
          ${skipped ? '—' : pct + '<span style="font-size:13px;opacity:.55">%</span>'}
        </div>
        <div class="score-bar-bg">
          <div class="score-bar-fill" style="width:${skipped?0:pct}%;background:${col}"></div>
        </div>
        <div class="score-desc">${MODULE_DESC[mod] || ''}</div>
      </div>`;
  }

  // Plain-English explanations
  const eg = document.getElementById('explainGrid');
  eg.innerHTML = '';
  for (const [mod, score] of Object.entries(d.module_scores)) {
    const col = scoreColor(score);
    const pct = Math.round(score * 100);
    eg.innerHTML += `
      <div class="explain-card">
        <div class="explain-mod">
          <span>${MODULE_NAMES[mod] || mod}</span>
          <span style="color:${col};font-weight:600">${pct}%</span>
        </div>
        <div class="explain-text">${plainEnglish(mod, score)}</div>
      </div>`;
  }

  // Findings
  const fc = document.getElementById('findingsContainer');
  fc.innerHTML = '';
  if (!d.findings || d.findings.length === 0) {
    fc.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">No significant findings.</div>';
  } else {
    const order = {HIGH:0,MEDIUM:1,LOW:2};
    const sorted = [...d.findings].sort((a,b) =>
      (order[a.severity.toUpperCase()]||2) - (order[b.severity.toUpperCase()]||2)
    );
    for (const f of sorted) {
      const sev = f.severity.toUpperCase();
      const ts  = f.temporal_location
        ? `<span class="finding-ts">⏱ ${f.temporal_location.start_seconds.toFixed(2)}s – ${f.temporal_location.end_seconds.toFixed(2)}s</span>`
        : '';
      fc.innerHTML += `
        <div class="finding ${sev}">
          <span class="finding-sev ${sev}">${sev}</span>
          <div>
            <div class="finding-title">${f.title}</div>
            <div class="finding-module">${f.module}</div>
            <div class="finding-desc">${f.description}</div>
            ${ts}
          </div>
          <span class="finding-conf">${Math.round(f.confidence * 100)}%</span>
        </div>`;
    }
  }

  // Audio properties
  const ap = d.audio;
  const ig = document.getElementById('infoGrid');
  ig.innerHTML = [
    ['Codec',        ap.codec || '—'],
    ['Format',       ap.codec_enum || '—'],
    ['Duration',     ap.duration_s != null ? ap.duration_s.toFixed(1)+'s' : '—'],
    ['Sample Rate',  ap.sample_rate ? ap.sample_rate.toLocaleString()+' Hz' : '—'],
    ['Bitrate',      ap.bitrate_bps ? Math.round(ap.bitrate_bps/1000)+' kbps' : '—'],
    ['Channels',     ap.channels != null ? ap.channels : '—'],
    ['Container',    ap.container || '—'],
    ['File Size',    ap.file_size_bytes ? fmtBytes(ap.file_size_bytes) : '—'],
  ].map(([l,v]) => `<div class="vi-card"><div class="vi-label">${l}</div><div class="vi-val">${v}</div></div>`).join('');

  document.getElementById('calibNote').textContent =
    d.calibration_note || 'Pre-calibration thresholds in use.';
}
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/v1/docs")
def api_docs():
    from flask import render_template_string
    return render_template_string(API_DOCS_HTML)


# Main analysis endpoint (used by the UI)
@app.route("/api/v1/analyze", methods=["POST"])
def analyze():
    if "audio" not in request.files:
        return _err("No 'audio' field in request", "MISSING_FILE")
    file = request.files["audio"]
    if not file.filename:
        return _err("Empty filename", "EMPTY_FILENAME")

    filename = Path(file.filename).name
    suffix   = Path(filename).suffix or ".m4a"
    tmp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"

    try:
        file.save(str(tmp_path))
        return jsonify(_run_analysis(tmp_path, filename)), 200
    except Exception as e:
        import traceback as _tb
        tb = _tb.format_exc()
        return _err(f"Analysis failed: {e} ||| TRACEBACK: {tb}", "ANALYSIS_ERROR", 500)
    finally:
        if tmp_path.exists():
            try: tmp_path.unlink()
            except: pass


@app.route("/api/v1/health", methods=["GET"])
def health():
    from flask import Response
    return Response("OK", status=200, mimetype="text/plain")


@app.route("/api/v1/version", methods=["GET"])
def version():
    return jsonify({
        "system":          "AudioForensics",
        "version":         "0.9.0-pre-calibration",
        "phases_complete": [0,1,2,3,4,5,6,7,8],
        "modules": ["metadata","enf","noise","compression",
                    "reverberation","voice","fusion"],
    })


@app.errorhandler(413)
def too_large(e):
    return _err("File too large. Maximum: 200MB", "FILE_TOO_LARGE", 413)

@app.errorhandler(404)
def not_found(e):
    return _err("Endpoint not found", "NOT_FOUND", 404)

@app.errorhandler(405)
def method_not_allowed(e):
    return _err("Method not allowed", "METHOD_NOT_ALLOWED", 405)


# ── Core analysis logic ───────────────────────────────────────────────────────

def _run_analysis(audio_path: Path, filename: str) -> dict:
    """Run full 6-module analysis. Returns a JSON-serialisable dict."""
    import time as _time, datetime as _dt
    t0 = _time.time()

    from core.ingestion.pipeline                     import IngestionPipeline
    from modules.metadata.metadata_module            import MetadataModule
    from modules.enf.enf_module                      import ENFModule
    from modules.noise.noise_module                  import NoiseModule
    from modules.compression.compression_module      import CompressionModule
    from modules.reverberation.reverberation_module  import ReverberationModule
    from modules.voice.voice_module                  import VoiceModule
    from modules.fusion.fusion_engine                import FusionEngine

    work = UPLOAD_DIR / f"af_work_{uuid.uuid4().hex}"
    case = IngestionPipeline(work).ingest(audio_path)

    for Mod in [MetadataModule, ENFModule, NoiseModule,
                CompressionModule, ReverberationModule, VoiceModule]:
        case.add_score(Mod()._safe_run(case))

    result  = FusionEngine().fuse(case)
    ap      = case.audio_profile
    elapsed = round(_time.time() - t0, 2)

    # ── Module scores from case (List[ModuleScore]) ───────────────────────
    module_scores   = {}
    skipped_modules = []
    for ms in case.module_scores:
        module_scores[ms.module] = round(float(ms.score), 4)
        if ms.skipped:
            skipped_modules.append(ms.module)

    # ── Findings ──────────────────────────────────────────────────────────
    findings = []
    for f in result.all_findings:
        fd = {
            "module":      f.module,
            "title":       f.title,
            "severity":    f.severity.name if hasattr(f.severity, "name") else str(f.severity),
            "confidence":  round(float(f.confidence), 4),
            "description": f.description,
        }
        tl = getattr(f, "temporal_location", None)
        if tl:
            fd["temporal_location"] = {
                "start_seconds": round(float(tl.start_seconds), 3),
                "end_seconds":   round(float(tl.end_seconds),   3),
            }
        findings.append(fd)

    # ── Audio profile ─────────────────────────────────────────────────────
    ce = ap.codec_enum  # May be None for unrecognised codecs
    try:
        ce_name     = ce.name              if ce is not None else "UNKNOWN"
        ce_lossy    = bool(ce.is_lossy)    if ce is not None else None
        ce_mob_loss = bool(ce.is_mobile_lossless) if ce is not None else None
    except Exception:
        ce_name, ce_lossy, ce_mob_loss = "UNKNOWN", None, None

    ef = ap.effective_max_freq
    dur = ap.duration_seconds

    return {
        "case_id":            case.case_id,
        "status":             "completed",
        "timestamp":          _dt.datetime.utcnow().isoformat() + "Z",
        "filename":           filename,
        "elapsed_s":          elapsed,

        "verdict":            result.verdict.value,
        "verdict_label":      result.verdict.label,
        "fused_probability":  round(float(result.fused_probability), 4),
        "confidence":         round(float(result.confidence), 4),

        "elevated_modules":     result.elevated_modules,
        "skipped_modules":      skipped_modules,
        "corroboration_factor": round(float(result.corroboration_factor), 4),
        "has_conflict":         result.has_conflict,
        "conflict_description": result.conflict_description or "",

        "module_scores":    module_scores,
        "adjusted_weights": {k: round(float(v), 4) for k, v in result.adjusted_weights.items()},

        "total_findings": len(findings),
        "findings":       findings,

        "calibration_note": result.calibration_note or "",

        "audio": {
            "codec":              ap.codec_name,
            "codec_enum":         ce_name,
            "is_lossy":           ce_lossy,
            "is_mobile_lossless": ce_mob_loss,
            "duration_s":         round(float(dur), 3) if dur else None,
            "sample_rate":        ap.sample_rate,
            "bitrate_bps":        ap.bitrate_bps,
            "channels":           ap.channels,
            "container":          ap.container_format,
            "effective_max_freq_hz": round(float(ef), 1) if ef else None,
            "file_size_bytes":    ap.file_size_bytes,
        },
    }


def _err(message: str, code: str = "ERROR", status: int = 400):
    return jsonify({"error": message, "code": code}), status


# ── API Docs page ─────────────────────────────────────────────────────────────
API_DOCS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AudioForensics — API Reference</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');
:root {
  --bg:#0b0d10; --surface:#111418; --surface2:#171c23; --border:#1f252e;
  --accent:#00c9a7; --text:#dce8f0; --muted:#4e6070;
  --green:#27ae60; --amber:#e67e22; --red:#e74c3c;
  --mono:'IBM Plex Mono',monospace; --sans:'IBM Plex Sans',sans-serif;
}
* { box-sizing:border-box; margin:0; padding:0; }
body { background:var(--bg); color:var(--text); font-family:var(--sans); font-size:14px; line-height:1.7; }
header { background:var(--surface); border-bottom:1px solid var(--border); padding:0 40px; height:56px; display:flex; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:10; }
.logo { font-family:var(--mono); font-size:13px; font-weight:600; color:var(--accent); letter-spacing:.12em; text-transform:uppercase; }
.back { font-family:var(--mono); font-size:11px; color:var(--muted); text-decoration:none; border:1px solid var(--border); padding:4px 10px; border-radius:4px; transition:all .2s; }
.back:hover { border-color:var(--accent); color:var(--accent); }
main { max-width: 900px; margin: 0 auto; padding: 48px 32px; }
h1 { font-size:28px; font-weight:600; color:var(--text); margin-bottom:8px; }
.subtitle { font-size:14px; color:var(--muted); margin-bottom:48px; }
h2 { font-family:var(--mono); font-size:14px; font-weight:600; color:var(--accent); letter-spacing:.1em; text-transform:uppercase; margin:40px 0 16px; border-bottom:1px solid var(--border); padding-bottom:10px; }
h3 { font-size:16px; font-weight:600; margin:28px 0 10px; }
p { color:var(--muted); margin-bottom:12px; }

.endpoint { background:var(--surface); border:1px solid var(--border); border-radius:10px; margin-bottom:24px; overflow:hidden; }
.endpoint-header { padding:16px 20px; display:flex; align-items:center; gap:14px; border-bottom:1px solid var(--border); }
.method { font-family:var(--mono); font-size:11px; font-weight:600; padding:3px 10px; border-radius:4px; }
.method.POST { background:rgba(0,201,167,.15); color:var(--accent); }
.method.GET  { background:rgba(39,174,96,.15);  color:#27ae60; }
.path  { font-family:var(--mono); font-size:14px; font-weight:600; color:var(--text); }
.ep-desc { padding:0 20px 16px; font-size:13px; color:var(--muted); margin-top:12px; }

.params { padding:0 20px 20px; }
.param-table { width:100%; border-collapse:collapse; font-size:13px; }
.param-table th { text-align:left; font-family:var(--mono); font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted); padding:8px 12px; background:var(--surface2); border-bottom:1px solid var(--border); }
.param-table td { padding:10px 12px; border-bottom:1px solid var(--border); vertical-align:top; }
.param-table tr:last-child td { border-bottom:none; }
.param-name { font-family:var(--mono); font-size:12px; color:var(--text); }
.param-type { font-family:var(--mono); font-size:11px; color:var(--muted); }
.param-req  { font-family:var(--mono); font-size:10px; color:var(--accent); }

.code-block { background:var(--surface2); border:1px solid var(--border); border-radius:8px; padding:18px 20px; font-family:var(--mono); font-size:12px; line-height:1.7; overflow-x:auto; margin:12px 0 20px; white-space:pre; }
.kw  { color:var(--accent); }
.str { color:#f0a070; }
.num { color:#70d0f0; }
.key { color:#b0c8e8; }
.cmt { color:var(--muted); }

.response-fields { display:flex; flex-direction:column; gap:6px; padding:0 20px 20px; }
.rf { display:grid; grid-template-columns:200px 80px 1fr; gap:12px; align-items:start; font-size:12px; padding:8px 0; border-bottom:1px solid var(--border); }
.rf:last-child { border-bottom:none; }
.rf-name  { font-family:var(--mono); color:var(--text); }
.rf-type  { font-family:var(--mono); color:var(--muted); font-size:11px; }
.rf-desc  { color:var(--muted); }

.verdict-table { width:100%; border-collapse:collapse; font-size:13px; margin:12px 0 20px; }
.verdict-table th { text-align:left; font-family:var(--mono); font-size:10px; letter-spacing:.1em; text-transform:uppercase; color:var(--muted); padding:8px 12px; background:var(--surface2); border-bottom:1px solid var(--border); }
.verdict-table td { padding:10px 12px; border-bottom:1px solid var(--border); font-family:var(--mono); font-size:12px; }

::-webkit-scrollbar { width:6px; } ::-webkit-scrollbar-track { background:var(--bg); } ::-webkit-scrollbar-thumb { background:var(--border); border-radius:3px; }
</style>
</head>
<body>
<header>
  <div class="logo">AudioForensics · API Reference</div>
  <a class="back" href="/">← Back to UI</a>
</header>
<main>

<h1>AudioForensics HTTP API</h1>
<p class="subtitle">REST API for programmatic audio forensic analysis. All endpoints return JSON. No authentication required (add API key middleware before production deployment).</p>

<div style="background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:0 8px 8px 0;padding:14px 18px;margin-bottom:40px;font-size:13px;color:var(--muted);">
  Base URL: <span style="font-family:var(--mono);color:var(--text)">http://localhost:5051</span><br>
  Content-Type for uploads: <span style="font-family:var(--mono);color:var(--text)">multipart/form-data</span><br>
  Content-Type for responses: <span style="font-family:var(--mono);color:var(--text)">application/json</span>
</div>

<h2>Endpoints</h2>

<!-- POST /api/v1/analyze -->
<div class="endpoint">
  <div class="endpoint-header">
    <span class="method POST">POST</span>
    <span class="path">/api/v1/analyze</span>
  </div>
  <div class="ep-desc">
    Upload an audio file and receive a complete forensic analysis as JSON.
    All 6 modules run synchronously — response time is 10–30 seconds depending on file length.
  </div>
  <div class="params">
    <table class="param-table">
      <tr><th>Field</th><th>Type</th><th>Required</th><th>Description</th></tr>
      <tr>
        <td><span class="param-name">audio</span></td>
        <td><span class="param-type">file</span></td>
        <td><span class="param-req">required</span></td>
        <td>Audio file. Accepted formats: WAV, MP3, M4A, AAC, FLAC, OGG, OPUS, AMR, WMA. Max 200MB.</td>
      </tr>
    </table>
  </div>
  <div class="params">
    <div style="font-family:var(--mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Example — cURL</div>
    <div class="code-block"><span class="kw">curl</span> -X POST http://localhost:5051/api/v1/analyze \
  -F <span class="str">"audio=@recording.m4a"</span> | python3 -m json.tool</div>
    <div style="font-family:var(--mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:16px 0 8px">Example — Python</div>
    <div class="code-block"><span class="kw">import</span> requests

<span class="kw">with</span> open(<span class="str">"recording.m4a"</span>, <span class="str">"rb"</span>) <span class="kw">as</span> f:
    r = requests.post(
        <span class="str">"http://localhost:5051/api/v1/analyze"</span>,
        files={<span class="str">"audio"</span>: f},
    )
data = r.json()
print(data[<span class="str">"verdict_label"</span>], data[<span class="str">"fused_probability"</span>])</div>
    <div style="font-family:var(--mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:16px 0 8px">Example — JavaScript (fetch)</div>
    <div class="code-block"><span class="kw">const</span> fd = <span class="kw">new</span> FormData();
fd.append(<span class="str">"audio"</span>, audioFile);

<span class="kw">const</span> r    = <span class="kw">await</span> fetch(<span class="str">"/api/v1/analyze"</span>, { method: <span class="str">"POST"</span>, body: fd });
<span class="kw">const</span> data = <span class="kw">await</span> r.json();
console.log(data.verdict_label, data.fused_probability);</div>
  </div>
</div>

<!-- GET /api/v1/health -->
<div class="endpoint">
  <div class="endpoint-header">
    <span class="method GET">GET</span>
    <span class="path">/api/v1/health</span>
  </div>
  <div class="ep-desc">Liveness check. Returns plain-text "OK" with HTTP 200 when the server is running.</div>
  <div class="params">
    <div class="code-block"><span class="kw">curl</span> http://localhost:5051/api/v1/health
<span class="cmt"># → OK</span></div>
  </div>
</div>

<!-- GET /api/v1/version -->
<div class="endpoint">
  <div class="endpoint-header">
    <span class="method GET">GET</span>
    <span class="path">/api/v1/version</span>
  </div>
  <div class="ep-desc">Returns system version and list of active modules.</div>
  <div class="params">
    <div class="code-block">{
  <span class="key">"system"</span>:          <span class="str">"AudioForensics"</span>,
  <span class="key">"version"</span>:         <span class="str">"0.9.0-pre-calibration"</span>,
  <span class="key">"phases_complete"</span>: [<span class="num">0</span>,<span class="num">1</span>,<span class="num">2</span>,<span class="num">3</span>,<span class="num">4</span>,<span class="num">5</span>,<span class="num">6</span>,<span class="num">7</span>,<span class="num">8</span>],
  <span class="key">"modules"</span>: [<span class="str">"metadata"</span>,<span class="str">"enf"</span>,<span class="str">"noise"</span>,<span class="str">"compression"</span>,<span class="str">"reverberation"</span>,<span class="str">"voice"</span>,<span class="str">"fusion"</span>]
}</div>
  </div>
</div>

<h2>Response Schema — /api/v1/analyze</h2>

<div class="response-fields">
  <div class="rf"><span class="rf-name">case_id</span><span class="rf-type">string</span><span class="rf-desc">Unique UUID for this analysis run</span></div>
  <div class="rf"><span class="rf-name">status</span><span class="rf-type">string</span><span class="rf-desc">Always "completed" on success</span></div>
  <div class="rf"><span class="rf-name">timestamp</span><span class="rf-type">string</span><span class="rf-desc">ISO-8601 UTC timestamp of analysis</span></div>
  <div class="rf"><span class="rf-name">filename</span><span class="rf-type">string</span><span class="rf-desc">Original filename as uploaded</span></div>
  <div class="rf"><span class="rf-name">elapsed_s</span><span class="rf-type">number</span><span class="rf-desc">Total analysis time in seconds</span></div>
  <div class="rf"><span class="rf-name">verdict</span><span class="rf-type">string</span><span class="rf-desc">Machine-readable verdict constant (see Verdict Values below)</span></div>
  <div class="rf"><span class="rf-name">verdict_label</span><span class="rf-type">string</span><span class="rf-desc">Human-readable label: "Likely Authentic", "Inconclusive", etc.</span></div>
  <div class="rf"><span class="rf-name">fused_probability</span><span class="rf-type">number</span><span class="rf-desc">0.0–1.0 fused tampering probability from all modules</span></div>
  <div class="rf"><span class="rf-name">confidence</span><span class="rf-type">number</span><span class="rf-desc">0.0–1.0 system confidence in the verdict</span></div>
  <div class="rf"><span class="rf-name">elevated_modules</span><span class="rf-type">string[]</span><span class="rf-desc">Module names whose scores exceeded the elevation threshold (0.35)</span></div>
  <div class="rf"><span class="rf-name">skipped_modules</span><span class="rf-type">string[]</span><span class="rf-desc">Module names skipped (e.g. ENF skipped on outdoor recordings)</span></div>
  <div class="rf"><span class="rf-name">corroboration_factor</span><span class="rf-type">number</span><span class="rf-desc">Multiplier applied when multiple modules agree (1.0–1.27)</span></div>
  <div class="rf"><span class="rf-name">has_conflict</span><span class="rf-type">boolean</span><span class="rf-desc">True when modules strongly disagree with each other</span></div>
  <div class="rf"><span class="rf-name">conflict_description</span><span class="rf-type">string</span><span class="rf-desc">Human-readable conflict explanation if has_conflict is true</span></div>
  <div class="rf"><span class="rf-name">module_scores</span><span class="rf-type">object</span><span class="rf-desc">Per-module scores: {"enf": 0.0, "noise": 0.057, …}</span></div>
  <div class="rf"><span class="rf-name">adjusted_weights</span><span class="rf-type">object</span><span class="rf-desc">Effective weights after ENF redistribution and metadata gating</span></div>
  <div class="rf"><span class="rf-name">total_findings</span><span class="rf-type">integer</span><span class="rf-desc">Total number of individual findings across all modules</span></div>
  <div class="rf"><span class="rf-name">findings</span><span class="rf-type">Finding[]</span><span class="rf-desc">Array of individual findings (see Finding Object below)</span></div>
  <div class="rf"><span class="rf-name">calibration_note</span><span class="rf-type">string</span><span class="rf-desc">Notes on skipped modules or pre-calibration status</span></div>
  <div class="rf"><span class="rf-name">audio</span><span class="rf-type">object</span><span class="rf-desc">Detected audio properties: codec, duration_s, sample_rate, bitrate_bps, channels, container, etc.</span></div>
</div>

<h2>Verdict Values</h2>
<table class="verdict-table">
  <tr><th>verdict (string)</th><th>verdict_label</th><th>fused_probability range</th><th>Meaning</th></tr>
  <tr><td style="color:var(--green)">likely_authentic</td><td>Likely Authentic</td><td>0.00 – 0.20</td><td>No forensic evidence of tampering found</td></tr>
  <tr><td style="color:var(--amber)">inconclusive</td><td>Inconclusive</td><td>0.20 – 0.40</td><td>Minor anomalies found but insufficient for a verdict</td></tr>
  <tr><td style="color:var(--red)">likely_tampered</td><td>Likely Tampered</td><td>0.40 – 0.65</td><td>Multiple modules flagged suspicious signals</td></tr>
  <tr><td style="color:var(--red)">tampered</td><td>Tampered</td><td>0.65 – 1.00</td><td>Strong consistent evidence of editing or tampering</td></tr>
</table>

<h2>Finding Object</h2>
<div class="response-fields">
  <div class="rf"><span class="rf-name">module</span><span class="rf-type">string</span><span class="rf-desc">Module that produced this finding: "enf", "noise", "compression", etc.</span></div>
  <div class="rf"><span class="rf-name">title</span><span class="rf-type">string</span><span class="rf-desc">Short finding title</span></div>
  <div class="rf"><span class="rf-name">severity</span><span class="rf-type">string</span><span class="rf-desc">"HIGH", "MEDIUM", or "LOW"</span></div>
  <div class="rf"><span class="rf-name">confidence</span><span class="rf-type">number</span><span class="rf-desc">0.0–1.0 module confidence in this specific finding</span></div>
  <div class="rf"><span class="rf-name">description</span><span class="rf-type">string</span><span class="rf-desc">Detailed technical description of the finding</span></div>
  <div class="rf"><span class="rf-name">temporal_location</span><span class="rf-type">object?</span><span class="rf-desc">Optional: {start_seconds, end_seconds} locating the finding in the recording</span></div>
</div>

<h2>Error Responses</h2>
<div class="code-block">{
  <span class="key">"error"</span>: <span class="str">"No 'audio' field in request"</span>,
  <span class="key">"code"</span>:  <span class="str">"MISSING_FILE"</span>
}</div>
<div class="response-fields">
  <div class="rf"><span class="rf-name">MISSING_FILE</span><span class="rf-type">400</span><span class="rf-desc">No audio field in multipart form</span></div>
  <div class="rf"><span class="rf-name">EMPTY_FILENAME</span><span class="rf-type">400</span><span class="rf-desc">File field present but filename is empty</span></div>
  <div class="rf"><span class="rf-name">ANALYSIS_ERROR</span><span class="rf-type">500</span><span class="rf-desc">Analysis pipeline failed — check error message for details</span></div>
  <div class="rf"><span class="rf-name">FILE_TOO_LARGE</span><span class="rf-type">413</span><span class="rf-desc">File exceeds 200MB limit</span></div>
  <div class="rf"><span class="rf-name">NOT_FOUND</span><span class="rf-type">404</span><span class="rf-desc">Unknown endpoint</span></div>
</div>

<h2>Module Weights</h2>
<p>The fusion engine uses these base weights. Metadata is gated — it only contributes when at least one audio module is independently elevated.</p>
<div class="code-block"><span class="key">"ENF"</span>:          <span class="num">0.30</span>  <span class="cmt"># Electric Network Frequency — highest forensic value</span>
<span class="key">"Noise"</span>:        <span class="num">0.22</span>  <span class="cmt"># Noise floor consistency</span>
<span class="key">"Compression"</span>:  <span class="num">0.18</span>  <span class="cmt"># Codec artifact detection</span>
<span class="key">"Reverberation"</span>:<span class="num">0.12</span>  <span class="cmt"># Room acoustic fingerprint</span>
<span class="key">"Voice"</span>:        <span class="num">0.10</span>  <span class="cmt"># Speaker fundamental frequency</span>
<span class="key">"Metadata"</span>:     <span class="num">0.08</span>  <span class="cmt"># Tags/timestamps (gated — see above)</span></div>

</main>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5051)
    args = parser.parse_args()

    print(f"\nAudioForensics — Web UI + API")
    print(f"{'─'*42}")
    print(f"  UI:      http://{args.host}:{args.port}")
    print(f"  API:     http://{args.host}:{args.port}/api/v1/analyze")
    print(f"  Docs:    http://{args.host}:{args.port}/api/v1/docs")
    print(f"  Health:  http://{args.host}:{args.port}/api/v1/health")
    print()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
