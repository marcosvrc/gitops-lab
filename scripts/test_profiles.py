"""Testes de perfis e mapeamentos da Composition webapp-local.

Gera WebApps sinteticas, roda `crossplane composition render` e verifica
os valores esperados de CPU, memoria, imagem, replicas, porta, nome e
namespace. Tambem valida o schema XRD para o caso de rejeicao de perfil
inexistente.

Objetivo: um mapeamento incorreto na Composition faz o CI falhar, mesmo
que o YAML continue sintaticamente valido.
"""

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

import yaml
from jsonschema import Draft7Validator, ValidationError


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform/webapp"

API_VERSION = "platform.example.org/v1alpha1"
KIND = "WebApp"
COMPOSITION_NAME = "webapp-local"
CROSSPLANE_VERSION = "v2.4.2"

PROFILES = {
    "small":  {"req_cpu": "50m",   "req_mem": "64Mi",  "lim_cpu": "250m",  "lim_mem": "128Mi"},
    "medium": {"req_cpu": "100m",  "req_mem": "128Mi", "lim_cpu": "500m",  "lim_mem": "256Mi"},
    "large":  {"req_cpu": "250m",  "req_mem": "256Mi", "lim_cpu": "1000m", "lim_mem": "512Mi"},
}

# Sem size: requests vem da base da Composition e limits dos defaults da XRD.
NO_SIZE_DEFAULTS = {"req_cpu": "50m", "req_mem": "64Mi", "lim_cpu": "250m", "lim_mem": "128Mi"}


def read_yaml(path):
    return yaml.safe_load(Path(path).read_text())


def build_schema():
    """Monta o schema reforcado usado para validar WebApps."""
    xrd = read_yaml(PLATFORM / "xrd.yaml")
    version = next(v for v in xrd["spec"]["versions"] if v["referenceable"])
    schema = deepcopy(version["schema"]["openAPIV3Schema"]["properties"]["spec"])
    schema["additionalProperties"] = False
    schema["properties"]["crossplane"] = {
        "type": "object",
        "properties": {
            "compositionRef": {
                "type": "object",
                "properties": {"name": {"const": COMPOSITION_NAME}},
                "required": ["name"],
                "additionalProperties": False,
            }
        },
        "required": ["compositionRef"],
        "additionalProperties": False,
    }
    schema["required"] = list(dict.fromkeys(schema.get("required", []) + ["crossplane"]))
    Draft7Validator.check_schema(schema)
    return schema


def apply_defaults(spec, schema):
    """Aplica os defaults do schema (XRD nao atua pelo API server no render)."""
    result = deepcopy(spec)
    for field, definition in schema["properties"].items():
        if field not in result and "default" in definition:
            result[field] = deepcopy(definition["default"])
    return result


