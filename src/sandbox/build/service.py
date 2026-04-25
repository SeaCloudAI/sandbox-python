from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote, urlencode

from ..core.transport import BaseTransport
from ..core.exceptions import ValidationError
from .models import (
    BuildLogsParams,
    BuildStatusParams,
    GetTemplateParams,
    ListTemplatesParams,
    TemplateCreateRequest,
    TemplateUpdateRequest,
)

_TEMPLATE_REQUEST_FIELDS = {
    "name",
    "visibility",
    "baseTemplateID",
    "dockerfile",
    "image",
    "envs",
    "cpuCount",
    "memoryMB",
    "diskSizeMB",
    "ttlSeconds",
    "port",
    "startCmd",
    "readyCmd",
}


class BuildService(BaseTransport):
    def direct_build(self, body: Mapping[str, Any]) -> dict[str, Any]:
        if body is None:
            raise ValidationError("direct build request is required")
        return self._request_json(
            "POST",
            "/build",
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
            expected_statuses=(202,),
        )

    def create_template(
        self,
        body: TemplateCreateRequest | None = None,
    ) -> dict[str, Any]:
        self._validate_template_body(body)
        return self._request_json(
            "POST",
            "/api/v1/templates",
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body or {},
            expected_statuses=(202,),
        )

    def list_templates(
        self,
        params: ListTemplatesParams | None = None,
    ) -> list[dict[str, Any]]:
        self._validate_list_templates_params(params)
        path = self._with_query("/api/v1/templates", self._encode_list_templates_params(params))
        return self._request_json("GET", path)

    def get_template_by_alias(self, alias: str) -> dict[str, Any]:
        if not alias.strip():
            raise ValidationError("alias is required")
        return self._request_json("GET", f"/api/v1/templates/aliases/{quote(alias, safe='')}")

    def get_template(
        self,
        template_id: str,
        params: GetTemplateParams | None = None,
    ) -> dict[str, Any]:
        self._require_template_id(template_id)
        self._validate_get_template_params(params)
        path = self._with_query(
            f"/api/v1/templates/{quote(template_id, safe='')}",
            self._encode_get_template_params(params),
        )
        return self._request_json("GET", path)

    def update_template(
        self,
        template_id: str,
        body: TemplateUpdateRequest | None = None,
    ) -> dict[str, Any]:
        self._require_template_id(template_id)
        self._validate_template_body(body)
        return self._request_json(
            "PATCH",
            f"/api/v1/templates/{quote(template_id, safe='')}",
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body or {},
        )

    def delete_template(self, template_id: str) -> None:
        self._require_template_id(template_id)
        self._request_empty(
            "DELETE",
            f"/api/v1/templates/{quote(template_id, safe='')}",
            expected_statuses=(204,),
        )

    def create_build(
        self,
        template_id: str,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_template_id(template_id)
        self._validate_build_request(body)
        response = self._request_json(
            "POST",
            f"/api/v1/templates/{quote(template_id, safe='')}/builds",
            headers=None if body is None or self._is_empty_build_request(body) else self.build_headers({"Content-Type": "application/json"}),
            body=None if body is None or self._is_empty_build_request(body) else body,
            expected_statuses=(202,),
        )
        result = dict(response)
        result["empty"] = len(result) == 0
        return result

    def get_build_file(self, template_id: str, hash_value: str) -> dict[str, Any]:
        self._require_template_id(template_id)
        self._require_hash(hash_value)
        return self._request_json("GET", f"/api/v1/templates/{quote(template_id, safe='')}/files/{quote(hash_value, safe='')}")

    def rollback_template(
        self,
        template_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._require_template_id(template_id)
        if not str(body.get("buildID", "")).strip():
            raise ValidationError("buildID is required")
        return self._request_json(
            "POST",
            f"/api/v1/templates/{quote(template_id, safe='')}/rollback",
            headers=self.build_headers({"Content-Type": "application/json"}),
            body=body,
        )

    def list_builds(self, template_id: str) -> dict[str, Any]:
        self._require_template_id(template_id)
        return self._request_json("GET", f"/api/v1/templates/{quote(template_id, safe='')}/builds")

    def get_build(
        self,
        template_id: str,
        build_id: str,
    ) -> dict[str, Any]:
        self._require_template_id(template_id)
        self._require_build_id(build_id)
        return self._request_json("GET", f"/api/v1/templates/{quote(template_id, safe='')}/builds/{quote(build_id, safe='')}")

    def get_build_status(
        self,
        template_id: str,
        build_id: str,
        params: BuildStatusParams | None = None,
    ) -> dict[str, Any]:
        self._require_template_id(template_id)
        self._require_build_id(build_id)
        self._validate_build_status_params(params)
        path = self._with_query(
            f"/api/v1/templates/{quote(template_id, safe='')}/builds/{quote(build_id, safe='')}/status",
            self._encode_build_status_params(params),
        )
        raw = self._request_json("GET", path)
        return self._normalize_build_status_response(raw)

    def get_build_logs(
        self,
        template_id: str,
        build_id: str,
        params: BuildLogsParams | None = None,
    ) -> dict[str, Any]:
        self._require_template_id(template_id)
        self._require_build_id(build_id)
        self._validate_build_logs_params(params)
        path = self._with_query(
            f"/api/v1/templates/{quote(template_id, safe='')}/builds/{quote(build_id, safe='')}/logs",
            self._encode_build_logs_params(params),
        )
        return self._request_json("GET", path)

    def _require_template_id(self, template_id: str) -> None:
        if not template_id.strip():
            raise ValidationError("template_id is required")

    def _require_build_id(self, build_id: str) -> None:
        if not build_id.strip():
            raise ValidationError("build_id is required")

    def _require_hash(self, hash_value: str) -> None:
        if not hash_value.strip():
            raise ValidationError("hash is required")
        if len(hash_value) != 64 or any(ch not in "0123456789abcdef" for ch in hash_value):
            raise ValidationError("hash must be a 64-character lowercase hex SHA256")

    def _validate_list_templates_params(self, params: ListTemplatesParams | None) -> None:
        if params is None:
            return
        if params.limit is not None and (not isinstance(params.limit, int) or params.limit < 0 or params.limit > 100):
            raise ValidationError("template list limit must be an integer between 0 and 100")
        if params.offset is not None and (not isinstance(params.offset, int) or params.offset < 0):
            raise ValidationError("template list offset must be a non-negative integer")

    def _validate_get_template_params(self, params: GetTemplateParams | None) -> None:
        if params is None:
            return
        if params.limit is not None and (not isinstance(params.limit, int) or params.limit < 0 or params.limit > 100):
            raise ValidationError("template build history limit must be an integer between 0 and 100")

    def _validate_template_body(self, body: Mapping[str, Any] | None) -> None:
        if body is None:
            return
        for key in body.keys():
            if key not in _TEMPLATE_REQUEST_FIELDS:
                raise ValidationError(f"template field {key} is not supported by the public SDK")
        visibility = str(body.get("visibility", "")).strip().lower()
        if visibility == "official":
            raise ValidationError("official templates are not supported by the public SDK")

    def _validate_build_request(self, body: Mapping[str, Any] | None) -> None:
        if body is None:
            return
        build_id = str(body.get("buildID", "")).strip()
        if build_id:
            if len(build_id) > 63 or not self._is_dns_label(build_id):
                raise ValidationError("buildID must be a lowercase DNS label up to 63 characters")
        files_hash = str(body.get("filesHash", "")).strip()
        if files_hash and not self._is_sha256(files_hash):
            raise ValidationError("filesHash must be a 64-character lowercase hex SHA256")
        if str(body.get("fromImageRegistry", "")).strip():
            raise ValidationError("fromImageRegistry is not supported yet")
        if body.get("force") is not None:
            raise ValidationError("force rebuild is not supported yet")

        hashes: set[str] = set()
        if files_hash:
            hashes.add(files_hash)
        for index, step in enumerate(body.get("steps") or []):
            step_type = str(step.get("type", "")).strip()
            if not step_type:
                raise ValidationError(f"steps[{index}].type is required")
            if step_type not in {"files", "context"}:
                raise ValidationError(f"steps[{index}].type must be files or context")
            step_hash = str(step.get("filesHash", "")).strip()
            if not step_hash:
                raise ValidationError(f"steps[{index}].filesHash is required")
            if not self._is_sha256(step_hash):
                raise ValidationError(f"steps[{index}].filesHash must be a 64-character lowercase hex SHA256")
            if step.get("args"):
                raise ValidationError(f"steps[{index}].args is not supported yet")
            if step.get("force") is not None:
                raise ValidationError(f"steps[{index}].force is not supported yet")
            hashes.add(step_hash)
        if len(hashes) > 1:
            raise ValidationError("multiple different filesHash values are not supported yet")

    def _validate_build_status_params(self, params: BuildStatusParams | None) -> None:
        if params is None:
            return
        if params.logs_offset is not None and (not isinstance(params.logs_offset, int) or params.logs_offset < 0):
            raise ValidationError("build logsOffset must be a non-negative integer")
        if params.limit is not None and (not isinstance(params.limit, int) or params.limit < 0 or params.limit > 100):
            raise ValidationError("build status limit must be an integer between 0 and 100")

    def _validate_build_logs_params(self, params: BuildLogsParams | None) -> None:
        if params is None:
            return
        if params.cursor is not None and (not isinstance(params.cursor, int) or params.cursor < 0):
            raise ValidationError("build logs cursor must be a non-negative integer")
        if params.limit is not None and (not isinstance(params.limit, int) or params.limit < 0 or params.limit > 100):
            raise ValidationError("build logs limit must be an integer between 0 and 100")
        if params.direction is not None and params.direction not in {"forward", "backward"}:
            raise ValidationError('build logs direction must be "forward" or "backward"')
        if params.source is not None and params.source not in {"temporary", "persistent"}:
            raise ValidationError('build logs source must be "temporary" or "persistent"')

    def _encode_list_templates_params(self, params: ListTemplatesParams | None) -> dict[str, Any]:
        if params is None:
            return {}
        query: dict[str, Any] = {}
        if params.visibility:
            query["visibility"] = params.visibility
        if params.team_id:
            query["teamID"] = params.team_id
        if params.limit is not None:
            query["limit"] = str(params.limit)
        if params.offset is not None:
            query["offset"] = str(params.offset)
        return query

    def _encode_get_template_params(self, params: GetTemplateParams | None) -> dict[str, str]:
        if params is None:
            return {}
        query: dict[str, str] = {}
        if params.limit is not None:
            query["limit"] = str(params.limit)
        if params.next_token:
            query["nextToken"] = params.next_token
        return query

    def _encode_build_status_params(self, params: BuildStatusParams | None) -> dict[str, str]:
        if params is None:
            return {}
        query: dict[str, str] = {}
        if params.logs_offset is not None:
            query["logsOffset"] = str(params.logs_offset)
        if params.limit is not None:
            query["limit"] = str(params.limit)
        if params.level:
            query["level"] = params.level
        return query

    def _encode_build_logs_params(self, params: BuildLogsParams | None) -> dict[str, str]:
        if params is None:
            return {}
        query: dict[str, str] = {}
        if params.cursor is not None:
            query["cursor"] = str(params.cursor)
        if params.limit is not None:
            query["limit"] = str(params.limit)
        if params.direction:
            query["direction"] = params.direction
        if params.level:
            query["level"] = params.level
        if params.source:
            query["source"] = params.source
        return query

    def _normalize_build_status_response(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        raw_logs = raw.get("logs") if isinstance(raw.get("logs"), list) else []
        raw_log_entries = raw.get("logEntries") if isinstance(raw.get("logEntries"), list) else []
        log_entries = raw_log_entries or [entry for entry in raw_logs if isinstance(entry, dict)]
        return {
            "buildID": str(raw.get("buildID", "")),
            "templateID": str(raw.get("templateID", "")),
            "status": str(raw.get("status", "")),
            "logs": [entry for entry in raw_logs if isinstance(entry, str)],
            "logEntries": [
                {
                    "timestamp": str(entry.get("timestamp", "")),
                    "level": str(entry.get("level", "")),
                    "step": str(entry.get("step", "")),
                    "message": str(entry.get("message", "")),
                }
                for entry in log_entries
            ],
            "reason": raw.get("reason"),
            "createdAt": str(raw.get("createdAt", "")),
            "updatedAt": str(raw.get("updatedAt", "")),
        }

    def _with_query(self, path: str, params: Mapping[str, Any]) -> str:
        if not params:
            return path
        return f"{path}?{urlencode(params, doseq=True)}"

    def _is_empty_build_request(self, body: Mapping[str, Any]) -> bool:
        return not str(body.get("buildID", "")).strip() and not str(body.get("fromTemplate", "")).strip() and not str(
            body.get("fromImage", "")
        ).strip() and not str(body.get("fromImageRegistry", "")).strip() and body.get("force") is None and not (
            body.get("steps") or []
        ) and not str(body.get("filesHash", "")).strip() and not str(body.get("startCmd", "")).strip() and not str(
            body.get("readyCmd", "")
        ).strip()

    def _is_dns_label(self, value: str) -> bool:
        if not value:
            return False
        if value[0] == "-" or value[-1] == "-":
            return False
        return all(ch.islower() or ch.isdigit() or ch == "-" for ch in value)

    def _is_sha256(self, value: str) -> bool:
        return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)
