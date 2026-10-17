from copy import deepcopy
from pathlib import Path
import subprocess

import yaml
from jsonschema import Draft7Validator


def read_yaml(path):
    return yaml.safe_load(Path(path).read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


root = Path(__file__).resolve().parents[1]
platform = root / "platform/webapp"
build = root / "build"
build.mkdir(exist_ok=True)

# Pasta em apps/ -> namespace esperado. Toda WebApp precisa estar em uma
# dessas pastas e declarar o namespace correspondente.
ENVIRONMENTS = {
    "webapps": "webapps",
    "dev": "webapps-dev",
    "prod": "webapps-prod",
}

xrd = read_yaml(platform / "xrd.yaml")
composition = read_yaml(platform / "composition.yaml")
function = read_yaml(platform / "function.yaml")

version = next(v for v in xrd["spec"]["versions"] if v["referenceable"])
api_version = f'{xrd["spec"]["group"]}/{version["name"]}'
kind = xrd["spec"]["names"]["kind"]

require(
    composition["spec"]["compositeTypeRef"]
    == {"apiVersion": api_version, "kind": kind},
    "Composition aponta para uma API diferente da XRD.",
)

for step in composition["spec"]["pipeline"]:
    require(
        step["functionRef"]["name"] == function["metadata"]["name"],
        "A Function referenciada pela Composition nao corresponde ao arquivo.",
    )

schema = deepcopy(
    version["schema"]["openAPIV3Schema"]["properties"]["spec"]
)
schema["additionalProperties"] = False
schema["properties"]["crossplane"] = {
    "type": "object",
    "properties": {
        "compositionRef": {
            "type": "object",
            "properties": {
                "name": {"const": composition["metadata"]["name"]}
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "compositionUpdatePolicy": {
            "type": "string",
            "enum": ["Automatic", "Manual"],
        },
        "compositionRevisionRef": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        "compositionRevisionSelector": {
            "type": "object",
            "properties": {
                "matchLabels": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                }
            },
            "required": ["matchLabels"],
            "additionalProperties": False,
        },
    },
    "required": ["compositionRef"],
    "additionalProperties": False,
}
schema["required"] = list(
    dict.fromkeys(schema.get("required", []) + ["crossplane"])
)
Draft7Validator.check_schema(schema)

total = 0
for folder, expected_namespace in ENVIRONMENTS.items():
    env_dir = root / "apps" / folder
    if not env_dir.is_dir():
        continue

    files = sorted(env_dir.glob("*.yaml")) + sorted(env_dir.glob("*.yml"))
    if not files:
        continue

    env_build = build / folder
    env_build.mkdir(parents=True, exist_ok=True)

    for path in files:
        xr = read_yaml(path)
        require(xr["apiVersion"] == api_version, f"{path}: apiVersion incorreta.")
        require(xr["kind"] == kind, f"{path}: kind incorreto.")
        require(
            xr["metadata"].get("namespace") == expected_namespace,
            f"{path}: namespace deve ser {expected_namespace} "
            f"(pasta apps/{folder}).",
        )
        Draft7Validator(schema).validate(xr["spec"])

        # O render nao passa pelo API Server. Aplicamos aqui os defaults
        # simples definidos na XRD atual antes de executar a Composition.
        normalized = deepcopy(xr)
        for field, definition in schema["properties"].items():
            if field not in normalized["spec"] and "default" in definition:
                normalized["spec"][field] = deepcopy(definition["default"])

        input_path = env_build / f"{path.stem}-input.yaml"
        input_path.write_text(yaml.safe_dump(normalized))

        result = subprocess.run(
            [
                "crossplane", "composition", "render",
                str(input_path),
                str(platform / "composition.yaml"),
                str(platform / "function.yaml"),
                "--crossplane-version=v2.4.2",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )

        documents = [
            doc for doc in yaml.safe_load_all(result.stdout) if doc
        ]
        resources = [
            doc for doc in documents
            if not (
                doc.get("apiVersion") == api_version
                and doc.get("kind") == kind
            )
        ]
        require(
            sorted(doc["kind"] for doc in resources)
            == ["ConfigMap", "Deployment", "Service"],
            f"{path}: esperado exatamente um ConfigMap, um Deployment e um Service.",
        )

        output_path = env_build / f"{path.stem}-resources.yaml"
        output_path.write_text(yaml.safe_dump_all(resources))

        subprocess.run(
            ["kubeconform", "-strict", "-summary", str(output_path)],
            check=True,
        )
        print(f"OK: {path.relative_to(root)}", flush=True)
        total += 1

require(total > 0, "Nenhuma WebApp encontrada nas pastas configuradas.")
print(f"\nTotal validado: {total} WebApp(s).", flush=True)