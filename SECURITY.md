# Security Policy

## Supported versions

CLIRadar is currently pre-1.0. Security fixes are applied to the latest release
only.

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| < 0.2 | No |

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose credentials,
device data, or permit command execution. Use the repository's private reporting
channel.

Include:

- affected version and operating system;
- vendor/device family, without production credentials or addresses;
- minimal reproduction steps;
- expected and observed behavior;
- potential impact.

Never attach passwords, private keys, raw production session logs, or internal
network addresses.

## Security boundaries

CLIRadar is intended only for devices the operator is authorized to inspect. Use
a read-only account and validate a vendor profile in a lab before production.
The scanner maps the CLI syntax exposed to the authenticated account; it does
not bypass authorization or guarantee vulnerability detection.

Known upstream exception: Paramiko 4.0.0 is reported under
`PYSEC-2026-2858`. CLIRadar explicitly disables the affected legacy `ssh-rsa`
host-key and user-signature algorithms while awaiting an upstream fixed release.
