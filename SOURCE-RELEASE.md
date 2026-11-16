# Source release 0.1.0-alpha.1 (alpha)

The source folders are src/engine, src/gateway, src/workstation, src/gpu-pack,
src/release-tools, src/build and src/kb-tools. The converter is separate under
src/gpu-pack/containers/converter-agpl; its code shares the project
AGPL-3.0-or-later licence and its source remains in that folder.

The engine API metadata and gateway version are 1.0.0. The original Python
package import names are retained. RELEASE.json records the public source
identity, model revisions, driver floors and knowledge-base compatibility.
Its final derived generation and fingerprint fields are deliberately pending
until the content build is complete.

This is project source, not a copy of the image contents. Build tools need
external operating-system sets, packages, toolchains, model files and licence
inputs at their pinned versions. Some build input registers retain explicit
unresolved fields until those image builds finish. No image rebuild or running
system verification is claimed by this source release. The release-day checks
remain listed in the staging report.

The source release keeps deterministic point identifiers and settings-format
markers used by the content and image builds. Private collection support,
personal defaults, update history and excluded content are cut by the source
export. Existing UI program sources are retained without a new design pass.

## Known issues in 0.1.0-alpha.1

- The guided install needs a disk of 160 GB or more; a compact layout comes in the beta.
- During the guided install, the optional question about a root maintenance SSH key prints
  "printf: not found" instead of its prompt. Press Enter to skip it (no maintenance key, the default). Entering
  a key there does not work in this build.
- If a previous install attempt left an OpenBSD partition on the target disk, the installer reuses that
  partition's size; wipe the disk first (from the installer's (S)hell: `bioctl -d` on the old softraid volume if
  one is attached, then `dd if=/dev/zero of=/dev/rsdNc bs=1m count=16`, then reboot).
