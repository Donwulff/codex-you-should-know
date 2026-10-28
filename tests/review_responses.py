def clear_response(category="no_concern", reason="The tool results support the requested work."):
    return {"finding": None, "no_finding": {"category": category, "reason": reason}}
