# Isolated Tool Launchers

Bio-Harness can run selected hard-to-package tools from isolated tool-specific
environments without changing the default benchmark/runtime environment.

Current supported isolated-tool launcher targets:

- `cnvkit.py` via dedicated Python virtual environment
- `prokka` via dedicated Docker-backed wrapper
- `STAR-Fusion` via dedicated Docker-backed wrapper
- `flye` via dedicated Ubuntu-based `linux/amd64` Docker wrapper
- `trinity` via dedicated Ubuntu-based `linux/amd64` Docker wrapper

The launcher registry is optional. If it is not configured, Bio-Harness keeps
its existing behavior and searches the normal system and Pixi paths.

Bio-Harness also has a runtime recovery path:

```bash
bio-harness-run --auto-setup-isolated-tools
```

In `scientific_harness` mode this is enabled by default; it can also be enabled
explicitly on other modes with the same flag. That allows the harness to
attempt isolated-tool recipe setup automatically when a run fails because one
of these tools is missing.

## Config Path

Default launcher config path:

```text
workspace/tool_launchers.json
```

Override with:

```bash
export BIO_HARNESS_TOOL_LAUNCHERS_PATH=/abs/path/to/tool_launchers.json
```

## Configure CNVkit In Its Own Virtual Environment

```bash
python3 scripts/configure_isolated_tools.py --tool cnvkit.py --install
```

This creates a dedicated Python virtual environment and installs `cnvkit`
inside it, then writes a launcher entry that the harness can use automatically.

## Install Docker-Backed Wrappers

```bash
python3 scripts/configure_isolated_tools.py \
  --tool prokka \
  --tool STAR-Fusion \
  --tool flye \
  --tool trinity \
  --install
```

## Generic Recipe Setup

```bash
python3 scripts/configure_isolated_tools.py --tool cnvkit.py --install
python3 scripts/configure_isolated_tools.py --tool prokka --install
python3 scripts/configure_isolated_tools.py --tool STAR-Fusion --install
python3 scripts/configure_isolated_tools.py --tool flye --install
python3 scripts/configure_isolated_tools.py --tool trinity --install
```

If you already have a tool-specific binary path and want to register that
instead of using the default recipe:

```bash
python3 scripts/configure_isolated_tools.py --tool prokka --binary-path prokka=/abs/path/to/prokka
python3 scripts/configure_isolated_tools.py --tool STAR-Fusion --binary-path STAR-Fusion=/abs/path/to/STAR-Fusion
```

For `flye` and `trinity`, the current supported recipe path is the Docker-backed
Ubuntu image workflow above rather than direct external binary registration.

## Config Format

```json
{
  "version": 1,
  "tools": {
    "cnvkit.py": {
      "argv": ["/abs/path/to/.tool-envs/cnvkit/bin/cnvkit.py"]
    },
    "prokka": {
      "argv": ["/abs/path/to/prokka"]
    },
    "STAR-Fusion": {
      "argv": ["/abs/path/to/STAR-Fusion"]
    },
    "flye": {
      "argv": ["/abs/path/to/.tool-envs/flye/bin/flye"]
    },
    "trinity": {
      "argv": ["/abs/path/to/.tool-envs/trinity/bin/trinity"]
    }
  }
}
```

`argv` is treated as the executable prefix for that tool. The harness will use
it when rendering commands and when checking tool availability.

## Design Intent

- Keep the default harness environment simple and reproducible
- Avoid forcing incompatible native stacks into one Pixi environment
- Let the harness execute isolated tools automatically once they are registered
- Preserve existing behavior when no launcher is configured

## Current Caveats

- `flye` now has a working Ubuntu-based `linux/amd64` isolated launcher and the
  wrapper can execute through it, but the tiny smoke assembly on this machine is
  still being `SIGKILL`ed during Flye's internal assembly stage.
- `trinity` now has a working Ubuntu-based `linux/amd64` isolated launcher and
  the wrapper can execute through it. Pinning a modern `salmon` fixed the prior
  dependency mismatch, but the current Trinity/Inchworm runtime still hits
  `illegal instruction` on this machine.
