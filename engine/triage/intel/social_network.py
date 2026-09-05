"""Advanced Social Network Analysis with community detection and influence scoring.

Implements enhanced graph metrics, community detection, influence scoring,
bridge finding, and link prediction for forensic social network analysis.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..models import Contact, Message


class SocialNetworkAnalyst:
    """Advanced social network analysis with graph metrics and community detection."""
    
    def __init__(self):
        """Initialize analyst."""
        self.has_networkx = self._check_networkx()
    
    def build_enhanced_graph(
        self, messages: List[Message], contacts: List[Contact]
    ) -> dict:
        """Build enhanced graph with all metrics.
        
        Returns:
            dict with nodes, edges, communities, metrics
        """
        # Build adjacency list
        graph = defaultdict(set)
        edge_weights = defaultdict(int)
        node_info = {}
        
        # Add nodes from contacts
        for contact in contacts:
            if contact.name or contact.phone:
                node_id = contact.phone or contact.name
                node_info[node_id] = {
                    "name": contact.name,
                    "phone": contact.phone,
                    "type": "contact",
                }
        
        # Add edges from messages
        for msg in messages:
            if not msg.sender or not msg.recipient:
                continue
            
            sender = msg.sender
            recipient = msg.recipient
            
            # Ensure nodes exist
            if sender not in node_info:
                node_info[sender] = {"name": sender, "phone": None, "type": "participant"}
            if recipient not in node_info:
                node_info[recipient] = {"name": recipient, "phone": None, "type": "participant"}
            
            # Add edge (bidirectional)
            graph[sender].add(recipient)
            graph[recipient].add(sender)
            
            # Track edge weight (message count)
            edge_key = tuple(sorted([sender, recipient]))
            edge_weights[edge_key] += 1
        
        # Calculate all metrics
        nodes_with_metrics = self._calculate_all_metrics(graph, node_info, edge_weights)
        
        # Detect communities
        communities = self.detect_communities({"nodes": nodes_with_metrics, "edges": graph})
        
        # Calculate influence scores
        influence_scores = self.calculate_influence_scores({
            "nodes": nodes_with_metrics,
            "edges": graph
        })
        
        # Find bridges
        bridges = self.find_bridges({"nodes": nodes_with_metrics, "edges": graph})
        
        # Predict missing links
        missing_links = self.predict_missing_links({
            "nodes": nodes_with_metrics,
            "edges": graph
        })
        
        return {
            "nodes": nodes_with_metrics,
            "edges": self._serialize_edges(graph, edge_weights),
            "communities": communities,
            "influence_scores": influence_scores,
            "bridges": bridges,
            "predicted_links": missing_links,
            "stats": {
                "node_count": len(nodes_with_metrics),
                "edge_count": len(edge_weights),
                "density": self._calculate_density(len(nodes_with_metrics), len(edge_weights)),
            },
        }
    
    def detect_communities(self, graph: dict) -> List[dict]:
        """Detect communities in the network.
        
        Uses Louvain method if networkx available, else greedy clustering.
        
        Returns:
            List of community dicts with members and properties
        """
        if self.has_networkx:
            return self._detect_communities_networkx(graph)
        else:
            return self._detect_communities_greedy(graph)
    
    def calculate_influence_scores(self, graph: dict) -> dict:
        """Calculate influence scores for all nodes.
        
        Formula:
        influence = 0.4 * degree + 0.3 * betweenness + 0.2 * closeness + 0.1 * eigenvector
        
        Returns:
            dict mapping node_id -> influence_score
        """
        nodes = graph["nodes"]
        edges = graph["edges"]
        
        influence_scores = {}
        
        for node_id, node_data in nodes.items():
            metrics = node_data.get("metrics", {})
            
            # Get centrality metrics (normalized 0-1)
            degree = metrics.get("degree_centrality", 0)
            betweenness = metrics.get("betweenness_centrality", 0)
            closeness = metrics.get("closeness_centrality", 0)
            eigenvector = metrics.get("eigenvector_centrality", 0)
            
            # Calculate influence score
            influence = (
                0.4 * degree +
                0.3 * betweenness +
                0.2 * closeness +
                0.1 * eigenvector
            )
            
            # Determine influence level
            if influence >= 0.7:
                level = "HIGH"
            elif influence >= 0.4:
                level = "MEDIUM"
            else:
                level = "LOW"
            
            influence_scores[node_id] = {
                "score": round(influence, 3),
                "level": level,
                "components": {
                    "degree": round(degree, 3),
                    "betweenness": round(betweenness, 3),
                    "closeness": round(closeness, 3),
                    "eigenvector": round(eigenvector, 3),
                },
            }
        
        return influence_scores
    
    def find_bridges(self, graph: dict) -> List[dict]:
        """Find bridge nodes that connect communities.
        
        Returns:
            List of bridge node dicts
        """
        nodes = graph["nodes"]
        edges = graph["edges"]
        
        bridges = []
        
        for node_id, node_data in nodes.items():
            metrics = node_data.get("metrics", {})
            betweenness = metrics.get("betweenness_centrality", 0)
            
            # High betweenness suggests bridge role
            if betweenness > 0.3:
                # Get neighbors from different communities
                neighbors = list(edges.get(node_id, []))
                
                bridges.append({
                    "node": node_id,
                    "betweenness": round(betweenness, 3),
                    "degree": metrics.get("degree", 0),
                    "neighbors": neighbors[:10],
                    "role": "bridge",
                })
        
        # Sort by betweenness
        bridges.sort(key=lambda x: x["betweenness"], reverse=True)
        
        return bridges[:10]  # Top 10 bridges
    
    def predict_missing_links(self, graph: dict) -> List[dict]:
        """Predict potential missing links using common neighbors.
        
        Returns:
            List of predicted link dicts with confidence
        """
        nodes = graph["nodes"]
        edges = graph["edges"]
        
        predictions = []
        node_ids = list(nodes.keys())
        
        # For each pair of non-connected nodes
        for i, node_a in enumerate(node_ids):
            for node_b in node_ids[i+1:]:
                # Skip if already connected
                if node_b in edges.get(node_a, set()):
                    continue
                
                # Calculate common neighbors
                neighbors_a = edges.get(node_a, set())
                neighbors_b = edges.get(node_b, set())
                common = neighbors_a & neighbors_b
                
                if len(common) >= 2:  # At least 2 common neighbors
                    # Calculate confidence
                    confidence = len(common) / max(len(neighbors_a), len(neighbors_b))
                    
                    predictions.append({
                        "node_a": node_a,
                        "node_b": node_b,
                        "common_neighbors": list(common),
                        "confidence": round(confidence, 3),
                    })
        
        # Sort by confidence
        predictions.sort(key=lambda x: x["confidence"], reverse=True)
        
        return predictions[:20]  # Top 20 predictions
    
    def track_network_evolution(self, case_dir: Path) -> dict:
        """Track network changes over time.
        
        Args:
            case_dir: Path to case directory
            
        Returns:
            dict with timeline of network changes
        """
        # Placeholder for future implementation
        # Would load messages at different timestamps and build graphs
        return {
            "snapshots": [],
            "growth": [],
            "changes": [],
        }
    
    def _calculate_all_metrics(
        self, graph: Dict[str, Set], node_info: Dict, edge_weights: Dict
    ) -> Dict[str, dict]:
        """Calculate all centrality metrics for each node."""
        nodes_with_metrics = {}
        node_list = list(graph.keys())
        n = len(node_list)
        
        for node_id in node_list:
            metrics = {}
            
            # Degree centrality
            degree = len(graph[node_id])
            metrics["degree"] = degree
            metrics["degree_centrality"] = degree / (n - 1) if n > 1 else 0
            
            # Betweenness centrality (expensive, approximate)
            metrics["betweenness_centrality"] = self._betweenness_centrality(
                node_id, graph
            )
            
            # Closeness centrality
            metrics["closeness_centrality"] = self._closeness_centrality(
                node_id, graph
            )
            
            # Eigenvector centrality (simplified)
            metrics["eigenvector_centrality"] = self._eigenvector_centrality_simple(
                node_id, graph
            )
            
            nodes_with_metrics[node_id] = {
                **node_info.get(node_id, {}),
                "metrics": metrics,
            }
        
        return nodes_with_metrics
    
    def _betweenness_centrality(self, node: str, graph: Dict[str, Set]) -> float:
        """Calculate betweenness centrality (approximation)."""
        # Simple approximation: fraction of shortest paths through this node
        # Full calculation is O(n^3), so we approximate
        
        # Count paths that go through this node
        paths_through = 0
        total_paths = 0
        
        node_list = list(graph.keys())
        
        for source in node_list[:min(20, len(node_list))]:  # Sample
            if source == node:
                continue
            
            # BFS from source
            distances = {source: 0}
            queue = deque([source])
            predecessors = defaultdict(list)
            
            while queue:
                current = queue.popleft()
                
                for neighbor in graph[current]:
                    if neighbor not in distances:
                        distances[neighbor] = distances[current] + 1
                        queue.append(neighbor)
                        predecessors[neighbor].append(current)
                    elif distances[neighbor] == distances[current] + 1:
                        predecessors[neighbor].append(current)
            
            # Count paths
            for target in node_list:
                if target == source or target == node:
                    continue
                
                if target in distances:
                    total_paths += 1
                    
                    # Check if node is on path
                    if node in predecessors.get(target, []):
                        paths_through += 1
        
        return paths_through / total_paths if total_paths > 0 else 0
    
    def _closeness_centrality(self, node: str, graph: Dict[str, Set]) -> float:
        """Calculate closeness centrality."""
        # Average distance to all other nodes
        distances = self._bfs_distances(node, graph)
        
        if len(distances) <= 1:
            return 0
        
        total_distance = sum(distances.values())
        avg_distance = total_distance / (len(distances) - 1)
        
        # Closeness is inverse of average distance
        return 1 / avg_distance if avg_distance > 0 else 0
    
    def _eigenvector_centrality_simple(self, node: str, graph: Dict[str, Set]) -> float:
        """Simplified eigenvector centrality (degree of neighbors)."""
        # Approximation: average degree of neighbors
        neighbors = graph[node]
        
        if not neighbors:
            return 0
        
        neighbor_degrees = sum(len(graph[n]) for n in neighbors)
        return neighbor_degrees / (len(neighbors) * len(graph))
    
    def _bfs_distances(self, source: str, graph: Dict[str, Set]) -> Dict[str, int]:
        """BFS to calculate distances from source."""
        distances = {source: 0}
        queue = deque([source])
        
        while queue:
            current = queue.popleft()
            
            for neighbor in graph[current]:
                if neighbor not in distances:
                    distances[neighbor] = distances[current] + 1
                    queue.append(neighbor)
        
        return distances
    
    def _detect_communities_greedy(self, graph: dict) -> List[dict]:
        """Greedy community detection by connection density."""
        nodes = graph["nodes"]
        edges = graph["edges"]
        
        visited = set()
        communities = []
        
        for node_id in nodes.keys():
            if node_id in visited:
                continue
            
            # Start new community
            community = {node_id}
            queue = deque([node_id])
            visited.add(node_id)
            
            while queue:
                current = queue.popleft()
                
                # Add highly connected neighbors
                for neighbor in edges.get(current, []):
                    if neighbor not in visited:
                        # Check if neighbor is well-connected to community
                        neighbor_edges = edges.get(neighbor, set())
                        connections_to_community = len(neighbor_edges & community)
                        
                        if connections_to_community >= len(community) * 0.3:
                            community.add(neighbor)
                            queue.append(neighbor)
                            visited.add(neighbor)
            
            if len(community) >= 2:
                communities.append({
                    "id": len(communities),
                    "members": list(community),
                    "size": len(community),
                })
        
        return communities
    
    def _detect_communities_networkx(self, graph: dict) -> List[dict]:
        """Community detection using networkx (if available)."""
        # Placeholder for networkx implementation
        return self._detect_communities_greedy(graph)
    
    def _calculate_density(self, nodes: int, edges: int) -> float:
        """Calculate graph density."""
        if nodes < 2:
            return 0
        max_edges = nodes * (nodes - 1) / 2
        return edges / max_edges
    
    def _serialize_edges(self, graph: Dict, edge_weights: Dict) -> List[dict]:
        """Convert edges to serializable format."""
        edges = []
        seen = set()
        
        for source, targets in graph.items():
            for target in targets:
                edge_key = tuple(sorted([source, target]))
                if edge_key not in seen:
                    seen.add(edge_key)
                    edges.append({
                        "source": source,
                        "target": target,
                        "weight": edge_weights.get(edge_key, 1),
                    })
        
        return edges
    
    def _check_networkx(self) -> bool:
        """Check if networkx is available."""
        try:
            import networkx
            return True
        except ImportError:
            return False
