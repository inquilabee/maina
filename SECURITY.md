# Security policy

maina downloads and runs third-party executables, so security reports are welcome and taken seriously.

## Supported versions

maina is Beta (0.x). Security fixes land on the latest release only.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting:
on the repository, open the **Security** tab and choose **Report a vulnerability**.

Include what you found, how to reproduce it (a manifest, an archive, or a call that triggers it),
and the maina and Python versions. You can expect an acknowledgement within a few days.

## Scope

In scope: maina's own handling of untrusted input — archive extraction, download integrity
and redirects, path containment for manifest values, environment trimming (`env_allow`),
and argv construction.

By design, a registered registry directory is **trusted code** (it can ship parsers that run
in-process) and a manifest names the programs that run. Pointing maina at a catalog you do not
trust is out of scope; see the [security model](https://inquilabee.github.io/maina/user/security/)
for what maina does and does not defend against.
