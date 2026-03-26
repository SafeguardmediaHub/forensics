"""
VideoForensics — PDF Report Generator (Phase 9)

Produces a structured, court-admissible forensic report from a
completed FusionModule result. The report is designed to be:

    - Self-contained: all evidence and methodology documented
    - Traceable: case ID, file hash, and analysis timestamps on every page
    - Layered: executive summary first, full technical detail at the end
    - Honest: pre-calibration caveat prominently displayed

Report structure:
    Page 1   — Cover: case ID, file info, verdict, analyst note
    Page 2   — Executive Summary: verdict, module score table, timeline
    Page 3+  — Findings: grouped by module, with severity and timestamps
    Last     — Technical Appendix: methodology, weights, raw scores, hash chain
"""

from __future__ import annotations
import hashlib
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak,
    NextPageTemplate,
)
from reportlab.platypus.flowables import Flowable

# ── Palette ───────────────────────────────────────────────────────────────────

class P:
    BLACK       = colors.HexColor("#0D0D0D")
    WHITE       = colors.HexColor("#FFFFFF")
    DARK_GREY   = colors.HexColor("#2C2C2C")
    MID_GREY    = colors.HexColor("#6B6B6B")
    LIGHT_GREY  = colors.HexColor("#E8E8E8")
    RULE        = colors.HexColor("#C8C8C8")
    ACCENT      = colors.HexColor("#1A3A6B")   # deep navy — professional

    # Verdict colours
    AUTHENTIC   = colors.HexColor("#1A6B3A")   # dark green
    INCONCLUSIVE= colors.HexColor("#7A5A00")   # amber
    LIKELY_TAMP = colors.HexColor("#8B3A00")   # burnt orange
    TAMPERED    = colors.HexColor("#8B0000")   # dark red

    # Severity
    SEV_HIGH    = colors.HexColor("#8B0000")
    SEV_MEDIUM  = colors.HexColor("#7A5A00")
    SEV_LOW     = colors.HexColor("#2C4A7A")

    # Module bar fill
    BAR_FILL    = colors.HexColor("#1A3A6B")
    BAR_BG      = colors.HexColor("#E8ECF4")
    BAR_ELEV    = colors.HexColor("#8B0000")


VERDICT_COLOR = {
    "likely_authentic": P.AUTHENTIC,
    "inconclusive":     P.INCONCLUSIVE,
    "likely_tampered":  P.LIKELY_TAMP,
    "tampered":         P.TAMPERED,
}


# ── Custom Flowables ──────────────────────────────────────────────────────────

class ScoreBar(Flowable):
    """Horizontal bar chart cell for module scores."""

    def __init__(self, score: float, elevated: bool, width: float = 80*mm, height: float = 4*mm):
        super().__init__()
        self.score    = max(0.0, min(1.0, float(score)))
        self.elevated = elevated
        self.width    = width
        self.height   = height

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        w = self.width
        h = self.height
        # Background
        self.canv.setFillColor(P.BAR_BG)
        self.canv.rect(0, 0, w, h, fill=1, stroke=0)
        # Fill
        fill_w = w * self.score
        fill_color = P.BAR_ELEV if self.elevated else P.BAR_FILL
        self.canv.setFillColor(fill_color)
        if fill_w > 0:
            self.canv.rect(0, 0, fill_w, h, fill=1, stroke=0)
        # Threshold line at 0.30
        threshold_x = w * 0.30
        self.canv.setStrokeColor(P.MID_GREY)
        self.canv.setLineWidth(0.5)
        self.canv.line(threshold_x, 0, threshold_x, h)


class VerdictBanner(Flowable):
    """Full-width coloured verdict banner."""

    def __init__(self, verdict: str, label: str, probability: float,
                 confidence: float, width: float):
        super().__init__()
        self.verdict     = verdict
        self.label       = label
        self.probability = probability
        self.confidence  = confidence
        self.width       = width
        self.height      = 22*mm

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c       = self.canv
        w, h    = self.width, self.height
        vc      = VERDICT_COLOR.get(self.verdict, P.MID_GREY)

        # Background
        c.setFillColor(vc)
        c.roundRect(0, 0, w, h, 3*mm, fill=1, stroke=0)

        # Verdict label (large)
        c.setFillColor(P.WHITE)
        c.setFont("Helvetica-Bold", 18)
        label_text = f"VERDICT: {self.label.upper()}"
        c.drawCentredString(w / 2, h - 14*mm, label_text)

        # Probability and confidence (smaller)
        c.setFont("Helvetica", 9)
        details = (f"Tampering Probability: {self.probability:.3f}   |   "
                   f"Confidence: {self.confidence:.3f}")
        c.drawCentredString(w / 2, h - 19*mm, details)


