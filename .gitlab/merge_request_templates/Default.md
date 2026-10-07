<!--
Default merge-request template. Keep the Testing section as prose describing
what you actually ran, never a checklist of intentions.
-->

## Summary

<!-- One or two sentences: what this MR does and why. -->

## Changes

<!-- Bullet the notable changes. Group by area (ansible / kubernetes / terraform / scripts / CI / docs). -->

-

## Testing done

<!--
Describe the verification you performed, in prose. For example:
"`task lint` clean; `task flux:lint` validated the Flux corpus; ran the
playbook with `--check` against one host and the diff was empty."
-->

## Deploy notes

<!--
Anything the operator or reviewer must know before this reaches the cluster:
new 1Password items to create in the Homelab vault, Flux-side wiring a new
Kustomization needs (both substituteFrom ConfigMaps, a dependsOn edge, a
netpol-baseline default-deny), a host that must be deployed with Ansible
before Flux reconciles, a weisssrv.infra pin bump that needs a re-vendor and
the four Terraform ?ref= pins by hand, a supervised Terraform apply, or how
to roll back. "None" is a valid answer.
-->
