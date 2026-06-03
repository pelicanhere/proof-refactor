"""
Lean workspace checking utilities.
"""

import subprocess
from pathlib import Path
from typing import List, Tuple
from multiprocessing import Pool, cpu_count


def find_lean_files(folder_path: str | Path) -> List[Path]:
    """
    Recursively find all .lean files in a folder.

    Args:
        folder_path: Path to the folder to search

    Returns:
        Sorted list of .lean file paths
    """
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder does not exist: {folder_path}")

    if not folder.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {folder_path}")

    lean_files = []
    for file_path in folder.rglob("*.lean"):
        if file_path.is_file():
            lean_files.append(file_path)

    return sorted(lean_files)


def find_lean_project_root(file_path: Path) -> Path:
    """
    Find the Lean project root (directory containing lean-toolchain).

    Args:
        file_path: Path to a file or directory

    Returns:
        Project root path, or the file's parent if not found
    """
    current = file_path.parent if file_path.is_file() else file_path
    while current != current.parent:  # Until reaching root
        lean_toolchain = current / "lean-toolchain"
        if lean_toolchain.exists():
            return current
        current = current.parent
    # If not found, return file's parent directory
    return file_path.parent if file_path.is_file() else file_path


def check_lean_file(file_path: Path, timeout: int = 600) -> Tuple[bool, bool, str, str]:
    """
    Check a single .lean file for errors and sorry warnings.

    Args:
        file_path: Path to the .lean file
        timeout:   Seconds to wait for lake env lean (default 600)

    Returns:
        (has_error, has_sorry_warning, stdout, stderr)
    """
    try:
        # Find project root containing lean-toolchain
        project_root = find_lean_project_root(file_path)

        # Use lake env lean command to check the file
        result = subprocess.run(
            ["lake", "env", "lean", str(file_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(project_root),  # Run in project root
        )

        stdout = result.stdout
        stderr = result.stderr

        # Check for errors
        has_error = (
            "error" in stdout.lower()
            or "error" in stderr.lower()
            or result.returncode != 0
        )

        # Check for sorry warnings
        has_sorry_warning = "sorry" in stdout.lower() or "sorry" in stderr.lower()

        return has_error, has_sorry_warning, stdout, stderr

    except subprocess.TimeoutExpired:
        return True, False, "", f"Check timed out ({timeout}s)"
    except Exception as e:
        return True, False, "", f"Execution error: {str(e)}"


def check_lake_build(workspace_dir: str | Path, timeout: int = 600) -> Tuple[bool, str, str]:
    """
    Run `lake build` from an explicit Lean workspace directory.

    Args:
        workspace_dir: Lean workspace root, normally task.cwd
        timeout: Seconds to wait for lake build

    Returns:
        (has_error, stdout, stderr)
    """
    try:
        root = Path(workspace_dir).resolve()
        result = subprocess.run(
            ["lake", "build"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(root),
        )
        return result.returncode != 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return True, "", f"lake build timed out ({timeout}s)"
    except Exception as e:
        return True, "", f"lake build execution error: {str(e)}"


def _check_wrapper(file_path: Path) -> Tuple[Path, bool, bool, str, str]:
    """
    Wrapper function for multiprocessing.

    Returns:
        (file_path, has_error, has_sorry_warning, stdout, stderr)
    """
    has_error, has_sorry_warning, stdout, stderr = check_lean_file(file_path)
    return (file_path, has_error, has_sorry_warning, stdout, stderr)


def check_lean_files_parallel(
    lean_files: List[Path], num_proc: int = 1
) -> List[Tuple[Path, bool, bool, str, str]]:
    """
    Check multiple .lean files in parallel.

    Args:
        lean_files: List of .lean file paths
        num_proc: Number of parallel processes (default: 1; pass None for CPU count)

    Returns:
        List of (file_path, has_error, has_sorry_warning, stdout, stderr)
    """
    if num_proc is None:
        num_proc = cpu_count()

    with Pool(processes=num_proc) as pool:
        results = pool.map(_check_wrapper, lean_files)

    return results


def remove_extraction_import(file_path: Path) -> bool:
    """
    Strip any line whose content is exactly `import Extraction` from file_path.

    Returns True if at least one line was removed, False otherwise.
    """
    text = file_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    new_lines = [line for line in lines if line.strip() != "import Extraction"]
    if len(new_lines) == len(lines):
        return False
    file_path.write_text("".join(new_lines), encoding="utf-8")
    return True
