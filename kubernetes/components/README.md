# Kustomize components

Reusable fragments included by name from an app or platform `kustomization.yaml`
(`components:`). A component is applied after the including kustomization's own
resources, so the `namespace:` transformer there is what places the resources a
component ships — a kustomization with no top-level namespace patches it in
instead (see `apps/gitlab-runner-reaper/kustomization.yaml`).

| Component | Ships | Included by |
|---|---|---|
| `netpol-baseline` | `default-deny-ingress` (podSelector `{}`) | every namespace except the two documented exceptions below |
| `netpol-egress-dns` | `allow-egress-dns` — kube-dns on 53/udp+tcp | namespaces whose kube-dns egress is namespace-wide |
| `netpol-egress-apiserver` | `allow-egress-apiserver` — the three k3s server IPs on 6443 | controllers that watch/patch cluster state |
| `netpol-egress-public` | `allow-egress-public` — 0.0.0.0/0 on 443 minus the RFC-reserved except-list | namespaces whose only outbound path is public HTTPS |
| `gitlab-runner-common` | the HelmRelease config both runner releases share | `apps/gitlab-runner`, `apps/gitlab-runner-privileged` |

## Why some egress rules are still copy-pasted

The three egress components each ship a **whole policy selecting the whole
namespace** (`podSelector: {}`). That is the only shape a component can take —
kustomize has no mechanism to share a rule *inside* another policy — and it
carries a hazard: a policy selecting every pod grants the rule to every pod.

So a recurring egress rule is converted only where the policy it came from
already selected the whole namespace. Where the rule lives inside an app-scoped
policy (`allow-egress-authentik`, the runner `*-egress` pair, the four `recipes`
policies, `allow-egress-onepassword-connect`, …) it stays inline: replacing it
with a namespace-wide component would hand the same egress to every other pod in
that namespace, including the ones the current policy deliberately denies.

`scripts/check-netpol-except-parity.py` (config: `scripts/netpol-except.yaml`) is
what keeps the remaining copies honest. It is deliberately more than a
"nobody declares their own /0 except-list" check — its rule-shaped arms catch the
edits that re-open the LAN without touching an except-list at all (a rule with no
`to:`, a `0.0.0.0/1` + `128.0.0.0/1` split, a bare `192.168.0.0/16` peer) and it
owns a second canonical list, `lan-fence`, that no component ships.

## Ingress default-deny exceptions

`netpol-baseline` is mandatory in every namespace except two, and both ship
their own equivalent — `downloads` (its local default-deny covers ingress *and*
egress) and `flux-system` (upstream gotk manifests).

`kube-system` is included via
`kubernetes/infrastructure/configs/kube-system-policies/`, which is also where
its complete allow set lives (CoreDNS :53 and :9153, metrics-server :10250,
kured :8080). That kustomization is the only place kube-system policies belong:
the deny is only safe while the allow set is complete, so the two must be
reviewed together and land in one reconcile.

`docs/29-flux-operations.md` § Network policy exceptions is the canonical list
and carries the full rationale — a *third* unfenced namespace is a bug, not a
precedent.
