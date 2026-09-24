def index_definitions(definition_scheme: list) -> dict:
  return {d["path"]: d for d in definition_scheme if d.get("type") not in ("table", "section")}


def leaf_schema(data_type: str, definition: dict) -> dict:

  opts = ((definition or {}).get("settings") or {}).get("options_source", {}).get("options")
  if opts:
    value_schema = {"type": ["string", "null"], "enum": [*opts, None]}
  else:
    value_schema = {"type": [TYPE_MAP.get(data_type, "string"), "null"]}


  props = {
      "value": value_schema,
      "source": {"type": "string", "enum": ["extracted", "default"]},
      "reasoning": {"type": "string", "maxLength": 200},
      "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
      "source_pages": {
          "type": "array",
          "items": {
              "type": "object",
              "properties": {"page": {"type": "integer"}},
              "required": ["page"],
              "additionalProperties": False

          }
          },
      "confidence_reason": {"type": "string", "maxLength": 200}
  }

  return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def raw_scheme_to_llm_json_schema(node, definitions: dict, path: str = "") -> dict | None:
  if isinstance(node, str):
    return leaf_schema(node, definitions.get(path))

  if isinstance(node, dict):
    props = {
        k: raw_scheme_to_llm_json_schema(v, definitions, f"{path}.{k}" if path else k)
        for k, v in node.items()
        }
    return {"type": "object", "properties": props,
            "required": list(props), "additionalProperties": False}

  if isinstance(node, list):
    if not node:
      return {"type": "array", "items": {"type": "object", "properties": {},
              "additionalProperties": False}}

    item = raw_scheme_to_llm_json_schema(node[0], definitions, path + ".[]")
    return {"type": "array", "items": item, "maxItems": 150}
  return None


def as_root_scheme(node, definitions: dict, path: str):
  schema = raw_scheme_to_llm_json_schema(node, definitions, path)
  if schema.get("type") == "array":
    return {"type": "object", "properties": {"items": schema},
            "required": ["items"], "additionalProperties": False}, True
  return schema, False


def get_required_fields(node, definitions: dict, path: str) -> list[str]:
  out = []

  if isinstance(node, str):
    d = definitions.get(path) or {}
    if (d.get("settings") or {}).get("is_required"):
      out.append(path)
  elif isinstance (node, dict):
    for k, v in node.items():
      out += get_required_fields(v, definitions, f"{path}.{k}")
  elif isinstance(node, list) and node:
    out += get_required_fields(node[0], definitions, path + ".[]")

  return out