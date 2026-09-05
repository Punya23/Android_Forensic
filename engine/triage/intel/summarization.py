"""AI-powered conversation summarization with entity extraction.

Generates multi-level summaries of conversations (one-line, paragraph, full, timeline)
and extracts structured entities (names, places, dates, amounts, contacts).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import Message
from .llm import LLMProvider, get_provider


class ConversationSummarizer:
    """AI-powered conversation summarization with entity extraction."""
    
    def __init__(self, provider: Optional[LLMProvider] = None):
        """Initialize summarizer with optional LLM provider."""
        self.provider = provider or get_provider()
    
    def summarize_conversation(
        self, messages: List[Message], chat_id: str
    ) -> dict:
        """Generate full summary of a conversation.
        
        Args:
            messages: List of Message objects
            chat_id: Unique conversation identifier
            
        Returns:
            dict with one_line, summary, key_events, entities, sentiment
        """
        if not messages:
            return self._empty_summary(chat_id)
        
        # Sort messages by timestamp
        sorted_msgs = sorted(messages, key=lambda m: m.timestamp or "")
        
        # Extract participants
        participants = self._extract_participants(sorted_msgs)
        
        # Generate summaries at different levels
        one_line = self._generate_one_line(sorted_msgs)
        paragraph = self._generate_paragraph(sorted_msgs)
        full_summary = self._generate_full_summary(sorted_msgs)
        
        # Build timeline of key events
        key_events = self._extract_key_events(sorted_msgs)
        
        # Extract entities
        entities = self.extract_entities(sorted_msgs)
        
        # Analyze sentiment
        sentiment = self._analyze_sentiment(sorted_msgs)
        
        return {
            "chat_id": chat_id,
            "participants": participants,
            "message_count": len(sorted_msgs),
            "date_range": {
                "start": sorted_msgs[0].timestamp if sorted_msgs else None,
                "end": sorted_msgs[-1].timestamp if sorted_msgs else None,
            },
            "one_line": one_line,
            "summary": paragraph,
            "full_summary": full_summary,
            "key_events": key_events,
            "entities": entities,
            "sentiment": sentiment,
        }
    
    def summarize_all_conversations(self, case_dir: Path) -> dict:
        """Summarize all conversations in a case.
        
        Args:
            case_dir: Path to case directory containing messages
            
        Returns:
            dict mapping chat_id -> summary
        """
        # Group messages by chat_id
        conversations = self._load_conversations(case_dir)
        
        summaries = {}
        for chat_id, messages in conversations.items():
            summaries[chat_id] = self.summarize_conversation(messages, chat_id)
        
        return summaries
    
    def extract_entities(self, messages: List[Message]) -> dict:
        """Extract structured entities from messages.
        
        Returns:
            dict with people, places, dates, amounts, phone_numbers, upi_ids
        """
        entities = {
            "people": [],
            "places": [],
            "dates": [],
            "amounts": [],
            "phone_numbers": [],
            "upi_ids": [],
        }
        
        # Combine all message text
        full_text = " ".join(m.text for m in messages if m.text)
        
        # Extract people (names)
        entities["people"] = self._extract_names(messages)
        
        # Extract places
        entities["places"] = self._extract_places(full_text)
        
        # Extract dates and times
        entities["dates"] = self._extract_dates(full_text)
        
        # Extract money amounts
        entities["amounts"] = self._extract_amounts(full_text)
        
        # Extract phone numbers
        entities["phone_numbers"] = self._extract_phone_numbers(full_text)
        
        # Extract UPI IDs
        entities["upi_ids"] = self._extract_upi_ids(full_text)
        
        return entities
    
    def _generate_one_line(self, messages: List[Message]) -> str:
        """Generate one-line summary (most important message/topic)."""
        if not messages:
            return "Empty conversation"
        
        # Try LLM first
        if self.provider and self.provider.available:
            llm_summary = self._llm_one_line(messages)
            if llm_summary:
                return llm_summary
        
        # Fall back to heuristic: longest message or last message
        if len(messages) == 1:
            return messages[0].text[:100] if messages[0].text else "Single message"
        
        # Find message with most keywords
        scored = []
        for msg in messages:
            if not msg.text:
                continue
            score = len(msg.text.split()) + len(re.findall(r'[!?]', msg.text)) * 2
            scored.append((score, msg.text))
        
        if scored:
            scored.sort(reverse=True)
            return scored[0][1][:150]
        
        return f"Conversation with {len(messages)} messages"
    
    def _generate_paragraph(self, messages: List[Message]) -> str:
        """Generate 5-8 sentence summary."""
        if not messages:
            return "No messages in conversation."
        
        # Try LLM first
        if self.provider and self.provider.available:
            llm_summary = self._llm_paragraph(messages)
            if llm_summary:
                return llm_summary
        
        # Fall back to template-based summary
        participants = self._extract_participants(messages)
        msg_count = len(messages)
        
        # Extract key topics (simple keyword frequency)
        topics = self._extract_topics(messages)
        
        summary_parts = []
        if len(participants) == 2:
            summary_parts.append(
                f"{participants[0]} and {participants[1]} exchanged {msg_count} messages."
            )
        else:
            summary_parts.append(
                f"Conversation between {', '.join(participants[:3])} with {msg_count} messages."
            )
        
        if topics:
            summary_parts.append(f"Main topics: {', '.join(topics[:3])}.")
        
        # Add first and last message context
        if messages[0].text:
            summary_parts.append(f"Started with: '{messages[0].text[:50]}...'")
        if len(messages) > 1 and messages[-1].text:
            summary_parts.append(f"Ended with: '{messages[-1].text[:50]}...'")
        
        return " ".join(summary_parts)
    
    def _generate_full_summary(self, messages: List[Message]) -> str:
        """Generate comprehensive summary."""
        if self.provider and self.provider.available:
            llm_summary = self._llm_full_summary(messages)
            if llm_summary:
                return llm_summary
        
        # Fall back to extended paragraph
        return self._generate_paragraph(messages)
    
    def _extract_key_events(self, messages: List[Message]) -> List[dict]:
        """Extract timeline of key events."""
        events = []
        
        # Patterns for key events
        event_patterns = [
            (r'\bmeet(?:ing)?\b', "meeting_mention"),
            (r'\bcall\b', "call_mention"),
            (r'\btonight\b|\btoday\b|\btomorrow\b', "time_reference"),
            (r'\b(?:bring|send|give)\b', "exchange_mention"),
            (r'\b(?:money|cash|payment|₹|\$)\b', "financial_mention"),
            (r'\b(?:urgent|important|asap)\b', "urgency"),
        ]
        
        for msg in messages:
            if not msg.text:
                continue
            
            # Check for event patterns
            for pattern, event_type in event_patterns:
                if re.search(pattern, msg.text, re.IGNORECASE):
                    events.append({
                        "time": msg.timestamp or "",
                        "sender": msg.sender or "unknown",
                        "event": msg.text[:100],
                        "type": event_type,
                    })
                    break  # One event per message
        
        # Limit to top 10 events
        return events[:10]
    
    def _analyze_sentiment(self, messages: List[Message]) -> dict:
        """Analyze conversation sentiment."""
        if not messages:
            return {"overall": "neutral", "timeline": []}
        
        # Simple sentiment analysis based on keywords
        positive_words = {"good", "great", "thanks", "ok", "yes", "sure", "👍", "😊"}
        negative_words = {"no", "not", "can't", "won't", "bad", "sorry", "😞", "😠"}
        
        timeline = []
        positive_count = 0
        negative_count = 0
        
        for msg in messages:
            if not msg.text:
                continue
            
            text_lower = msg.text.lower()
            pos = sum(1 for w in positive_words if w in text_lower)
            neg = sum(1 for w in negative_words if w in text_lower)
            
            if pos > neg:
                sentiment = "positive"
                positive_count += 1
            elif neg > pos:
                sentiment = "negative"
                negative_count += 1
            else:
                sentiment = "neutral"
            
            timeline.append({
                "time": msg.timestamp or "",
                "sentiment": sentiment,
            })
        
        # Overall sentiment
        if positive_count > negative_count * 1.5:
            overall = "positive"
        elif negative_count > positive_count * 1.5:
            overall = "negative"
        else:
            overall = "neutral"
        
        return {
            "overall": overall,
            "timeline": timeline[:20],  # Limit timeline
            "positive_ratio": positive_count / len(messages) if messages else 0,
            "negative_ratio": negative_count / len(messages) if messages else 0,
        }
    
    def _extract_participants(self, messages: List[Message]) -> List[str]:
        """Extract unique participant names."""
        participants = set()
        for msg in messages:
            if msg.sender:
                participants.add(msg.sender)
        return sorted(list(participants))
    
    def _extract_names(self, messages: List[Message]) -> List[str]:
        """Extract person names from messages."""
        names = set()
        
        # Get names from senders
        for msg in messages:
            if msg.sender and len(msg.sender) > 2:
                # Clean phone numbers, keep only names
                if not re.match(r'^\+?\d+', msg.sender):
                    names.add(msg.sender)
        
        # Extract capitalized names from text
        for msg in messages:
            if msg.text:
                # Find capitalized words (potential names)
                potential_names = re.findall(r'\b[A-Z][a-z]{2,}\b', msg.text)
                names.update(potential_names)
        
        return sorted(list(names))[:20]  # Top 20 names
    
    def _extract_places(self, text: str) -> List[str]:
        """Extract location mentions."""
        places = set()
        
        # Common location patterns
        location_keywords = [
            "airport", "station", "mall", "office", "home", "hotel", "restaurant",
            "cafe", "park", "beach", "temple", "church", "mosque", "hospital",
            "school", "college", "university", "market", "shop", "store", "docks"
        ]
        
        text_lower = text.lower()
        for keyword in location_keywords:
            if keyword in text_lower:
                # Extract context around location
                pattern = rf'\b\w+\s+{keyword}\b'
                matches = re.findall(pattern, text_lower, re.IGNORECASE)
                places.update(matches)
        
        # Extract "at [location]" patterns
        at_matches = re.findall(r'\bat\s+([A-Z][a-zA-Z\s]{2,20})', text)
        places.update(at_matches)
        
        return sorted(list(places))[:10]
    
    def _extract_dates(self, text: str) -> List[str]:
        """Extract date and time mentions."""
        dates = []
        
        # Patterns for dates
        date_patterns = [
            r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b',  # DD/MM/YYYY
            r'\b(?:today|tomorrow|tonight|yesterday)\b',
            r'\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
            r'\b\d{1,2}:\d{2}\s*(?:am|pm)?\b',  # Time
        ]
        
        for pattern in date_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            dates.extend(matches)
        
        return sorted(list(set(dates)))[:10]
    
    def _extract_amounts(self, text: str) -> List[str]:
        """Extract money amounts."""
        amounts = []
        
        # Patterns for money
        money_patterns = [
            r'₹\s*\d+(?:,\d{3})*(?:\.\d{2})?',  # Indian Rupee
            r'\$\s*\d+(?:,\d{3})*(?:\.\d{2})?',  # Dollar
            r'\b\d+(?:,\d{3})*\s*(?:rupees|rs|inr)\b',
            r'\b\d+k\b',  # 5k notation
            r'\b\d+\s*lakh\b',  # Indian lakh
        ]
        
        for pattern in money_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            amounts.extend(matches)
        
        return sorted(list(set(amounts)))[:10]
    
    def _extract_phone_numbers(self, text: str) -> List[str]:
        """Extract phone numbers."""
        # Indian phone number patterns
        patterns = [
            r'\+91[- ]?\d{10}',  # +91 prefix
            r'\b[6-9]\d{9}\b',   # 10-digit mobile
            r'\b\d{3}[- ]?\d{3}[- ]?\d{4}\b',  # General format
        ]
        
        phones = []
        for pattern in patterns:
            matches = re.findall(pattern, text)
            phones.extend(matches)
        
        return sorted(list(set(phones)))[:5]
    
    def _extract_upi_ids(self, text: str) -> List[str]:
        """Extract UPI IDs."""
        # UPI ID pattern: user@bank
        pattern = r'\b[a-zA-Z0-9._-]+@[a-zA-Z]+\b'
        upi_ids = re.findall(pattern, text)
        
        # Filter to common UPI providers
        upi_providers = ['paytm', 'phonepe', 'gpay', 'googlepay', 'ybl', 'okaxis', 'okhdfcbank']
        filtered = [uid for uid in upi_ids if any(p in uid.lower() for p in upi_providers)]
        
        return sorted(list(set(filtered)))[:5]
    
    def _extract_topics(self, messages: List[Message]) -> List[str]:
        """Extract main topics from conversation."""
        # Simple keyword frequency
        word_freq = defaultdict(int)
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'is', 'are', 'was', 'were', 'be', 'been', 'being'}
        
        for msg in messages:
            if not msg.text:
                continue
            words = re.findall(r'\b\w{4,}\b', msg.text.lower())
            for word in words:
                if word not in stop_words:
                    word_freq[word] += 1
        
        # Sort by frequency
        topics = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [word for word, _ in topics[:5]]
    
    def _llm_one_line(self, messages: List[Message]) -> Optional[str]:
        """Generate one-line summary using LLM."""
        try:
            # Sample messages for LLM (max 10)
            sample = messages[:10] if len(messages) > 10 else messages
            text = "\n".join(f"{m.sender}: {m.text}" for m in sample if m.text)
            
            system = "Summarize this conversation in one sentence (max 150 chars)."
            reasoning = self.provider.generate(system, text[:500])
            
            if reasoning and len(reasoning) < 200:
                return reasoning.strip()
        except Exception:
            pass
        return None
    
    def _llm_paragraph(self, messages: List[Message]) -> Optional[str]:
        """Generate paragraph summary using LLM."""
        try:
            sample = messages[:20] if len(messages) > 20 else messages
            text = "\n".join(f"{m.sender}: {m.text}" for m in sample if m.text)
            
            system = "Summarize this conversation in 5-8 sentences."
            reasoning = self.provider.generate(system, text[:1000])
            
            if reasoning:
                return reasoning.strip()
        except Exception:
            pass
        return None
    
    def _llm_full_summary(self, messages: List[Message]) -> Optional[str]:
        """Generate full summary using LLM."""
        try:
            sample = messages[:50] if len(messages) > 50 else messages
            text = "\n".join(f"{m.sender}: {m.text}" for m in sample if m.text)
            
            system = "Provide a comprehensive summary of this conversation, including key topics, participants, and important events."
            reasoning = self.provider.generate(system, text[:2000])
            
            if reasoning:
                return reasoning.strip()
        except Exception:
            pass
        return None
    
    def _load_conversations(self, case_dir: Path) -> Dict[str, List[Message]]:
        """Load all conversations from case directory."""
        # Placeholder - in production, this would load from parsed message files
        # For now, return empty dict
        return {}
    
    def _empty_summary(self, chat_id: str) -> dict:
        """Return empty summary structure."""
        return {
            "chat_id": chat_id,
            "participants": [],
            "message_count": 0,
            "date_range": {"start": None, "end": None},
            "one_line": "Empty conversation",
            "summary": "No messages in this conversation.",
            "full_summary": "No messages in this conversation.",
            "key_events": [],
            "entities": {
                "people": [],
                "places": [],
                "dates": [],
                "amounts": [],
                "phone_numbers": [],
                "upi_ids": [],
            },
            "sentiment": {"overall": "neutral", "timeline": []},
        }
