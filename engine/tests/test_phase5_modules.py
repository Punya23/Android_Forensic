"""Tests for Phase 5 modules: Jurisdiction, Team Collaboration, and Intelligence Sharing."""

import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime

# Import Phase 5 modules
from triage.case_management.jurisdiction import (
    link_cases_across_jurisdictions,
    get_district_cases,
    get_state_statistics,
    export_to_ncrb,
    get_station_dashboard,
    create_task_force,
)

from triage.collaboration.team import (
    add_examiner_to_case,
    share_case,
    annotate_evidence,
    create_discussion,
    post_to_discussion,
    assign_task,
    update_task_status,
    update_case_status,
    get_team_dashboard,
)

from triage.intelligence.sharing import (
    extract_crime_pattern,
    match_crime_pattern,
    add_to_network_database,
    query_knowledge_graph,
    search_case_repository,
    get_trend_analysis,
)


# ==================== Jurisdiction Tests ====================

def test_create_task_force():
    """Test multi-agency task force creation"""
    task_force_id = create_task_force(
        case_ids=['CASE001', 'CASE002'],
        agencies=['CBI', 'Local Police', 'Cyber Cell'],
        name='Cyber Fraud Task Force'
    )
    
    assert task_force_id
    assert len(task_force_id) == 8  # 8 character ID


def test_link_cases_empty():
    """Test case linking with empty repository"""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = link_cases_across_jurisdictions(
            case_ids=['CASE001'],
            repository_path=tmpdir
        )
        
        assert 'links' in result
        assert 'network' in result
        assert 'summary' in result
        assert result['summary']['total_cases'] == 1


def test_get_district_cases():
    """Test getting district cases"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create mock case
        case_dir = Path(tmpdir) / 'CASE001'
        case_dir.mkdir()
        
        meta = {
            'case_id': 'CASE001',
            'district': 'MUMBAI',
            'crime_type': 'Cyber Fraud',
            'status': 'PENDING'
        }
        
        with open(case_dir / 'meta.json', 'w') as f:
            json.dump(meta, f)
        
        cases = get_district_cases('MUMBAI', tmpdir)
        
        assert len(cases) == 1
        assert cases[0]['case_id'] == 'CASE001'


def test_get_state_statistics():
    """Test state-level statistics"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create multiple mock cases
        for i in range(5):
            case_dir = Path(tmpdir) / f'CASE{i:03d}'
            case_dir.mkdir()
            
            meta = {
                'case_id': f'CASE{i:03d}',
                'state': 'MH',
                'district': 'MUMBAI' if i < 3 else 'PUNE',
                'crime_type': 'Cyber Fraud' if i < 3 else 'Financial Fraud',
                'status': 'Resolved' if i < 2 else 'Pending'
            }
            
            with open(case_dir / 'meta.json', 'w') as f:
                json.dump(meta, f)
        
        stats = get_state_statistics('MH', tmpdir)
        
        assert stats['total_cases'] == 5
        assert stats['resolved_cases'] == 2
        assert stats['pending_cases'] == 3
        assert 'Cyber Fraud' in stats['by_crime_type']
        assert 'MUMBAI' in stats['by_district']


def test_export_to_ncrb():
    """Test NCRB format export"""
    with tempfile.TemporaryDirectory() as tmpdir:
        case_dir = Path(tmpdir)
        
        meta = {
            'case_id': 'CASE001',
            'fir_number': 'FIR001',
            'crime_type': 'Cyber Fraud',
            'date': '2024-01-01',
            'station': 'Cyber Cell',
            'district': 'MUMBAI',
            'state': 'MH',
        }
        
        with open(case_dir / 'meta.json', 'w') as f:
            json.dump(meta, f)
        
        ncrb_data = export_to_ncrb(str(case_dir))
        
        assert 'ncrb_version' in ncrb_data
        assert 'FIR001' in ncrb_data


def test_get_station_dashboard():
    """Test police station dashboard generation"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create case for station
        case_dir = Path(tmpdir) / 'CASE001'
        case_dir.mkdir()
        
        meta = {
            'case_id': 'CASE001',
            'station': 'CYBER-CELL-01',
            'crime_type': 'Online Fraud',
            'status': 'PENDING',
            'priority': 'HIGH'
        }
        
        with open(case_dir / 'meta.json', 'w') as f:
            json.dump(meta, f)
        
        html = get_station_dashboard('CYBER-CELL-01', tmpdir)
        
        assert 'Station Dashboard' in html
        assert 'CYBER-CELL-01' in html
        assert 'Total Cases' in html


# ==================== Team Collaboration Tests ====================

def test_add_examiner_to_case():
    """Test adding examiner to case"""
    with tempfile.TemporaryDirectory() as tmpdir:
        case_dir = Path(tmpdir) / 'CASE001'
        case_dir.mkdir()
        
        result = add_examiner_to_case(
            'CASE001',
            'examiner@police.gov',
            'analyst',
            tmpdir
        )
        
        assert result == True
        
        # Verify team file created
        team_file = case_dir / 'team.json'
        assert team_file.exists()
        
        with open(team_file, 'r') as f:
            team_data = json.load(f)
        
        assert len(team_data['members']) == 1
        assert team_data['members'][0]['examiner'] == 'examiner@police.gov'
        assert team_data['members'][0]['role'] == 'analyst'


def test_share_case():
    """Test case sharing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        case_dir = Path(tmpdir) / 'CASE001'
        case_dir.mkdir()
        
        permissions = {
            'read': True,
            'write': False,
            'export': True,
            'share': False
        }
        
        result = share_case('CASE001', 'another@police.gov', permissions, tmpdir)
        
        assert result == True
        
        sharing_file = case_dir / 'sharing.json'
        assert sharing_file.exists()


