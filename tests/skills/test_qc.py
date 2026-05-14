import pytest
from pathlib import Path
from bio_harness.skills.library.qc import fastqc_run

# Define a fixture for the base_output_dir to ensure it's created and cleaned up
@pytest.fixture
def setup_output_dir(tmp_path):
    base_output_dir = tmp_path / "workspace" / "qc_results"
    base_output_dir.mkdir(parents=True, exist_ok=True)
    return base_output_dir


@pytest.fixture
def sample_fastq_files(tmp_path):
    f1 = tmp_path / "test1.fastq.gz"
    f2 = tmp_path / "test2.fastq.gz"
    f1.write_text("x", encoding="utf-8")
    f2.write_text("x", encoding="utf-8")
    return f1, f2

def test_fastqc_run_basic(setup_output_dir, sample_fastq_files):
    """Test basic FastQC command string construction with minimal parameters."""
    
    input_file = str(sample_fastq_files[0])
    output_dir = "some_dir" # This should be resolved to be under workspace/qc_results/

    command_string = fastqc_run(input_file=input_file, output_dir=output_dir, _base_output_path=setup_output_dir)

    expected_output_path = (setup_output_dir / output_dir).resolve()

    expected_command = f"mkdir -p {expected_output_path} && fastqc --outdir {expected_output_path} --threads 2 {input_file}"
    assert command_string == expected_command

def test_fastqc_run_all_parameters(setup_output_dir, sample_fastq_files, tmp_path):
    """Test FastQC command string construction with all optional parameters."""

    input_file = f"{sample_fastq_files[0]} {sample_fastq_files[1]}"
    output_dir = "custom_results"
    threads = 4
    contaminants = str(tmp_path / "adapters.fa")
    Path(contaminants).write_text("x", encoding="utf-8")
    casava = True

    command_string = fastqc_run(
        input_file=input_file,
        output_dir=output_dir,
        threads=threads,
        contaminants=contaminants,
        casava=casava,
        _base_output_path=setup_output_dir
    )

    expected_output_path = (setup_output_dir / output_dir).resolve()

    expected_command = (
        f"mkdir -p {expected_output_path} && fastqc --outdir {expected_output_path} --threads {threads} "
        f"--contaminants {contaminants} --casava {sample_fastq_files[0]} {sample_fastq_files[1]}"
    )
    assert command_string == expected_command

def test_fastqc_run_relative_output_dir_resolution(setup_output_dir, sample_fastq_files):
    """Relative output_dir values should resolve under the provided base output path."""
    base_output_dir = setup_output_dir
    input_file = str(sample_fastq_files[0])
    output_dir_rel = "../attacker_dir"

    command_string = fastqc_run(input_file=input_file, output_dir=output_dir_rel, _base_output_path=base_output_dir)
    expected_output_path = (base_output_dir / output_dir_rel).resolve()

    assert command_string.startswith(f"mkdir -p {expected_output_path} && ")
    assert f"--outdir {expected_output_path}" in command_string

def test_fastqc_run_output_dir_sub_resolution(setup_output_dir, sample_fastq_files):
    """Subdirectory output paths should resolve as expected under the base output directory."""

    base_output_dir = setup_output_dir

    input_file = str(sample_fastq_files[0])
    output_dir_sub = "my_sample_run"
    
    command_string = fastqc_run(input_file=input_file, output_dir=output_dir_sub, _base_output_path=base_output_dir)

    expected_output_path = (base_output_dir / output_dir_sub).resolve()

    assert command_string.startswith(f"mkdir -p {expected_output_path} && ")
    assert f"--outdir {expected_output_path}" in command_string


def test_fastqc_run_directory_input_expansion(setup_output_dir, tmp_path):
    """Directory inputs should expand to contained FASTQ files."""
    input_dir = tmp_path / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "a.fastq.gz").write_text("x", encoding="utf-8")
    (input_dir / "b.fq").write_text("x", encoding="utf-8")
    (input_dir / "notes.txt").write_text("x", encoding="utf-8")

    command_string = fastqc_run(
        input_file=str(input_dir),
        output_dir="dir_case",
        _base_output_path=setup_output_dir,
    )

    assert str((input_dir / "a.fastq.gz").resolve()) in command_string
    assert str((input_dir / "b.fq").resolve()) in command_string
    assert "notes.txt" not in command_string


def test_fastqc_run_directory_input_without_fastq_raises(setup_output_dir, tmp_path):
    """Directory inputs without FASTQ files should raise a clear error."""
    input_dir = tmp_path / "no_fastq"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "readme.md").write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="No FASTQ files found"):
        fastqc_run(
            input_file=str(input_dir),
            output_dir="dir_case",
            _base_output_path=setup_output_dir,
        )


def test_fastqc_run_missing_path_raises(setup_output_dir):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        fastqc_run(
            input_file="/tmp/definitely_missing_fastq_1234567.fastq.gz",
            output_dir="dir_case",
            _base_output_path=setup_output_dir,
        )
