from __future__ import annotations

import os
import time
import unittest

from sandbox.build import BuildService
from sandbox.core import APIError


def should_run_integration() -> bool:
    return os.getenv("SANDBOX_RUN_INTEGRATION") == "1"


@unittest.skipUnless(should_run_integration(), "set SANDBOX_RUN_INTEGRATION=1 to run integration tests")
class BuildPlaneIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base_url = os.getenv("SANDBOX_TEST_BASE_URL", "")
        api_key = os.getenv("SANDBOX_TEST_API_KEY", "")
        build_image = os.getenv("SANDBOX_TEST_BUILD_IMAGE", "docker.io/library/alpine:3.20")

        if not base_url or not api_key:
            raise unittest.SkipTest("build integration test env is incomplete")

        cls.service = BuildService(base_url=base_url, api_key=api_key)
        cls.build_image = build_image

    def test_direct_build_anonymous_polling(self) -> None:
        try:
            direct = self.service.direct_build({
                "project": "sdk-build-integration",
                "image": "python-direct-build",
                "tag": f"t{time.time_ns()}",
                "dockerfile": "FROM alpine:3.20\nRUN echo direct-build-test >/tmp/direct-build.txt\n",
            })
        except APIError as exc:
            if exc.status_code == 404:
                self.skipTest("direct build endpoint is not exposed by this gateway")
            raise
        template_id = direct["templateID"]
        build_id = direct["buildID"]
        self.assertTrue(template_id)
        self.assertTrue(build_id)

        try:
            status = self._wait_for_build_ready(template_id, build_id)
            self.assertEqual(status["status"], "ready")

            build = self.service.get_build(template_id, build_id)
            self.assertEqual(build["buildID"], build_id)

            logs = self.service.get_build_logs(template_id, build_id)
            self.assertIsInstance(logs["logs"], list)
        finally:
            try:
                self.service.delete_template(template_id)
            except APIError as exc:
                if exc.status_code != 404:
                    raise

    def test_template_lifecycle(self) -> None:
        name = f"python-build-sdk-{time.time_ns()}"
        alias = name
        created = self.service.create_template({
            "name": name,
            "alias": alias,
        })
        template_id = created["templateID"]
        build_id = created.get("buildID", "")
        self.assertTrue(template_id)

        if not build_id:
            requested_build_id = f"build-{time.time_ns():x}"[:32]
            build_resp = self.service.create_build(template_id, requested_build_id, {"fromImage": self.build_image})
            self.assertEqual(build_resp, {})
            build_id = requested_build_id

        try:
            listed = self.service.list_templates(None)
            self.assertIsInstance(listed, list)

            aliased = self.service.get_template_by_alias(alias)
            self.assertEqual(aliased["templateID"], template_id)

            resolved = self.service.resolve_template_ref(template_id)
            self.assertEqual(resolved["templateID"], template_id)

            detail = self.service.get_template(template_id)
            self.assertEqual(detail["templateID"], template_id)
            self.assertIsInstance(detail.get("builds", []), list)

            updated = self.service.update_template(template_id, {"public": False})
            self.assertTrue(updated["names"])

            file_resp = self.service.get_build_file(template_id, "a" * 64)
            self.assertIn("present", file_resp)

            history = self.service.list_builds(template_id)
            self.assertGreaterEqual(history["total"], 0)

            if build_id:
                build = self.service.get_build(template_id, build_id)
                self.assertEqual(build["buildID"], build_id)

                status = self.service.get_build_status(template_id, build_id)
                self.assertEqual(status["buildID"], build_id)

                logs = self.service.get_build_logs(template_id, build_id)
                self.assertIsInstance(logs["logs"], list)

                rolled = self.service.rollback_template(template_id, {"buildID": build_id})
                self.assertEqual(rolled["templateID"], template_id)
        finally:
            try:
                self.service.delete_template(template_id)
            except APIError as exc:
                if exc.status_code != 404:
                    raise

    def _wait_for_build_ready(self, template_id: str, build_id: str) -> dict:
        deadline = time.time() + 180
        last = None
        while time.time() < deadline:
            status = self.service.get_build_status(template_id, build_id)
            last = status
            if status["status"] == "ready":
                return status
            if status["status"] == "error":
                self.fail(f"build failed: {status}")
            time.sleep(2)
        self.fail(f"build did not complete before deadline: {last}")
        return {}
