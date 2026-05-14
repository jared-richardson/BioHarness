import psutil
import platform
from typing import Annotated
from pydantic import BaseModel, Field

class SystemProfile(BaseModel):
    """
    Represents a snapshot of the system's hardware and operating environment.
    """
    total_memory_gb: Annotated[float, Field(description="Total physical memory in GB.")]
    available_memory_gb: Annotated[float, Field(description="Available physical memory in GB.")]
    cpu_physical_cores: Annotated[int, Field(description="Number of physical CPU cores.")]
    cpu_logical_cores: Annotated[int, Field(description="Number of logical CPU cores (includes hyper-threading).")]
    architecture: Annotated[str, Field(description="System architecture (e.g., 'arm64', 'x86_64').")]
    platform: Annotated[str, Field(description="Operating system platform (e.g., 'Darwin', 'Linux').")]
    is_apple_silicon: Annotated[bool, Field(description="True if running on Apple Silicon (ARM64 Mac).")]

def get_system_profile() -> SystemProfile:
    """
    Gathers detailed system profile information including memory, CPU, architecture,
    platform, and specifically detects if running on Apple Silicon.

    Returns:
        SystemProfile: A Pydantic model instance containing the system's profile.
    """
    # Memory information
    mem = psutil.virtual_memory()
    total_memory_gb = round(mem.total / (1024**3), 2)
    available_memory_gb = round(mem.available / (1024**3), 2)

    # CPU information
    cpu_physical_cores = psutil.cpu_count(logical=False)
    cpu_logical_cores = psutil.cpu_count(logical=True)

    # Platform and architecture
    system_platform = platform.system()
    system_architecture = platform.machine()
    
    is_apple_silicon = False
    if system_platform == "Darwin" and system_architecture == "arm64":
        is_apple_silicon = True
        
    # On some systems, platform.machine() might return 'aarch64' for Apple Silicon.
    # We should normalize 'aarch64' to 'arm64' for consistency if it's indeed Apple Silicon.
    if system_platform == "Darwin" and system_architecture == "aarch64":
        system_architecture = "arm64"
        is_apple_silicon = True
        
    return SystemProfile(
        total_memory_gb=total_memory_gb,
        available_memory_gb=available_memory_gb,
        cpu_physical_cores=cpu_physical_cores,
        cpu_logical_cores=cpu_logical_cores,
        architecture=system_architecture,
        platform=system_platform,
        is_apple_silicon=is_apple_silicon,
    )

def recommend_aligner(genome_size_gb: float) -> str:
    """
    Provides a recommendation for a suitable aligner based on available system RAM
    and the target genome size.

    Args:
        genome_size_gb: The size of the reference genome in Gigabytes.

    Returns:
        A string recommending an aligner or suggesting alternative action.
    """
    mem = psutil.virtual_memory()
    available_ram_gb = round(mem.available / (1024**3), 2)

    recommendation = ""
    warning = ""

    if genome_size_gb > 3 and available_ram_gb < 16:
        warning = "Warning: Insufficient RAM for Human Genome Alignment. Suggest using Cloud or Cluster."
    
    if available_ram_gb > 32:
        recommendation = "STAR (Fastest, High RAM)"
    elif available_ram_gb < 32: # This covers cases where available RAM is between 0 and 32 GB
        if genome_size_gb <= 3 and available_ram_gb > 8: # Arbitrary threshold for reasonable low-mem alignment
             recommendation = "Subread (Low Mem)"
        else:
             recommendation = "Salmon (Pseudo) or Subread (Low Mem)"
    
    if warning:
        return f"{recommendation}. {warning}"
    return recommendation
