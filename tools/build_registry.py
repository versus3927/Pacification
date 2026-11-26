from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "pacification" / "providers.json"

SOURCES = {
    "hamed": "https://raw.githubusercontent.com/Hamed-244/sms-bomber/main/providers.json",
    "alihtt": "https://raw.githubusercontent.com/Alihtt/SMS-Bomber/main/bomber.py",
    "tbomb": "https://raw.githubusercontent.com/TheSpeedX/TBomb/master/apidata.json",
    "bombx": "https://raw.githubusercontent.com/BetterCallShiv/BomBX-CLI/main/api_config.json",
}

CC_TO_ISO = {
    "218": "LY",
    "977": "NP",
    "91": "IN",
    "multi": None,
}


def fetch(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Pacification registry builder"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def replace_number(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("Replace phonenumber here", "{local0}")
    if isinstance(value, dict):
        return {key: replace_number(item) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_number(item) for item in value]
    return value


def from_hamed(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    result: list[dict[str, Any]] = []
    for name, entry in data.items():
        body = replace_number(entry.get("parameters") or {})
        result.append(
            {
                "name": name,
                "kind": "otp",
                "method": str(entry.get("method", "POST")).upper(),
                "url": replace_number(entry["url"]),
                "countries": ["IR"],
                "body_type": "json",
                "body": body,
                "success": {"status": [200, 201, 202, 204]},
            }
        )
    return result


def alihtt_value(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id == "number":
        return "{local0}"
    if isinstance(node, ast.Dict):
        return {
            alihtt_value(key): alihtt_value(value)
            for key, value in zip(node.keys, node.values)
        }
    if isinstance(node, (ast.List, ast.Tuple)):
        return [alihtt_value(item) for item in node.elts]
    if isinstance(node, ast.JoinedStr):
        text = ast.unparse(node)
        if text.startswith(("f'", 'f"')):
            text = text[2:-1]
        text = text.replace("{number.replace('0', '+98', 1)}", "{intl}")
        text = text.replace('{number.replace("0", "+98", 1)}', "{intl}")
        text = text.replace("{number.replace('0', '98', 1)}", "{cc_local}")
        text = text.replace('{number.replace("0", "98", 1)}', "{cc_local}")
        return text.replace("{number}", "{local0}")
    if isinstance(node, ast.Call):
        text = ast.unparse(node)
        if ".lstrip(" in text:
            return "{subscriber}"
        if "+98" in text:
            return "{intl}"
        if '"98"' in text or "'98'" in text:
            return "{cc_local}"
        return "{local0}"
    return None


def from_alihtt(raw: str) -> list[dict[str, Any]]:
    tree = ast.parse(raw)
    result: list[dict[str, Any]] = []
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef):
            continue
        kind = {"bomb": "otp", "call": "call"}.get(function.name)
        if kind is None:
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "sites"
                for target in node.targets
            ):
                continue
            if not isinstance(node.value, ast.List):
                continue
            for index, item in enumerate(node.value.elts):
                if not isinstance(item, (ast.List, ast.Tuple)) or len(item.elts) != 2:
                    continue
                url = alihtt_value(item.elts[0])
                payload = item.elts[1]
                is_get = (
                    isinstance(payload, ast.Constant)
                    and str(payload.value).lower() == "get"
                )
                spec: dict[str, Any] = {
                    "name": f"alihtt-{kind}-{index + 1}",
                    "kind": kind,
                    "method": "GET" if is_get else "POST",
                    "url": str(url),
                    "countries": ["IR"],
                    "success": {"status": [200, 201, 202, 204]},
                }
                if not is_get:
                    body = alihtt_value(payload)
                    if body is not None:
                        spec["body_type"] = "json"
                        spec["body"] = body
                result.append(spec)
    return result


def from_tbomb(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    result: list[dict[str, Any]] = []
    for kind, groups in data.items():
        if kind not in {"sms", "call"} or not isinstance(groups, dict):
            continue
        group_kind = "call" if kind == "call" else "otp"
        for code, entries in groups.items():
            if not isinstance(entries, list):
                continue
            country = CC_TO_ISO.get(str(code), str(code))
            for index, entry in enumerate(entries):
                spec: dict[str, Any] = {
                    "name": f"tbomb-{normalize_name(str(entry.get('name', code)) + '-' + str(index))}",
                    "kind": group_kind,
                    "method": str(entry.get("method", "GET")).upper(),
                    "url": str(entry["url"]),
                    "success": {"status": [200, 201, 202, 204]},
                }
                if country:
                    spec["countries"] = [country]
                if entry.get("json") is not None:
                    spec["body_type"] = "json"
                    spec["body"] = entry["json"]
                elif entry.get("data") is not None:
                    spec["body_type"] = "form"
                    spec["body"] = entry["data"]
                if entry.get("params"):
                    spec["query"] = entry["params"]
                if entry.get("headers"):
                    spec["headers"] = entry["headers"]
                identifier = entry.get("identifier")
                if identifier:
                    spec["success"]["regex"] = re.escape(str(identifier))
                result.append(spec)
    return result


def from_bombx(raw: str) -> list[dict[str, Any]]:
    groups = json.loads(raw).get("BomBX_API", {})
    result: list[dict[str, Any]] = []
    for name, entry in groups.items():
        if str(entry.get("type", "sms")).lower() != "sms":
            continue
        spec: dict[str, Any] = {
            "name": f"bombx-{normalize_name(name)}",
            "kind": "otp",
            "method": str(entry.get("method", "GET")).upper(),
            "url": str(entry["url"]).replace("{phone}", "{subscriber}"),
            "countries": ["IN"],
            "success": {"status": [200, 201, 202, 204]},
        }
        data = entry.get("data")
        if isinstance(data, dict):
            spec["body_type"] = "json"
            spec["body"] = {
                key: str(value).replace("{phone}", "{subscriber}")
                for key, value in data.items()
            }
        if entry.get("headers"):
            spec["headers"] = entry["headers"]
        result.append(spec)
    return result


def merge(base: list[dict[str, Any]], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {normalize_name(str(item["name"])) for item in base}
    merged = list(base)
    for spec in additions:
        key = normalize_name(str(spec["name"]))
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(spec)
    return merged


def main() -> None:
    base = json.loads(REGISTRY.read_text(encoding="utf-8"))["providers"]
    additions: list[dict[str, Any]] = []
    for name, url in SOURCES.items():
        try:
            raw = fetch(url)
        except Exception as exc:
            print(f"skip {name}: {exc}")
            continue
        converters = {
            "hamed": from_hamed,
            "alihtt": from_alihtt,
            "tbomb": from_tbomb,
            "bombx": from_bombx,
        }
        found = converters[name](raw)
        additions.extend(found)
        print(f"{name}: {len(found)}")

    providers = merge(base, additions)
    REGISTRY.write_text(
        json.dumps({"providers": providers}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"total: {len(providers)}")


if __name__ == "__main__":
    main()
