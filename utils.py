def index_definitions(definition_scheme: list) -> dict:
  return {d["path"]: d for d in definition_scheme if d.get("type") not in ("table", "section")}


def leaf_schema(data_type: str, definition: dict) -> dict:
  TYPE_MAP = dict(string="string", number="number", boolean="boolean", options="string", date="string")

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


def format_rules(settings: dict) -> str:

    s = settings
    r = []

    if s.get("predefine_primary_keys"):
      keys = "; ".join(f'"{k}"' for k in s.get("predefine_primary_keys"))
      r.append(
          f"this column takes one of a fixed set of names, written exactly as given: {keys}. "
          f"Create one record per name, in this order. When the agreement provides no such "
          f"basket, still create the record and leave its other fields empty"
      )

    if s.get("alphanumeric"):                r.append("letters and digits only")
    if s.get("max_length"):                  r.append(f"max {s['max_length']} characters")
    if s.get("date_format"):                 r.append(f"date format is {s['date_format']}")
    if s.get("number_type"):                 r.append(f"number type is {s['number_type']}")
    if s.get("min") is not None:             r.append(f"min is {s['min']}")
    if s.get("max") is not None:             r.append(f"max is {s['max']}")
    if s.get("allow_negative") is False:     r.append("non-negative")
    if s.get("allow_spaces") is False:       r.append("remove all spaces")

    return "; ".join(r)


def collect_paths(node, path):
  out = []

  if isinstance(node, str):
    out.append(path)

  elif isinstance(node, dict):
    for k, v in node.items():
      out += collect_paths(v, f"{path}.{k}")

  elif isinstance(node, list) and node:
    out = collect_paths(node[0], path + ".[]")

  return out


def build_field_instruction(node, definitions, path):
  abs_paths = collect_paths(node, path)

  out = []

  for p in abs_paths:
    d = definitions.get(p) or {}
    description = d.get("description")
    rule = format_rules(d.get("settings") or {})
    field_name = p.split(".")[-1]

    if rule:
      rule += f". Format applies to {field_name} field only."
    elif d.get("data_type") == "options":
      rule += "Answer with one of the field's options based on the governing clause."
    else:
      rule = "Extract the value as described above."


    out.append(f"FIELD: {field_name}\n meaning: {description}\n rules: {rule}")

  return "\n\n".join(out)