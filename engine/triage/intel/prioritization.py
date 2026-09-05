"""Intelligent Evidence Prioritization using ML-based scoring.

Implements a multi-factor scoring system to prioritize forensic findings by
combining severity, entity matching, source type, temporal recency, and uniqueness.
Uses LLM provider for reasoning when available, falls back to heuristic scoring.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, List, Optional

from ..models import Serialisable
from .analysis import Finding
from .llm import LLMProvider, get_provider


class EvidencePrioritizer:
    """ML-based evidence prioritization with multi-factor scoring.
    
    Priority scoring factors (weights):
    - Severity (critical/warn/info): 40%
    - Entity match (suspect/victim names): 25%
    - Source type (messages/calls/locations): 15%
    - Temporal recency: 10%
    - Evidence uniqueness: 10%
    """
    
    # Scoring weights
    WEIGHT_SEVERITY = 0.40
    WEIGHT_ENTITY = 0.25
    WEIGHT_SOURCE = 0.15
    WEIGHT_RECENCY = 0.10
    WEIGHT_UNIQUENESS = 0.10
    
    # Severity scores
    SEVERITY_SCORES = {
        "critical": 100,
        "high": 85,
        "warn": 70,
        "warning": 70,
        "medium": 50,
        "info": 30,
        "low": 20,
    }
    
    # Source type scores
    SOURCE_SCORES = {
        "message": 100,
        "recovered": 95,
        "call": 90,
        "location": 85,
        "media": 75,
        "browser": 70,
        "app": 60,
        "system": 40,
    }
    
    def __init__(self, provider: Optional[LLMProvider] = None):
        """Initialize prioritizer with optional LLM provider."""
        self.provider = provider or get_provider()
        self.feedback_history: dict[str, dict] = {}
    
    def score_evidence(self, finding: Finding, case_context: str) -> dict:
        """Score a single finding and return priority score with reasoning.
        
        Args:
            finding: Finding object to score
            case_context: Case profile text (entities, keywords, case type)
            
        Returns:
            dict with score (0-100), priority level, reasoning, and factor breakdown
        """
        factors = {}
        
        # Factor 1: Severity (40% weight)
        severity = finding.severity.lower() if finding.severity else "info"
        severity_score = self.SEVERITY_SCORES.get(severity, 30)
        factors["severity"] = int(severity_score * self.WEIGHT_SEVERITY)
        
        # Factor 2: Entity match (25% weight)
        entity_score = self._score_entity_match(finding, case_context)
        factors["entity_match"] = int(entity_score * self.WEIGHT_ENTITY)
        
        # Factor 3: Source type (15% weight)
        source_score = self._score_source_type(finding)
        factors["source"] = int(source_score * self.WEIGHT_SOURCE)
        
        # Factor 4: Temporal recency (10% weight)
        recency_score = self._score_recency(finding)
        factors["recency"] = int(recency_score * self.WEIGHT_RECENCY)
        
        # Factor 5: Evidence uniqueness (10% weight)
        uniqueness_score = self._score_uniqueness(finding)
        factors["uniqueness"] = int(uniqueness_score * self.WEIGHT_UNIQUENESS)
        
        # Calculate total score
        total_score = sum(factors.values())
        
        # Determine priority level
        if total_score >= 80:
            priority = "CRITICAL"
        elif total_score >= 60:
            priority = "HIGH"
        elif total_score >= 40:
            priority = "MEDIUM"
        else:
            priority = "LOW"
        
        # Generate reasoning
        reasoning = self._generate_reasoning(finding, factors, case_context)
        
        return {
            "finding_id": finding.id,
            "score": total_score,
            "priority": priority,
            "reasoning": reasoning,
            "factors": factors,
            "severity": severity,
            "entity_matches": finding.entities_matched,
            "keyword_matches": finding.keywords_matched,
        }
    
    def rank_evidence(self, findings: List[Finding], case_context: str) -> List[dict]:
        """Rank all findings by priority score.
        
        Args:
            findings: List of Finding objects
            case_context: Case profile text
            
        Returns:
            List of scored findings sorted by priority (highest first)
        """
        scored = []
        for finding in findings:
            scored.append(self.score_evidence(finding, case_context))
        
        # Sort by score descending
        scored.sort(key=lambda x: x["score"], reverse=True)
        
        return scored
    
    def learn_from_feedback(self, case_id: str, examiner_feedback: dict) -> None:
        """Update scoring model based on examiner feedback.
        
        Args:
            case_id: Case identifier
            examiner_feedback: dict with finding_id -> {"useful": bool, "priority": str}
        """
        # Store feedback for future model training
        self.feedback_history[case_id] = examiner_feedback
        
        # Placeholder for future ML model training
        # In production, this would:
        # 1. Extract features from misclassified findings
        # 2. Adjust weights based on feedback
        # 3. Retrain scoring model
        # 4. Persist updated model
    
    def _score_entity_match(self, finding: Finding, case_context: str) -> float:
        """Score based on entity matching (suspect/victim names).
        
        Returns: 0-100 score
        """
        if not finding.entities_matched:
            return 0
        
        # Extract entities from case context
        case_entities = self._extract_entities_from_context(case_context)
        
        # Check for matches
        matches = 0
        for entity in finding.entities_matched:
            entity_lower = entity.lower()
            for case_entity in case_entities:
                if case_entity in entity_lower or entity_lower in case_entity:
                    matches += 1
                    break
        
        if not case_entities:
            # If no case context, any entity match is valuable
            return min(len(finding.entities_matched) * 30, 100)
        
        # Score based on percentage of matched entities
        match_ratio = matches / len(case_entities) if case_entities else 0
        return min(match_ratio * 100, 100)
    
    def _score_source_type(self, finding: Finding) -> float:
        """Score based on source/category type.
        
        Returns: 0-100 score
        """
        category = finding.category.lower() if finding.category else "other"
        return self.SOURCE_SCORES.get(category, 50)
    
    def _score_recency(self, finding: Finding) -> float:
        """Score based on temporal recency.
        
        Returns: 0-100 score
        """
        if not finding.timestamp:
            return 50  # Neutral score for unknown timestamps
        
        try:
            # Parse ISO-8601 timestamp
            ts = datetime.fromisoformat(finding.timestamp.replace("Z", "+00:00"))
            now = datetime.now(ts.tzinfo)
            
            # Calculate days ago
            days_ago = (now - ts).days
            
            # Score: More recent = higher score
            if days_ago < 1:
                return 100  # Today
            elif days_ago < 7:
                return 90   # This week
            elif days_ago < 30:
                return 75   # This month
            elif days_ago < 90:
                return 60   # Last 3 months
            elif days_ago < 180:
                return 40   # Last 6 months
            else:
                return 20   # Older than 6 months
        except Exception:
            return 50  # Neutral for unparseable timestamps
    
    def _score_uniqueness(self, finding: Finding) -> float:
        """Score based on evidence uniqueness.
        
        Returns: 0-100 score
        """
        # Factors for uniqueness:
        # - Recovered/carved evidence is unique
        # - Multiple matched keywords/entities increase value
        # - Source file diversity
        
        score = 50  # Base score
        
        # Recovered evidence is more valuable
        if finding.confidence in ("recovered", "carved"):
            score += 30
        
        # Multiple entity matches increase uniqueness
        if len(finding.entities_matched) > 2:
            score += 15
        elif len(finding.entities_matched) > 0:
            score += 5
        
        # Multiple keyword matches
        if len(finding.keywords_matched) > 3:
            score += 10
        elif len(finding.keywords_matched) > 0:
            score += 5
        
        return min(score, 100)
    
    def _generate_reasoning(
        self, finding: Finding, factors: dict, case_context: str
    ) -> str:
        """Generate human-readable reasoning for the priority score.
        
        Uses LLM if available, falls back to template-based reasoning.
        """
        # Try LLM reasoning first
        if self.provider and self.provider.available:
            reasoning = self._llm_reasoning(finding, factors, case_context)
            if reasoning:
                return reasoning
        
        # Fall back to template-based reasoning
        return self._template_reasoning(finding, factors)
    
    def _llm_reasoning(
        self, finding: Finding, factors: dict, case_context: str
    ) -> Optional[str]:
        """Generate reasoning using LLM provider."""
        try:
            system = """You are a forensic analyst explaining evidence prioritization.
