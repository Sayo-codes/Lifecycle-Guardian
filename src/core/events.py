"""GitLab Webhook Event models and data schemas."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GitLabUser(BaseModel):
    id: int
    name: str
    username: str
    avatar_url: Optional[str] = None
    email: Optional[str] = None


class GitLabProject(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    web_url: str
    git_ssh_url: Optional[str] = None
    git_http_url: Optional[str] = None
    path_with_namespace: str
    default_branch: Optional[str] = "main"


class MergeRequestAttributes(BaseModel):
    id: int
    iid: int
    title: str
    description: Optional[str] = None
    state: str
    action: Optional[str] = None
    target_branch: str
    source_branch: str
    target_project_id: int
    source_project_id: int
    author_id: int
    merge_commit_sha: Optional[str] = None
    last_commit: Optional[Dict[str, Any]] = None
    url: Optional[str] = None
    labels: List[Dict[str, Any]] = Field(default_factory=list)


class MergeRequestEvent(BaseModel):
    """Payload sent by GitLab when a merge request event occurs."""

    object_kind: str
    event_type: Optional[str] = None
    user: GitLabUser
    project: GitLabProject
    object_attributes: MergeRequestAttributes
    labels: List[Dict[str, Any]] = Field(default_factory=list)
    changes: Optional[Dict[str, Any]] = None

    @property
    def is_merged(self) -> bool:
        """Check whether the event corresponds to an MR that was just merged."""
        return (
            self.object_kind == "merge_request"
            and (
                self.object_attributes.action == "merge"
                or self.object_attributes.state == "merged"
            )
        )
