import json


def parse_json_object(text: str) -> dict:
    content = text.strip()

    if content.startswith("```"):
        content = content.strip("`").strip()
        if content.startswith("json"):
            content = content[4:].strip()

    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or start > end:
        raise ValueError("No JSON object found in model response.")

    return json.loads(content[start : end + 1])
