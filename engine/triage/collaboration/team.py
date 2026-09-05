"""Team collaboration features for multi-examiner forensic investigations.

Provides functionality for:
- Multi-examiner case assignment
- Secure case sharing with permissions
- Evidence annotation and comments
- Discussion threads
- Task assignment and tracking
- Investigation progress monitoring
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def add_examiner_to_case(case_id: str, examiner: str, role: str, repository_path: Optional[str] = None) -> bool:
    """Add examiner to case with specific role.
    
    Args:
        case_id: Case identifier
        examiner: Examiner identifier (email or ID)
        role: Role ('lead', 'analyst', 'reviewer', 'observer')
        repository_path: Path to case repository
        
    Returns:
        True if successfully added, False otherwise
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    case_path = Path(repository_path) / case_id
    if not case_path.exists():
        return False
    
    # Load team data
    team_file = case_path / 'team.json'
    if team_file.exists():
        with open(team_file, 'r') as f:
            team_data = json.load(f)
    else:
        team_data = {'members': [], 'history': []}
    
    # Check if examiner already exists
    existing = [m for m in team_data['members'] if m['examiner'] == examiner]
    if existing:
        # Update role
        existing[0]['role'] = role
        existing[0]['updated_at'] = datetime.now().isoformat()
    else:
        # Add new member
        team_data['members'].append({
            'examiner': examiner,
            'role': role,
            'added_at': datetime.now().isoformat(),
            'status': 'active',
        })
    
    # Log the change
    team_data['history'].append({
        'action': 'add_examiner',
        'examiner': examiner,
        'role': role,
        'timestamp': datetime.now().isoformat(),
    })
    
    # Save team data
    with open(team_file, 'w') as f:
        json.dump(team_data, f, indent=2)
    
    return True


def share_case(case_id: str, recipient: str, permissions: Dict, repository_path: Optional[str] = None) -> bool:
    """Share case with another examiner or agency.
    
    Args:
        case_id: Case identifier
        recipient: Recipient identifier (examiner or agency)
        permissions: Permission dict {'read': bool, 'write': bool, 'export': bool, 'share': bool}
        repository_path: Path to case repository
        
    Returns:
        True if successfully shared, False otherwise
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    case_path = Path(repository_path) / case_id
    if not case_path.exists():
        return False
    
    # Load sharing data
    sharing_file = case_path / 'sharing.json'
    if sharing_file.exists():
        with open(sharing_file, 'r') as f:
            sharing_data = json.load(f)
    else:
        sharing_data = {'shares': [], 'audit': []}
    
    # Create share record
    share_id = str(uuid.uuid4())[:8]
    share_record = {
        'share_id': share_id,
        'recipient': recipient,
        'permissions': permissions,
        'shared_at': datetime.now().isoformat(),
        'expires_at': None,  # Optional expiration
        'status': 'active',
    }
    
    # Add or update share
    existing = [s for s in sharing_data['shares'] if s['recipient'] == recipient]
    if existing:
        existing[0].update(share_record)
    else:
        sharing_data['shares'].append(share_record)
    
    # Audit log
    sharing_data['audit'].append({
        'action': 'share',
        'recipient': recipient,
        'permissions': permissions,
        'timestamp': datetime.now().isoformat(),
    })
    
    # Save sharing data
    with open(sharing_file, 'w') as f:
        json.dump(sharing_data, f, indent=2)
    
    return True


def annotate_evidence(evidence_id: str, annotation: str, examiner: str, case_id: str, 
                     repository_path: Optional[str] = None) -> bool:
    """Add annotation to evidence.
    
    Args:
        evidence_id: Evidence item identifier
        annotation: Annotation text
        examiner: Examiner making annotation
        case_id: Case identifier
        repository_path: Path to case repository
        
    Returns:
        True if annotation added successfully
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    case_path = Path(repository_path) / case_id
    if not case_path.exists():
        return False
    
    # Load annotations
    annotations_file = case_path / 'annotations.json'
    if annotations_file.exists():
        with open(annotations_file, 'r') as f:
            annotations_data = json.load(f)
    else:
        annotations_data = {}
    
    # Add annotation
    if evidence_id not in annotations_data:
        annotations_data[evidence_id] = []
    
    annotation_record = {
        'annotation_id': str(uuid.uuid4())[:8],
        'text': annotation,
        'examiner': examiner,
        'timestamp': datetime.now().isoformat(),
        'edited': False,
    }
    
    annotations_data[evidence_id].append(annotation_record)
    
    # Save annotations
    with open(annotations_file, 'w') as f:
        json.dump(annotations_data, f, indent=2)
    
    return True


