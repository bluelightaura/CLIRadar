# Changelog

All notable changes to CLIRadar are documented in this file. The project follows
Semantic Versioning.

## [Unreleased]

### Added

- The device's running configuration is read and matched against the crawled
  catalog (`discovery.config_commands`, default `display current-configuration`
  then `show running-config`; written to `output.config_tree`). This closes the
  loop between the two halves of the scanner: the crawl says what the box *can*
  be told to do, the configuration says what it *has* been told, and a
  configured line the catalog does not contain is the one piece of evidence
  that a command surface is incomplete without a second device to compare
  against. The dump is parsed structurally - indentation and section separators
  rebuild the view hierarchy - so a platform this code has never seen still
  yields a tree; each line is then looked up under every view path the crawl
  actually stood in, and folded instance numbers let `interface 10GE1/0/24`
  match a catalog walked through `10GE1/0/1`. The catalog carries only the
  shape of each finding (`configuration.missing_from_catalog`, values folded
  away); the full parsed tree keeps the real values and is written to a
  separate private file, because it describes one network rather than a
  platform. Passwords, keys and community strings are blanked before either is
  written. Reading it is one read-only command with its own `capture_timeout`,
  run before the crawl so a session that dies later still leaves it behind.
- `discovery.parser_profile` exposes the two vendor-deviation flags the crawler
  already had (`accept_undescribed_options`, `error_words_are_commands`) to the
  configuration file, so a platform that names commands with words like
  `error-down` or lists options without descriptions can be read correctly
  without editing code. Both stay off by default - each trades a class of
  missed commands for a class of invented ones.
- A manual is read by a package of its own (`cliradar.docparse`): a document is
  split into cards, each card's syntax block and parameter table are read back
  into each other, and what differs between manuals is data rather than code -
  a JSON profile names one document's headings, column titles and vocabulary,
  and two ship with the package (a Russian L3200 reference and an English
  Centec one). A `.docx` is streamed rather than held in memory, because the
  manual this was written for is 5 MB packed and 113 MB open.

### Changed

- The crawl walks in passes ordered by usefulness: ordinary branches first,
  seed commands next, and the `no`/`undo`/`default` mirror last. The negation
  branch duplicates the whole tree while describing removal rather than
  capability, so a scan cut short by a query limit or a dead session now loses
  the mirror instead of the commands themselves. Nothing is dropped - the
  mirror is still crawled to completion when the scan runs its course.
- Context-opening probes no longer type a value the operator did not supply
  (`discovery.probe_invented_values`, default false). Filling a numeric range
  with its minimum reads as innocent and is not: inside an interface view it
  turns `speed <10-40000>` into `speed 10` and `mtu <68-9216>` into `mtu 68` on
  a live port. Candidates left untried for want of a sample are counted in
  `probes_unsampled` with the parameters that would unlock them, so the cost is
  reported rather than paid silently. Set the flag only against a lab device.
- Documentation commands come from the document's own shape instead of from
  matching the words the operator typed. The old reader found a command when a
  manual happened to phrase it the way the operator did and missed it
  otherwise; the new one follows headings, syntax blocks and parameter tables,
  and a name that arrives broken - split across lines, welded to its heading,
  bulleted with U+2219, or joined to its neighbour where a paged conversion
  lost the gutter - is repaired from evidence in the same document rather than
  by guesswork. A weld is undone only where both halves stand alone in the
  document three times as often as the weld itself, because an invented split
  puts a command in the catalog the device will deny having while a weld left
  alone changes nothing. The reference manual yields 14588 commands, every one
  of them carrying a description.
- A command described by two manuals of the same device keeps the description
  in the language the operator reads. The first non-empty text won, and first
  meant earliest filename, so commands described in both manuals shipped the
  Russian sentence to an English reader purely because "Centec" sorts before
  "L3200". A profile now states its manual's language and the launcher's own
  setting decides, with nothing new to configure.

### Fixed

- The permissive parser profile now reads an undescribed numeric range
  (`  <1-100>` alone on its line) as the parameter it is. A replay of the full
  reference log - 18201 device answers, 82306 options - against the fixed
  parser shows zero lost and zero invented options; the last three losses were
  all this shape, and the 87 remaining "losses" a naive reading reports are
  the device redrawing the typed text, which must be dropped and is.
- The HTML report gained a "configuration versus catalog" section: coverage,
  and the configured-but-never-crawled lines with their values folded away.
- The probe safety check reads the whole command, not just its first word. A
  denied verb is often not the head - `reset saved-configuration` erases the
  box, `request system reboot` restarts it, `schedule reboot` does it later -
  and a head-only test let all three through. The denylist also gained the
  verbs those examples use (`reset`, `commit`, `rollback`, `request`, and more).
- Confirmation dialogs are recognised in every form the platform writes them,
  not just `(y/n)`. VRP also asks `[Y/N]`, `(y or n)`, `[yes,no] (no)` and a
  bare `Continue?`; a form the pattern missed left the device reading a yes/no
  while the scan typed its next command into the open dialog.
