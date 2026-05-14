# Security Policy

Bio-Harness is a local-first research system that runs bioinformatics tools,
model backends, and user-selected workflows on the machine or environment where
it is installed. Security reports are especially important when they involve
command rendering, file path handling, model/tool sandboxing, local credential
exposure, or unsafe handling of user-supplied data.

## Supported Versions

Security fixes target the latest public release branch and the current default
branch.

## Reporting A Vulnerability

Please do not open a public issue for suspected security vulnerabilities. Use
GitHub private vulnerability reporting when available. If private reporting is
not available for the repository, contact the maintainer through a non-public
channel listed on the repository profile.

Please include:

- the affected Bio-Harness version or commit;
- the operating system and execution backend;
- the smallest reproduction steps you can share safely;
- whether the issue requires untrusted input, a malicious model response, a
  crafted workflow, or a particular tool installation;
- any logs with secrets, private paths, and human data removed.

## Local Execution Notes

Bio-Harness can execute shell commands and bioinformatics wrappers as part of a
planned workflow. Treat workflow inputs, tool definitions, model outputs, and
downloaded references as code-adjacent material. Review plans before execution
when running outside a trusted sandbox, and avoid running the harness with
unneeded elevated privileges.

Do not share human genomic data, access tokens, API keys, private SSH keys, or
private local paths in public reports.
