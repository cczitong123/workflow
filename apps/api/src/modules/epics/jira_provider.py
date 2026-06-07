from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

import config as app_config_module
from config import JiraConfig
from modules.epics.repository import normalize_epic_payload
from modules.shared.models import EpicRecord


class JiraEpicProvider:
    def __init__(self, config: JiraConfig) -> None:
        self.config = config

    def has_saved_token(self, *, token: str | None = None) -> bool:
        return bool(token or self.config.personal_token)

    def validate_token(self, token: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/rest/api/2/myself",
            token=token,
        )

    def connect(self, token: str, remember_locally: bool) -> dict[str, Any]:
        return self.validate_token(token)

    def list_projects(self, *, token: str | None = None) -> list[dict[str, Any]]:
        data = self._request_json(
            "GET",
            "/rest/api/2/project",
            token=token,
        )
        visible_keys = set(self.config.visible_project_keys)
        projects = [
            {
                "key": item.get("key", ""),
                "name": item.get("name", ""),
            }
            for item in data
            if item.get("key") and (not visible_keys or item.get("key", "").upper() in visible_keys)
        ]
        projects.sort(key=lambda item: item["key"])
        return projects[: self.config.project_list_limit]

    def list_epics(self, project_key: str, *, token: str | None = None) -> list[dict[str, Any]]:
        project_key = project_key.strip().upper()
        fields = ["summary", "status", "issuetype"]
        data = self._request_json(
            "GET",
            "/rest/api/2/search",
            token=token,
            params={
                "jql": f'project="{project_key}" AND issuetype=Epic ORDER BY key DESC',
                "fields": ",".join(fields),
                "startAt": 0,
                "maxResults": self.config.epic_list_limit,
            },
        )
        issues = data.get("issues", [])
        return [
            {
                "key": issue.get("key", ""),
                "summary": issue.get("fields", {}).get("summary", ""),
                "status": issue.get("fields", {}).get("status", {}).get("name", ""),
            }
            for issue in issues
            if issue.get("key")
        ]

    def get_epic(self, issue_key: str, *, token: str | None = None) -> EpicRecord:
        issue_key = issue_key.strip().upper()
        fields = [
            "summary",
            "description",
            "status",
            "priority",
            "fixVersions",
            "versions",
            "components",
            "security",
            "labels",
            "issuelinks",
            "subtasks",
            "comment",
            "attachment",
            "issuetype",
            self.config.parent_link_field,
            self.config.epic_link_field,
        ]
        payload = self._request_json(
            "GET",
            f"/rest/api/2/issue/{issue_key}",
            token=token,
            params={"fields": ",".join(fields)},
        )
        fields_payload = payload.get("fields", {})
        attachments = fields_payload.get("attachment", [])
        if attachments:
            fields_payload["attachment"] = [item.get("content") for item in attachments if item.get("content")]
        normalized_payload = {
            "key": payload.get("key", issue_key),
            "fields": fields_payload,
        }
        return normalize_epic_payload(normalized_payload)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        active_token = token or self._current_token()
        if not active_token:
            raise RuntimeError("Jira token is required before Jira data can be loaded.")
        if not self.config.base_url:
            raise RuntimeError("Jira base URL is not configured.")
        headers = {
            "Authorization": f"Bearer {active_token}",
            "Accept": "application/json",
        }
        url = f"{self.config.base_url}{path}"
        with httpx.Client(timeout=60) as client:
            response = client.request(method, url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

    def _current_token(self) -> str:
        return self.config.personal_token
