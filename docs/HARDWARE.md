# Hardware

What Deep32 needs, part by part. Except for the Workstation guided-install disk minimum below, the figures
come from `install/matrix.json`, the file the inventory program and the installer both read. Run
`deep32-inventory` (see [INSTALL.md](../INSTALL.md#1-check-your-machine)) and it tells you which of these rows
your machine meets.

> **Status, 14 November 2026: pre-release.** Nothing below has been tested as a Deep32 install yet. "Yes"
> means the part is designed for that hardware and is expected to work; "experimental" means expected and
> untested even after release. Where a row rests on something already proven, it says so. Only the Workstation
> guided-install disk minimum below is fixed; the other disk figures remain placeholders.


## Short answer

| | Minimum | Recommended |
|---|---|---|
| Workstation alone (keyword search only) | 64-bit Intel or AMD with virtualisation and SLAT; 6 threads and 12 GB on the host; Disk: 160 GB minimum for the guided install (the guided layout reserves room for two system update slots, the Library content and swap) | As below |
| Workstation and Forge on one machine | 8 threads, 32 GB; an NVIDIA card of compute capability 7.5 or newer with 6 GB; Disk: 160 GB minimum for the guided install (the guided layout reserves room for two system update slots, the Library content and swap); Forge space is additional | 12 or more threads and 32 GB or more (16 threads and 64 GB leave room); an AVX2 processor; an NVIDIA RTX 20 series or newer card with 11 GB or more; a solid-state disk |
| Forge alone | 4 threads and 16 GB on the host; a card as above; 100 GB free | As above |
| Headend | Any PC that runs Claude Code or Codex | Windows for the Ask window's relay in version 1.0 |

The Workstation has no model of its own: meaning search and reranking need a Forge, on the same machine or
another. Without one, search works on keywords only.


## Processor

| Processor | Workstation | Forge | Notes |
|---|---|---|---|
| Intel 4th generation Core and later, Xeon E5 v3 and later, AMD Ryzen and EPYC (AVX2) | Yes | Yes | The safe class for everything |
| Intel 12th generation and later with fast and efficient cores | Yes | Yes | VMs are sized by the fast cores only |
| Intel 2nd and 3rd generation Core, Xeon E5 v1 and v2 (AVX, no AVX2) | Yes | To be tested | Proven on this class in daily use: the search engine, the gateway, the Qdrant vector store and PyTorch's processor build. Not on this class: the MinerU part of the PDF converter, whose llama.cpp needs x86-64-v3 (AVX2, FMA). Not yet tested: the embeddings server's processor image and the simpler PyMuPDF converter |
| Any other 64-bit Intel or AMD without AVX | Yes, untested | To be tested | The Workstation's programs are built for the plain 64-bit baseline, so no newer instruction set is needed |
| Low-power chips (Atom, Celeron, Pentium Silver class) | Not supported | No | A speed rule: the Workstation runs the engine, the gateway, the desktop and the Library in one VM |
| ARM (Apple Silicon, Snapdragon and others) | No | No | Headend only |

Any machine that hosts a VM needs virtualisation (Intel VT-x or AMD-V) switched on in the firmware, with
second-level address translation (Intel EPT or AMD RVI). GPU passthrough also needs the IOMMU (Intel VT-d or
AMD-Vi) switched on in the firmware and the kernel.

On Proxmox VE the default vCPU type (`x86-64-v2-AES`) hides AVX from the guest. The installer gives the Forge VM
CPU type `host` (or `x86-64-v3`). (planned)


## Threads and memory

The inventory works out these sizes today, and the installer will size the VMs by them (planned). Two threads
and 4 GB always stay with the host.

| Host | What fits | Workstation VM | Forge |
|---|---|---|---|
| Under 8 threads or under 32 GB | The Workstation alone, or a headend only | 4 threads, 8 GB | None |
| 8 to 11 threads, 32 GB or more | Both, tight | 4 threads, 8 to 12 GB | 2 to 4 threads, 12 to 16 GB |
| 12 to 15 threads, 32 GB or more | Both, comfortable | 4 to 6 threads, 12 to 16 GB | 4 threads, 16 GB |
| 16 or more threads, 64 GB or more | Both with room | 8 threads, 16 GB | 6 to 8 threads, 16 to 32 GB |

Threads are counted on the fast cores only when a processor has fast and efficient cores. Memory is the
installed memory in GB.


## Disk

| Part | Disk space |
|---|---|
| Workstation | Disk: 160 GB minimum for the guided install (the guided layout reserves room for two system update slots, the Library content and swap) |
| Forge | 100 GB |
| Both on one machine | Disk: 160 GB minimum for the guided install (the guided layout reserves room for two system update slots, the Library content and swap); Forge space is additional |

The Workstation guided-install minimum is fixed; the Forge figure remains a placeholder until the discs are
built and measured. Solid-state storage is strongly recommended.


## Graphics card

CUDA means NVIDIA. The Forge's embeddings server is Text Embeddings Inference (TEI) 1.9, which publishes one
image per card generation. The inventory reads your card and names the matching image; the installer will
use it (planned).

| Card class | Compute capability | TEI image | TEI's own label | Deep32 Forge |
|---|---|---|---|---|
| RTX 20 series, Titan RTX, Quadro RTX, T4 | 7.5 | `turing-1.9` | Experimental | Yes: the class the reference system runs |
| GTX 16 series, Quadro T series | 7.5 | `turing-1.9` | Experimental | Experimental |
| RTX 30 series, RTX A series | 8.6 | `86-1.9` | Stable | Yes |
| A10, A40, A16, A2 | 8.6 | `86-1.9` | Stable | Experimental |
| A100, A30 | 8.0 | `1.9` | Stable | Experimental |
| RTX 40 series | 8.9 | `89-1.9` | Stable | Yes |
| L4, L40 | 8.9 | `89-1.9` | Stable | Experimental |
| H100, H200 | 9.0 | `hopper-1.9` | Stable | Experimental |
| B200, GB200 | 10.0 | `100-1.9` | Experimental | Experimental |
| RTX 50 series | 12.0 | `120-1.9` | Experimental | Experimental |
| Volta (V100, Titan V), GTX 10 series and older | Under 7.5 | None | Not supported | No: the processor path is used |
| AMD or Intel graphics | | | | No GPU path: the processor path is used |

Notes:

- TEI marks its whole Turing image experimental upstream, the RTX 20 class included. Deep32's reference system
  runs that image on an RTX 20 card.
- On GTX 16 cards the reranker's half-precision mode can produce invalid scores. The Forge checks for this and
  falls back to full precision. (planned)
- Data-centre cards are marked experimental here because no test has been run on one yet.

**Driver.** The reranker's CUDA 13.0 build needs NVIDIA driver branch R580 or newer, on every card. On WSL2
the Windows driver is the only driver: nothing is installed as a Linux display driver inside WSL. A booted
disc 3 carries a driver matched to its own kernel.

**Card memory.**

| Card memory | What runs |
|---|---|
| 11 GB or more | Both models; the PDF converter one job at a time, pausing both models while it runs |
| 8 GB | Serving with both models is expected to fit (to be measured); a full knowledge-base build is unproven |
| 6 GB | Embeddings only; reranking off or on the processor |
| Less, or no card | Keyword search only, unless a Forge in processor mode embeds the queries (TEI's processor image, untested). Building a knowledge base on the processor is slow |

A locally served language model (after version 1.0) needs a card of its own. The Forge's card is sized for
embeddings and reranking only.


## Hosts and guests

| Software | Target |
|---|---|
| Windows 11 Pro, Enterprise or Education with Hyper-V and WSL2 | Reference |
| Proxmox VE with GPU passthrough | Reference |
| Linux with KVM and Docker | Supported |
| Windows 11 Home, VirtualBox, VMware Workstation, VMware ESXi | Experimental |
| macOS | Headend only |
| Headend agents | Claude Code and Codex |

Notes for the Workstation, which runs OpenBSD 8.0:

- On Hyper-V the Workstation VM is Generation 1. OpenBSD does not support Generation 2.
- On Hyper-V the desktop draws through the VESA display driver, which needs one kernel setting
  (`machdep.allowaperture`); the installer sets it. (planned)
- On KVM and Proxmox, UEFI firmware (OVMF) with its framebuffer is the likely display path. Untested.
- Never give the Workstation a virtio-gpu display: OpenBSD 8.0 has no driver for it.
- For a real install on a laptop, the tested hardware profile is the Dell Latitude 5430. Other laptops run the
  Workstation as a VM.

Notes for the Forge, which runs Ubuntu 24.04:

- A normal Hyper-V VM cannot be given the graphics card. On Windows the Forge runs in WSL2, and its network
  rides on the Windows host's Tailscale node.
- On Proxmox and KVM the card is passed through to the Forge VM, and the host loses it while that VM runs.


## Network

Nodes on one machine or one network should reach each other directly. Through Tailscale's relays every search
slows by tens of milliseconds. `deep32-install verify` checks the path (planned). Tailscale needs outbound
UDP for direct paths; with UDP blocked it falls back to its relays over HTTPS.