Provide a brief (1-2 sentence) explanation of why this evidence is important."""
            
            prompt = f"""Evidence:
- Type: {finding.category}
- Severity: {finding.severity}
- Snippet: {finding.snippet[:200]}
- Entities: {', '.join(finding.entities_matched) if finding.entities_matched else 'none'}
- Keywords: {', '.join(finding.keywords_matched) if finding.keywords_matched else 'none'}

Scoring factors:
- Severity: {factors.get('severity', 0)}
- Entity match: {factors.get('entity_match', 0)}
- Source: {factors.get('source', 0)}
- Recency: {factors.get('recency', 0)}
- Uniqueness: {factors.get('uniqueness', 0)}

Case context: {case_context[:300]}

Explain why this evidence is prioritized at this level."""
            
            reasoning = self.provider.generate(system, prompt)
            if reasoning and len(reasoning) > 20:
                return reasoning.strip()
        except Exception:
            pass
        
        return None
    
    def _template_reasoning(self, finding: Finding, factors: dict) -> str:
        """Generate template-based reasoning."""
        parts = []
        
        # Severity
        if factors["severity"] >= 35:
            parts.append(f"Critical/high severity ({finding.severity})")
        
        # Entity matches
        if factors["entity_match"] > 20 and finding.entities_matched:
            entities_str = ", ".join(finding.entities_matched[:3])
            parts.append(f"matches key entities: {entities_str}")
        
        # Source type
        if factors["source"] >= 13:
            parts.append(f"valuable source type ({finding.category})")
        
        # Recency
        if factors["recency"] >= 9:
            parts.append("recent activity")
        elif factors["recency"] <= 3:
            parts.append("historical data")
        
        # Uniqueness
        if factors["uniqueness"] >= 8:
            parts.append("unique/recovered evidence")
        
        if not parts:
            return f"Standard priority based on {finding.category} evidence"
        
        return ". ".join(parts).capitalize() + "."
    
    def _extract_entities_from_context(self, context: str) -> List[str]:
        """Extract entity names from case context."""
        entities = []
        
        # Look for common entity patterns
        # Names after "suspect:", "victim:", "subject:", etc.
        patterns = [
            r"suspect[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            r"victim[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            r"subject[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            r"person[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, context, re.IGNORECASE)
            entities.extend(matches)
        
        # Also extract any capitalized words (potential names)
        words = re.findall(r'\b[A-Z][a-z]+\b', context)
        entities.extend([w for w in words if len(w) > 2])
        
        # Deduplicate and lowercase for matching
        return list(set(e.lower() for e in entities if e))
