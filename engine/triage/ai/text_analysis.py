"""AI-Powered Text Analysis System

Provides deep analysis for text extracted during triage:
1. Named Entity Recognition (NER)
2. Sentiment Analysis
3. Topic Modeling
4. Language Identification

Uses ML libraries (spacy, textblob, sklearn, langdetect) if available,
with graceful fallbacks to rule-based analysis.
"""

from __future__ import annotations

import collections
import html
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

# Fallback regex patterns for NER
_PATTERNS = {
    "PERSON": re.compile(r"\b[A-Z][a-z]+\s[A-Z][a-z]+\b"),
    "PHONE": re.compile(r"\b(?:\+?91[\-\s]?)?[6789]\d{9}\b|\b\d{3}[-\.\s]??\d{3}[-\.\s]??\d{4}\b"),
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "URL": re.compile(r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+"),
    "MONEY": re.compile(r"(?:\$|€|£|₹|Rs\.?|INR)\s*\d+(?:,\d{3})*(?:\.\d{2})?|\d+(?:,\d{3})*(?:\.\d{2})?\s*(?:rupees|dollars|euros|rs)", re.I),
    "DATE": re.compile(r"\b\d{1,4}[/-]\d{1,2}[/-]\d{2,4}\b"),
    "TIME": re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?\b"),
    "IP_ADDRESS": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
}

# --- Lazy Loading Helpers ---
_SPACY_MODEL = None
def _get_spacy():
    global _SPACY_MODEL
    if _SPACY_MODEL is not None:
        return _SPACY_MODEL
    try:
        import spacy # type: ignore
        _SPACY_MODEL = spacy.load("en_core_web_sm")
    except Exception:
        _SPACY_MODEL = False
    return _SPACY_MODEL

def _get_textblob():
    try:
        from textblob import TextBlob # type: ignore
        return TextBlob
    except ImportError:
        return None

def _get_sklearn_lda():
    try:
        from sklearn.decomposition import LatentDirichletAllocation # type: ignore
        from sklearn.feature_extraction.text import CountVectorizer # type: ignore
        return LatentDirichletAllocation, CountVectorizer
    except ImportError:
        return None, None

def _get_langdetect():
    try:
        from langdetect import detect_langs # type: ignore
        return detect_langs
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Named Entity Recognition (NER)
# ---------------------------------------------------------------------------

def extract_entities_deep(text: str) -> Dict[str, Any]:
    """Extract entities using spacy if available, else regex."""
    if not text:
        return {"entities": [], "stats": {}, "model_used": "none"}
    
    entities: List[Dict[str, Any]] = []
    nlp = _get_spacy()
    
    if nlp:
        try:
            doc = nlp(text)
            for ent in doc.ents:
                entities.append({
                    "type": ent.label_,
                    "value": ent.text,
                    "start": ent.start_char,
                    "end": ent.end_char,
                    "confidence": 0.90, # Spacy standard pipeline doesn't emit confidence trivially
                })
            model_used = "spacy"
        except Exception:
            nlp = False

    if not nlp:
        # Fallback to regex
        for ent_type, pat in _PATTERNS.items():
            for m in pat.finditer(text):
                entities.append({
                    "type": ent_type,
                    "value": m.group(),
                    "start": m.start(),
                    "end": m.end(),
                    "confidence": 0.70, # Lower confidence for regex
                })
        model_used = "regex_fallback"

    # Deduplicate overlapping entities (prefer longest)
    entities = sorted(entities, key=lambda x: (x["start"], -len(x["value"])))
    deduped = []
    last_end = -1
    for e in entities:
        if e["start"] >= last_end:
            deduped.append(e)
            last_end = e["end"]

    # Generate Stats
    stats = collections.defaultdict(int)
    for e in deduped:
        stats[e["type"]] += 1

    return {
        "entities": deduped,
        "stats": dict(stats),
        "model_used": model_used
    }


# ---------------------------------------------------------------------------
# Sentiment Analysis (Deep)
# ---------------------------------------------------------------------------

def analyze_sentiment_deep(text: str) -> Dict[str, Any]:
    """Deep sentiment analysis with fallback."""
    if not text:
        return {"score": 0.0, "magnitude": 0.0, "sentiment": "neutral", "model_used": "none"}
    
    TextBlob = _get_textblob()
    
    if TextBlob:
        try:
            blob = TextBlob(text)
            score = blob.sentiment.polarity
            mag = blob.sentiment.subjectivity
            
            # Sentence level
            sentences = []
            for s in blob.sentences:
                sentences.append({
                    "text": str(s),
                    "score": s.sentiment.polarity,
                    "magnitude": s.sentiment.subjectivity
                })
                
            sentiment = "positive" if score > 0.1 else ("negative" if score < -0.1 else "neutral")
            return {
                "score": score,
                "magnitude": mag,
                "sentiment": sentiment,
                "sentences": sentences,
                "model_used": "textblob"
            }
        except Exception:
            pass

    # Fallback
    t_low = text.lower()
    pos_words = {"good", "great", "excellent", "happy", "love", "awesome", "yes"}
    neg_words = {"bad", "terrible", "awful", "sad", "hate", "angry", "no", "kill", "fail"}
    
    words = set(re.findall(r"\w+", t_low))
    pos_count = len(words & pos_words)
    neg_count = len(words & neg_words)
    
    total = pos_count + neg_count
    if total == 0:
        return {"score": 0.0, "magnitude": 0.0, "sentiment": "neutral", "model_used": "keyword_fallback"}
        
    score = (pos_count - neg_count) / max(len(words), 1)
    # Scale it up slightly for the sake of range
    score = max(min(score * 5, 1.0), -1.0)
    sentiment = "positive" if score > 0.1 else ("negative" if score < -0.1 else "neutral")
    
    return {
        "score": score,
        "magnitude": total / max(len(words), 1),
        "sentiment": sentiment,
        "model_used": "keyword_fallback"
    }


def analyze_sentiment_timeline(messages: List[Dict]) -> Dict[str, Any]:
    """Track sentiment over time from a list of messages."""
    timeline = []
    for msg in sorted(messages, key=lambda x: x.get("timestamp", "")):
        body = str(msg.get("body", msg.get("text", "")))
        if not body:
            continue
        ts = msg.get("timestamp", "")
        sent = analyze_sentiment_deep(body)
        timeline.append({
            "timestamp": ts,
            "score": sent["score"],
            "sentiment": sent["sentiment"]
        })
        
    return {"timeline": timeline, "count": len(timeline)}


# ---------------------------------------------------------------------------
# Topic Modeling (Deep)
# ---------------------------------------------------------------------------

def extract_topics_deep(texts: List[str], num_topics: int = 5) -> Dict[str, Any]:
    """Extract topics using LDA with fallback to simple term frequency."""
    valid_texts = [t for t in texts if len(t.strip()) > 10]
    if len(valid_texts) < 3:
        return {"topics": [], "model_used": "none", "error": "Insufficient data"}
        
    LDA, Vectorizer = _get_sklearn_lda()
    
    if LDA and Vectorizer:
        try:
            vec = Vectorizer(max_features=1000, stop_words="english")
            X = vec.fit_transform(valid_texts)
            lda = LDA(n_components=min(num_topics, len(valid_texts)), random_state=42)
            doc_topic_matrix = lda.fit_transform(X)
            
            feature_names = vec.get_feature_names_out()
            topics = []
            for topic_idx, topic in enumerate(lda.components_):
                top_idx = topic.argsort()[:-6:-1]
                topics.append({
                    "topic_id": topic_idx,
                    "keywords": [feature_names[i] for i in top_idx],
                    "weight": float(topic.sum())
                })
                
            return {
                "topics": topics,
                "doc_topic_matrix": doc_topic_matrix.tolist(),
                "model_used": "sklearn_lda"
            }
        except Exception as e:
            logging.error(f"LDA failed: {e}")
            
    # Fallback: Simple term frequency
    words = []
    for t in valid_texts:
        words.extend(re.findall(r"\b[a-z]{4,}\b", t.lower()))
    
    stop_words = {"that", "this", "with", "from", "your", "have", "they"}
    words = [w for w in words if w not in stop_words]
    
    counter = collections.Counter(words)
    top_words = [w for w, _ in counter.most_common(num_topics * 3)]
    
    topics = []
    for i in range(min(num_topics, max(1, len(top_words)//3))):
        topics.append({
            "topic_id": i,
            "keywords": top_words[i*3:(i+1)*3],
            "weight": 1.0
        })
        
    return {
        "topics": topics,
        "model_used": "term_frequency_fallback"
    }


# ---------------------------------------------------------------------------
# Language Identification
# ---------------------------------------------------------------------------

def detect_language(text: str) -> Dict[str, Any]:
    """Detect language of text with fallback."""
    if not text or len(text.strip()) < 3:
        return {"language": "unknown", "confidence": 0.0, "model_used": "none"}
        
    detect_langs = _get_langdetect()
    
    if detect_langs:
        try:
            res = detect_langs(text)
            if res:
                return {
                    "language": res[0].lang,
                    "confidence": res[0].prob,
                    "all_detected": [{"lang": r.lang, "prob": r.prob} for r in res],
                    "model_used": "langdetect"
                }
        except Exception:
            pass
            
    # Fallback: character analysis
    ascii_count = sum(1 for c in text if ord(c) < 128)
    if ascii_count / len(text) > 0.8:
        return {"language": "en", "confidence": 0.6, "model_used": "charset_fallback"}
        
    # Check for Cyrillic
    cyrillic = sum(1 for c in text if 0x0400 <= ord(c) <= 0x04FF)
    if cyrillic / len(text) > 0.4:
        return {"language": "ru", "confidence": 0.6, "model_used": "charset_fallback"}
        
    # Check for Devanagari (Hindi)
    dev = sum(1 for c in text if 0x0900 <= ord(c) <= 0x097F)
    if dev / len(text) > 0.4:
        return {"language": "hi", "confidence": 0.6, "model_used": "charset_fallback"}
        
    return {"language": "unknown", "confidence": 0.0, "model_used": "charset_fallback"}
