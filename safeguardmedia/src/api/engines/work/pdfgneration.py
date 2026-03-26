"""
PDF Report Generator for Frame Analysis Results
Generates comprehensive reports with visuals, findings, and evidence
"""

from datetime import datetime
import os
import json

# reportlab is an optional dependency — guard the import so this module can
# always be loaded.  generate_pdf_report() will raise a clear ImportError
# with install instructions if reportlab is absent at call time.
try:
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Image,
        Table, TableStyle, PageBreak, KeepTogether
    )
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    _REPORTLAB_OK = True
except ImportError:
    _REPORTLAB_OK = False

class FrameAnalysisPDFGenerator:
    """Generate comprehensive PDF reports with visuals"""
    
    def __init__(self, results_json_path, output_pdf_path=None):
        self.results_json_path = results_json_path
        
        # Auto-generate PDF name if not provided
        if output_pdf_path is None:
            base_name = os.path.splitext(results_json_path)[0]
            output_pdf_path = f"{base_name}_report.pdf"
        
        self.output_pdf_path = output_pdf_path
        
        # Load results
        with open(results_json_path, 'r') as f:
            self.results = json.load(f)
        
        # Setup styles
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
        
        # Create document
        self.doc = SimpleDocTemplate(
            output_pdf_path,
            pagesize=letter,
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=1*inch,
            bottomMargin=1*inch
        )
        
        self.story = []
    
    def _setup_custom_styles(self):
        """Setup custom paragraph styles"""
        # Title style
        self.styles.add(ParagraphStyle(
            name='CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1a365d'),
            spaceAfter=30,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        ))
        
        # Section header
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor('#2563eb'),
            spaceAfter=12,
            spaceBefore=20,
            fontName='Helvetica-Bold'
        ))
        
        # Finding header
        self.styles.add(ParagraphStyle(
            name='FindingHeader',
            parent=self.styles['Heading3'],
            fontSize=12,
            textColor=colors.HexColor('#dc2626'),
            spaceAfter=6,
            spaceBefore=10,
            fontName='Helvetica-Bold'
        ))
        
        # Warning text
        self.styles.add(ParagraphStyle(
            name='Warning',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#d97706'),
            spaceAfter=20,
            leftIndent=20,
            rightIndent=20,
            alignment=TA_JUSTIFY,
            borderColor=colors.HexColor('#fef3c7'),
            borderWidth=1,
            borderPadding=10,
            backColor=colors.HexColor('#fffbeb')
        ))
    
    def generate(self):
        """Generate the complete PDF report"""
        self._add_title_page()
        self._add_executive_summary()
        self._add_media_information()
        self._add_findings_section()
        self._add_evidence_artifacts()
        self._add_technical_details()
        self._add_disclaimer()
        
        # Build PDF
        self.doc.build(self.story)
        print(f"✓ PDF report generated: {self.output_pdf_path}")
        return self.output_pdf_path
    
    def _add_title_page(self):
        """Add title page"""
        # Title
        title = Paragraph(
            "Frame Analysis Forensic Report",
            self.styles['CustomTitle']
        )
        self.story.append(title)
        self.story.append(Spacer(1, 0.3*inch))
        
        # Job ID
        job_id = self.results.get('job_id', 'N/A')
        job_text = Paragraph(
            f"<b>Analysis ID:</b> {job_id}",
            self.styles['Normal']
        )
        self.story.append(job_text)
        self.story.append(Spacer(1, 0.1*inch))
        
        # Date
        completed_at = self.results.get('completed_at', 'N/A')
        try:
            date_obj = datetime.fromisoformat(completed_at)
            date_str = date_obj.strftime("%B %d, %Y at %I:%M %p")
        except:
            date_str = completed_at
        
        date_text = Paragraph(
            f"<b>Analysis Completed:</b> {date_str}",
            self.styles['Normal']
        )
        self.story.append(date_text)
        self.story.append(Spacer(1, 0.5*inch))
        
        # Warning banner
        warning = Paragraph(
            "<b>IMPORTANT NOTICE:</b> This report presents anomaly signals detected through "
            "automated analysis. Findings do not constitute proof of manipulation and should "
            "be verified by human experts. Platform recompression and encoding may affect results.",
            self.styles['Warning']
        )
        self.story.append(warning)
        self.story.append(PageBreak())
    
    def _add_executive_summary(self):
        """Add executive summary"""
        header = Paragraph("Executive Summary", self.styles['SectionHeader'])
        self.story.append(header)
        
        findings = self.results.get('findings', [])
        findings_count = len(findings)
        
        # Summary table
        summary_data = [
            ['Metric', 'Value'],
            ['Total Anomalies Detected', str(findings_count)],
            ['High Severity', str(sum(1 for f in findings if 'HIGH' in str(f.get('severity', ''))))],
            ['Medium Severity', str(sum(1 for f in findings if 'MEDIUM' in str(f.get('severity', ''))))],
            ['Low Severity', str(sum(1 for f in findings if 'LOW' in str(f.get('severity', ''))))],
        ]
        
        summary_table = Table(summary_data, colWidths=[3*inch, 2*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('PADDING', (0, 0), (-1, -1), 8),
        ]))
        
        self.story.append(summary_table)
        self.story.append(Spacer(1, 0.3*inch))
        
        # Overall assessment
        if findings_count == 0:
            assessment = "No significant anomalies detected. Media appears temporally consistent."
            color = colors.green
        elif findings_count <= 3:
            assessment = "Minor anomalies detected. Further investigation recommended."
            color = colors.orange
        else:
            assessment = "Multiple anomalies detected. Detailed review strongly recommended."
            color = colors.red
        
        assessment_text = Paragraph(
            f"<b>Assessment:</b> <font color='{color}'>{assessment}</font>",
            self.styles['Normal']
        )
        self.story.append(assessment_text)
        self.story.append(Spacer(1, 0.3*inch))
    
    def _add_media_information(self):
        """Add media information section"""
        header = Paragraph("Media Information", self.styles['SectionHeader'])
        self.story.append(header)
        
        media_info = self.results.get('media_info', {})
        media_type = self.results.get('media_type', 'Unknown')
        
        # Create info table
        info_data = [['Property', 'Value']]
        info_data.append(['Media Type', media_type])
        
        for key, value in media_info.items():
            formatted_key = key.replace('_', ' ').title()
            info_data.append([formatted_key, str(value)])
        
        info_table = Table(info_data, colWidths=[2.5*inch, 3*inch])
        info_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f3f4f6')),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('PADDING', (0, 0), (-1, -1), 8),
        ]))
        
        self.story.append(info_table)
        self.story.append(Spacer(1, 0.3*inch))
    
    def _add_findings_section(self):
        """Add detailed findings"""
        header = Paragraph("Detailed Findings", self.styles['SectionHeader'])
        self.story.append(header)
        
        findings = self.results.get('findings', [])
        
        if not findings:
            no_findings = Paragraph(
                "No anomalies were detected in this analysis.",
                self.styles['Normal']
            )
            self.story.append(no_findings)
            self.story.append(Spacer(1, 0.2*inch))
            return
        
        # Group by severity
        high = [f for f in findings if 'HIGH' in str(f.get('severity', ''))]
        medium = [f for f in findings if 'MEDIUM' in str(f.get('severity', ''))]
        low = [f for f in findings if 'LOW' in str(f.get('severity', ''))]
        
        # Add high severity findings
        if high:
            self._add_severity_group("High Severity Findings", high, colors.red)
        
        # Add medium severity findings
        if medium:
            self._add_severity_group("Medium Severity Findings", medium, colors.orange)
        
        # Add low severity findings
        if low:
            self._add_severity_group("Low Severity Findings", low, colors.HexColor('#f59e0b'))
    
    def _add_severity_group(self, title, findings, color):
        """Add a group of findings by severity"""
        group_header = Paragraph(
            f"<font color='{color}'>{title} ({len(findings)})</font>",
            self.styles['FindingHeader']
        )
        self.story.append(group_header)
        
        for i, finding in enumerate(findings, 1):
            self._add_single_finding(finding, i, color)
        
        self.story.append(Spacer(1, 0.2*inch))
    
    def _add_single_finding(self, finding, number, color):
        """Add a single finding with details"""
        finding_type = finding.get('type', 'Unknown').replace('FindingType.', '').replace('_', ' ')
        location = finding.get('location', {})
        explanation = finding.get('explanation', 'No explanation provided')
        metrics = finding.get('metrics', {})
        
        # Finding header
        location_str = self._format_location(location)
        finding_title = Paragraph(
            f"<b>Finding #{number}: {finding_type}</b> - {location_str}",
            self.styles['Normal']
        )
        self.story.append(finding_title)
        self.story.append(Spacer(1, 0.05*inch))
        
        # Explanation
        explanation_text = Paragraph(
            f"<i>{explanation}</i>",
            self.styles['Normal']
        )
        self.story.append(explanation_text)
        self.story.append(Spacer(1, 0.1*inch))
        
        # Metrics table
        if metrics:
            metrics_data = [['Metric', 'Value']]
            for key, value in metrics.items():
                formatted_key = key.replace('_', ' ').title()
                if isinstance(value, float):
                    formatted_value = f"{value:.4f}"
                else:
                    formatted_value = str(value)
                metrics_data.append([formatted_key, formatted_value])
            
            metrics_table = Table(metrics_data, colWidths=[2*inch, 1.5*inch])
            metrics_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), color),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fafafa')),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('PADDING', (0, 0), (-1, -1), 6),
            ]))
            
            self.story.append(metrics_table)
        
        self.story.append(Spacer(1, 0.15*inch))
    
    def _format_location(self, location):
        """Format location for display"""
        if 'start' in location and 'end' in location:
            if isinstance(location['start'], (int, float)) and location['start'] < 100:
                # Likely frame indices
                return f"Frames {location['start']}-{location['end']}"
            else:
                # Likely timestamps
                return f"{location['start']:.2f}s - {location['end']:.2f}s"
        elif 'frames' in location:
            frames = location['frames']
            if len(frames) <= 5:
                return f"Frames: {', '.join(map(str, frames))}"
            else:
                return f"Frames: {', '.join(map(str, frames[:5]))}... ({len(frames)} total)"
        return "Location unknown"
    
    def _add_evidence_artifacts(self):
        """Add evidence artifacts section with images"""
        header = Paragraph("Evidence Artifacts", self.styles['SectionHeader'])
        self.story.append(header)
        
        findings = self.results.get('findings', [])
        artifacts_added = 0
        
        for i, finding in enumerate(findings[:5], 1):  # Limit to first 5 to keep PDF size reasonable
            artifacts = finding.get('evidence_artifacts', [])
            
            if not artifacts:
                continue
            
            finding_type = finding.get('type', 'Unknown').replace('FindingType.', '').replace('_', ' ')
            
            artifact_header = Paragraph(
                f"<b>Evidence for Finding #{i}: {finding_type}</b>",
                self.styles['Normal']
            )
            self.story.append(artifact_header)
            self.story.append(Spacer(1, 0.1*inch))
            
            # Add images (before, after, diff)
            images_to_show = []
            for artifact_path in artifacts[:3]:  # Show max 3 per finding
                if os.path.exists(artifact_path):
                    try:
                        img = Image(artifact_path, width=2*inch, height=1.5*inch)
                        filename = os.path.basename(artifact_path)
                        images_to_show.append([img, Paragraph(filename, self.styles['Normal'])])
                    except Exception as e:
                        print(f"Could not load image {artifact_path}: {e}")
            
            if images_to_show:
                # Create table of images
                img_table = Table(images_to_show, colWidths=[2*inch, 3*inch])
                img_table.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('PADDING', (0, 0), (-1, -1), 6),
                ]))
                self.story.append(img_table)
                artifacts_added += 1
            
            self.story.append(Spacer(1, 0.2*inch))
        
        if artifacts_added == 0:
            no_artifacts = Paragraph(
                "No visual evidence artifacts available for this analysis.",
                self.styles['Normal']
            )
            self.story.append(no_artifacts)
        
        self.story.append(Spacer(1, 0.2*inch))
    
    def _add_technical_details(self):
        """Add technical analysis details"""
        header = Paragraph("Technical Details", self.styles['SectionHeader'])
        self.story.append(header)
        
        parameters = self.results.get('parameters', {})
        
        tech_data = [['Parameter', 'Value']]
        for key, value in parameters.items():
            formatted_key = key.replace('_', ' ').title()
            tech_data.append([formatted_key, str(value)])
        
        tech_table = Table(tech_data, colWidths=[2.5*inch, 3*inch])
        tech_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#475569')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f1f5f9')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        
        self.story.append(tech_table)
        self.story.append(Spacer(1, 0.3*inch))
    
    def _add_disclaimer(self):
        """Add legal disclaimer"""
        self.story.append(PageBreak())
        
        header = Paragraph("Disclaimer & Methodology", self.styles['SectionHeader'])
        self.story.append(header)
        
        disclaimer_text = """
        <b>Analysis Methodology:</b><br/>
        This report was generated through automated frame-by-frame analysis using computer vision 
        techniques including SSIM (Structural Similarity Index), histogram comparison, sharpness 
        detection, and compression artifact analysis.<br/><br/>
        
        <b>Interpretation of Results:</b><br/>
        Findings in this report represent statistical anomalies and temporal inconsistencies detected 
        in the analyzed media. These findings should be interpreted as indicators requiring further 
        investigation, not as definitive proof of manipulation.<br/><br/>
        
        <b>Limitations:</b><br/>
        • Platform recompression (social media, messaging apps) can trigger false positives<br/>
        • Legitimate editing for creative purposes may be flagged<br/>
        • Advanced manipulation techniques may evade detection<br/>
        • Results should be verified by qualified forensic analysts<br/><br/>
        
        <b>Legal Notice:</b><br/>
        This analysis is provided for informational purposes only. The creators of this software 
        make no warranties about the completeness, reliability, or accuracy of this information. 
        This report should not be used as the sole basis for legal, business, or personal decisions.
        """
        
        disclaimer = Paragraph(disclaimer_text, self.styles['Normal'])
        self.story.append(disclaimer)


# Usage function
def generate_pdf_report(json_path, output_pdf=None):
    """
    Generate PDF report from JSON results

    Args:
        json_path: Path to results JSON file
        output_pdf: Optional output PDF path (auto-generated if None)

    Returns:
        Path to generated PDF
    """
    if not _REPORTLAB_OK:
        raise ImportError(
            "reportlab is not installed. Run: pip install reportlab"
        )
    generator = FrameAnalysisPDFGenerator(json_path, output_pdf)
    return generator.generate()


# Command-line usage
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python pdf_generator.py <results.json> [output.pdf]")
        sys.exit(1)
    
    json_path = sys.argv[1]
    output_pdf = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not os.path.exists(json_path):
        print(f"Error: JSON file not found: {json_path}")
        sys.exit(1)
    
    try:
        pdf_path = generate_pdf_report(json_path, output_pdf)
        print(f"\n✓ Success! PDF report generated:")
        print(f"  {pdf_path}")
    except Exception as e:
        print(f"\n✗ Error generating PDF: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)