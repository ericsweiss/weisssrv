# Local patches

`build-hermes-agent` applies every `*.patch` here to the SHA-verified upstream
tree before the image build (`git apply -p1`, loud failure on drift). **No
patches are carried right now.**

## Convention

- Name files `NNNN-short-subject.patch`, numbered in apply order.
- Open each patch with comment lines (`#`) stating what it changes, why upstream
  needs it, and the exact `hermes_version` the hunks were verified against.
- Produce the diff against a clone of `NousResearch/hermes-agent` at the pinned
  tag, so paths are repo-relative and `-p1` applies them.
- Bump the `-rN` in `hermes_image_version` whenever a patch changes, or the
  nodes keep serving the cached image.

## On every `hermes_version` bump

Re-verify every patch against the new tag. Drop any whose change landed
upstream; rebase the rest. A patch whose target file moved or vanished fails the
build job, not lint, so check before merging the bump.

Only files inside the Hermes repo can be patched this way. Plugins that Hermes
installs at runtime from its plugin catalog live on the data volume, outside the
image, and cannot be reached from here.
