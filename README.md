# CLIRadar

![CLIRadar — parses all the commands](docs/assets/banner.png)

[Русская версия](README_RU.md)

CLIRadar is a small command-line scanner for network-device CLIs. There is no
GUI or service layer: configuration is YAML, input documentation is a local
folder, and output is a YAML command catalog plus a small standalone HTML
report.

The tool is vendor-neutral and works with any device exposing a Cisco-like
CLI — interactive contextual `?` help (Cisco IOS/NX-OS, Huawei VRP, Eltex,
QTech, CIT, and similar). A new platform is described by a single YAML
profile with no code changes.[^cli]

[^cli]: Devices without contextual `?` help (for example MikroTik RouterOS or
    web/NETCONF-only equipment) are not supported.

It sends contextual `?` without Enter and clears the input line with `Ctrl-U`.
CLIRadar never submits a discovered command with Enter. Some network operating
systems can still attach side effects to contextual help, so validate a new
device type in a lab first.

## Three modes

### `docs` — documentation catalog without SSH

CLIRadar parses `.txt`, `.md`, and `.rst` files from `vendor_docs/` and writes
the extracted command catalog to `output/cli_doc.yml`. Device configuration and
password are not required.

### `compare` — device truth versus documentation

CLIRadar first inventories the accessible device CLI from the root `?`. Every
documented command not met during the walk is then verified with a single
contextual query — without expanding its branch — so documentation syntax
variants cannot snowball into extra queries. Every entry is marked as
`matched`, `undocumented`, `missing_on_device`, or `not_observed`.

### `audit` — full CLI inventory

CLIRadar walks the same tree from the root `?`, marks every discovered command
as present, and does not read documentation.

Numeric parameters are traversed with a safe sample while their placeholder is
preserved in the catalog. For example, the catalog records
`show vlan <1-4094> brief` while the switch receives `show vlan 1 brief ?`.
Configure samples for vendor-specific values such as `WORD`, IP addresses, and
interface names.

## Quick start

```bash
python -m pip install .
cp config.example.yml config.yml
```

Dependencies are declared only in `pyproject.toml`. CLIRadar does not create,
package, or commit a virtual environment.

Add the switch SSH host key to `known_hosts`, edit `config.yml`, and provide the
password through the process environment:

```bash
export SWITCH_PASSWORD='...'
```

```bash
cliradar --config config.yml --check-config
cliradar docs --config config.yml --docs vendor_docs
cliradar compare --config config.yml --docs vendor_docs
cliradar audit --config config.yml
cliradar audit --config config.yml --enter-modes
```

## Transport and privileged mode

`device.transport` selects how the CLI is reached: `ssh` (default, port 22) or
`telnet` (default port 23). Many switches ship with SSH disabled and are managed
over telnet until it is turned on, so either transport reaches the same crawl.
For telnet there is no host key to pin; for SSH the host key is still verified.

A Cisco-like login lands in an unprivileged view whose prompt ends in `>`, while
the full command surface and the running configuration sit behind an `enable`
step whose prompt ends in `#`. Set `device.enable: true` to raise the session to
privileged mode right after login, so the crawl also sees privileged and
configuration commands. The secret is read from the environment variable named
by `device.enable_password_env` (leave it unset when the device grants `enable`
without a password); a wrong secret is reported as a clear error rather than a
silent under-scan.

```bash
export SWITCH_PASSWORD='...'
export ENABLE_SECRET='...'   # only when device.enable is set and a secret is required
```

## Configuration contexts

Contextual help from the login context lists only that context's commands.
Everything that lives inside `configure`, a VLAN view, an interface view or any
similar context stays invisible to it. `--enter-modes` walks those contexts, and
on a real switch that yields several times more commands.

Contexts are **not listed in the code**: the scanner does not know what a VLAN
or a VRF is. One rule decides everything - a command after which the prompt
changed has opened a new context. Vendor-specific subsystems and undocumented
modes are therefore found on their own.

Position is never remembered, it is proven. `SW1(config-vlan-10)#` folds into
the fingerprint `(config-vlan-*)#`, and that fingerprint is checked before every
help query. An answer from an unexpected context is not trusted: the navigator
replays the recorded entry path and repeats the query. If `exit` does not
return where expected, `quit` and `end` are tried, and as a last resort a fresh
channel is opened - a new channel always starts in a known state.

Before enabling it:

- entering a context **executes a command**. On many platforms `vlan 10` creates
  the VLAN rather than opening a view, which is why this is off by default;
