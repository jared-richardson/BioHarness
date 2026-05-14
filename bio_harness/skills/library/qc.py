from pathlib import Path
import shlex
import glob

FASTQ_SUFFIXES = (".fastq", ".fq", ".fastq.gz", ".fq.gz")


def _expand_fastqc_inputs(input_file: str) -> list[str]:
    """
    Expands an input token list into concrete FastQC-readable file paths.
    If a token is a directory, all FASTQ/FQ files in that directory are added.
    """
    expanded: list[str] = []
    for token in input_file.split():
        if any(ch in token for ch in ["*", "?", "["]):
            matches = sorted(glob.glob(token))
            if not matches:
                raise FileNotFoundError(f"Input glob '{token}' matched no files.")
            expanded.extend(matches)
            continue

        path = Path(token).expanduser()
        if path.is_dir():
            files = sorted(
                [
                    p
                    for p in path.rglob("*")
                    if p.is_file() and any(str(p).lower().endswith(sfx) for sfx in FASTQ_SUFFIXES)
                ]
            )
            if not files:
                raise ValueError(
                    f"No FASTQ files found in directory '{path}'. "
                    f"Expected one of: {', '.join(FASTQ_SUFFIXES)}"
                )
            expanded.extend([str(p.resolve()) for p in files])
        else:
            if not path.exists():
                raise FileNotFoundError(f"Input path does not exist: '{path}'")
            expanded.append(str(path))
    if not expanded:
        raise ValueError("No input files provided for fastqc_run.")
    return expanded

def fastqc_run(
    input_file: str,
    output_dir: str,
    threads: int = 2,
    contaminants: str = None,
    casava: bool = False,
    _base_output_path: Path = None,
) -> str: # Changed return type to str
    """
    Constructs the FastQC shell command for quality control on raw sequencing data (FASTQ files).

    Args:
        input_file: Path to the input FASTQ file(s). Can be a single file or a space-separated list of files.
        output_dir: Directory to save FastQC reports.
        threads: Number of threads to use.
        contaminants: An optional file containing a list of contaminants to screen against.
        casava: If True, files come from raw Casava 1.8 or later output.
        _base_output_path: Internal parameter for testing, allows overriding the base output directory.

    Returns:
        A string representing the FastQC shell command.
    """
    if _base_output_path:
        base = Path(_base_output_path).resolve()
        candidate = Path(output_dir).expanduser()
        resolved_output_dir = candidate if candidate.is_absolute() else (base / candidate)
        resolved_output_dir = resolved_output_dir.resolve()
    else:
        resolved_output_dir = Path(output_dir).expanduser().resolve()

    command = ["fastqc"]
    command.extend(["--outdir", str(resolved_output_dir)])
    command.extend(["--threads", str(threads)])

    if contaminants:
        command.extend(["--contaminants", contaminants])
    if casava:
        command.append("--casava")

    input_files = _expand_fastqc_inputs(input_file)
    command.extend(input_files)

    prep = f"mkdir -p {shlex.quote(str(resolved_output_dir))} && "
    return prep + " ".join(shlex.quote(part) for part in command)
