from __future__ import annotations

from typing import Any, Mapping


class TemplateBuildBuilder:
    def __init__(self) -> None:
        self._request: dict[str, Any] = {"steps": []}

    def from_image(self, image: str) -> "TemplateBuildBuilder":
        self._request["fromImage"] = image
        return self

    def from_template(self, template: str) -> "TemplateBuildBuilder":
        self._request["fromTemplate"] = template
        return self

    def from_image_registry(self, config: Mapping[str, Any]) -> "TemplateBuildBuilder":
        self._request["fromImageRegistry"] = dict(config)
        return self

    def force(self, enabled: bool = True) -> "TemplateBuildBuilder":
        self._request["force"] = enabled
        return self

    def copy(
        self,
        src: str,
        dest: str,
        files_hash: str,
        *,
        force: bool | None = None,
    ) -> "TemplateBuildBuilder":
        return self._push_step({
            "type": "COPY",
            "args": [src, dest],
            "filesHash": files_hash,
            "force": force,
        })

    def run(self, command: str, *, force: bool | None = None) -> "TemplateBuildBuilder":
        return self._push_step({
            "type": "RUN",
            "args": [command],
            "force": force,
        })

    def env(
        self,
        name_or_values: str | Mapping[str, str],
        value: str | None = None,
    ) -> "TemplateBuildBuilder":
        args: list[str] = []
        if isinstance(name_or_values, str):
            args.extend([name_or_values, value or ""])
        else:
            for key, env_value in name_or_values.items():
                args.extend([key, env_value])
        return self._push_step({
            "type": "ENV",
            "args": args,
        })

    def workdir(self, path: str, *, force: bool | None = None) -> "TemplateBuildBuilder":
        return self._push_step({
            "type": "WORKDIR",
            "args": [path],
            "force": force,
        })

    def user(self, user: str, *, force: bool | None = None) -> "TemplateBuildBuilder":
        return self._push_step({
            "type": "USER",
            "args": [user],
            "force": force,
        })

    def start_cmd(self, command: str) -> "TemplateBuildBuilder":
        self._request["startCmd"] = command
        return self

    def ready_cmd(self, command: str) -> "TemplateBuildBuilder":
        self._request["readyCmd"] = command
        return self

    def files_hash(self, files_hash: str) -> "TemplateBuildBuilder":
        self._request["filesHash"] = files_hash
        return self

    def to_request(self) -> dict[str, Any]:
        request = dict(self._request)
        request["steps"] = [dict(step, args=list(step.get("args") or [])) for step in self._request.get("steps", [])]
        if self._request.get("fromImageRegistry") is not None:
            request["fromImageRegistry"] = dict(self._request["fromImageRegistry"])
        return request

    def _push_step(self, step: Mapping[str, Any]) -> "TemplateBuildBuilder":
        normalized = dict(step)
        if normalized.get("force") is None:
            normalized.pop("force", None)
        self._request.setdefault("steps", []).append(normalized)
        return self


def template_build() -> TemplateBuildBuilder:
    return TemplateBuildBuilder()
