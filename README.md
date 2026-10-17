# gitops-lab

Laboratório local de GitOps e Platform Engineering com Crossplane. Expõe uma API customizada `WebApp` que, por trás, materializa um `Deployment`, um `Service` e um `ConfigMap`. Argo CD acompanha a branch `main` e aplica tudo no cluster.

Rodando em cluster `kind` sobre Rancher Desktop, sem AWS ou outras dependências de nuvem.

## Arquitetura

```
   Git (main)
       │
       ▼
   Argo CD        ── observa manifestos em bootstrap/, apps/* e platform/
       │
       ▼
   Crossplane     ── reconcilia WebApp (XR) via Composition webapp-local
       │
       ├──► Deployment
       ├──► Service
       └──► ConfigMap
```

### Responsabilidades

| Componente | Papel |
|---|---|
| **Argo CD** | Lê o Git, aplica manifestos, mantém sincronização (`automated` + `selfHeal`) e detecta drift |
| **Crossplane** | Reconcilia a XR `WebApp` usando a `Composition webapp-local` + CompositionRevisions |
| **function-patch-and-transform** | Motor da Composition: aplica os `patches` que mapeiam campos da `WebApp` para `Deployment`/`Service`/`ConfigMap` |
| **kind + Rancher Desktop** | Fornece o cluster local (um nó, control-plane) |

## Versões

| Software | Versão |
|---|---|
| Crossplane | 2.4.2 |
| Argo CD | 3.5.5 |
| function-patch-and-transform | 0.8.2 |
| Python (CI) | 3.12 |
| yamllint | 1.35.1 |
| jsonschema | 4.23.0 |
| kubeconform | 0.7.0 |
| crossplane CLI | 2.5.0 (com `--crossplane-version=v2.4.2`) |

## Estrutura

```
.
├── apps/
│   ├── hello/                  Deployment + Service nativos (primeiro exercício)
│   ├── webapps/                WebApps iniciais (minha-webapp, segunda-webapp)
│   ├── dev/                    Ambiente dev: webapp-dev + api-webapp
│   └── prod/                   Ambiente prod simulado: webapp-prod (pinned)
├── platform/
│   └── webapp/                 XRD, Composition, Function, RBAC
├── bootstrap/                  Argo CD Applications (hello, platform, webapps, webapps-dev, webapps-prod)
├── scripts/
│   ├── check_yaml.py           yamllint + normalização de fim de linha
│   ├── validate_webapps.py     schema + render + kubeconform (CI)
│   ├── test_profiles.py        12 + 5 testes de perfis e mapeamentos (CI)
│   └── diagnose.py             diagnóstico rápido do cluster
├── .github/workflows/
│   └── validate.yaml           CI
├── .yamllint.yaml
└── .gitignore
```

## Instalação

### Pré-requisitos

- macOS com **Rancher Desktop** (ou Docker Desktop) — provê `docker` e `kind`
- `kubectl` apontando para o contexto `kind-gitops-lab`
- Python 3.12+ e `jsonschema`, `PyYAML`, `yamllint` para rodar os scripts localmente
- `crossplane` CLI 2.5.x e `kubeconform` 0.7.x para o `validate_webapps.py` local

### Setup do cluster

1. Criar o cluster kind com os limites de inotify corretos (a VM do Rancher Desktop vem com `fs.inotify.max_user_instances=128`, o que derruba o `kube-proxy`):
   ```bash
   kind-up --name gitops-lab        # ou kind-up fix-only gitops-lab em cluster existente
   ```
2. Instalar Argo CD no namespace `argocd` (manifestos ou Helm, fora do escopo deste repo).
3. Instalar Crossplane 2.4.2 no namespace `crossplane-system`.
4. Aplicar os bootstrap Applications:
   ```bash
   kubectl apply -f bootstrap/
   ```
   A partir daí Argo CD toma conta de tudo.

### Setup local (opcional, para rodar scripts)

```bash
python3 -m venv .venv
.venv/bin/pip install yamllint==1.35.1 PyYAML==6.0.2 jsonschema==4.23.0
# Rancher Desktop: aponte o DOCKER_HOST para o socket certo
export DOCKER_HOST=unix://$HOME/.rd/docker.sock
```

