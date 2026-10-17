#!/usr/bin/env python3
"""Diagnostico rapido dos problemas comuns no gitops-lab.

Para cada camada (do GitOps ao workload), reporta recursos que nao
estao saudaveis e sugere o proximo comando a executar.

Fluxo:
1. Argo CD Applications fora de Synced/Healthy
2. WebApps com Synced=False ou Ready=False
3. Functions do Crossplane com problema
4. Deployments com replicas abaixo do desejado
5. Eventos Warning recentes em namespaces relevantes

Sai com codigo != 0 se encontrou problemas.
"""

import json
import subprocess
import sys


NAMESPACES = ["webapps", "webapps-dev", "webapps-prod", "argocd", "crossplane-system"]
WORKLOAD_NAMESPACES = ["webapps", "webapps-dev", "webapps-prod"]
MAX_EVENTS_PER_NS = 5


def kubectl_json(*args):
    result = subprocess.run(
        ["kubectl", *args, "-o", "json"],
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def kubectl_text(*args):
    result = subprocess.run(
        ["kubectl", *args],
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout if result.returncode == 0 else ""


def check_argo_applications():
    issues = []
    data = kubectl_json("get", "applications", "-n", "argocd")
    if not data:
        return issues
    for app in data.get("items", []):
        name = app["metadata"]["name"]
        sync = app.get("status", {}).get("sync", {}).get("status", "Unknown")
        health = app.get("status", {}).get("health", {}).get("status", "Unknown")
        if sync != "Synced" or health != "Healthy":
            issues.append({
                "resource": f"Application {name}",
                "symptom": f"sync={sync}, health={health}",
                "next": f"kubectl describe application {name} -n argocd",
            })
    return issues


def check_webapps():
    issues = []
    data = kubectl_json("get", "webapps.platform.example.org", "-A")
    if not data:
        return issues
    for wa in data.get("items", []):
        ns = wa["metadata"]["namespace"]
        name = wa["metadata"]["name"]
        conds = {c["type"]: c for c in wa.get("status", {}).get("conditions", [])}
        synced = conds.get("Synced", {})
        ready = conds.get("Ready", {})
        bad = []
        if synced.get("status") != "True":
            bad.append(f"Synced={synced.get('status')} ({synced.get('reason', '-')})")
        if ready.get("status") != "True":
            bad.append(f"Ready={ready.get('status')} ({ready.get('reason', '-')})")
        if bad:
            issues.append({
                "resource": f"WebApp {ns}/{name}",
                "symptom": "; ".join(bad),
                "next": f"kubectl describe webapp {name} -n {ns}",
            })
    return issues


def check_functions():
    issues = []
    data = kubectl_json("get", "functions.pkg.crossplane.io")
    if not data:
        return issues
    for fn in data.get("items", []):
        name = fn["metadata"]["name"]
        conds = {c["type"]: c for c in fn.get("status", {}).get("conditions", [])}
        healthy = conds.get("Healthy", {})
        installed = conds.get("Installed", {})
        if healthy.get("status") != "True" or installed.get("status") != "True":
            issues.append({
                "resource": f"Function {name}",
                "symptom": (
                    f"installed={installed.get('status')} "
                    f"({installed.get('reason', '-')}), "
                    f"healthy={healthy.get('status')} "
                    f"({healthy.get('reason', '-')})"
                ),
                "next": f"kubectl describe function {name}",
            })
    return issues


def check_deployments():
    issues = []
    for ns in WORKLOAD_NAMESPACES:
        data = kubectl_json("get", "deployments", "-n", ns)
        if not data:
            continue
        for dep in data.get("items", []):
            name = dep["metadata"]["name"]
            desired = dep["spec"].get("replicas", 0)
            available = dep.get("status", {}).get("availableReplicas", 0) or 0
            if available < desired:
                issues.append({
                    "resource": f"Deployment {ns}/{name}",
                    "symptom": f"available={available}/{desired}",
                    "next": (
                        f"kubectl describe deployment {name} -n {ns}; "
                        f"kubectl get pods -n {ns} -l app={name} -o wide"
                    ),
                })
    return issues


def check_warning_events():
    issues = []
    for ns in NAMESPACES:
        text = kubectl_text(
            "get", "events", "-n", ns,
            "--field-selector", "type=Warning",
            "--sort-by=.lastTimestamp",
            "-o", "custom-columns=TIME:.lastTimestamp,REASON:.reason,"
            "OBJECT:.involvedObject.name,MESSAGE:.message",
            "--no-headers",
        )
        lines = [line for line in text.strip().split("\n") if line.strip()]
        for line in lines[-MAX_EVENTS_PER_NS:]:
            issues.append({
                "resource": f"Event ({ns})",
                "symptom": line.strip(),
                "next": f"kubectl get events -n {ns} --sort-by=.lastTimestamp",
            })
    return issues


def main():
    sections = [
        ("Argo Applications", check_argo_applications),
        ("WebApps", check_webapps),
        ("Functions do Crossplane", check_functions),
        ("Deployments (workload)", check_deployments),
        ("Eventos Warning recentes", check_warning_events),
    ]
    total = 0
    for title, fn in sections:
        print(f"\n== {title} ==")
        issues = fn()
        if not issues:
            print("  OK")
        else:
            for issue in issues:
                print(f"  - {issue['resource']}: {issue['symptom']}")
                print(f"      -> {issue['next']}")
            total += len(issues)
    print(f"\nResumo: {total} problema(s) encontrado(s).")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
