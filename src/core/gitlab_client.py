from typing import Any, Dict, List, Optional

try:
    import gitlab
    _has_gitlab = True
except ImportError:
    gitlab = None
    _has_gitlab = False

from src.config import settings
from src.utils.logger import logger



class GitLabClient:
    """Wrapper around python-gitlab providing DevSecOps lifecycle operations."""

    def __init__(
        self,
        url: Optional[str] = None,
        private_token: Optional[str] = None,
        dry_run: Optional[bool] = None,
    ):
        self.url = url or settings.gitlab_url
        self.token = private_token or settings.gitlab_private_token
        self.dry_run = dry_run if dry_run is not None else settings.dry_run
        self._gl = None

        if _has_gitlab and self.token and not self.dry_run:
            try:
                self._gl = gitlab.Gitlab(self.url, private_token=self.token)
                self._gl.auth()
                logger.info("gitlab_connected", url=self.url)
            except Exception as e:
                logger.warning("gitlab_connection_failed", error=str(e), fallback="dry_run_mode")
                self._gl = None
        else:
            logger.info("gitlab_client_initialized", mode="dry_run" if self.dry_run else "no_token_stub")


    def get_project(self, project_id: int) -> Optional[Any]:
        """Fetch project instance."""
        if not self._gl:
            return None
        try:
            return self._gl.projects.get(project_id)
        except Exception as e:
            logger.error("get_project_failed", project_id=project_id, error=str(e))
            return None

    def post_mr_comment(self, project_id: int, mr_iid: int, body: str) -> bool:
        """Post a comment/note to a merge request."""
        if self.dry_run or not self._gl:
            logger.info("[DRY_RUN] post_mr_comment", project_id=project_id, mr_iid=mr_iid, body_preview=body[:100])
            return True

        project = self.get_project(project_id)
        if not project:
            return False

        try:
            mr = project.mergerequests.get(mr_iid)
            mr.notes.create({"body": body})
            logger.info("mr_comment_posted", project_id=project_id, mr_iid=mr_iid)
            return True
        except Exception as e:
            logger.error("post_mr_comment_failed", project_id=project_id, mr_iid=mr_iid, error=str(e))
            return False

    def get_mr_pipelines(self, project_id: int, mr_iid: int) -> List[Dict[str, Any]]:
        """Fetch pipelines associated with a merge request."""
        if not self._gl:
            return []
        project = self.get_project(project_id)
        if not project:
            return []
        try:
            mr = project.mergerequests.get(mr_iid)
            pipelines = mr.pipelines()
            return [p.asdict() for p in pipelines]
        except Exception as e:
            logger.error("get_mr_pipelines_failed", project_id=project_id, mr_iid=mr_iid, error=str(e))
            return []

    def create_release(
        self,
        project_id: int,
        tag_name: str,
        name: str,
        description: str,
        ref: str = "main",
    ) -> bool:
        """Create a GitLab Release with a Git tag."""
        if self.dry_run or not self._gl:
            logger.info(
                "[DRY_RUN] create_release",
                project_id=project_id,
                tag_name=tag_name,
                name=name,
                ref=ref,
            )
            return True

        project = self.get_project(project_id)
        if not project:
            return False

        try:
            project.releases.create({
                "name": name,
                "tag_name": tag_name,
                "ref": ref,
                "description": description,
            })
            logger.info("release_created", project_id=project_id, tag=tag_name)
            return True
        except Exception as e:
            logger.error("create_release_failed", project_id=project_id, tag=tag_name, error=str(e))
            return False

    def create_issue(
        self,
        project_id: int,
        title: str,
        description: str,
        labels: Optional[List[str]] = None,
    ) -> Optional[int]:
        """Create a GitLab issue (e.g. for security gate blocking or alerts)."""
        if self.dry_run or not self._gl:
            logger.info("[DRY_RUN] create_issue", project_id=project_id, title=title)
            return 9999

        project = self.get_project(project_id)
        if not project:
            return None

        try:
            issue = project.issues.create({
                "title": title,
                "description": description,
                "labels": labels or ["devsecops", "lifecycle-guardian"],
            })
            logger.info("issue_created", project_id=project_id, issue_iid=issue.iid)
            return issue.iid
        except Exception as e:
            logger.error("create_issue_failed", project_id=project_id, error=str(e))
            return None


gitlab_client = GitLabClient()
