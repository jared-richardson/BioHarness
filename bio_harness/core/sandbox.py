from pathlib import Path
import os
import logging

logger = logging.getLogger(__name__)

class BioSandboxError(Exception):
    """Custom exception for BioSandbox related errors."""
    pass

class BioSandbox:
    """
    Manages safe path validation and file import operations within the Bio-Harness workspace.
    Ensures that file writes are restricted to the designated 'workspace/' directory.
    """
    def __init__(self, workspace_root: Path):
        """
        Initializes the BioSandbox with the path to the workspace root.

        Args:
            workspace_root: The pathlib.Path object pointing to the root of the workspace directory.
        """
        self.workspace_root = workspace_root.resolve()
        if not self.workspace_root.is_dir():
            logger.warning(f"Workspace root '{self.workspace_root}' does not exist, creating it.")
            self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.input_dir = self.workspace_root / "inputs"
        self.input_dir.mkdir(parents=True, exist_ok=True) # Ensure inputs directory exists

    def validate_path(self, path: Path, allow_write: bool = False) -> Path:
        """
        Validates a given path against sandbox rules.

        Args:
            path: The pathlib.Path object to validate.
            allow_write: If True, the path must resolve to be inside the workspace_root.
                         If False, the path can be anywhere (system read access).

        Returns:
            The resolved and validated pathlib.Path object.

        Raises:
            BioSandboxError: If the path violates sandbox rules.
        """
        resolved_path = path.resolve()

        if allow_write:
            # For write operations, path must be inside the workspace_root
            try:
                resolved_path.relative_to(self.workspace_root)
            except ValueError:
                raise BioSandboxError(
                    f"Write access denied: Path '{path}' resolves to '{resolved_path}', "
                    f"which is outside the allowed workspace: '{self.workspace_root}'."
                )
        # For read operations (allow_write=False), any path is allowed,
        # but we still resolve it to ensure it's a valid, absolute path.

        return resolved_path

    def import_file(self, src: Path) -> Path:
        """
        Imports a file by creating a symlink from the source file to workspace/inputs/{filename}.
        This protects the original data from modification.

        Args:
            src: The pathlib.Path object of the source file to import.

        Returns:
            The pathlib.Path object of the created symlink within workspace/inputs/.

        Raises:
            BioSandboxError: If the source file does not exist, or if symlink creation fails.
        """
        src_resolved = src.resolve()

        if not src_resolved.is_file():
            raise BioSandboxError(f"Source file for import does not exist: '{src_resolved}'")

        dest_file = self.input_dir / src_resolved.name
        
        # Ensure the destination directory exists (self.input_dir is already created in __init__)
        # If a symlink with the same name already exists, remove it before creating a new one
        if dest_file.exists():
            if dest_file.is_symlink():
                dest_file.unlink() # Remove existing symlink
            else:
                raise BioSandboxError(f"Destination '{dest_file}' exists and is not a symlink. Cannot import.")

        try:
            os.symlink(src_resolved, dest_file)
            logger.info(f"Created symlink from '{src_resolved}' to '{dest_file}'")
        except OSError as e:
            raise BioSandboxError(f"Failed to create symlink for '{src_resolved}' to '{dest_file}': {e}") from e
        
        return dest_file
