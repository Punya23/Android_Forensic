import html
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor

try:
    from textblob import TextBlob
    HAS_TB = True
except ImportError:
    HAS_TB = False
    logging.warning("textblob not installed. Sentiment analysis will be stubbed.")

try:
    from ..metrics import track_stage_time
except ImportError:
    import contextlib
    @contextlib.contextmanager
    def track_stage_time(stage: str):
        yield

def analyze_sentiment(text: str) -> Dict[str, Any]:
    """Analyze sentiment of text for behavioral understanding."""
    if not text:
        return {"sentiment": "neutral", "score": 0.0, "magnitude": 0.0}
        
    if not HAS_TB:
        # Extreme naive fallback
        t_low = text.lower()
        if "hate" in t_low or "kill" in t_low or "angry" in t_low:
            return {"sentiment": "negative", "score": -0.8, "magnitude": 0.8}
        if "love" in t_low or "happy" in t_low or "great" in t_low:
            return {"sentiment": "positive", "score": 0.8, "magnitude": 0.8}
        return {"sentiment": "neutral", "score": 0.0, "magnitude": 0.0}

    blob = TextBlob(text)
    polarity = blob.sentiment.polarity
    subjectivity = blob.sentiment.subjectivity
    
    if polarity > 0.1:
        sentiment = "positive"
    elif polarity < -0.1:
        sentiment = "negative"
    else:
        sentiment = "neutral"
        
    return {
        "sentiment": sentiment,
        "score": polarity,
        "magnitude": subjectivity # TextBlob uses subjectivity, using it as proxy for magnitude
    }

def analyze_sentiment_batch(texts: List[str]) -> List[Dict[str, Any]]:
    """Batch sentiment analysis using parallel processing."""
    with track_stage_time("ai_sentiment"):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(analyze_sentiment, texts))
    return results

def analyze_sentiment_over_time(messages: List[Dict]) -> List[Dict[str, Any]]:
    """Analyze sentiment over time."""
    timeline = []
    
    # Sort messages by time
    sorted_msgs = sorted(
        [m for m in messages if m.get("timestamp")], 
        key=lambda x: x["timestamp"]
    )
    
    for msg in sorted_msgs:
        body = str(msg.get("body", ""))
        sent = analyze_sentiment(body)
        
        timeline.append({
            "timestamp": msg["timestamp"],
            "score": sent["score"],
            "sentiment": sent["sentiment"],
            "message_snippet": body[:50]
        })
        
    return timeline

def detect_sentiment_spikes(sentiment_data: List[Dict]) -> List[Dict]:
    """Detect sentiment spikes."""
    spikes = []
    
    for idx, item in enumerate(sentiment_data):
        score = item.get("score", 0.0)
        # Defining a spike as highly negative or highly positive
        if score < -0.7:
            spikes.append({
                "timestamp": item.get("timestamp"),
                "type": "negative_spike",
                "score": score,
                "snippet": item.get("message_snippet", "")
            })
        elif score > 0.7:
            spikes.append({
                "timestamp": item.get("timestamp"),
                "type": "positive_spike",
                "score": score,
                "snippet": item.get("message_snippet", "")
            })
            
    return spikes

def generate_sentiment_report(sentiments: List[Dict]) -> str:
    """Generate HTML sentiment report."""
    html_out = ["<div class='ai-sentiment-report'>", "<h2>Sentiment Analysis</h2>"]
    
    if not sentiments:
        html_out.append("<p>No sentiment data available.</p></div>")
        return "\n".join(html_out)
        
    spikes = detect_sentiment_spikes(sentiments)
    
    html_out.append("<h3>Notable Sentiment Spikes</h3>")
    if spikes:
        html_out.append("<ul>")
        for spike in spikes:
            color = "red" if spike["type"] == "negative_spike" else "green"
            html_out.append(f"<li><span style='color: {color}'>[{spike['type'].upper()}]</span> ")
            html_out.append(f"{spike['timestamp']} (Score: {spike['score']:.2f}): {html.escape(spike['snippet'])}...</li>")
        html_out.append("</ul>")
    else:
        html_out.append("<p>No extreme sentiment spikes detected.</p>")
        
    html_out.append("</div>")
    return "\n".join(html_out)
