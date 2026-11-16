# Cryptography in the source release

The source release calls standard cryptographic libraries and tools rather than
implementing a new cipher. Release tools use SHA-256 file digests and OpenPGP
signatures through GnuPG. The gateway hashes authentication tokens with SHA-256
and uses constant-time comparison. TLS connections use the platform and Python
TLS libraries.

The installation and runtime source also invokes the platform's OpenSSH and
Tailscale tools. Their cryptographic implementations and licences belong to
those components; they are not reimplemented by this source release.

The release owner reviews the final cryptography inventory, third-party binary
records and the place and date of publication before publication.
