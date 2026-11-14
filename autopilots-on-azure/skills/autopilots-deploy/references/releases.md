# Immutable releases and standalone packages

A release JSON has exactly `schema = 1`, a full lowercase `source_commit`
and `images` containing exactly runtime, gateway and simulator GHCR references
with `@sha256:<64 lowercase hex>`. No tags, secrets or extra keys are allowed.
Use [Read-ImageRelease](../scripts/Read-ImageRelease.ps1) before provisioning.
[image-release.example.json](../assets/image-release.example.json) documents
the shape only; its placeholders must fail validation.

## Updating pins

Obtain all three digests from a successful image workflow for one reviewed
source commit. Verify anonymous access to those public GHCR digests. Record
the workflow provenance separately; do not assume the latest main commit has
built images. Write the exact workflow source commit in the release JSON and
use matching deployment source. Do not reuse old digests with a new commit
label. A release manifest is necessary provenance, not proof of functional
acceptance.

```powershell
.\scripts\Read-ImageRelease.ps1 -Path 'D:\customer-package\image-release.json' -Commit '<full-reviewed-source-commit>'
```

The deployer independently compares release `source_commit` to Git HEAD or,
in git-free snapshots, `deployment-manifest.json`. Documentation-only skill
updates may come from a later public commit than image/deployer source, but
the actual deployment source must still match the images. New deployer
capabilities require a real corresponding source/image release.

## Package builder contract

The skill can be copied into a customer ZIP without dependencies outside its
folder. The **builder**, not this skill, must supply:

* Root `manifest.json`: `entryPoint` is
  `.agents/skills/autopilots-deploy/SKILL.md`, `source.commit` is the source pin
  and `files` lists every non-manifest file as `{path, sha256}`.
* `assets/source.lock.json` inside the installed skill: `commit`, `archive`,
  `sha256`, `sourcePath = "autopilots-on-azure"` and
  `imageRelease = {path, sha256}`. Paths are relative to the package root.
* Source archive rooted at `autopilots-on-azure/`, preserving `uv.lock`,
  Terraform lockfiles and `deployment-manifest.json` with `source_commit`.
* Real root `image-release.json` matching the source lock and source manifest.

No Terraform state, credentials, generated env files, live receipts or customer
secrets belong in a public package. Validate all hashes before extraction.
[Initialize-Workspace](../scripts/Initialize-Workspace.ps1) refuses traversal,
links, hash mismatches and an existing source directory; it extracts matching
source outside the package and creates private input templates without
overwriting existing configuration.