- A manual the reader declined is now named instead of silently counted as
  read. Every way the reading returns nothing says why, files that yielded no
  command are listed, and the formats this reader cannot open at all (`.pdf`,
  `.doc`, `.odt` and their kin) are refused by name rather than dropped before
  the loop ever starts - a folder holding four PDFs beside one manual used to
  draw not a word about them, and a command count printed either way is what
  made a refusal look like a success. A document over the size limit is refused
  with its size spelled out.
- Markdown that is not a command reference no longer contributes commands. The
  line reader took `pytest` and `pip-audit` out of a README, an arrow diagram
  out of an architecture note, and a ping line out of a rescue procedure -
  each of which `compare` then reported as a command the device was missing.
  Three rules, each resting on what the document says about itself: a fence
  declaring a language other than the device's own is skipped whole, a line
  carrying an arrow is showing a result rather than a command, and a markdown
  table is read as commands only when its header says it holds them. The real
  manuals do not move by a single command.
- A parameter table no longer damages the descriptions it hands over. A header
  the name column could not hold broke in two and its orphaned tail opened the
  first description on 53 tables; a row repeating the name it is filed under
  made 99.9% of an honest manual's rows look like the corruption a damaged
  manual really has; and the name was taken off in two places at once, which
  ate a word of the description as soon as both passes landed.
- Marking answers one question one way. Brackets holding a single token offer
  nothing to choose between and are read as optional rather than as a choice;
  and a card headed `no mac-address` no longer has the `no` of its own name
  skipped as though it negated some other command, which slid the comparison
  one token along and shipped 55 of that manual's 89 negated forms as
  `no <mac-address>`.

### Added (earlier in this cycle)

- Firmware stamp in every catalog and at the top of the HTML report
  (`discovery.version_commands`, default `show version`). A command surface only
  means something next to the software that exposed it: without the stamp two
  catalogs cannot be told apart, and an unexplained difference between runs
  cannot be attributed to an upgrade. The commands run once before the crawl.
  Because they are executed for real, a command whose verb could modify the
  device is refused when the configuration loads, and a device that rejects the
  command yields an unstamped report rather than a failed scan. The captured
  text is redacted before it is stored: a version banner is exactly where a
  hostname, serial number and base MAC live, and pointing the setting at a
  configuration dump would otherwise put credentials in a shared report.
- Telnet transport (`device.transport: telnet`). Some switch builds accept an
  SSH password but never grant an interactive shell - SSH serves only netconf
  and the CLI lives on telnet (port 23) or a terminal-server console line. The
  telnet channel answers option negotiation and hands up a clean stream, so
  every help query, position check and pager rule is reused unchanged; the
  navigator and crawler drive it exactly as they drive SSH. Login answers the
  Username/Password prompts and turns a rejected password into a clear error.
- Probing no longer descends into a nested foreign shell (`shell`, `bash`,
  `vtysh`, ...). Such a shell is a separate CLI whose prompts collide with the
  root fingerprint, so the position proof cannot hold there; in a lab every
  position check inside one forced a channel rebuild.

- Repeated shapes in the command tree are copied instead of walked
  (`discovery.deduplicate_subtrees`). A CLI grammar repeats itself: on the
  reference platform 18000 visited nodes had only 694 distinct option sets,
  because enumerations such as log levels or logging sources all continue the
  same way. A node whose option set was already seen has its subtree copied
  from the node that was walked. Because that is an assumption and not a fact,
  a sample of the copies is re-queried on the device; a copy that answers
  differently is listed in `derived_mismatched` and marks the scan incomplete.
  Copies obey `max_depth`, and hitting `max_derived_entries` is reported
  rather than silently truncating.
- Help responses are read up to the line the CLI redraws after answering `?`
  (prompt plus the text typed so far) instead of waiting out an idle timeout.
  On the reference platform this raised throughput from 3 to 20 queries per
  second per channel; platforms that do not redraw fall back to the timeout.
- Extra shell channels also share the traversal of each configuration context,
  after every worker has proven with its own prompt that it stands there.
- Option flags such as `-q` are catalogued but no longer expanded: they combine
  freely, so permuting them multiplies the queue without describing any new
  command.
- Configuration contexts are discovered and crawled (`--enter-modes`,
  `discovery.enter_modes`). A CLI is walked as a graph of contexts rather than a
  list of known modes: a command after which the prompt fingerprint changes has
  opened a new context, so vendor-specific and undocumented modes are found
  without naming any of them in code. Position is proven from the prompt before
  every help query, `exit` is verified rather than assumed, and a context that
  cannot be restored is repaired with a fresh channel replaying the entry path.
  This is the only feature that executes commands: probes obey a denylist of
  run-ending commands, confirmation dialogs are declined, and every executed
  command is reported in `executed_commands` and in the HTML report.
- Parallel device crawling over extra shell channels of the same SSH session
  (`discovery.parallel_channels`), with silent fallback when the device grants
  fewer channels.
- Live terminal progress line with percentage, queue size, and estimated time
  remaining; disabled by `--quiet`.
- Two additional exports written by every mode: a nested token tree
  (`output/commands_tree.yml`) and a flat human-readable
  `command: description` list (`output/commands_human.yml`).
