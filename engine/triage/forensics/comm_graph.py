import json
import collections
from typing import Dict, List
import datetime

# Optional networkx for advanced metrics
try:
    import networkx as nx

    HAS_NX = True
except ImportError:
    HAS_NX = False


def build_communication_graph(calls: List[Dict], sms: List[Dict]) -> Dict:
    """Build communication graph from call and SMS history."""
    graph = {
        "nodes": {},
        "edges": collections.defaultdict(
            lambda: {"count": 0, "types": set(), "timestamps": []}
        ),
    }

    # Process Calls
    for call in calls:
        num = call.get("number")
        if not num:
            continue

        # Add node
        if num not in graph["nodes"]:
            graph["nodes"][num] = {
                "id": num,
                "label": call.get("name", num),
                "degree": 0,
            }

        # Add edge (Assume owner is "ME")
        edge_key = tuple(sorted(["ME", num]))
        graph["edges"][edge_key]["count"] += 1
        graph["edges"][edge_key]["types"].add("call")
        if call.get("timestamp"):
            graph["edges"][edge_key]["timestamps"].append(call["timestamp"])

    # Process SMS
    for msg in sms:
        num = msg.get("sender") or msg.get("chat_id")
        if not num or num == "me":
            continue

        if num not in graph["nodes"]:
            graph["nodes"][num] = {"id": num, "label": num, "degree": 0}

        edge_key = tuple(sorted(["ME", num]))
        graph["edges"][edge_key]["count"] += 1
        graph["edges"][edge_key]["types"].add("sms")
        if msg.get("timestamp"):
            graph["edges"][edge_key]["timestamps"].append(msg["timestamp"])

    # Node "ME"
    graph["nodes"]["ME"] = {"id": "ME", "label": "Device Owner", "degree": 0}

    # Format output for D3
    output = {
        "nodes": list(graph["nodes"].values()),
        "edges": [
            {
                "source": k[0],
                "target": k[1],
                "weight": v["count"],
                "types": list(v["types"]),
                "timestamps": v["timestamps"],
            }
            for k, v in graph["edges"].items()
        ],
    }

    # Calculate degree
    for edge in output["edges"]:
        graph["nodes"][edge["source"]]["degree"] += edge["weight"]
        graph["nodes"][edge["target"]]["degree"] += edge["weight"]

    return output


def identify_central_nodes(graph: Dict) -> List[Dict]:
    """Identify central communication nodes."""
    nodes = sorted(graph["nodes"], key=lambda x: x.get("degree", 0), reverse=True)
    return nodes[:10]  # Top 10


def detect_anomalies(graph: Dict) -> List[Dict]:
    """Detect anomalies in communication graph."""
    anomalies = []

    for edge in graph["edges"]:
        # Anomaly 1: Very high frequency
        if edge["weight"] > 100:
            anomalies.append(
                {
                    "source": edge["source"],
                    "target": edge["target"],
                    "reason": f"High communication frequency ({edge['weight']} events)",
                    "severity": "MEDIUM",
                }
            )

        # Anomaly 2: Odd hours (e.g., between 1 AM and 5 AM)
        odd_hours = 0
        for ts in edge["timestamps"]:
            try:
                dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if 1 <= dt.hour <= 5:
                    odd_hours += 1
            except Exception:
                pass

        if odd_hours > 5:
            anomalies.append(
                {
                    "source": edge["source"],
                    "target": edge["target"],
                    "reason": f"Frequent communication at odd hours ({odd_hours} events between 1AM-5AM)",
                    "severity": "HIGH",
                }
            )

    return anomalies


def detect_communication_clusters(graph: Dict) -> List[Dict]:
    """Detect clusters/communities in graph Using Louvain algorithm if nx is available."""
    if not HAS_NX:
        return [{"cluster_id": 0, "nodes": [n["id"] for n in graph["nodes"]]}]

    G = nx.Graph()
    for edge in graph["edges"]:
        G.add_edge(edge["source"], edge["target"], weight=edge["weight"])

    try:
        from networkx.algorithms.community import louvain_communities

        communities = louvain_communities(G, weight="weight")
        return [{"cluster_id": i, "nodes": list(c)} for i, c in enumerate(communities)]
    except Exception:
        # Fallback if algo fails or isn't in this nx version
        return []


def generate_graph_report(graph: Dict) -> str:
    """Generate HTML graph report."""
    anomalies = detect_anomalies(graph)
    central = identify_central_nodes(graph)

    html_out = ["<div class='graph-report'>", "<h2>Communication Graph Analysis</h2>"]

    html_out.append("<h3>Central Nodes (Top Communicators)</h3><ul>")
    for node in central:
        if node["id"] != "ME":
            html_out.append(f"<li>{node['label']} (Degree: {node['degree']})</li>")
    html_out.append("</ul>")

    html_out.append("<h3>Anomalies</h3>")
    if anomalies:
        html_out.append("<ul>")
        for a in anomalies:
            html_out.append(
                f"<li><strong>{a['severity']}</strong>: {a['source']} <-> {a['target']} - {a['reason']}</li>"
            )
        html_out.append("</ul>")
    else:
        html_out.append("<p>No anomalies detected.</p>")

    # Placeholder for D3.js visualization injection
    html_out.append("<div id='d3-graph-container'>")
    html_out.append("<script>")
    html_out.append(f"const graphData = {json.dumps(graph)};")
    html_out.append("// D3.js rendering logic goes here")
    html_out.append("</script>")
    html_out.append("</div>")

    html_out.append("</div>")
    return "\n".join(html_out)
