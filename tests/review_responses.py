def clear_response(
    category="no_concern", reason="No concrete concern was identified in the supplied evidence."
):
    return {"finding": None, "no_finding": {"category": category, "reason": reason}}