# ── Style Sheet ───────────────────────────────────────────────────────────────

def make_styles() -> dict:
    base = getSampleStyleSheet()

    def s(name, **kw) -> ParagraphStyle:
        parent = kw.pop("parent", "Normal")
        ps = ParagraphStyle(name, parent=base[parent], **kw)
        return ps

    return {
        "h1": s("h1", parent="Heading1", fontSize=16, textColor=P.ACCENT,
                 spaceBefore=8, spaceAfter=4, fontName="Helvetica-Bold"),
        "h2": s("h2", parent="Heading2", fontSize=12, textColor=P.ACCENT,
                 spaceBefore=6, spaceAfter=3, fontName="Helvetica-Bold"),
        "h3": s("h3", fontSize=10, textColor=P.DARK_GREY, spaceBefore=4,
                 spaceAfter=2, fontName="Helvetica-Bold"),
        "body": s("body", fontSize=9, textColor=P.DARK_GREY,
                   leading=13, spaceAfter=4, alignment=TA_JUSTIFY),
        "mono": s("mono", fontSize=8, fontName="Courier",
                   textColor=P.DARK_GREY, leading=11),
        "caption": s("caption", fontSize=8, textColor=P.MID_GREY,
                      alignment=TA_CENTER),
        "label": s("label", fontSize=8, textColor=P.MID_GREY,
                    fontName="Helvetica-Bold"),
        "finding_title": s("finding_title", fontSize=9, textColor=P.DARK_GREY,
                            fontName="Helvetica-Bold", spaceAfter=1),
        "finding_desc": s("finding_desc", fontSize=8.5, textColor=P.DARK_GREY,
                           leading=12, spaceAfter=2, alignment=TA_JUSTIFY),
        "footer": s("footer", fontSize=7.5, textColor=P.MID_GREY,
                     alignment=TA_CENTER),
        "cover_title": s("cover_title", fontSize=26, textColor=P.ACCENT,
                          fontName="Helvetica-Bold", alignment=TA_CENTER,
                          spaceBefore=0, spaceAfter=4),
        "cover_sub": s("cover_sub", fontSize=12, textColor=P.MID_GREY,
                        alignment=TA_CENTER, spaceAfter=2),
        "cover_meta": s("cover_meta", fontSize=9, textColor=P.DARK_GREY,
                         alignment=TA_CENTER),
        "caveat": s("caveat", fontSize=8, textColor=P.MID_GREY,
                     leading=11, alignment=TA_JUSTIFY,
                     borderColor=P.RULE, borderWidth=0.5,
                     borderPadding=4),
    }


# ── Page Callbacks ────────────────────────────────────────────────────────────

def _make_header_footer(case_id: str, filename: str, report_date: str,
                         page_label: str):
    """Return onPage callback that draws header/footer on every page."""

    def on_page(canvas, doc):
        canvas.saveState()
        w, h = A4

        # Header rule
        canvas.setStrokeColor(P.ACCENT)
        canvas.setLineWidth(1.2)
        canvas.line(15*mm, h - 12*mm, w - 15*mm, h - 12*mm)

        # Header left: system name
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(P.ACCENT)
        canvas.drawString(15*mm, h - 10*mm, "VideoForensics")

        # Header right: case ID (truncated)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(P.MID_GREY)
        canvas.drawRightString(w - 15*mm, h - 10*mm,
                               f"Case: {case_id[:16]}...")

        # Footer rule
        canvas.setStrokeColor(P.RULE)
        canvas.setLineWidth(0.5)
        canvas.line(15*mm, 10*mm, w - 15*mm, 10*mm)

        # Footer centre: filename
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(P.MID_GREY)
        canvas.drawCentredString(w / 2, 6.5*mm, filename)

        # Footer right: page number
        canvas.drawRightString(w - 15*mm, 6.5*mm,
                               f"Page {doc.page}")

        # Footer left: date
        canvas.drawString(15*mm, 6.5*mm, report_date)

        canvas.restoreState()

    return on_page


