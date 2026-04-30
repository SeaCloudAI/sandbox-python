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

_TEMPLATE_CREATE_FIELDS = {
    "name",
    "tags",
    "alias",
    "teamID",
    "cpuCount",
    "memoryMB",
    "extensions",
}

_TEMPLATE_UPDATE_FIELDS = {
    "public",
    "extensions",
}

_BUILD_REQUEST_FIELDS = {
    "fromTemplate",
    "fromImage",
    "fromImageRegistry",
    "force",
    "steps",
    "filesHash",
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
        self._validate_template_create_body(body)
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

    def resolve_template_ref(self, ref: str) -> dict[str, Any]:
        if not ref.strip():
            raise ValidationError("ref is required")
        return self._request_json("GET", f"/api/v1/templates/resolve/{quote(ref, safe='')}")

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
        self._validate_template_update_body(body)
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
        build_id: str,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_template_id(template_id)
        self._require_build_id(build_id)
        self._validate_client_build_id(build_id)
        self._validate_build_request(body)
        return self._request_json(
            "POST",
            f"/api/v1/templates/{quote(template_id, safe='')}/builds/{quote(build_id, safe='')}",
            headers=None if body is None or self._is_empty_build_request(body) else self.build_headers({"Content-Type": "application/json"}),
            body=None if body is None or self._is_empty_build_request(body) else body,
            expected_statuses=(202,),
        )

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
        return self._request_json("GET", path)

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

    def _validate_client_build_id(self, build_id: str) -> None:
        trimmed = build_id.strip()
        if len(trimmed) > 63 or not self._is_dns_label(trimmed):
            raise ValidationError("build_id must be a lowercase DNS label up to 63 characters")

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

    def _validate_template_create_body(self, body: Mapping[str, Any] | None) -> None:
        if body is None:
            return
        for key in body.keys():
            if key not in _TEMPLATE_CREATE_FIELDS:
                raise ValidationError(f"template field {key} is not supported by the public SDK")
        if body.get("extensions") is not None:
            self._validate_template_extensions(body.get("extensions"))

    def _validate_template_update_body(self, body: Mapping[str, Any] | None) -> None:
        if body is None:
            return
        for key in body.keys():
            if key not in _TEMPLATE_UPDATE_FIELDS:
                raise ValidationError(f"template field {key} is not supported by the public SDK")
        if body.get("extensions") is not None:
            self._validate_template_extensions(body.get("extensions"))

    def _validate_build_request(self, body: Mapping[str, Any] | None) -> None:
        if body is None:
            return
        for key in body.keys():
            if key not in _BUILD_REQUEST_FIELDS:
                raise ValidationError(f"build field {key} is not supported by the public SDK")
        if "buildID" in body:
            raise ValidationError("buildID must be provided in the create_build path, not in body")
        files_hash = str(body.get("filesHash", "")).strip()
        if files_hash and not self._is_sha256(files_hash):
            raise ValidationError("filesHash must be a 64-character lowercase hex SHA256")
        if body.get("force") is not None and not isinstance(body.get("force"), bool):
            raise ValidationError("force must be a boolean")
        if body.get("fromImageRegistry") is not None:
            self._validate_registry_config(body.get("fromImageRegistry"))
        for index, step in enumerate(body.get("steps") or []):
            step_type = str(step.get("type", "")).strip().upper()
            if not step_type:
                raise ValidationError(f"steps[{index}].type is required")
            if step_type == "COPY":
                step_hash = str(step.get("filesHash", "")).strip()
                if not step_hash:
                    raise ValidationError(f"steps[{index}].filesHash is required for COPY")
                if not self._is_sha256(step_hash):
                    raise ValidationError(f"steps[{index}].filesHash must be a 64-character lowercase hex SHA256")
                if len(step.get("args") or []) < 2:
                    raise ValidationError(f"steps[{index}].args must include src and dest for COPY")
                continue
            if step_type == "ENV":
                if len(step.get("args") or []) == 0 or len(step.get("args") or []) % 2 != 0:
                    raise ValidationError(f"steps[{index}].args must contain ENV key/value pairs")
                continue
            if step_type in {"RUN", "WORKDIR", "USER"}:
                if not str((step.get("args") or [""])[0]).strip():
                    raise ValidationError(f"steps[{index}].args must include the {step_type} value")
                continue
            raise ValidationError(f"steps[{index}].type must be one of COPY, ENV, RUN, WORKDIR, USER")

    def _validate_registry_config(self, config) -> None:
        if not isinstance(config, dict):
            raise ValidationError("fromImageRegistry must be an object")
        registry_type = str(config.get("type", "")).strip()
        if not registry_type:
            raise ValidationError("fromImageRegistry.type is required")
        if registry_type == "registry":
            if not str(config.get("username", "")).strip() or not str(config.get("password", "")).strip():
                raise ValidationError("fromImageRegistry registry config requires username and password")
            return
        if registry_type == "aws":
            if not str(config.get("awsAccessKeyId", "")).strip() or not str(config.get("awsSecretAccessKey", "")).strip() or not str(config.get("awsRegion", "")).strip():
                raise ValidationError("fromImageRegistry aws config requires awsAccessKeyId, awsSecretAccessKey, and awsRegion")
            return
        if registry_type == "gcp":
            if not str(config.get("serviceAccountJson", "")).strip():
                raise ValidationError("fromImageRegistry gcp config requires serviceAccountJson")
            return
        raise ValidationError(f"fromImageRegistry.type {registry_type!r} is not supported")

    def _validate_template_extensions(self, extensions) -> None:
        if not isinstance(extensions, dict):
            raise ValidationError("extensions must be an object")
        for key in extensions.keys():
            if key != "seacloud":
                raise ValidationError(f"template extension {key} is not supported by the public SDK")
        seacloud = extensions.get("seacloud")
        if seacloud is None:
            return
        if not isinstance(seacloud, dict):
            raise ValidationError("extensions.seacloud must be an object")
        allowed = {"baseTemplateID", "visibility", "envs", "storageType", "storageSizeGB"}
        for key in seacloud.keys():
            if key not in allowed:
                raise ValidationError(f"template extension field {key} is not supported by the public SDK")
        if str(seacloud.get("visibility", "")).strip() == "official":
            raise ValidationError("extensions.seacloud.visibility=official is not supported by the public SDK")

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

    def _with_query(self, path: str, params: Mapping[str, Any]) -> str:
        if not params:
            return path
        return f"{path}?{urlencode(params, doseq=True)}"

    def _is_empty_build_request(self, body: Mapping[str, Any]) -> bool:
        return not str(body.get("fromTemplate", "")).strip() and not str(
            body.get("fromImage", "")
        ).strip() and body.get("fromImageRegistry") is None and body.get("force") is None and not (
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
