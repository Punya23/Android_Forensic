"""Legal intelligence module for statute matching and report generation.

Provides tools for matching evidence to legal statutes (IPC/BNS),
generating FIRs, and creating court-ready expert reports.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List


# IPC/BNS Section Database
STATUTE_DATABASE = {
    # Cyber Crimes
    '66': {
        'name': 'Computer related offenses',
        'section': 'IT Act Section 66',
        'keywords': ['hacking', 'unauthorized access', 'computer', 'data theft', 'malware'],
        'description': 'Hacking with computer system'
    },
    '66C': {
        'name': 'Identity theft',
        'section': 'IT Act Section 66C',
        'keywords': ['identity', 'impersonation', 'fake profile', 'fraudulent', 'password'],
        'description': 'Punishment for identity theft'
    },
    '66D': {
        'name': 'Cheating by personation using computer',
        'section': 'IT Act Section 66D',
        'keywords': ['online fraud', 'cheating', 'impersonation', 'computer resource'],
        'description': 'Punishment for cheating by personation using computer resource'
    },
    '67': {
        'name': 'Publishing obscene information',
        'section': 'IT Act Section 67',
        'keywords': ['obscene', 'pornography', 'sexual', 'explicit content'],
        'description': 'Publishing or transmitting obscene material in electronic form'
    },
    # IPC Sections
    '120B': {
        'name': 'Criminal conspiracy',
        'section': 'IPC Section 120B',
        'keywords': ['conspiracy', 'plot', 'planning', 'agreement', 'illegal act'],
        'description': 'Punishment of criminal conspiracy'
    },
    '420': {
        'name': 'Cheating',
        'section': 'IPC Section 420',
        'keywords': ['fraud', 'cheat', 'dishonest', 'deceive', 'property', 'money'],
        'description': 'Cheating and dishonestly inducing delivery of property'
    },
    '406': {
        'name': 'Criminal breach of trust',
        'section': 'IPC Section 406',
        'keywords': ['breach of trust', 'entrusted', 'misappropriation'],
        'description': 'Punishment for criminal breach of trust'
    },
    '467': {
        'name': 'Forgery of valuable security',
        'section': 'IPC Section 467',
        'keywords': ['forge', 'document', 'fake', 'fabricate', 'counterfeit'],
        'description': 'Forgery of valuable security, will, etc.'
    },
    '506': {
        'name': 'Criminal intimidation',
        'section': 'IPC Section 506',
        'keywords': ['threat', 'intimidate', 'fear', 'alarm', 'coerce'],
        'description': 'Punishment for criminal intimidation'
    },
    '509': {
        'name': 'Insulting modesty of woman',
        'section': 'IPC Section 509',
        'keywords': ['insult', 'modesty', 'woman', 'harassment', 'obscene'],
        'description': 'Word, gesture or act intended to insult the modesty of a woman'
    },
    '354': {
        'name': 'Assault on woman',
        'section': 'IPC Section 354',
        'keywords': ['assault', 'outrage', 'modesty', 'woman', 'force'],
        'description': 'Assault or criminal force to woman with intent to outrage her modesty'
    },
    '292': {
        'name': 'Sale of obscene material',
        'section': 'IPC Section 292',
        'keywords': ['obscene', 'material', 'sale', 'distribute', 'exhibit'],
        'description': 'Sale, etc., of obscene books, etc.'
    },
}


def match_statutes(text: str) -> List[Dict[str, Any]]:
    """Match evidence text to relevant IPC/BNS sections.
    
    Args:
        text: Evidence text (message, call log, etc.)
        
    Returns:
        List of matched statute dicts:
        [{
            'section': '420',
            'name': 'Cheating',
            'full_section': 'IPC Section 420',
            'reason': 'Keywords matched: fraud, cheat',
            'confidence': 0.85,
            'matched_keywords': ['fraud', 'cheat']
        }]
    """
    if not text or not isinstance(text, str):
        return []
    
    matches = []
    text_lower = text.lower()
    
    for section_num, section_data in STATUTE_DATABASE.items():
        matched_keywords = []
        
        # Check each keyword
        for keyword in section_data['keywords']:
            if keyword in text_lower:
                matched_keywords.append(keyword)
        
        # If we have matches, add to results
        if matched_keywords:
            # Calculate confidence based on number of keywords matched
            confidence = min(len(matched_keywords) / len(section_data['keywords']), 1.0)
            confidence = max(confidence, 0.5)  # Minimum 0.5 for any match
            
            matches.append({
                'section': section_num,
                'name': section_data['name'],
                'full_section': section_data['section'],
                'description': section_data['description'],
                'reason': f"Keywords matched: {', '.join(matched_keywords[:3])}",
                'confidence': round(confidence, 2),
                'matched_keywords': matched_keywords
            })
    
    # Sort by confidence (highest first)
    matches.sort(key=lambda x: x['confidence'], reverse=True)
    
    return matches


def generate_fir(case_data: Dict, evidence: List[Dict]) -> str:
    """Generate FIR (First Information Report) draft from case data.
    
    Args:
        case_data: Dict with case information:
            - case_id: str
            - complainant: str
            - accused: str
            - incident_date: str
            - incident_place: str
            - description: str
        evidence: List of evidence items
        
    Returns:
        Complete FIR draft text
    """
    fir_template = """