def create_discussion(case_id: str, topic: str, creator: str, repository_path: Optional[str] = None) -> Dict:
    """Create discussion thread for case.
    
    Args:
        case_id: Case identifier
        topic: Discussion topic/title
        creator: Discussion creator
        repository_path: Path to case repository
        
    Returns:
        Dict with discussion details
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    case_path = Path(repository_path) / case_id
    case_path.mkdir(parents=True, exist_ok=True)
    
    # Load discussions
    discussions_file = case_path / 'discussions.json'
    if discussions_file.exists():
        with open(discussions_file, 'r') as f:
            discussions_data = json.load(f)
    else:
        discussions_data = {'threads': []}
    
    # Create discussion thread
    thread_id = str(uuid.uuid4())[:8]
    thread = {
        'thread_id': thread_id,
        'topic': topic,
        'creator': creator,
        'created_at': datetime.now().isoformat(),
        'status': 'open',
        'messages': [],
        'participants': [creator],
    }
    
    discussions_data['threads'].append(thread)
    
    # Save discussions
    with open(discussions_file, 'w') as f:
        json.dump(discussions_data, f, indent=2)
    
    return thread


def post_to_discussion(case_id: str, thread_id: str, message: str, author: str,
                       repository_path: Optional[str] = None) -> bool:
    """Post message to discussion thread.
    
    Args:
        case_id: Case identifier
        thread_id: Discussion thread ID
        message: Message text
        author: Message author
        repository_path: Path to case repository
        
    Returns:
        True if message posted successfully
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    case_path = Path(repository_path) / case_id
    discussions_file = case_path / 'discussions.json'
    
    if not discussions_file.exists():
        return False
    
    with open(discussions_file, 'r') as f:
        discussions_data = json.load(f)
    
    # Find thread
    thread = None
    for t in discussions_data['threads']:
        if t['thread_id'] == thread_id:
            thread = t
            break
    
    if not thread:
        return False
    
    # Add message
    message_record = {
        'message_id': str(uuid.uuid4())[:8],
        'author': author,
        'text': message,
        'timestamp': datetime.now().isoformat(),
    }
    
    thread['messages'].append(message_record)
    
    # Add author to participants if not already
    if author not in thread['participants']:
        thread['participants'].append(author)
    
    # Save discussions
    with open(discussions_file, 'w') as f:
        json.dump(discussions_data, f, indent=2)
    
    return True


def assign_task(case_id: str, task: Dict, repository_path: Optional[str] = None) -> bool:
    """Assign task to examiner.
    
    Args:
        case_id: Case identifier
        task: Task dict with 'title', 'description', 'assignee', 'priority', 'due_date'
        repository_path: Path to case repository
        
    Returns:
        True if task assigned successfully
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    case_path = Path(repository_path) / case_id
    case_path.mkdir(parents=True, exist_ok=True)
    
    # Load tasks
    tasks_file = case_path / 'tasks.json'
    if tasks_file.exists():
        with open(tasks_file, 'r') as f:
            tasks_data = json.load(f)
    else:
        tasks_data = {'tasks': []}
    
    # Create task
    task_id = str(uuid.uuid4())[:8]
    task_record = {
        'task_id': task_id,
        'title': task.get('title'),
        'description': task.get('description'),
        'assignee': task.get('assignee'),
        'assigner': task.get('assigner', 'system'),
        'priority': task.get('priority', 'medium'),
        'status': 'pending',
        'created_at': datetime.now().isoformat(),
        'due_date': task.get('due_date'),
        'completed_at': None,
    }
    
    tasks_data['tasks'].append(task_record)
    
    # Save tasks
    with open(tasks_file, 'w') as f:
        json.dump(tasks_data, f, indent=2)
    
    return True


def update_task_status(case_id: str, task_id: str, status: str, repository_path: Optional[str] = None) -> bool:
    """Update task status.
    
    Args:
        case_id: Case identifier
        task_id: Task identifier
        status: New status ('pending', 'in_progress', 'completed', 'blocked')
        repository_path: Path to case repository
        
    Returns:
        True if status updated successfully
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    case_path = Path(repository_path) / case_id
    tasks_file = case_path / 'tasks.json'
    
    if not tasks_file.exists():
        return False
    
    with open(tasks_file, 'r') as f:
        tasks_data = json.load(f)
    
    # Find and update task
    for task in tasks_data['tasks']:
        if task['task_id'] == task_id:
            task['status'] = status
            task['updated_at'] = datetime.now().isoformat()
            
            if status == 'completed':
                task['completed_at'] = datetime.now().isoformat()
            
            # Save tasks
            with open(tasks_file, 'w') as f:
                json.dump(tasks_data, f, indent=2)
            
            return True
    
    return False


