Deep32 GPU container pack 1.0.0

This data disc carries offline Docker and WSL forms, container images, models,
licences, corresponding source and verification evidence. It does not boot or
install an operating system. A bare-metal installer is planned for a later release.

Use a Linux NVIDIA GPU host with Docker Engine, Docker Compose, the NVIDIA driver,
the NVIDIA Container Toolkit, Python 3, systemd and a signed-in Tailscale host.
The Windows form requires Windows 11, WSL 2 and a Windows NVIDIA driver with WSL
support; Docker and the toolkit inside its new distribution come from this disc.
Minimum versions are recorded in pins.json. Host checks also require free storage
and unused 127.0.0.1 ports 8081 and 8082. Install host prerequisites beforehand.
Neither form downloads files or installs a host NVIDIA driver.

Linux: mount the disc read-only, change to its root and run:

    sha256sum -c SHA256SUMS
    sudo python3 -B forms/host-requirements.py --form docker --kit "$PWD" --payload "$PWD/deep32/payload"
    sudo install -D -m 0600 /path/to/settings.json /etc/deep32-forge/settings.json
    sudo bash forms/load-docker.sh "$PWD/deep32/payload"

Supply valid C7 settings for role forge; PACK.md includes the required fields.
First boot waits for settings, host sign-in and pairing with the Workstation.

Windows: mount the ISO, verify SHA256SUMS using sha256sum in a Linux environment,
then use PowerShell to import a fresh distribution (replace E: with its drive):

    wsl --import Deep32Forge C:\Deep32Forge E:\forms\forge-wsl.tar --version 2
    wsl -d Deep32Forge -u root -- python3 -B /opt/deep32-forge/forms/host-requirements.py --form wsl --payload /mnt/e/deep32/payload
    wsl -d Deep32Forge -u root -- install -D -m 0600 /mnt/c/path/to/settings.json /etc/deep32-forge/settings.json
    wsl -d Deep32Forge -u root -- bash /opt/deep32-forge/forms/provision-wsl.sh /mnt/e/deep32/payload

The Windows host signs in; the distribution creates no second Tailscale node.
Use the Workstation's existing pairing workflow when Forge reports pairing.
Treat its short-lived pairing code as private. Do not print tokens or key files.

PACK.md gives the full setup, pairing, stop/removal and licence instructions.
The AGPL text is deep32/payload/licences/AGPL-3.0.txt; complete corresponding
source is under deep32/payload/source, including production-converter.
