import shutil
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import Field

from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mcp_server_config.v1.environments.base import Environment

logger = get_logger("cli.config")


class UVEnvironment(Environment):
    """Configuration for Python environment setup."""

    type: Literal["uv"] = "uv"

    python: str | None = Field(
        default=None,
        description="Python version constraint",
        examples=["3.10", "3.11", "3.12"],
    )

    dependencies: list[str] | None = Field(
        default=None,
        description="Python packages to install with PEP 508 specifiers",
        examples=[["fastmcp>=2.0,<3", "httpx", "pandas>=2.0"]],
    )

    requirements: str | None = Field(
        default=None,
        description="Path to requirements.txt file",
        examples=["requirements.txt", "../requirements/prod.txt"],
    )

    project: str | None = Field(
        default=None,
        description="Path to project directory containing pyproject.toml",
        examples=[".", "../my-project"],
    )

    editable: list[str] | None = Field(
        default=None,
        description="Directories to install in editable mode",
        examples=[[".", "../my-package"], ["/path/to/package"]],
    )

    def build_command(self, command: list[str]) -> list[str]:
        """Build complete uv run command with environment args and command to execute.

        Args:
            command: Command to execute (e.g., ["fastmcp", "run", "server.py"])

        Returns:
            Complete command ready for subprocess.run, including "uv" prefix if needed.
            If no environment configuration is set, returns the command unchanged.
        """
        # If no environment setup is needed, return command as-is
        if not self._must_run_with_uv():
            return command

        args = ["uv", "run"]

        # Add project if specified
        if self.project:
            args.extend(["--project", str(self.project)])

        # Add Python version if specified (only if no project, as project has its own Python)
        if self.python and not self.project:
            args.extend(["--python", self.python])

        # Always add dependencies, requirements, and editable packages
        # These work with --project to add additional packages on top of the project env
        if self.dependencies:
            for dep in sorted(set(self.dependencies)):
                args.extend(["--with", dep])

        # Add requirements file
        if self.requirements:
            args.extend(["--with-requirements", str(self.requirements)])

        # Add editable packages
        if self.editable:
            for editable_path in self.editable:
                args.extend(["--with-editable", str(editable_path)])

        # Add the command
        args.extend(command)

        return args

    def _must_run_with_uv(self) -> bool:
        """Check if this environment config requires uv to set up.

        Returns:
            True if any environment settings require uv run
        """
        return any(
            [
                self.python is not None,
                self.dependencies is not None,
                self.requirements is not None,
                self.project is not None,
                self.editable is not None,
            ]
        )

    async def prepare(self, output_dir: Path | None = None) -> None:
        """Prepare the Python environment using uv.

        Args:
            output_dir: Directory where the persistent uv project will be created.
                       If None, creates a temporary directory for ephemeral use.
        """

        # Check if uv is available
        if not shutil.which("uv"):
            raise RuntimeError(
                "uv is not installed. Please install it with: "
                "curl -LsSf https://astral.sh/uv/install.sh | sh"
            )

        # Only prepare environment if there are actual settings to apply
        if not self._must_run_with_uv():
            logger.debug("No environment settings configured, skipping preparation")
            return

        # Handle None case for ephemeral use
        if output_dir is None:
            import tempfile

            output_dir = Path(tempfile.mkdtemp(prefix="fastmcp-env-"))
            logger.info(f"Creating ephemeral environment in {output_dir}")
        else:
            logger.info(f"Creating persistent environment in {output_dir}")
            output_dir = Path(output_dir).resolve()

        # Initialize the project
        logger.debug(f"Initializing uv project in {output_dir}")
        try:
            subprocess.run(
                [
                    "uv",
                    "init",
                    "--project",
                    str(output_dir),
                    "--name",
                    "fastmcp-env",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            # If project already exists, that's fine - continue
            if "already initialized" in e.stderr.lower():
                logger.debug(
                    f"Project already initialized at {output_dir}, continuing..."
                )
            else:
                logger.error(f"Failed to initialize project: {e.stderr}")
                raise RuntimeError(f"Failed to initialize project: {e.stderr}") from e

        # Pin Python version if specified
        if self.python:
            logger.debug(f"Pinning Python version to {self.python}")
            try:
                subprocess.run(
                    [
                        "uv",
                        "python",
                        "pin",
                        self.python,
                        "--project",
                        str(output_dir),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to pin Python version: {e.stderr}")
                raise RuntimeError(f"Failed to pin Python version: {e.stderr}") from e

        # Add dependencies with --no-sync to defer installation
        # dependencies ALWAYS include fastmcp; this is compatible with
        # specific fastmcp versions that might be in the dependencies list
        dependencies = (self.dependencies or []) + ["fastmcp"]
        logger.debug(f"Adding dependencies: {', '.join(dependencies)}")
        try:
            subprocess.run(
                [
                    "uv",
                    "add",
                    *dependencies,
                    "--no-sync",
                    "--project",
                    str(output_dir),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to add dependencies: {e.stderr}")
            raise RuntimeError(f"Failed to add dependencies: {e.stderr}") from e

        # Add requirements file if specified
        if self.requirements:
            logger.debug(f"Adding requirements from {self.requirements}")
            # Resolve requirements path relative to current directory
            req_path = Path(self.requirements).resolve()
            try:
                subprocess.run(
                    [
                        "uv",
                        "add",
                        "-r",
                        str(req_path),
                        "--no-sync",
                        "--project",
                        str(output_dir),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to add requirements: {e.stderr}")
                raise RuntimeError(f"Failed to add requirements: {e.stderr}") from e

        # Add editable packages if specified
        if self.editable:
            editable_paths = [str(Path(e).resolve()) for e in self.editable]
            logger.debug(f"Adding editable packages: {', '.join(editable_paths)}")
            try:
                subprocess.run(
                    [
                        "uv",
                        "add",
                        "--editable",
                        *editable_paths,
                        "--no-sync",
                        "--project",
                        str(output_dir),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to add editable packages: {e.stderr}")
                raise RuntimeError(
                    f"Failed to add editable packages: {e.stderr}"
                ) from e

        # Final sync to install everything
        logger.info("Installing dependencies...")
        try:
            subprocess.run(
                ["uv", "sync", "--project", str(output_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to sync dependencies: {e.stderr}")
            raise RuntimeError(f"Failed to sync dependencies: {e.stderr}") from e

        logger.info(f"Environment prepared successfully in {output_dir}")
