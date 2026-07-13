from pathlib import Path

from rank_rent.domain.models import DeploymentResult


class LocalStagingDeploymentProvider:
    async def deploy_staging(self, build_directory: Path, project_slug: str) -> DeploymentResult:
        index = build_directory / "index.html"
        if not index.exists():
            return DeploymentResult(
                provider="local",
                url="",
                status="failed",
                error_details=f"{index} does not exist",
            )
        return DeploymentResult(
            provider="local",
            url=build_directory.resolve().as_uri(),
            commit_or_build_id=project_slug,
            status="deployed",
        )