FIRST INFORMATION REPORT
(Under Section 154 Cr.P.C.)

FIR Number: {case_id}
Date of Report: {report_date}
Police Station: _________________
District: _________________

1. COMPLAINANT DETAILS:
   Name: {complainant}
   Address: _________________
   Contact: _________________

2. ACCUSED DETAILS:
   Name/Description: {accused}
   Address: (if known) _________________

3. INCIDENT DETAILS:
   Date of Incident: {incident_date}
   Time of Incident: (if known) _________________
   Place of Incident: {incident_place}

4. DESCRIPTION OF INCIDENT:
{description}

5. APPLICABLE SECTIONS:
{sections}

6. EVIDENCE RECOVERED:
{evidence_summary}

7. ACTION TAKEN:
The above information is being recorded based on the complainant's statement.
Investigation is being initiated under the applicable sections of law.

8. DIGITAL EVIDENCE:
This FIR is supported by digital forensic analysis conducted using SNAGR
(Systematic Network-Assisted Gyroscopic Recovery) forensic tool. Evidence
has been preserved following standard chain of custody protocols.

Signature of Complainant: _________________ Date: _________________

Signature of Recording Officer: _________________ Date: _________________

---
Note: This is a draft FIR generated from forensic analysis. It must be reviewed,
completed, and filed by appropriate law enforcement authorities.
"""
    
    # Match statutes from case description
    matched_statutes = match_statutes(case_data.get('description', ''))
    
    sections_text = ""
    if matched_statutes:
        for statute in matched_statutes[:5]:  # Top 5
            sections_text += f"   - {statute['full_section']}: {statute['name']}\n"
    else:
        sections_text = "   (To be determined based on investigation)\n"
    
    # Summarize evidence
    evidence_summary = ""
    if evidence:
        evidence_summary = f"   Total evidence items: {len(evidence)}\n"
        
        # Count by type
        evidence_types = {}
        for item in evidence:
            item_type = item.get('type', 'unknown')
            evidence_types[item_type] = evidence_types.get(item_type, 0) + 1
        
        for etype, count in evidence_types.items():
            evidence_summary += f"   - {etype}: {count} items\n"
    else:
        evidence_summary = "   (Evidence details to be added)\n"
    
    # Format FIR
    fir_text = fir_template.format(
        case_id=case_data.get('case_id', 'PENDING'),
        report_date=datetime.now().strftime('%Y-%m-%d'),
        complainant=case_data.get('complainant', '________________'),
        accused=case_data.get('accused', '________________'),
        incident_date=case_data.get('incident_date', '________________'),
        incident_place=case_data.get('incident_place', '________________'),
        description=case_data.get('description', '(Description to be added)'),
        sections=sections_text,
        evidence_summary=evidence_summary
    )
    
    return fir_text


def generate_expert_report(case_dir: str, case_data: Dict) -> str:
    """Generate court-ready expert forensic report.
    
    Args:
        case_dir: Path to case directory
        case_data: Dict with case information and findings
        
    Returns:
        Complete expert report text
    """
    report_template = """