# ── Report Builder ────────────────────────────────────────────────────────────

class ForensicReportBuilder:
    """
    Builds a multi-page PDF forensic report from a FusionModule result.
    """

    PAGE_W, PAGE_H = A4
    MARGIN = 18*mm
    CONTENT_W = PAGE_W - 2 * MARGIN

    MODULE_ORDER = ["metadata", "compression", "noise", "temporal", "lighting", "audio"]
    MODULE_LABELS = {
        "metadata":    "Metadata Integrity",
        "compression": "Compression Artifacts",
        "noise":       "Noise & Camera Fingerprint",
        "temporal":    "Temporal Integrity",
        "lighting":    "Lighting & Physics",
        "audio":       "Audio Forensics",
    }

    SEVERITY_LABEL = {"high": "HIGH", "medium": "MED", "low": "LOW"}
    SEVERITY_COLOR = {
        "high":   P.SEV_HIGH,
        "medium": P.SEV_MEDIUM,
        "low":    P.SEV_LOW,
    }

    def __init__(self):
        self.styles = make_styles()

    def build(
        self,
        output_path: Path,
        case_id: str,
        filename: str,
        video_profile: Any,
        fusion_result: Any,
        analysis_timestamp: Optional[datetime] = None,
    ) -> Path:
        """
        Build the full PDF report.

        Args:
            output_path:        Where to write the PDF
            case_id:            Forensic case UUID
            filename:           Original video filename
            video_profile:      VideoProfile object
            fusion_result:      ModuleResult from FusionModule
            analysis_timestamp: When analysis was run (default: now)

        Returns:
            output_path after writing
        """
        if analysis_timestamp is None:
            analysis_timestamp = datetime.now(timezone.utc)

        report_date = analysis_timestamp.strftime("%Y-%m-%d %H:%M UTC")
        m = fusion_result.metadata

        # ── Document setup ────────────────────────────────────────────────
        doc = BaseDocTemplate(
            str(output_path),
            pagesize       = A4,
            leftMargin     = self.MARGIN,
            rightMargin    = self.MARGIN,
            topMargin      = 18*mm,
            bottomMargin   = 16*mm,
            title          = f"VideoForensics Report — {filename}",
            author         = "VideoForensics Automated Analysis",
            subject        = f"Case {case_id}",
        )

        # Page template with header/footer
        frame = Frame(
            self.MARGIN, 16*mm,
            self.CONTENT_W, self.PAGE_H - 34*mm,
            id="main",
        )
        on_page = _make_header_footer(case_id, filename, report_date, "")
        template = PageTemplate(id="main", frames=[frame], onPage=on_page)
        doc.addPageTemplates([template])

        # ── Build story ───────────────────────────────────────────────────
        story = []
        story += self._cover_page(case_id, filename, video_profile, m, report_date)
        story += [PageBreak()]
        story += self._executive_summary(filename, video_profile, m, fusion_result)
        story += [PageBreak()]
        story += self._findings_section(fusion_result, m)
        story += [PageBreak()]
        story += self._technical_appendix(case_id, filename, video_profile, m,
                                           fusion_result, analysis_timestamp)

        doc.build(story)
        return output_path

    # ── Cover Page ────────────────────────────────────────────────────────────

    def _cover_page(self, case_id, filename, vp, m, report_date) -> list:
        S = self.styles
        story = []

        story.append(Spacer(1, 20*mm))

        # Title block
        story.append(Paragraph("VideoForensics", S["cover_title"]))
        story.append(Paragraph("Digital Video Forensic Analysis Report",
                                S["cover_sub"]))
        story.append(Spacer(1, 8*mm))

        # Horizontal rule
        story.append(HRFlowable(width="100%", thickness=1.5,
                                 color=P.ACCENT, spaceAfter=8*mm))

        # Verdict banner
        story.append(VerdictBanner(
            verdict     = m["verdict"],
            label       = m["verdict_label"],
            probability = m["fused_probability"],
            confidence  = m["confidence"],
            width       = self.CONTENT_W,
        ))
        story.append(Spacer(1, 8*mm))

        # File info table
        duration_s = (vp.total_frames / vp.frame_rate) if vp.frame_rate else 0
        filesize_mb = (vp.file_size_bytes / 1_048_576) if vp.file_size_bytes else 0

        info_data = [
            ["File", filename],
            ["Format", f"{vp.codec.upper()} · {vp.width}×{vp.height} · "
                       f"{vp.frame_rate:.1f} fps · {duration_s:.1f}s"],
            ["Size", f"{filesize_mb:.2f} MB  ({vp.file_size_bytes:,} bytes)"],
            ["SHA-256", vp.sha256_hash or "—"],
            ["Case ID", case_id],
            ["Analysis Date", report_date],
            ["Elapsed", f"{m.get('total_elapsed_s', 0):.1f}s"],
        ]

        tbl = Table(info_data, colWidths=[32*mm, self.CONTENT_W - 32*mm])
        tbl.setStyle(TableStyle([
            ("FONTNAME",    (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME",    (1, 0), (1, -1), "Courier"),
            ("FONTSIZE",    (0, 0), (-1, -1), 8.5),
            ("TEXTCOLOR",   (0, 0), (0, -1), P.MID_GREY),
            ("TEXTCOLOR",   (1, 0), (1, -1), P.DARK_GREY),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [P.WHITE, P.LIGHT_GREY]),
            ("TOPPADDING",  (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("GRID",        (0, 0), (-1, -1), 0.3, P.RULE),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 8*mm))

        # Pre-calibration caveat
        story.append(Paragraph(
            "<b>Important — Pre-Calibration Notice:</b>  "
            "Probability scores in this report are pre-calibration estimates produced "
            "by the VideoForensics automated analysis system. Absolute probability values "
            "will be refined in a future calibration phase against a labelled corpus. "
            "Verdict thresholds and relative module rankings are reliable for investigative "
            "use. This report should be reviewed by a qualified forensic examiner before "
            "being presented as standalone evidence in legal proceedings.",
            S["caveat"],
        ))

        return story

    # ── Executive Summary ─────────────────────────────────────────────────────

    def _executive_summary(self, filename, vp, m, fusion_result) -> list:
        S = self.styles
        story = []

        story.append(Paragraph("Executive Summary", S["h1"]))
        story.append(HRFlowable(width="100%", thickness=0.5,
                                 color=P.RULE, spaceAfter=4))

        # Narrative summary
        story.append(Paragraph(fusion_result.summary, S["body"]))
        story.append(Spacer(1, 4*mm))

        # ── Module score table ────────────────────────────────────────────
        story.append(Paragraph("Forensic Domain Scores", S["h2"]))

        header = ["Domain", "Score", "Visual", "Weight", "Status"]
        rows   = [header]

        module_scores = m.get("module_scores", {})
        adj_weights   = m.get("adjusted_weights", {})
        elevated      = m.get("modules_in_agreement", [])

        for mod in self.MODULE_ORDER:
            score  = float(module_scores.get(mod, 0.0))
            weight = float(adj_weights.get(mod, 0.0))
            is_el  = mod in elevated
            status = "▲ ELEVATED" if is_el else "—"
            label  = self.MODULE_LABELS.get(mod, mod)
            rows.append([
                label,
                f"{score:.3f}",
                ScoreBar(score, is_el, width=55*mm, height=4*mm),
                f"{weight:.3f}",
                status,
            ])

        col_widths = [48*mm, 16*mm, 57*mm, 16*mm, 25*mm]
        tbl = Table(rows, colWidths=col_widths)
        tbl.setStyle(TableStyle([
            # Header row
            ("BACKGROUND",  (0, 0), (-1, 0), P.ACCENT),
            ("TEXTCOLOR",   (0, 0), (-1, 0), P.WHITE),
            ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, 0), 8.5),
            ("ALIGN",       (0, 0), (-1, 0), "CENTER"),
            # Data rows
            ("FONTSIZE",    (0, 1), (-1, -1), 8.5),
            ("TEXTCOLOR",   (0, 1), (-1, -1), P.DARK_GREY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [P.WHITE, P.LIGHT_GREY]),
            ("ALIGN",       (1, 1), (1, -1), "CENTER"),
            ("ALIGN",       (3, 1), (3, -1), "CENTER"),
            ("ALIGN",       (4, 1), (4, -1), "CENTER"),
            ("TOPPADDING",  (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("GRID",        (0, 0), (-1, -1), 0.3, P.RULE),
            ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 4*mm))

        # Fusion formula note
        story.append(Paragraph(
            f"<b>Fusion:</b> Base score {m['base_score']:.3f} × "
            f"corroboration multiplier {m['corroboration_multiplier']}× "
            f"({m['n_elevated_modules']} domain(s) elevated) = "
            f"<b>{m['fused_probability']:.3f}</b>. "
            f"Elevation threshold: 0.30. "
            f"Multiplier schedule: 1×/1×/1.25×/1.50×/1.75× for 0/1/2/3/4+ domains.",
            S["caption"],
        ))
        story.append(Spacer(1, 5*mm))

        # ── Finding count summary ─────────────────────────────────────────
        story.append(Paragraph("Finding Summary", S["h2"]))
        non_fusion = [f for f in fusion_result.findings if f.module != "fusion"]
        by_module  = {}
        by_sev     = {"high": 0, "medium": 0, "low": 0}
        for f in non_fusion:
            by_module.setdefault(f.module, []).append(f)
            by_sev[f.severity.value] = by_sev.get(f.severity.value, 0) + 1

        sev_data = [
            ["Severity", "Count"],
            ["HIGH",   str(by_sev.get("high", 0))],
            ["MEDIUM", str(by_sev.get("medium", 0))],
            ["LOW",    str(by_sev.get("low", 0))],
            ["TOTAL",  str(sum(by_sev.values()))],
        ]
        sev_tbl = Table(sev_data, colWidths=[30*mm, 20*mm])
        sev_tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0), P.ACCENT),
            ("TEXTCOLOR",   (0, 0), (-1, 0), P.WHITE),
            ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME",    (0, 4), (-1, 4), "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, -1), 9),
            ("ALIGN",       (1, 0), (1, -1), "CENTER"),
            ("ROWBACKGROUNDS", (0, 1), (-1, 3), [P.WHITE, P.LIGHT_GREY, P.WHITE]),
            ("BACKGROUND",  (0, 4), (-1, 4), P.LIGHT_GREY),
            ("GRID",        (0, 0), (-1, -1), 0.3, P.RULE),
            ("TOPPADDING",  (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]))

        # Place severity table and conflict note side by side
        conflict_text = ""
        if m.get("has_conflict"):
            conflict_text = (
                "<b>⚠ Conflict Detected:</b>  " + m.get("conflict_description", "")
            )
        else:
            conflict_text = (
                "No inter-module conflict detected. All elevated modules are "
                "consistent in their assessment direction."
            )

        side_data = [[
            sev_tbl,
            Paragraph(conflict_text, S["body"]),
        ]]
        side_tbl = Table(side_data, colWidths=[55*mm, self.CONTENT_W - 60*mm])
        side_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (1, 0), (1, -1), 8*mm),
        ]))
        story.append(side_tbl)

        return story

    # ── Findings Section ──────────────────────────────────────────────────────

    def _findings_section(self, fusion_result, m) -> list:
        S = self.styles
        story = []

        story.append(Paragraph("Detailed Findings", S["h1"]))
        story.append(HRFlowable(width="100%", thickness=0.5,
                                 color=P.RULE, spaceAfter=4))
        story.append(Paragraph(
            "Findings are grouped by forensic domain. Each finding includes "
            "a severity rating, confidence score, and where applicable, "
            "a temporal location within the video.",
            S["body"],
        ))
        story.append(Spacer(1, 4*mm))

        # Group findings by module (exclude fusion-level meta-findings)
        non_fusion = [f for f in fusion_result.findings if f.module != "fusion"]
        by_module  = {}
        for f in non_fusion:
            by_module.setdefault(f.module, []).append(f)

        for mod in self.MODULE_ORDER:
            findings = by_module.get(mod, [])
            if not findings:
                continue

            label = self.MODULE_LABELS.get(mod, mod.title())
            story.append(KeepTogether([
                Paragraph(label, S["h2"]),
                HRFlowable(width="100%", thickness=0.3, color=P.RULE, spaceAfter=3),
            ]))

            for f in findings:
                sev   = f.severity.value
                sev_c = self.SEVERITY_COLOR.get(sev, P.MID_GREY)
                sev_l = self.SEVERITY_LABEL.get(sev, sev.upper())

                # Finding header row: [SEV badge] | title | confidence | timestamp
                ts_str = ""
                if f.temporal_location:
                    ts_str = (f"{f.temporal_location.start_seconds:.2f}s – "
                              f"{f.temporal_location.end_seconds:.2f}s")

                header_data = [[
                    Paragraph(f'<font color="{sev_c.hexval()}">'
                              f'<b>[{sev_l}]</b></font>', S["label"]),
                    Paragraph(f.title, S["finding_title"]),
                    Paragraph(f"conf={f.confidence:.2f}", S["label"]),
                    Paragraph(ts_str, S["label"]),
                ]]
                header_tbl = Table(
                    header_data,
                    colWidths=[15*mm, self.CONTENT_W - 60*mm, 20*mm, 25*mm],
                )
                header_tbl.setStyle(TableStyle([
                    ("BACKGROUND",   (0, 0), (-1, -1), P.LIGHT_GREY),
                    ("TOPPADDING",   (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
                    ("LEFTPADDING",  (0, 0), (-1, -1), 4),
                    ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN",        (2, 0), (3, 0), "RIGHT"),
                ]))

                desc_para = Paragraph(f.description, S["finding_desc"])

                story.append(KeepTogether([
                    header_tbl,
                    Spacer(1, 1*mm),
                    desc_para,
                    Spacer(1, 3*mm),
                ]))

        # Fusion-level findings (corroboration, conflict)
        fusion_findings = [f for f in fusion_result.findings if f.module == "fusion"]
        if fusion_findings:
            story.append(Paragraph("Fusion Assessment", S["h2"]))
            story.append(HRFlowable(width="100%", thickness=0.3,
                                     color=P.RULE, spaceAfter=3))
            for f in fusion_findings:
                story.append(Paragraph(f"<b>{f.title}</b>", S["finding_title"]))
                story.append(Paragraph(f.description, S["finding_desc"]))
                story.append(Spacer(1, 3*mm))

        return story

    # ── Technical Appendix ────────────────────────────────────────────────────

    def _technical_appendix(self, case_id, filename, vp, m,
                              fusion_result, ts: datetime) -> list:
        S = self.styles
        story = []

        story.append(Paragraph("Technical Appendix", S["h1"]))
        story.append(HRFlowable(width="100%", thickness=0.5,
                                 color=P.RULE, spaceAfter=4))

        # ── A: Methodology ────────────────────────────────────────────────
        story.append(Paragraph("A. Methodology Overview", S["h2"]))
        story.append(Paragraph(
            "VideoForensics applies six independent forensic analysis modules, "
            "each examining a distinct physical or signal domain. Module scores "
            "are combined using an uncertainty-adjusted weighted fusion with a "
            "corroboration multiplier that increases the final score when multiple "
            "independent domains agree. The system does not require a reference "
            "copy of the original video.",
            S["body"],
        ))
        story.append(Spacer(1, 3*mm))

        methods = [
            ["Module", "Method", "Primary Signal"],
            ["Metadata",    "FFprobe stream analysis",     "Encoder signatures, timestamp integrity"],
            ["Compression", "DCT coefficient histograms",  "Double-compression artifacts"],
            ["Noise",       "PRNU fingerprint extraction", "Camera sensor consistency"],
            ["Temporal",    "Optical flow, frame hashing", "Motion discontinuities, frame duplication"],
            ["Lighting",    "Illumination field analysis", "Abrupt colour temperature change"],
            ["Audio",       "Spectral centroid tracking",  "Spectral discontinuities at edit boundaries"],
        ]
        meth_tbl = Table(methods, colWidths=[30*mm, 55*mm, self.CONTENT_W - 90*mm])
        meth_tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0), P.ACCENT),
            ("TEXTCOLOR",   (0, 0), (-1, 0), P.WHITE),
            ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [P.WHITE, P.LIGHT_GREY]),
            ("GRID",        (0, 0), (-1, -1), 0.3, P.RULE),
            ("TOPPADDING",  (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(meth_tbl)
        story.append(Spacer(1, 4*mm))

        # ── B: Weights and raw scores ─────────────────────────────────────
        story.append(Paragraph("B. Module Weights and Raw Scores", S["h2"]))

        base_weights = {
            "metadata": 0.15, "compression": 0.15, "noise": 0.25,
            "temporal": 0.20, "lighting": 0.15, "audio": 0.10,
        }
        ws_header = ["Module", "Base Weight", "Adj. Weight",
                     "Raw Score", "Weighted Score", "Uncertainty"]
        ws_rows = [ws_header]
        for mod in self.MODULE_ORDER:
            ws_rows.append([
                self.MODULE_LABELS.get(mod, mod),
                f"{base_weights.get(mod, 0):.3f}",
                f"{float(m['adjusted_weights'].get(mod, 0)):.3f}",
                f"{float(m['module_scores'].get(mod, 0)):.3f}",
                f"{float(m['weighted_scores'].get(mod, 0)):.3f}",
                f"{m.get('uncertainty_range', [0,0])[0]:.3f}–"
                f"{m.get('uncertainty_range', [0,0])[1]:.3f}",
            ])

        ws_tbl = Table(ws_rows, colWidths=[45*mm] + [25*mm]*5)
        ws_tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0), P.ACCENT),
            ("TEXTCOLOR",   (0, 0), (-1, 0), P.WHITE),
            ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, -1), 8),
            ("ALIGN",       (1, 0), (-1, -1), "CENTER"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [P.WHITE, P.LIGHT_GREY]),
            ("GRID",        (0, 0), (-1, -1), 0.3, P.RULE),
            ("TOPPADDING",  (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(ws_tbl)
        story.append(Spacer(1, 4*mm))

        # ── C: Integrity chain ────────────────────────────────────────────
        story.append(Paragraph("C. File Integrity", S["h2"]))
        integrity_data = [
            ["Property", "Value"],
            ["Original filename",  filename],
            ["SHA-256 hash",       vp.sha256_hash or "Not computed"],
            ["File size",          f"{vp.file_size_bytes:,} bytes"
                                   if vp.file_size_bytes else "Unknown"],
            ["Analysis timestamp", ts.strftime("%Y-%m-%d %H:%M:%S UTC")],
            ["Case ID",            case_id],
            ["Analysis system",    f"VideoForensics — Python {sys.version.split()[0]} "
                                   f"on {platform.system()} {platform.machine()}"],
        ]
        integ_tbl = Table(integrity_data, colWidths=[45*mm, self.CONTENT_W - 45*mm])
        integ_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0), P.ACCENT),
            ("TEXTCOLOR",    (0, 0), (-1, 0), P.WHITE),
            ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME",     (0, 1), (0, -1), "Helvetica-Bold"),
            ("FONTNAME",     (1, 1), (1, -1), "Courier"),
            ("FONTSIZE",     (0, 0), (-1, -1), 8),
            ("TEXTCOLOR",    (0, 1), (0, -1), P.MID_GREY),
            ("TEXTCOLOR",    (1, 1), (1, -1), P.DARK_GREY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [P.WHITE, P.LIGHT_GREY]),
            ("GRID",         (0, 0), (-1, -1), 0.3, P.RULE),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LEFTPADDING",  (0, 0), (-1, -1), 6),
            ("WORDWRAP",     (1, 2), (1, 2), 1),
        ]))
        story.append(integ_tbl)
        story.append(Spacer(1, 4*mm))

        # ── D: Limitations ────────────────────────────────────────────────
        story.append(Paragraph("D. Limitations and Caveats", S["h2"]))
        limitations = [
            ("Pre-calibration scores",
             "Module probability scores are pre-calibration estimates. "
             "Absolute values will be refined against a labelled corpus in a future "
             "calibration phase. Relative rankings and verdict thresholds are reliable "
             "for investigative guidance."),
            ("Synthetic and CGI content",
             "Videos generated by computer (CGI, screen recordings, AI-generated content) "
             "do not have physical camera noise signatures. PRNU and noise-based signals "
             "may produce elevated scores on such content even when no tampering is present."),
            ("Single-source splices",
             "If a video was tampered using footage from the same camera (e.g. two clips "
             "from the same device combined), the PRNU fingerprint will not change at the "
             "splice boundary. Temporal and audio modules remain effective in this case."),
            ("Transcoding chains",
             "Videos that have been legitimately transcoded (e.g. compressed for upload) "
             "will show encoder signatures in the metadata module. This alone is not "
             "evidence of tampering."),
            ("No reference required",
             "This system is blind — it does not require a reference copy of the original "
             "video. All assessments are made from the video file alone."),
        ]
        for title, text in limitations:
            story.append(Paragraph(
                f"<b>{title}:</b>  {text}",
                S["body"],
            ))

        return story