def test_annotate_evidence():
    """Test evidence annotation"""
    with tempfile.TemporaryDirectory() as tmpdir:
        case_dir = Path(tmpdir) / 'CASE001'
        case_dir.mkdir()
        
        result = annotate_evidence(
            'EVIDENCE001',
            'This evidence is critical - shows direct communication',
            'examiner@police.gov',
            'CASE001',
            tmpdir
        )
        
        assert result == True
        
        annotations_file = case_dir / 'annotations.json'
        assert annotations_file.exists()
        
        with open(annotations_file, 'r') as f:
            annotations = json.load(f)
        
        assert 'EVIDENCE001' in annotations
        assert len(annotations['EVIDENCE001']) == 1


def test_create_discussion():
    """Test discussion thread creation"""
    with tempfile.TemporaryDirectory() as tmpdir:
        thread = create_discussion(
            'CASE001',
            'Evidence Analysis Discussion',
            'examiner1@police.gov',
            tmpdir
        )
        
        assert 'thread_id' in thread
        assert thread['topic'] == 'Evidence Analysis Discussion'
        assert thread['creator'] == 'examiner1@police.gov'
        assert thread['status'] == 'open'


def test_post_to_discussion():
    """Test posting to discussion"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create discussion first
        thread = create_discussion(
            'CASE001',
            'Test Discussion',
            'examiner1@police.gov',
            tmpdir
        )
        
        # Post message
        result = post_to_discussion(
            'CASE001',
            thread['thread_id'],
            'I found suspicious patterns in the messages',
            'examiner2@police.gov',
            tmpdir
        )
        
        assert result == True


def test_assign_task():
    """Test task assignment"""
    with tempfile.TemporaryDirectory() as tmpdir:
        task = {
            'title': 'Analyze WhatsApp messages',
            'description': 'Review all WhatsApp messages for evidence',
            'assignee': 'analyst@police.gov',
            'priority': 'high',
            'due_date': '2024-12-31'
        }
        
        result = assign_task('CASE001', task, tmpdir)
        
        assert result == True


def test_update_task_status():
    """Test task status update"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create task first
        task = {
            'title': 'Test Task',
            'assignee': 'analyst@police.gov',
        }
        assign_task('CASE001', task, tmpdir)
        
        # Load tasks to get task_id
        case_dir = Path(tmpdir) / 'CASE001'
        with open(case_dir / 'tasks.json', 'r') as f:
            tasks_data = json.load(f)
        
        task_id = tasks_data['tasks'][0]['task_id']
        
        # Update status
        result = update_task_status('CASE001', task_id, 'completed', tmpdir)
        
        assert result == True


def test_update_case_status():
    """Test case status update"""
    with tempfile.TemporaryDirectory() as tmpdir:
        case_dir = Path(tmpdir) / 'CASE001'
        case_dir.mkdir()
        
        meta = {'case_id': 'CASE001', 'status': 'PENDING'}
        with open(case_dir / 'meta.json', 'w') as f:
            json.dump(meta, f)
        
        result = update_case_status('CASE001', 'ANALYZED', tmpdir)
        
        assert result == True
        
        # Verify status updated
        with open(case_dir / 'meta.json', 'r') as f:
            updated_meta = json.load(f)
        
        assert updated_meta['status'] == 'ANALYZED'
        assert 'status_history' in updated_meta


def test_get_team_dashboard():
    """Test team dashboard generation"""
    with tempfile.TemporaryDirectory() as tmpdir:
        case_dir = Path(tmpdir) / 'CASE001'
        case_dir.mkdir()
        
        # Add team member
        add_examiner_to_case('CASE001', 'examiner@police.gov', 'lead', tmpdir)
        
        # Assign task
        task = {'title': 'Test Task', 'assignee': 'examiner@police.gov'}
        assign_task('CASE001', task, tmpdir)
        
        html = get_team_dashboard('CASE001', tmpdir)
        
        assert 'Team Collaboration Dashboard' in html
        assert 'examiner@police.gov' in html


