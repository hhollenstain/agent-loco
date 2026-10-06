from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import docker
import docker.errors


@dataclass
class DockerResult:
    """Result of a Docker container execution."""

    success: bool
    return_code: int
    stdout: str
    stderr: str
    image_tag: str
    container_id: str
    build_log: str = ""


class DockerRunnerError(Exception):
    """Base exception for DockerRunner errors."""

    pass


class DockerDaemonError(DockerRunnerError):
    """Raised when Docker daemon is unreachable."""

    pass


class ImageBuildError(DockerRunnerError):
    """Raised when image build fails."""

    def __init__(self, message: str, build_log: str):
        super().__init__(message)
        self.build_log = build_log


class ContainerRunError(DockerRunnerError):
    """Raised when container exits with non-zero code."""

    def __init__(self, message: str, return_code: int, stdout: str, stderr: str):
        super().__init__(message)
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr


class DockerRunner:
    """
    Core Docker execution logic for sandboxing commands.

    Shared by both CLI wrapper and agent tool wrapper.
    """

    def __init__(
        self,
        docker_host: str | None = None,
        default_timeout: int = 300,
    ):
        """
        Initialize DockerRunner with connection parameters.

        Args:
            docker_host: Docker daemon endpoint (e.g., "unix:///var/run/docker.sock")
                if None, uses local socket via docker.from_env()
            default_timeout: Default command timeout in seconds
        """
        try:
            if docker_host:
                self._client = docker.DockerClient(base_url=docker_host)
            else:
                self._client = docker.from_env()
            self._default_timeout = default_timeout
        except Exception as exc:
            raise DockerDaemonError(f"Failed to connect to Docker daemon: {exc}") from exc

    def build_image(
        self,
        workspace: Path,
        tag: str,
        dockerfile: Path | None = None,
    ) -> DockerResult:
        """
        Build Docker image from workspace.

        Args:
            workspace: Path to workspace directory to Dockerize
            tag: Image tag (e.g., "agent-loco-sandbox:abc123")
            dockerfile: Optional custom Dockerfile; if None, uses bundled template

        Returns:
            DockerResult with build_log and status

        Raises:
            ImageBuildError: When image build fails
            DockerDaemonError: When daemon is unreachable
        """
        try:
            # Build the image using the workspace as context
            build_logs = []

            for log_line in self._client.images.build(
                path=str(workspace),
                tag=tag,
                dockerfile=str(dockerfile) if dockerfile else None,
                rm=True,
                decode=True,
            ):
                if "stream" in log_line:
                    build_logs.append(log_line["stream"])
                elif "error" in log_line:
                    error_msg = log_line.get("errorDetail", {}).get("message", log_line["error"])
                    raise ImageBuildError(error_msg, "\n".join(build_logs))

            # If we successfully built, return success
            return DockerResult(
                success=True,
                return_code=0,
                stdout="",
                stderr="",
                image_tag=tag,
                container_id="",
                build_log="\n".join(build_logs),
            )

        except docker.errors.BuildError as exc:
            logs = "\n".join(getattr(exc, "build_logs", []))
            raise ImageBuildError(f"Build failed: {exc.msg}", logs) from exc
        except Exception as exc:
            raise DockerDaemonError(f"Failed to build image: {exc}") from exc

    def run_command(
        self,
        workspace: Path,
        env_vars: dict[str, str] | None = None,
        command: str | None = None,
        timeout: int | None = None,
        custom_dockerfile: Path | None = None,
        image_tag: str | None = None,
    ) -> DockerResult:
        """
        Run a command in a sandboxed container.

        Args:
            workspace: Path to workspace directory (mounted as /workspace in container)
            env_vars: Environment variables to set in container
            command: Command to execute (e.g., "echo hello")
            timeout: Command timeout in seconds (overrides default_timeout if provided)
            custom_dockerfile: Optional custom Dockerfile path
            image_tag: Optional image tag; if None, generated from workspace hash

        Returns:
            DockerResult with command output and status

        Raises:
            DockerDaemonError: When daemon is unreachable
            ImageBuildError: When image build fails
            ContainerRunError: When command fails with non-zero exit
        """
        timeout = timeout or self._default_timeout

        if command is None:
            raise ValueError("Command is required")

        try:
            # Generate a unique tag based on workspace hash
            tag = image_tag or self._generate_image_tag(workspace)
            dockerfile_path = custom_dockerfile or self._default_dockerfile_path()

            # Build the container image first
            build_result = self.build_image(
                workspace=workspace,
                tag=tag,
                dockerfile=dockerfile_path,
            )

            if not build_result.success:
                raise ImageBuildError("Image build failed", build_result.build_log)

            # Run command in container with proper security settings
            container = self._client.containers.run(
                image=tag,
                command=["/bin/sh", "-c", command],
                environment=env_vars or {},
                working_dir="/workspace",
                volumes={str(workspace): {"bind": "/workspace", "mode": "rw"}},
                network_mode="none",
                privileged=False,
                read_only=True,
                cap_drop=["ALL"],
                user="1000:1000",
                remove=True,
                timeout=timeout,
                stdout=True,
                stderr=True,
            )

            # Note: container.run with remove=True doesn't return the output
            # So we need to run it differently and get the logs from a detached version
            container = self._client.containers.run(
                image=tag,
                command=["/bin/sh", "-c", command],
                environment=env_vars or {},
                working_dir="/workspace",
                volumes={str(workspace): {"bind": "/workspace", "mode": "rw"}},
                network_mode="none",
                privileged=False,
                read_only=True,
                cap_drop=["ALL"],
                user="1000:1000",
                detach=True,
                timeout=timeout,
            )

            # Get container output and exit code
            stdout_result = container.logs(stdout=True, stderr=False)
            stderr_result = container.logs(stdout=False, stderr=True)
            wait_result = container.wait()
            exit_code = wait_result["StatusCode"]

            stdout = (
                stdout_result.decode("utf-8") if isinstance(stdout_result, bytes) else stdout_result
            )
            stderr = (
                stderr_result.decode("utf-8") if isinstance(stderr_result, bytes) else stderr_result
            )
            return DockerResult(
                success=exit_code == 0,
                return_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                image_tag=tag,
                container_id=container.id,
            )

        except docker.errors.ContainerError as exc:
            raise ContainerRunError(
                f"Container failed with exit code {exc.exit_status}",
                exc.exit_status,
                exc.stderr.decode() if exc.stderr else "",
                exc.stdout.decode() if exc.stdout else "",
            ) from exc
        except docker.errors.ImageNotFound as exc:
            raise ImageBuildError(f"Image not found: {exc}", "") from exc
        except docker.errors.APIError as exc:
            raise DockerDaemonError(f"Docker API error: {exc.explanation}") from exc
        except Exception as exc:
            raise ContainerRunError(
                f"Unexpected error running container: {exc}",
                255,
                "",
                str(exc),
            ) from exc

    def _generate_image_tag(self, workspace: Path) -> str:
        """Generate a unique image tag based on workspace hash."""
        import hashlib

        workspace_str = str(workspace)
        hash_digest = hashlib.sha256(workspace_str.encode()).hexdigest()[:12]
        return f"agent-loco-sandbox:{hash_digest}"

    def _default_dockerfile_path(self) -> Path:
        """Return the path to the bundled Dockerfile template."""
        return Path(__file__).parent / "templates" / "Dockerfile.sandbox"

    def cleanup(self) -> int:
        """
        Remove cached images built by this runner.

        Returns:
            Approximate disk space freed in MB
        """
        try:
            # List and remove all agent-loco-sandbox images
            images = self._client.images.list(name="agent-loco-sandbox")
            total_bytes = 0

            for image in images:
                # Get size before removing
                if "Size" in image.attrs:
                    total_bytes += image.attrs["Size"]
                image.remove()

            return int(total_bytes / (1024 * 1024))

        except Exception as exc:
            raise DockerDaemonError(f"Cleanup failed: {exc}") from exc