## Contrato da WebApp

### Campos (`spec`)

| Campo | Tipo | Default | Validação |
|---|---|---|---|
| `image` | string | — | obrigatório |
| `replicas` | integer | 1 | 1–5 |
| `servicePort` | integer | 80 | 1–65535 |
| `containerPort` | integer | 80 | 1–65535 |
| `healthCheckPath` | string | `/` | — |
| `cpuLimit` | string | `250m` | enum: `250m`, `500m`, `1000m` |
| `memoryLimit` | string | `128Mi` | enum: `128Mi`, `256Mi`, `512Mi` |
| `size` | string | — | enum: `small`, `medium`, `large` |
| `config` | map[string]string | `{}` | injetado como env vars via ConfigMap |
| `crossplane.compositionRef.name` | string | — | obrigatório, deve ser `webapp-local` |
| `crossplane.compositionUpdatePolicy` | string | `Automatic` | enum: `Automatic`, `Manual` |
| `crossplane.compositionRevisionRef.name` | string | — | para pinning manual |

### Perfis (`size`)

| Perfil | req.cpu | req.mem | lim.cpu | lim.mem |
|---|---|---|---|---|
| `small`  | 50m  | 64Mi  | 250m  | 128Mi |
| `medium` | 100m | 128Mi | 500m  | 256Mi |
| `large`  | 250m | 256Mi | 1000m | 512Mi |

**Sem `size`:** `requests` vêm do base da Composition (50m / 64Mi); `limits` vêm de `cpuLimit`/`memoryLimit` (default 250m / 128Mi).
**Com `size`:** sobrepõe `cpuLimit`/`memoryLimit` para `requests` e `limits`.

## Fluxo de contribuição

1. `git switch main && git pull`
2. `git switch -c feat/<descricao>`
3. Editar
4. Validar localmente:
   ```bash
   .venv/bin/python scripts/check_yaml.py
   .venv/bin/python scripts/test_profiles.py        # requer Docker socket
   .venv/bin/python scripts/validate_webapps.py     # requer crossplane CLI + kubeconform
   ```
5. `git commit && git push`
6. Abrir PR → aguardar check `validate` passar → merge

### CI (`.github/workflows/validate.yaml`)

Em todo PR e push para `main`:

| Passo | Comando |
|---|---|
| yamllint | `yamllint -c .yamllint.yaml .github/workflows bootstrap platform apps .yamllint.yaml` |
| kubeconform nativos | `kubeconform -strict -summary apps/hello/ platform/webapp/rbac.yaml` |
| Validar WebApps | `python scripts/validate_webapps.py` (schema + render + kubeconform) |
| Testes de perfis | `python scripts/test_profiles.py` (17 testes) |

### Proteção da `main`

Ruleset no GitHub exige:
- PR obrigatório
- Status check `validate` precisa estar verde

## Operação

### Diagnóstico rápido

```bash
.venv/bin/python scripts/diagnose.py
```

Varre em 5 camadas: Argo Applications, WebApps, Functions do Crossplane, Deployments e eventos Warning. Para cada problema aponta sintoma + próximo comando. Exit code != 0 quando há problemas.

### Promoção dev → prod

```
apps/dev/<webapp>.yaml  ─PR→  main  ─Argo→  dev atualizado
                                       │
                                       └── prod intacto (pinned ou não é apps/prod)

apps/prod/<webapp>.yaml ─PR→  main  ─Argo→  prod atualizado
```

Preferir imagens com tag/digest identificável (`nginx:1.29`, `httpbin@sha256:...`).

### Rollback

- **Rollback de workload** (imagem errada em prod): `git revert <sha-da-promoção>`. Argo CD propaga.
- **Rollback de Composition** (nova revisão quebrada): editar `apps/prod/<webapp>.yaml` apontando `compositionRevisionRef.name` para uma revisão anterior. Listar com `kubectl get compositionrevisions`.

## Troubleshooting