- The HTML report got a live search box with a match counter and a refreshed
  self-contained layout; no external resources are loaded.

### Fixed

- Three parsing rules no longer discard commands the device listed. Measured
  against 18201 recorded help answers from the reference switch, they dropped
  112 commands, each of which also blocked its whole subtree:
  - `error`, `unknown-unicast` and `error-down` are command names, not error
    messages. The worded error patterns now apply only outside the indented
    block where options live; `%` and `^` still mark an error anywhere. This
    alone recovered 93 commands, among them `debug bgp error`.
  - Options listed without a description are kept (`debug udld all|event|sync`,
    `clear arp-miss anti-attack`). A lone word is only read as a token when it
    is shaped like one, so a wrapped description does not become a command.
  - An option whose description ends in `>` or `#` is no longer mistaken for a
    prompt, which had hidden all of `ip community-filter 1 deny|permit`.
- An incomplete walk no longer hides absences it actually proved. Completeness
  was a single flag for the whole scan, so one unexpanded parameter placeholder
  downgraded every documented-but-absent command to the non-committal
  `not_observed`. Every keyword a node offers is catalogued before any policy
  can skip it, so a node the scan stood on has a complete keyword list, and a
  keyword missing from it is missing from the device. Absence is now reported
  per command: proven where the walk enumerated the parent, hedged elsewhere.
  On the reference platform this turned 8842 of 12254 non-answers into real
  findings — including the 2367 `show ...` commands that firmware does not have.
- `python -m cliradar` runs again. The package had no `__main__`, so the form
  the stand runbooks use failed on a checkout that was never installed; only
  the `cliradar` console script worked. Exit codes are unchanged.
- `transport: telnet` without an explicit `device.port` now dials 23 instead of
  22. The port default was applied before the transport was known, so a telnet
  config spoke to sshd and waited out the read timeout with no useful error.
- Probing no longer touches the management path (`line`, `username`, `aaa`,
  `login`, `sshd`, ...). A `line vty` view configures the session doing the
  scanning; probing inside one stopped a switch from granting new sessions
  mid-run, and every context discovered afterwards became unreachable.
- Probing no longer fills in catch-all parameter placeholders (`WORD`, `NAME`,
  `STRING`, ...). One sample for such a placeholder applies to every unrelated
  command that accepts a word, which on a lab switch executed `hostname WORD`
  and renamed the device. Specific placeholders such as `IFNAME` name existing
  objects and are still entered.
- A session log that cannot be written no longer ends the scan: the failure is
  counted and reported instead. The log is a troubleshooting aid, and losing it
  must not cost an hour-long crawl.
- A dropped SSH session is repaired instead of raising `Socket is closed`: a
  fresh channel is opened, and if the transport itself is gone the connection
  is rebuilt and the recorded entry path replayed.
- Documentation-derived seeds in `compare` are now verification-only: their
  help output no longer spawns new crawl branches, preventing a combinatorial
  explosion on documentation syntax variants such as ACL alternatives.

### Changed

- Structured command-reference blocks are parsed from common English and
  Russian syntax headings, including wrapped lines, alternatives, and optional
  groups; surrounding prose and damaged converted references are rejected.
- Contextual help parsing now accepts common completion headers and Enter
  markers, ignores prompts, errors, and pager text, and advances common pagers.
- Configured samples can identify lowercase documentation placeholders such as
  `interface-name`.
- Safety documentation no longer assumes contextual `?` is side-effect-free on
  every network operating system.
- Every scan also writes a dependency-free HTML summary. Compare reports list
  confirmed missing commands and keep incomplete `not_observed` results
  explicitly separate.
- Added an offline `docs` mode and separate default catalogs:
  `cli_doc.yml`, `cli_real.yml`, and `cli_compare.yml`.

## [0.2.0] - 2026-07-30

### Changed

- Removed the GUI direction and made the command-line workflow the sole product
  interface.
- Split scanning into explicit `compare` and `audit` modes.
- Both modes treat the full device CLI walk from root `?` as the source of
  truth; comparison overlays documentation on that inventory.
- Parameter traversal now keeps placeholders in the catalog while using numeric
  or configured sample values in device queries.
- Plain `.txt` command lists and labeled `Command:`, `Syntax:`, `Usage:`, and
  `Example:` lines are parsed directly.
- Schema version 3 reports `matched`, `undocumented`, `missing_on_device`, and
  `not_observed`, plus scan completeness and summary counts.
- Full discovery traverses every keyword by default; optional `denied_tokens`
  remain available for exclusions.

## [0.1.0] - 2026-07-30

### Added

- Recursive network CLI discovery through contextual `?` help.
- Markdown, text, and reStructuredText seed-document scanning.
- YAML command catalog and raw session logging.
- Strict SSH host-key validation and legacy `ssh-rsa` blocking.
- Typed configuration validation and stable process exit codes.
- Query, depth, document, response-size, and dangerous-branch limits.
- Owner-only output permissions and symlink-safe writes.
- Unit, integration, lint, static security, and dependency checks.
- English and Russian user documentation plus Russian architecture guide.