def update_case_status(case_id: str, status: str, repository_path: Optional[str] = None) -> bool:
    """Update overall case status.
    
    Args:
        case_id: Case identifier
        status: New status ('PENDING', 'ANALYZED', 'UNDER_REVIEW', 'COMPLETED', 'CLOSED')
        repository_path: Path to case repository
        
    Returns:
        True if status updated successfully
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    case_path = Path(repository_path) / case_id
    meta_file = case_path / 'meta.json'
    
    if not meta_file.exists():
        return False
    
    with open(meta_file, 'r') as f:
        meta_data = json.load(f)
    
    # Update status
    old_status = meta_data.get('status')
    meta_data['status'] = status
    meta_data['status_updated_at'] = datetime.now().isoformat()
    
    # Track status history
    if 'status_history' not in meta_data:
        meta_data['status_history'] = []
    
    meta_data['status_history'].append({
        'from': old_status,
        'to': status,
        'timestamp': datetime.now().isoformat(),
    })
    
    # Save metadata
    with open(meta_file, 'w') as f:
        json.dump(meta_data, f, indent=2)
    
    return True


def get_team_dashboard(case_id: str, repository_path: Optional[str] = None) -> str:
    """Generate team collaboration dashboard HTML.
    
    Args:
        case_id: Case identifier
        repository_path: Path to case repository
        
    Returns:
        HTML string with team dashboard
    """
    if not repository_path:
        repository_path = str(Path.home() / '.snagr' / 'cases')
    
    case_path = Path(repository_path) / case_id
    
    # Load team data
    team_file = case_path / 'team.json'
    team_data = {}
    if team_file.exists():
        with open(team_file, 'r') as f:
            team_data = json.load(f)
    
    # Load tasks
    tasks_file = case_path / 'tasks.json'
    tasks_data = {'tasks': []}
    if tasks_file.exists():
        with open(tasks_file, 'r') as f:
            tasks_data = json.load(f)
    
    # Generate HTML
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Team Dashboard - {case_id}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
        .section {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .team-member {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px;
            border-bottom: 1px solid #eee;
        }}
        .role-badge {{
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
            text-transform: uppercase;
        }}
        .role-lead {{ background: #4CAF50; color: white; }}
        .role-analyst {{ background: #2196F3; color: white; }}
        .role-reviewer {{ background: #FF9800; color: white; }}
        .task-item {{
            padding: 15px;
            border-left: 4px solid #667eea;
            margin-bottom: 10px;
            background: #f9f9f9;
        }}
        .task-status {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
        }}
        .status-pending {{ background: #FFC107; color: #000; }}
        .status-in_progress {{ background: #2196F3; color: white; }}
        .status-completed {{ background: #4CAF50; color: white; }}
        .status-blocked {{ background: #F44336; color: white; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>👥 Team Collaboration Dashboard</h1>
        <p>Case: {case_id}</p>
    </div>
    
    <div class="section">
        <h2>Team Members ({len(team_data.get('members', []))})</h2>
"""
    
    for member in team_data.get('members', []):
        role_class = f"role-{member.get('role', 'observer')}"
        html += f"""
        <div class="team-member">
            <div>
                <strong>{member.get('examiner')}</strong><br>
                <small style="color: #666;">Added: {member.get('added_at', 'N/A')[:10]}</small>
            </div>
            <span class="role-badge {role_class}">{member.get('role', 'Observer')}</span>
        </div>
"""
    
    html += """
    </div>
    
    <div class="section">
        <h2>Tasks (""" + str(len(tasks_data['tasks'])) + """)</h2>
"""
    
    for task in tasks_data['tasks']:
        status_class = f"status-{task.get('status', 'pending')}"
        html += f"""
        <div class="task-item">
            <div style="display: flex; justify-content: space-between; margin-bottom: 10px;">
                <strong>{task.get('title')}</strong>
                <span class="task-status {status_class}">{task.get('status', 'Pending').replace('_', ' ').title()}</span>
            </div>
            <p style="color: #666; margin: 5px 0;">{task.get('description', '')}</p>
            <small style="color: #888;">
                Assigned to: {task.get('assignee')} • 
                Priority: {task.get('priority', 'medium').title()} •
                Due: {task.get('due_date', 'No deadline')}
            </small>
        </div>
"""
    
    html += """
    </div>
</body>
</html>
"""
    
    return html