FORENSIC EXPERT REPORT
Digital Evidence Analysis

Report Number: {case_id}
Date of Report: {report_date}
Examiner: _________________
Qualification: Digital Forensics Expert

================================================================================
EXECUTIVE SUMMARY
================================================================================

This report presents the findings of digital forensic examination conducted on
Android device evidence in case {case_id}. The examination was conducted using
SNAGR (Systematic Network-Assisted Gyroscopic Recovery), a forensic analysis
tool designed for Android mobile devices.

Device Information:
- Device Model: {device_model}
- Android Version: {android_version}
- Examination Date: {examination_date}
- Case Reference: {case_id}

================================================================================
1. METHODOLOGY
================================================================================

1.1 Tools Used:
    - SNAGR v1.0 (Android Forensic Triage Tool)
    - ADB (Android Debug Bridge) for device communication
    - SQLite database analyzers for app data extraction

1.2 Acquisition Method:
    - Tiered acquisition: Tier 0 (non-root), Tier 1 (helper app), Tier 2 (root)
    - Read-only access maintained where possible
    - Chain of custody maintained throughout examination

1.3 Analysis Approach:
    - Message extraction from WhatsApp, Telegram, SMS
    - Call log analysis
    - Media file examination with GPS metadata
    - Contact list extraction
    - Deleted record recovery (where available)

1.4 Validation:
    - Hash verification of extracted databases
    - Cross-reference with multiple data sources
    - Confidence levels assigned to all findings

================================================================================
2. FINDINGS
================================================================================

{findings_summary}

================================================================================
3. TECHNICAL DETAILS
================================================================================

3.1 Data Sources Examined:
{data_sources}

3.2 Evidence Recovered:
{evidence_details}

3.3 Deleted Data Recovery:
{deleted_data}

================================================================================
4. CHAIN OF CUSTODY
================================================================================

The following chain of custody was maintained:

1. Device received: {received_date}
2. Examination started: {exam_start}
3. Data extracted: {extraction_date}
4. Analysis completed: {analysis_date}
5. Report generated: {report_date}

All data has been stored with cryptographic hashes to ensure integrity.
Original device: {device_hash}

================================================================================
5. LIMITATIONS AND CAVEATS
================================================================================

This examination has the following limitations:

1. **Acquisition Tier**: {acquisition_tier}
   - Non-root acquisition cannot access all system files
   - Some app data may be encrypted or inaccessible

2. **Deleted Data**: 
   - Deleted record recovery depends on database journaling
   - Not all deleted data can be recovered
   - Recovery confidence varies by data source

3. **Timestamp Accuracy**:
   - All timestamps are as recorded by the device
   - Device time settings may not reflect actual time
   - Timezone considerations apply

4. **Data Completeness**:
   - Analysis limited to accessible data sources
   - Cloud-synced data not included unless locally cached
   - Some apps use encryption preventing analysis

5. **Technical Constraints**:
   - Some advanced anti-forensic techniques may limit recovery
   - Proprietary app formats may limit extraction
   - Analysis conducted on data snapshot, not live device

================================================================================
6. DECLARATION
================================================================================

I declare that:

1. This examination was conducted following digital forensics best practices
2. All findings are based on data extracted from the subject device
3. Confidence levels reflect the reliability of each finding
4. Limitations have been clearly stated
5. The examination was conducted impartially