- inside a context Enter is never pressed: commands are still collected with
  contextual help only;
- commands that would end the run itself (`reboot`, `reload`, `erase`, `write`,
  `logout`, `ping`, `terminal` and similar) are never probed;
- neither is the management path - `line`, `username`, `aaa`, `login`, `sshd`
  and similar. A `line vty` view holds the authentication and timeout settings
  of the very session doing the scanning: probing inside one stopped a lab
  switch from granting new sessions halfway through a run, and every context
  found after that became unreachable. One context is a cheap price for the
  scan reaching its end;
- catch-all parameter placeholders (`WORD`, `NAME`, `STRING`, `LINE`,
  `PASSWORD` and similar) are never filled in while probing. A specific
  placeholder such as `IFNAME` names an object that already exists, so
  entering it changes nothing; `WORD` means "any word" and appears on hundreds
  of unrelated commands - on a lab switch one such sample turned
  `hostname WORD` into a real rename. Interfaces, VLANs and other identified
  objects are still entered normally;
- a confirmation prompt (`[y/n]`, `[confirm]`) is answered "no"; an unanswered
  dialog would swallow every later keystroke;
- every pressed Enter is listed under `executed_commands` in the catalog and in
  the HTML report, so the exact set of changes is visible afterwards.

Run it against a lab device that can be reset before trusting it on anything
else.

Note that **probing changes the tree it measures**. An object a probe created
(`filter-list 1`, say) adds the commands that operate on it, and the next scan
will find them. Two consecutive catalogs are therefore not required to match
command for command; a before/after comparison is only meaningful between
identical device states.

```yaml
discovery:
  enter_modes: true    # same as --enter-modes
  max_contexts: 64     # guard against an exploding graph
```

## Documentation folder

`.txt`, `.md`, and `.rst` files are supported. A plain command list is the most
predictable input:

```text
# comment
show version
show interfaces status  Interface state
Syntax: show vlan <1-4094>
switch# show ip route
```

Markdown fenced code blocks and `| command | description |` tables are also
recognized.

For converted command references, `.txt` blocks headed by `Command Format`,
`Command Form`, `Command Syntax`, `Syntax`, `Формат команды`, or `Синтаксис`
are parsed as command grammar. Wrapped syntax is joined, `{a | b}` alternatives
and `[optional]` groups are expanded, and surrounding prose is ignored. A
reference whose conversion destroyed heading spacing is skipped instead of
being treated as a plain command list.

## Parameter samples

```yaml
discovery:
  parameter_policy: explore
  parameter_samples:
    WORD: Ethernet1/1
    A.B.C.D: 192.0.2.1
```

Unknown parameter types remain in the catalog but are not traversed until a
sample is configured. A configured key also marks a lowercase documentation
token such as `interface-name` as a parameter. The placeholder itself is never
sent as a value.

## Speed and progress

Device scans print a live progress line to the terminal:

```text
[########            ]  42% | запросов: 8214 | в очереди: 11330 | осталось: ~11м 48с
```

The percentage is computed against the known work front, so it can stall early
while the tree is still unfolding. `--quiet` disables the line.

The crawl can be parallelized with extra shell channels inside the same SSH
session:

```yaml
discovery:
  parallel_channels: 4
```

Devices with a low vty limit silently grant fewer channels and the scan
continues with what the device allows. `device.read_timeout` and
`device.idle_timeout` also trade scan speed against response completeness on
slow devices.

## Firmware stamp

A command surface only means something next to the software that exposed it,
so before the crawl starts CLIRadar runs the read-only commands listed in
`discovery.version_commands` and records their output in the catalog and at the
top of the HTML report. Two catalogs are only comparable once you can see they
describe the same firmware.

```yaml
discovery:
  version_commands:
    - show version      # 'display version' on VRP-style CLIs
```

The default is `show version`. Vendors disagree on the verb, a device that
rejects the command only produces an unstamped report rather than a failed
scan, and a command that could modify the device is refused at config load.
Set the list to `[]` to disable the stamp entirely — then the crawl presses
Enter only when `--enter-modes` opens a configuration context.

The captured text is redacted before it is stored: the echoed command and the
prompt are dropped, and hostname, serial-number, system-id and MAC lines are
masked, because the catalog promises `identity: redacted`. Credential-bearing
lines (`password`, `secret`, `community`, `key`, ...) are removed too, so
pointing the setting at `show running-config` does not put secrets into a
report you share. Redaction is a safety net, not a guarantee — review the
stamp before publishing a report from an unfamiliar platform.