def render(spec, name="test-webapp", namespace="webapps"):
    """Roda crossplane composition render e devolve os documentos nao-XR."""
    xr = {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {"name": name, "namespace": namespace},
        "spec": {**spec, "crossplane": {"compositionRef": {"name": COMPOSITION_NAME}}},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(xr, f)
        input_path = f.name
    try:
        result = subprocess.run(
            [
                "crossplane", "composition", "render",
                input_path,
                str(PLATFORM / "composition.yaml"),
                str(PLATFORM / "function.yaml"),
                f"--crossplane-version={CROSSPLANE_VERSION}",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    finally:
        Path(input_path).unlink()
    return [
        d for d in yaml.safe_load_all(result.stdout)
        if d and not (d.get("apiVersion") == API_VERSION and d.get("kind") == KIND)
    ]


def deployment(docs):
    return next(d for d in docs if d["kind"] == "Deployment")


def service(docs):
    return next(d for d in docs if d["kind"] == "Service")


def configmap(docs):
    return next(d for d in docs if d["kind"] == "ConfigMap")


def container_resources(dep):
    return dep["spec"]["template"]["spec"]["containers"][0]["resources"]


def check_resources(spec, expected, label):
    schema = build_schema()
    normalized = apply_defaults(spec, schema)
    docs = render(normalized)
    dep = deployment(docs)
    res = container_resources(dep)
    actual = {
        "req_cpu": res["requests"]["cpu"],
        "req_mem": res["requests"]["memory"],
        "lim_cpu": res["limits"]["cpu"],
        "lim_mem": res["limits"]["memory"],
    }
    assert actual == expected, f"{label}: esperado {expected}, obtido {actual}"


# ---- Testes ----

def test_profile_small():
    check_resources({"image": "nginx:1.28", "size": "small"}, PROFILES["small"], "size=small")


def test_profile_medium():
    check_resources({"image": "nginx:1.28", "size": "medium"}, PROFILES["medium"], "size=medium")


def test_profile_large():
    check_resources({"image": "nginx:1.28", "size": "large"}, PROFILES["large"], "size=large")


def test_no_size_uses_defaults():
    check_resources({"image": "nginx:1.28"}, NO_SIZE_DEFAULTS, "sem size")


def test_no_size_with_explicit_limits():
    expected = {"req_cpu": "50m", "req_mem": "64Mi", "lim_cpu": "500m", "lim_mem": "256Mi"}
    check_resources(
        {"image": "nginx:1.28", "cpuLimit": "500m", "memoryLimit": "256Mi"},
        expected,
        "sem size + limites explicitos",
    )


def test_size_takes_precedence_over_limits():
    # size: medium deve prevalecer sobre cpuLimit/memoryLimit explicitos.
    check_resources(
        {"image": "nginx:1.28", "size": "medium", "cpuLimit": "1000m", "memoryLimit": "512Mi"},
        PROFILES["medium"],
        "size medium > cpuLimit/memoryLimit explicitos",
    )


def test_invalid_profile_rejected():
    schema = build_schema()
    spec = {
        "image": "nginx:1.28",
        "size": "xlarge",
        "crossplane": {"compositionRef": {"name": COMPOSITION_NAME}},
    }
    try:
        Draft7Validator(schema).validate(spec)
    except ValidationError:
        return
    raise AssertionError("size=xlarge deveria ter sido rejeitado pelo schema da XRD")


def test_image_mapping():
    docs = render({"image": "busybox:1.36"})
    actual = deployment(docs)["spec"]["template"]["spec"]["containers"][0]["image"]
    assert actual == "busybox:1.36", f"image: esperado busybox:1.36, obtido {actual}"


def test_replicas_mapping():
    docs = render({"image": "nginx:1.28", "replicas": 4})
    actual = deployment(docs)["spec"]["replicas"]
    assert actual == 4, f"replicas: esperado 4, obtido {actual}"


def test_namespace_mapping():
    docs = render({"image": "nginx:1.28"}, namespace="webapps-dev")
    for doc in docs:
        actual = doc["metadata"].get("namespace")
        assert actual == "webapps-dev", (
            f"namespace de {doc['kind']}: esperado webapps-dev, obtido {actual}"
        )


def test_service_port_mapping():
    docs = render({"image": "nginx:1.28", "servicePort": 9090})
    actual = service(docs)["spec"]["ports"][0]["port"]
    assert actual == 9090, f"servicePort: esperado 9090, obtido {actual}"


def test_name_mapping():
    docs = render({"image": "nginx:1.28"}, name="widget-api")
    dep = deployment(docs)
    svc = service(docs)
    assert dep["metadata"]["name"] == "widget-api"
    assert svc["metadata"]["name"] == "widget-api"
    assert dep["spec"]["selector"]["matchLabels"]["app"] == "widget-api"
    assert dep["spec"]["template"]["metadata"]["labels"]["app"] == "widget-api"
    assert svc["spec"]["selector"]["app"] == "widget-api"


def test_container_port_mapping():
    docs = render({"image": "nginx:1.28", "containerPort": 5000})
    container = deployment(docs)["spec"]["template"]["spec"]["containers"][0]
    assert container["ports"][0]["containerPort"] == 5000
    assert container["readinessProbe"]["httpGet"]["port"] == 5000
    assert service(docs)["spec"]["ports"][0]["targetPort"] == 5000


def test_health_check_path_mapping():
    docs = render({"image": "nginx:1.28", "healthCheckPath": "/healthz"})
    probe = deployment(docs)["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]
    assert probe["httpGet"]["path"] == "/healthz"


def test_configmap_generated_with_name_and_data():
    docs = render(
        {"image": "nginx:1.28", "config": {"APP_NAME": "foo", "LOG_LEVEL": "debug"}},
        name="cfg-webapp",
    )
    cm = configmap(docs)
    assert cm["metadata"]["name"] == "cfg-webapp"
    assert cm["data"] == {"APP_NAME": "foo", "LOG_LEVEL": "debug"}


def test_configmap_empty_when_config_omitted():
    docs = render({"image": "nginx:1.28"})
    cm = configmap(docs)
    # Com config default {}, o ConfigMap existe sem dados.
    assert cm["data"] in (None, {}), f"ConfigMap data inesperado: {cm.get('data')}"


def test_container_envfrom_points_to_configmap():
    docs = render({"image": "nginx:1.28"}, name="env-webapp")
    container = deployment(docs)["spec"]["template"]["spec"]["containers"][0]
    sources = container.get("envFrom", [])
    assert any(
        s.get("configMapRef", {}).get("name") == "env-webapp" for s in sources
    ), f"envFrom nao referencia o ConfigMap env-webapp: {sources}"


TESTS = [
    test_profile_small,
    test_profile_medium,
    test_profile_large,
    test_no_size_uses_defaults,
    test_no_size_with_explicit_limits,
    test_size_takes_precedence_over_limits,
    test_invalid_profile_rejected,
    test_image_mapping,
    test_replicas_mapping,
    test_namespace_mapping,
    test_service_port_mapping,
    test_name_mapping,
    test_container_port_mapping,
    test_health_check_path_mapping,
    test_configmap_generated_with_name_and_data,
    test_configmap_empty_when_config_omitted,
    test_container_envfrom_points_to_configmap,
]


def main():
    failures = []
    for test in TESTS:
        try:
            test()
            print(f"OK:   {test.__name__}")
        except Exception as exc:
            failures.append((test.__name__, exc))
            print(f"FAIL: {test.__name__}: {exc}")
            traceback.print_exc(file=sys.stdout)

    print(f"\nTotal: {len(TESTS)} | Falhas: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
