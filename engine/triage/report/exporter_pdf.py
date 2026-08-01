import os
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from typing import Dict, Any, List

class PDFExporter:
    """
    Exports forensic reports to PDF format using ReportLab.
    """
    
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.styles = getSampleStyleSheet()
        
    def export(self, report_data: Dict[str, Any], filename: str) -> str:
        """
        Generates a PDF document from standard report data.
        """
        filepath = os.path.join(self.output_dir, filename)
        
        doc = SimpleDocTemplate(filepath, pagesize=A4,
                                rightMargin=72, leftMargin=72,
                                topMargin=72, bottomMargin=18)
                                
        Story = []
        
        # Title
        title = report_data.get('title', 'Forensic Report')
        Story.append(Paragraph(title, self.styles["Title"]))
        Story.append(Spacer(1, 24))
        
        # Metadata
        Story.append(Paragraph("Metadata", self.styles["Heading1"]))
        metadata = report_data.get('metadata', {})
        for key, value in metadata.items():
            text = f"<b>{key}:</b> {value}"
            Story.append(Paragraph(text, self.styles["Normal"]))
        Story.append(Spacer(1, 12))
        
        # Sections
        sections = report_data.get('sections', [])
        for section in sections:
            Story.append(Paragraph(section.get('heading', 'Section'), self.styles["Heading2"]))
            Story.append(Spacer(1, 12))
            
            content = section.get('content', '')
            if content:
                Story.append(Paragraph(content, self.styles["Normal"]))
                Story.append(Spacer(1, 12))
                
            # Add table if present
            if 'table_data' in section:
                table_data = section['table_data']
                if table_data:
                    headers = list(table_data[0].keys())
                    data = [headers]
                    for row in table_data:
                        data.append([str(row.get(h, '')) for h in headers])
                        
                    t = Table(data)
                    t.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                        ('GRID', (0, 0), (-1, -1), 1, colors.black)
                    ]))
                    Story.append(t)
                    Story.append(Spacer(1, 12))
                    
        doc.build(Story)
        return filepath
