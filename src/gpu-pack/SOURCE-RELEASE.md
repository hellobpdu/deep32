# Source release 1.0.0

This tree contains the GPU pack's programs, build recipes and configuration
templates. The supported install design is one Linux Docker form on Linux
Docker or Windows Docker Desktop's Linux containers, with no host systemd
dependency. See PACK.md for both hosts and FULL.md for the full role. Downloaded images, upstream packages, source documents, model weights
and evaluation data are separate inputs. pins.json records the immutable model
revisions and remaining inputs to pin; it is not a completed offline lock.

The source projection accepts RFC, Cisco, PowerShell and system collections.
Cisco CLI and feature builders accept Cisco source directories only. Identifier
term generation supports Cisco ISE messages. The derived layer builder checks
public categories, platforms, source directories and citations with positive
lists. The release still requires its source and content gates.

Optional original-document style extraction is an isolated converter process
under containers/converter-agpl/document-styles/. The KB-side document_styles.py only
launches that command and passes arguments. The converter Dockerfile includes
the helper. The helper imports PyMuPDF, which is AGPL, so like the rest of the
converter it is under AGPL-3.0-or-later; the converter's NOTICE covers it and its
complete corresponding source. The KB-side launcher is under the Apache License
2.0.

The engine's API metadata and gateway VERSION are 1.0.0. The historical point-id
namespace remains unchanged for knowledge-base compatibility. No index is built
by this source-only export. Build entry points require caller-supplied settings;
scripts that use DEEP32_BUILD_ENV, DEEP32_FORGE_PINS or DEEP32_BUILD_JOB name those
settings explicitly rather than assume one machine's configuration.

The separate WSL-distribution form is deprecated pending the lead's retirement
decision. Its builder, provisioner, rootfs handling, package pins and archive
assembly remain in source. Do not remove them or an existing distribution as
part of this change. Docker Desktop's WSL 2 backend is still used on Windows.
