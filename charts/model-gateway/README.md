# model-gateway

The single seam every model call passes through.

The assistant does not name models. It asks for `dss-intent`, `dss-moderation`,
`dss-planner`, `dss-composer` — four names — and this chart decides what each one
resolves to. Changing a model is a change to `models` in a values file: a
reviewed diff, applied by a Job, with nothing restarted.

Implemented with LiteLLM. PostgreSQL holds the vendor accounts, the model list
and the spending history.

## How a change reaches the gateway

```
edit values          → PR → review → merge
       ↓
helm upgrade / Argo sync
       ↓
ConfigMaps and Deployment applied
       ↓
post-upgrade Job reads the model list and calls the gateway's API
       ↓
the next answer uses the new model
```

Nothing restarts. Not the gateway, not the assistant. The Job talks to the
running gateway over HTTP and the router picks the change up immediately.

Two things follow from that, and both are deliberate:

- **The Job runs on every sync**, changed or not. Reconciling is idempotent, and
  a Job that only ran "when something changed" would need something to decide
  that — which is the part that goes wrong.
- **Editing the ConfigMap by hand does nothing.** Kubernetes does not watch it.
  Only a Helm or Argo sync runs the Job. That closes the same door as turning
  the admin screen off.

## What this chart will not let you do

The render fails, naming the value, rather than applying something that fails
quietly later:

| If | Why it fails |
|---|---|
| `saltKeySecret.name` is unset | it encrypts every vendor key; without it they are unreadable later |
| a model has no price | an unpriced model is recorded as free, and a spend limit over free never stops anything |
| `replicaCount > 1` and no `redis.host` | spend and rate limits are counted per process, so two replicas allow twice the limit |
| `models` is empty | the assistant would ask for a name the gateway does not have, and every turn would fail |

## Secrets

This chart renders no Secret, in line with the repository's rules. It names
Secrets that already exist:

| Secret | Holds |
|---|---|
| `database.urlSecret` | the PostgreSQL DSN — CNPG writes `uri` into `Secret/<cluster>-app` |
| `masterKeySecret` | the gateway's own admin credential |
| `saltKeySecret` | the key that encrypts vendor keys in the database |
| `credentials[].secretName` | one per vendor account |

**The salt key is the one to be careful with.** Create it once, keep it with the
other secrets, and never rotate it. Rotating it does not fail loudly: everything
saved earlier becomes unreadable, the gateway sends nonsense to the vendor, and
the vendor replies that the key is invalid. Losing it means re-entering every
vendor key.

## Vendor accounts and models

A credential is per vendor **account**, not per model. Every model on that
account shares it. A second credential is only needed when a second vendor
arrives.

```yaml
credentials:
  - name: azure-oan
    secretName: model-gateway-azure
    keys:
      api_key: AZURE_OPENAI_API_KEY
    values:
      api_base: https://<resource>.services.ai.azure.com/openai/v1

models:
  - name: dss-composer
    model: openai/<deployment>       # the vendor is the prefix
    credential: azure-oan
    inputCostPerToken: 0.00000015
    outputCostPerToken: 0.00000060
```

`model` carries the vendor as its prefix: `openai/`, `anthropic/`, `gemini/`,
`vertex_ai/`, `bedrock/`, `hosted_vllm/`. On Azure the part after `openai/` is
the **deployment** name, not a model family — the endpoint only answers for
deployments that exist.

Give two entries the same `name` and add `weight` to split traffic between them.

## Tracing

Point `otel.endpoint` at a collector that drops this gateway's internal spans,
not straight at the trace store. It records six to eight spans per model call
plus one per database write: unfiltered, one turn went from 22 recorded spans to
71, with database writes appearing under the step that writes the farmer's
answer.

## Install

```bash
helm dependency update charts/model-gateway
helm upgrade --install model-gateway charts/model-gateway \
  -f charts/model-gateway/examples/model-gateway.prod.yaml
helm test model-gateway
```

The assistant is then pointed at it with three values of its own: the four model
names, `OPENAI_BASE_URL` set to `http://model-gateway:4000/v1`, and a virtual key.