## Running configuration versus the catalog

The crawl answers "what can this box be told to do"; the running configuration
answers "what has it actually been told". CLIRadar reads it with one read-only
command before the crawl and matches every configured line against the catalog
it builds:

```yaml
discovery:
  config_commands:            # tried in order; first that answers wins
    - display current-configuration
    - show running-config
```

The dump is parsed structurally — indentation and section separators (`#` on
VRP, `!` elsewhere) rebuild the view hierarchy — so a platform CLIRadar has
never seen still yields a tree. Each line is then looked up under every view
path the scan actually stood in, with instance numbers folded, so
`interface 10GE1/0/24` matches a catalog that was walked through
`interface 10GE1/0/1`.

The disagreement is the finding. A configured line the catalog does not contain
means the device is executing a command the crawl never found — the only
evidence of an incomplete command surface that does not require a second device
to compare against. Those lines appear in the catalog under
`configuration.missing_from_catalog` and in the HTML report, with every value
folded to `<value>`: which command is missing describes the platform, the
address it was configured with describes your network.

Two artifacts come out of it, deliberately separate:

- the coverage summary inside the catalog and report — shapes only, shareable;
- `output/config_tree.yml` — the full parsed tree with real values, each
  unexplained line marked in place. It describes one live network, so it is
  written with private permissions and belongs in no report. Passwords, keys
  and community strings are blanked before it is written.

Reading the dump has its own budget (`device.capture_timeout`, default 120 s),
because a configuration is paged and orders of magnitude larger than a help
answer. Set `config_commands: []` to skip the whole step.

## Output

The default machine-readable outputs are:

- `docs` → `output/cli_doc.yml`;
- `audit` → `output/cli_real.yml`;
- `compare` → `output/cli_compare.yml`.

The catalog format is:

```yaml
schema_version: 3
mode: compare
device:
  identity: redacted
  firmware:
    captured_at_start: true
    results:
      - command: show version
        output: |-
          SwitchOS Software, Version 8.4.2
scan:
  complete: true
  queries: 1254
summary:
  device_commands: 4830
  documentation_commands: 4700
  matched: 4650
  undocumented: 180
  missing_on_device: 50
commands:
  - command: show version
    description: Displays software version
    executable: true
    source: [cli, documentation:vendor_docs/commands.txt]
    comparison_status: matched
```

`output/missing_commands.html` is generated by `compare`. It lists commands
confirmed as `missing_on_device`, followed — under a warning — by any
`not_observed` ones the walk never reached. Change the path with
`output.html_report`.

Every mode also writes two additional exports:

- `output/commands_tree.yml` — a nested token tree (node key = next word,
  empty leaf);
- `output/commands_human.yml` — a flat alphabetical `command: description`
  list.

In `compare` and `audit` the exports contain commands actually observed on the
device; in `docs` they contain documented commands. Change the paths with
`output.tree_catalog` and `output.human_catalog`.

Raw responses can be enabled with `output.raw_log`; logging is off by default.
On POSIX, catalog, HTML report, and log files use mode `0600`; on Windows they
inherit the parent directory ACL.

## Boundaries and safety

- Scan only devices you are authorized to inspect, using a read-only account
  and a lab device first.
- Unknown SSH host keys are rejected and legacy `ssh-rsa` is disabled.
- The password comes from the process environment and is not written to the
  catalog or raw log.
- CLIRadar explores only the current CLI context. It does not press Enter to
  enter configuration submodes.
- Contextual `?` is not universally side-effect-free: some NOS implementations
  can create defaults or alter state while producing help. Test a new platform
  in a lab with a read-only account and exclude risky branches with
  `denied_tokens`.
- Completeness depends on the vendor's contextual help, pagination, account
  privileges, scan limits, and usable parameter samples.
- Absence is judged per command, not by one flag for the whole scan. Every
  keyword a node offers is catalogued before any limit can skip it, so a node
  the walk stood on has a complete keyword list and a keyword absent from it is
  `missing_on_device`. Commands under a branch the walk never reached stay
  `not_observed`.
- `denied_tokens` can exclude branches and should be set from lab observations
  for each platform.

## Checks

```bash
ruff check .
pytest
bandit -q -r src
pip-audit
```

Developer architecture: [docs/ARCHITECTURE_RU.md](docs/ARCHITECTURE_RU.md).
Operations guide: [docs/OPERATIONS_RU.md](docs/OPERATIONS_RU.md).
