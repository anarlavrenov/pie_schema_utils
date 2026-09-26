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


def flatten_dict(f, path=""):

  out = {}

  if isinstance(f, dict) and "value" in f.keys():
    out[path] = f["value"]

  elif isinstance(f, dict):
    for k, v in f.items():
      out.update(flatten_dict(v, f"{path}.{k}" if path else k))

  elif isinstance(f, list):
    for i, v in enumerate(f):
      out.update(flatten_dict(v, f"{path}[{i}]"))
  return out


def norm_value(v):
  if v is None:
    return None
  s = str(v).strip().replace("$", "").replace(",", "").strip()
  s = s.replace("\u201c", '"').replace("\u201d", '"')
  s = s.replace("\u2018", '"').replace("\u2019", '"')
  s = s.replace("'", '"')
  s = re.sub(r"\s+", " ", s)
  s = re.sub(r"^(\d{2})[/\-. ](\d{2})$", r"\1\2", s)
  if s.lower() in ("null", "none", ""):
    return None

  try:
    f = float(s)
    if math.isfinite(f):
      return str(int(f)) if f == int(f) else str(f)
  except ValueError:
    pass

  return s.casefold()


def count_overlap(r_node, g_node):
  if not isinstance(r_node, dict) or not isinstance(g_node, dict):
    return 0
  hits = 0
  for k, gv in g_node.items():
    if not isinstance(gv, dict) or "value" not in gv:
      continue
    g_val = norm_value(gv.get("value"))
    if g_val is None:
      continue

    r_val = norm_value(r_node.get(k, {}).get("value")) if isinstance(r_node.get(k), dict) else None
    if r_val == g_val:
      hits += 1
  return hits


def align_by_key(r_node, g_node):

  if isinstance(g_node, list):
    r_list = r_node if isinstance(r_node, list) else []
    if not r_list:
      return [{} for _ in g_node]

    if not g_node:
      return list(r_list)

    cost = np.zeros((len(g_node), len(r_list)))
    for i, gr in enumerate(g_node):
      for j, rr in enumerate(r_list):
        cost[i][j] = -count_overlap(rr, gr)

    rows, cols = linear_sum_assignment(cost)
    pairs = {int(i): int(j) for i, j in zip(rows, cols)}

    out = [align_by_key(r_list[pairs[i]], gr) if i in pairs else {}
            for i, gr in enumerate(g_node)]

    used = set(pairs.values())
    return out + [r_list[j] for j in range(len(r_list)) if j not in used]


  if isinstance(g_node, dict) and "value" not in g_node:

    result = {}

    for k, v in r_node.items():
      if k in g_node:
        result[k] = align_by_key(v, g_node[k])
      else:
        result[k] = v

    return result

  return r_node


def calculate_metrics(r, g, valid):

  missing = 0
  both_empty = 0
  exact = 0
  wrong = 0

  missing_obj = object()

  for k, g_v in g.items():

    r_v = r.get(k, missing_obj)

    if r_v is missing_obj:
      missing += 1

    else:
      r_v = norm_value(r_v)
      g_v = norm_value(g_v)

      ok = valid.get(k)
      if ok is not None:
        matched = r_v in ok or (g_v is None and r_v is None)
      else:
        matched = r_v == g_v

      if matched:
        if g_v is None:
          both_empty += 1
        else:
          exact += 1

      else:
        wrong += 1

  extra = [k for k in set(r) - set(g) if norm_value(r[k]) is not None]
  accuracy = (exact + both_empty) / len(g)
  precision = exact / (exact + wrong + len(extra))
  recall = exact / (exact + wrong + missing)
  f1_score = 2 * (precision * recall) / max((precision + recall), 1e-9)

  print(f"Accuracy: {accuracy:.2f}")
  print(f"Precision: {precision:.2f}")
  print(f"Recall: {recall:.2f}")
  print(f"F1 score: {f1_score:.2f}")

  print("\n")

  print(f"exact: {exact} | both_empty: {both_empty} | wrong: {wrong} | missing: {missing} | extra: {len(extra)}")