# ==================== Intelligence Sharing Tests ====================

def test_extract_crime_pattern():
    """Test crime pattern extraction"""
    with tempfile.TemporaryDirectory() as tmpdir:
        case_dir = Path(tmpdir) / 'CASE001'
        case_dir.mkdir()
        derived_dir = case_dir / 'derived'
        derived_dir.mkdir()
        
        # Create meta
        meta = {
            'case_id': 'CASE001',
            'crime_type': 'Cyber Fraud',
            'date': '2024-01-01'
        }
        with open(case_dir / 'meta.json', 'w') as f:
            json.dump(meta, f)
        
        # Create mock messages
        messages = [
            {'app': 'whatsapp', 'text': 'Send money'},
            {'app': 'telegram', 'text': 'Transfer funds'},
        ]
        with open(derived_dir / 'messages.json', 'w') as f:
            json.dump(messages, f)
        
        pattern = extract_crime_pattern('CASE001', tmpdir)
        
        assert 'pattern_id' in pattern
        assert pattern['case_id'] == 'CASE001'
        assert 'mo_features' in pattern


def test_match_crime_pattern():
    """Test pattern matching"""
    pattern = {
        'crime_type': 'Cyber Fraud',
        'mo_features': {
            'communication_apps': ['whatsapp', 'telegram'],
            'payment_methods': ['UPI']
        }
    }
    
    matches = match_crime_pattern(pattern, threshold=0.5)
    
    assert isinstance(matches, list)


def test_add_to_network_database():
    """Test adding to criminal network database"""
    network_data = {
        'case_id': 'CASE001',
        'suspects': ['Suspect A', 'Suspect B'],
        'victims': ['Victim X'],
        'associates': ['Associate Y'],
        'crime_type': 'Organized Fraud'
    }
    
    result = add_to_network_database(network_data)
    
    assert result == True


def test_query_knowledge_graph():
    """Test knowledge graph querying"""
    results = query_knowledge_graph('cyber fraud')
    
    assert isinstance(results, list)


def test_search_case_repository():
    """Test case repository search"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create mock case
        case_dir = Path(tmpdir) / 'CASE001'
        case_dir.mkdir()
        
        meta = {
            'case_id': 'CASE001',
            'crime_type': 'Cyber Fraud',
            'description': 'Online banking fraud case',
            'status': 'PENDING'
        }
        with open(case_dir / 'meta.json', 'w') as f:
            json.dump(meta, f)
        
        results = search_case_repository(
            'fraud',
            filters={},
            repository_path=tmpdir
        )
        
        assert isinstance(results, list)
        if results:
            assert results[0]['case_id'] == 'CASE001'


def test_get_trend_analysis():
    """Test crime trend analysis"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create multiple cases over time
        for i in range(5):
            case_dir = Path(tmpdir) / f'CASE{i:03d}'
            case_dir.mkdir()
            
            meta = {
                'case_id': f'CASE{i:03d}',
                'crime_type': 'Cyber Fraud',
                'state': 'MH',
                'date': f'2024-0{i+1}-01'
            }
            with open(case_dir / 'meta.json', 'w') as f:
                json.dump(meta, f)
        
        trend = get_trend_analysis('Cyber Fraud', 'MH', tmpdir)
        
        assert 'crime_type' in trend
        assert 'temporal_trend' in trend
        assert 'statistics' in trend
        assert trend['statistics']['total_cases'] == 5


# ==================== Integration Tests ====================

def test_full_collaboration_workflow():
    """Test complete collaboration workflow"""
    with tempfile.TemporaryDirectory() as tmpdir:
        case_id = 'CASE001'
        case_dir = Path(tmpdir) / case_id
        case_dir.mkdir()
        
        # 1. Create case metadata
        meta = {'case_id': case_id, 'status': 'PENDING'}
        with open(case_dir / 'meta.json', 'w') as f:
            json.dump(meta, f)
        
        # 2. Add team members
        add_examiner_to_case(case_id, 'lead@police.gov', 'lead', tmpdir)
        add_examiner_to_case(case_id, 'analyst@police.gov', 'analyst', tmpdir)
        
        # 3. Create discussion
        thread = create_discussion(case_id, 'Case Strategy', 'lead@police.gov', tmpdir)
        
        # 4. Assign task
        task = {
            'title': 'Initial Analysis',
            'assignee': 'analyst@police.gov',
            'priority': 'high'
        }
        assign_task(case_id, task, tmpdir)
        
        # 5. Update case status
        update_case_status(case_id, 'ANALYZED', tmpdir)
        
        # Verify final state
        with open(case_dir / 'meta.json', 'r') as f:
            final_meta = json.load(f)
        
        assert final_meta['status'] == 'ANALYZED'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
