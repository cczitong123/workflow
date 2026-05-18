from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

import config as app_config_module
from config import JiraConfig, load_app_config
from modules.epics.repository import normalize_epic_payload
from modules.shared.models import EpicRecord


class JiraEpicProvider:
    def __init__(self, config: JiraConfig) -> None:
        self.config = config
        self._runtime_token: str | None = None

    def has_saved_token(self) -> bool:
        return bool(self._current_token())

    def validate_token(self, token: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/rest/api/2/myself",
            token=token,
        )

    def connect(self, token: str, remember_locally: bool) -> dict[str, Any]:
        profile = self.validate_token(token)
        self._runtime_token = token
        os.environ[f"{app_config_module.ENV_PREFIX}_JIRA_PERSONAL_TOKEN"] = token
        if remember_locally:
            _write_env_value("AGENTIC_WORKFLOW_JIRA_PERSONAL_TOKEN", token)
            refreshed = load_app_config()
            self.config = refreshed.jira
        return profile

    def list_projects(self) -> list[dict[str, Any]]:
        data = self._request_json(
            "GET",
            "/rest/api/2/project",
        )
        projects = [
            {
                "key": item.get("key", ""),
                "name": item.get("name", ""),
            }
            for item in data
            if item.get("key")
        ]
        projects.sort(key=lambda item: item["key"])
        return projects[: self.config.project_list_limit]

    def list_epics(self, project_key: str) -> list[dict[str, Any]]:
        project_key = project_key.strip().upper()
        fields = ["summary", "status", "issuetype"]
        data = self._request_json(
            "GET",
            "/rest/api/2/search",
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

    def get_epic(self, issue_key: str) -> EpicRecord:
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
        if self._runtime_token:
            return self._runtime_token
        return self.config.personal_token


def _write_env_value(key: str, value: str) -> None:
    env_path = app_config_module.PROJECT_ROOT / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    updated = False
    new_lines: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