| Sintoma | Causa provável | Primeiros passos |
|---|---|---|
| `kube-proxy` em `CrashLoopBackOff` | `fs.inotify.max_user_instances` baixo | `kind-up fix-only gitops-lab` |
| WebApp `Ready=False` com `Unready resources: config` | ConfigMap sem `readinessChecks: - type: None` | Checar Composition |
| Argo `OutOfSync` persistente com `prune: false` | Manifesto removido do Git — comportamento esperado | `kubectl delete webapp <name>` para exclusão controlada |
| Pods em `ImagePullBackOff` | Imagem/tag inexistente | `kubectl describe pod` + `scripts/diagnose.py` |
| API server com `TLS handshake timeout` | Cluster sobrecarregado | `docker exec gitops-lab-control-plane uptime` → ver load; reduzir workloads ou aumentar VM |
| CompositionRevision em uso incorreta | WebApp com `Automatic` pegou a `latest` | Confirmar `compositionRevisionRef` em prod |

## Exclusão e ciclo de vida

A XR `WebApp` tem o finalizer `composite.apiextensions.crossplane.io`. `Deployment`, `Service` e `ConfigMap` carregam `ownerReferences` apontando para a XR (`controller=true`, `blockOwnerDeletion=true`).

**Com `prune: false` (padrão em todos os Argo Apps deste repo):**
- Remover manifesto do Git **não** deleta nada no cluster — só vira `OutOfSync`.
- Exclusão correta: `kubectl delete webapp <nome> -n <ns>` → finalizer libera cascade de ConfigMap/Deployment/Service.

**Nunca habilitar `prune: true` em:**
- `platform` (apagar a XRD removeria TODAS as WebApps)
- `webapps-prod` (deleção acidental de PR destrói produção simulada)

Em `webapps-dev` pode fazer sentido se o fluxo exigir, mas não é padrão.

## Limitações

- **Cluster único** — dev e prod coabitam com separação apenas por namespace (sem NetworkPolicy, sem ResourceQuota).
- **Sem observabilidade com Prometheus/Grafana** — `kube-prometheus-stack` tentado duas vezes e descartado:
  - 1ª tentativa (6 vCPUs, 10 GB): load average chegou a 33 (5.5× saturado), apiserver com restarts em cascata, revertido.
  - 2ª tentativa (10 vCPUs, 10 GB): load average chegou a **83 (8.3× saturado)**, pior que a 1ª apesar do bump de CPU, apiserver com Timeout em `kubectl`, revertido.
  - **Conclusão:** CPU não é o gargalo isolado; é a combinação de admission webhooks + múltiplos ServiceMonitors fazendo scrape imediato + prometheus-operator reconciliando CRDs, criando avalanche de requests ao apiserver num cluster single-node. Alternativas viáveis estão no Roadmap. Observabilidade vigente é via `diagnose.py` + condições/eventos/logs nativos.
- **Sem segredos reais** — `config` vai pra `ConfigMap`; nenhum `Secret` é gerado.
- **Função única** — apenas `function-patch-and-transform`; sem lógica customizada em Go/Python.
- **Sem imagem com digest** — todas as WebApps usam tags móveis, não digests.
- **Sem app-of-apps** — Applications em `bootstrap/` são aplicadas via `kubectl apply` manual.

## Roadmap

- NetworkPolicy e ResourceQuota em `webapps-dev`/`webapps-prod` para isolamento mínimo.
- Monitoring: evitar `kube-prometheus-stack` nesta topologia. Candidatos a tentar: Prometheus community sem operator (sem CRDs/webhooks), VictoriaMetrics (`vmsingle` + `vmagent`, ~100 MB RAM) ou `kube-prometheus-stack` com `prometheusOperator.enabled: false` + Prometheus "estático".
- Secrets: suporte a `Secret` via novo recurso na Composition.
- Testes HTTP automáticos: `scripts/smoke.py` ou job de Argo rodando curl.
- App-of-apps: um `bootstrap/root.yaml` que vigia o próprio `bootstrap/`.
- Promoção automática: workflow que, após merge em dev, abre PR correspondente para prod.
