Deep32 GPU pack 1.0.0

One Docker form runs the same Linux containers on Linux Docker or Windows
Docker Desktop. This is a data disc, not an operating-system installer. It
contains offline images, models, licences, corresponding source and evidence.
Installation never pulls an image or model.

These instructions are for the next signed rebuild. The published alpha
media do not contain this portable Forge lifecycle or the optional full role.
Use PACK.md as the GPU pack install page for both hosts; FULL.md records the
full role's build, lifecycle and publication gates.

Linux needs the existing Docker Engine, Compose, an NVIDIA driver and the
NVIDIA Container Toolkit or qualified CDI setup, Python 3, OpenSSL and the
host's signed-in Tailscale client. No host systemd service is needed.

Windows needs Docker Desktop using Linux containers, its WSL 2 backend and
NVIDIA GPU support, a compatible Windows NVIDIA driver, native Python, OpenSSL and the
Windows host's signed-in Tailscale client. Start Docker Desktop first. Deep32
imports no separate distribution and installs no second Docker daemon.

Use the resolved release pins and measured storage envelope. Unresolved pins,
unknown prerequisites, inadequate space or failed GPU/model checks refuse.
Keep loopback ports 8081, 8082 and, for full, 8766 free.

Authenticate the release signature and verify SHA256SUMS. Mount signed disc 3
read-only on the GPU host and import matching disc 2 on the Workstation. Keep
the recorded GPU media path available for restart/resume. Forge is the default:
embeddings, reranker and authenticated door run here; converter and index-builder
jobs use separate Compose profiles. Content, live index, engine and gateway stay
on the Workstation. Linux's data root defaults to /opt/deep32;
Windows defaults to %LOCALAPPDATA%\Deep32. Change it with --data-root DIR.

From disc 3's root on Linux:

    sha256sum -c SHA256SUMS
    sudo bash forms/load-docker.sh "$PWD/deep32/payload" --settings /path/to/settings.json --data-root /opt/deep32

Windows uses src\release-tools\deep32-get.ps1 disc3 --install from the complete
source kit in an elevated console. PACK.md gives the exact PowerShell command.
J1, J2 and J4 use Forge with a Workstation; J5 has no GPU pack. J3 requires the
explicit --docker-role full choice, C7 profile headless and --content naming
signed disc 2 on this host. That opt-in adds the engine, gateway, importer and
live index without a Workstation. Both roles use the same Docker form, protected
settings staging and private data root. Both validate non-secret C7 settings;
use role forge, transport tailnet and publish serve-https.
Do not put credentials or pairing codes in settings, arguments or logs.

The loader supports start, stop, status, resume and upgrade. Docker restart
policies run without systemd. Stop preserves data and identity; resume honours
an intentional stop. Keep the role and data root unchanged for later actions.
Forge still needs the reviewed host publication/pairing adapter and Workstation
enrolment; its local-ready status does not establish remote pairing reachability. Full
needs its publication and headend enrolment handoff. Source fixtures do not
qualify an actual host or GPU.

Linux Forge retains the bounded local C8 listener and protected C6 pairing
console after authenticated model warm-up, including durable commit checks and
persisted lockout. Windows opens no code/window until its qualified protected
host-console adapter is supplied. Neither local result proves remote reachability.

Portable Forge conversion, recovery, build and bundle jobs use
forms/forge_jobs.py --data-root DIR on either host. PACK.md gives input paths,
arguments and recovery rules; INTERFACES.md retains the C3 signing gate.

The separate WSL-distribution archive, builder and provisioner are deprecated
pending the lead's decision and remain unchanged. Do not remove an existing
distribution. Docker Desktop's WSL 2 backend remains a Windows prerequisite.

Deep32's own code is under the Apache-2.0 license. Its license text is LICENSE
and its notice is NOTICE, both at the root of this pack. Both files are part of
the runtime kit, so forms/forge-docker.tar and every installation carry them,
and the door, reranker and builder images keep a copy beside their code. The
PDF converter is the exception in this pack. It uses PyMuPDF and MuPDF, which
are AGPL, so the converter is under AGPL-3.0-or-later. Its license text is
deep32/payload/licences/AGPL-3.0.txt. The converter also installs MinerU,
which is under the MinerU Open Source License: Apache-2.0 with additional terms.
A separate commercial license from the MinerU team is required above 100 million
monthly active users or USD 20 million in monthly revenue, and an online service
for third parties built on MinerU must say clearly that it uses MinerU. MinerU's
license text is licenses/MinerU-4.0.7-LICENSE.md, which is part of the runtime
kit too. Complete corresponding source is under deep32/payload/source,
including production-converter.
