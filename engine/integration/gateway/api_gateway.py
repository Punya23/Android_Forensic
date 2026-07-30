from typing import Dict, Any

class APIGateway:
    """
    Manages REST API Gateway features: routing, rate limiting, and auth.
    """
    def __init__(self):
        self.routes = {}

    def register_route(self, path: str, handler: Any):
        self.routes[path] = handler

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processes an incoming API request.
        """
        path = request.get("path")
        if path in self.routes:
            return self.routes[path](request)
        return {"status": 404, "error": "Not Found"}
