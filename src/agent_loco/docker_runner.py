from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, Any

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
        docker_host: Optional[str] = None,
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
        except Exception as e:
            raise DockerDaemonError(f"Failed to connect to Docker daemon: {e}") from e

    def build_image(
        self,
        workspace: Path,
        tag: str,
        dockerfile: Optional[Path] = None,
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

        except docker.errors.BuildError as e:
            raise ImageBuildError(f"Build failed: {e.msg}", "\n".join(e.build_logs))
        except Exception as e:
            raise DockerDaemonError(f"Failed to build image: {e}") from e

    def run_command(
        self,
        workspace: Path,
        env_vars: Optional[Dict[str, str]] = None,
        command: Optional[str] = None,
        timeout: Optional[int] = None,
        custom_dockerfile: Optional[Path] = None,
        image_tag: Optional[str] = None,
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
            build_result = self.build_image(workspace=workspace, tag=tag, dockerfile=dockerfile_path)
            
            if not build_result.success:
                raise ImageBuildError("Image build failed", build_result.build_log)
                
            # Run command in container with proper security settings
            result = self._client.containers.run(
                image=tag,
                command=command,
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
            )
            
            # For exec_run with correct output capture:
            # Let's modify approach to use exec_run for better output capture
            result = self._client.containers.run(
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
            
            # Since containers.run with remove=True doesn't return stdout/stderr directly,
            # we need to make this work properly with exec_run
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
            
            # Attach to container for outputs
            stdout, stderr = container.logs(stdout=True, stderr=True)
            
            # Wait for completion and get exit code
            exit_code = container.wait()["StatusCode"]
            
            return DockerResult(
                success=exit_code == 0,
                return_code=exit_code,
                stdout=stdout.decode("utf-8") if isinstance(stdout, bytes) else stdout,
                stderr=stderr.decode("utf-8") if isinstance(stderr, bytes) else stderr,
                image_tag=tag,
                container_id=container.id,
            )

        except docker.errors.ContainerError as e:
            raise ContainerRunError(
                f"Container failed with exit code {e.exit_status}",
                e.exit_status,
                e.stderr.decode() if e.stderr else "",
                e.stdout.decode() if e.stdout else "",
            ) from e
        except docker.errors.ImageNotFound as e:
            raise ImageBuildError(f"Image not found: {e}", "")
        except docker.errors.APIError as e:
            raise DockerDaemonError(f"Docker API error: {e.explanation}") from e
        except Exception as e:
            raise ContainerRunError(
                f"Unexpected error running container: {e}",
                255,
                "",
                str(e),
            ) from e

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
            
        except Exception as e:
            raise DockerDaemonError(f"Cleanup failed: {e}") from e
