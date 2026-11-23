"""Offline API-graph checks; object_info optionally strengthens port validation."""
from __future__ import annotations


def validate_api_graph(graph: dict, object_info: dict | None = None) -> list[str]:
    errors = []
    if not isinstance(graph, dict) or not graph or "nodes" in graph:
        return ["Expected nonempty ComfyUI API graph, not editor workflow"]
    nodes = {str(k): v for k, v in graph.items() if not str(k).startswith("__")}
    deps = {key: set() for key in nodes}
    for key, node in nodes.items():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            errors.append(f"{key}: missing class_type/inputs")
            continue
        spec = (object_info or {}).get(node["class_type"])
        if object_info is not None and spec is None:
            errors.append(f"{key}: unknown node class {node['class_type']}")
        if spec:
            for name in spec.get("input", {}).get("required", {}):
                if name not in node["inputs"]:
                    errors.append(f"{key}: missing required port {name}")
        for name, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                source, index = value
                if source not in nodes or index < 0:
                    errors.append(f"{key}.{name}: dangling link {value}")
                    continue
                deps[key].add(source)
                upstream = (object_info or {}).get(nodes[source].get("class_type"), {})
                outputs = upstream.get("output", [])
                if object_info and index >= len(outputs):
                    errors.append(f"{key}.{name}: invalid output slot {index}")
                elif spec and outputs:
                    inputs = {**spec.get("input", {}).get("required", {}), **spec.get("input", {}).get("optional", {})}
                    expected = inputs.get(name, [None])[0]
                    if isinstance(expected, str) and expected not in ("*", outputs[index]):
                        errors.append(f"{key}.{name}: type {outputs[index]} != {expected}")
    remaining = {k: set(v) for k, v in deps.items()}
    while remaining:
        ready = [k for k, value in remaining.items() if not value]
        if not ready:
            errors.append("Cycle in API graph")
            break
        for key in ready:
            remaining.pop(key)
        for value in remaining.values():
            value.difference_update(ready)
    return errors