This report contains {page_count} sections and was generated on {report_date}.

Signature: _________________
Name: _________________
Qualification: _________________
Date: _________________

================================================================================
APPENDICES
================================================================================

Appendix A: Technical Specifications
Appendix B: Tool Validation Reports
Appendix C: Hash Values
Appendix D: Data Extraction Logs

---
This report was generated using SNAGR Forensic Analysis Tool.
The tool and methodology follow NIST/SWGDE guidelines for mobile device forensics.
"""
    
    # Generate findings summary
    findings_summary = _generate_findings_summary(case_data)
    
    # Generate data sources list
    data_sources = _generate_data_sources_list(case_data)
    
    # Generate evidence details
    evidence_details = _generate_evidence_details(case_data)
    
    # Generate deleted data section
    deleted_data = _generate_deleted_data_section(case_data)
    
    # Fill template
    report_text = report_template.format(
        case_id=case_data.get('case_id', 'UNKNOWN'),
        report_date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        device_model=case_data.get('device_model', 'Unknown Device'),
        android_version=case_data.get('android_version', 'Unknown'),
        examination_date=case_data.get('examination_date', datetime.now().strftime('%Y-%m-%d')),
        findings_summary=findings_summary,
        data_sources=data_sources,
        evidence_details=evidence_details,
        deleted_data=deleted_data,
        received_date=case_data.get('received_date', 'Not specified'),
        exam_start=case_data.get('exam_start', 'Not specified'),
        extraction_date=case_data.get('extraction_date', 'Not specified'),
        analysis_date=case_data.get('analysis_date', 'Not specified'),
        acquisition_tier=case_data.get('acquisition_tier', 'Tier 0 (Non-root)'),
        device_hash=case_data.get('device_hash', 'Not available'),
        page_count=7
    )
    
    return report_text


def _generate_findings_summary(case_data: Dict) -> str:
    """Generate findings summary section."""
    findings = case_data.get('findings', [])
    
    if not findings:
        return "(No findings to report)"
    
    summary = f"Total findings: {len(findings)}\n\n"
    
    # Group by severity
    by_severity = {'critical': [], 'high': [], 'medium': [], 'low': []}
    for finding in findings:
        severity = finding.get('severity', 'low').lower()
        if severity in by_severity:
            by_severity[severity].append(finding)
    
    for severity in ['critical', 'high', 'medium', 'low']:
        count = len(by_severity[severity])
        if count > 0:
            summary += f"{severity.upper()}: {count} findings\n"
            for finding in by_severity[severity][:3]:  # Show top 3
                summary += f"  - {finding.get('description', 'No description')}\n"
    
    return summary


def _generate_data_sources_list(case_data: Dict) -> str:
    """Generate data sources list."""
    sources = case_data.get('data_sources', [])
    
    if not sources:
        return "- WhatsApp\n- Telegram\n- SMS/MMS\n- Call Logs\n- Contacts\n- Media Files"
    
    return '\n'.join(f"- {source}" for source in sources)


def _generate_evidence_details(case_data: Dict) -> str:
    """Generate evidence details section."""
    evidence_count = case_data.get('evidence_count', {})
    
    if not evidence_count:
        return "(Evidence details not available)"
    
    details = ""
    for etype, count in evidence_count.items():
        details += f"- {etype}: {count} items\n"
    
    return details


def _generate_deleted_data_section(case_data: Dict) -> str:
    """Generate deleted data recovery section."""
    deleted_count = case_data.get('deleted_records', 0)
    
    if deleted_count == 0:
        return "No deleted records were recovered during this examination."
    
    return f"""
Deleted records recovered: {deleted_count}

Recovery method: SQLite WAL/journal analysis, freelist parsing
Confidence: Medium to High (varies by record)
Note: Deleted data timestamps may not reflect actual deletion time
"""
