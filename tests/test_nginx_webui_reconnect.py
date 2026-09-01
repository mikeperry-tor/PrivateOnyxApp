from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "onyx/nginx/run-nginx-wrapper.sh"
CURRENT_TEMPLATE = ROOT / "onyx/onyx_data/data/nginx/app.conf.template"
CURRENT_RUNNER = ROOT / "onyx/onyx_data/data/nginx/run-nginx.sh"


class NginxReconnectTransformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="private onyx [reconnect] ")
        self.root = Path(self.temporary.name)
        self.templates = self.root / "templates with spaces"
        self.conf = self.root / "conf;not-a-command"
        self.bin = self.root / "bin"
        self.templates.mkdir()
        self.conf.mkdir()
        self.bin.mkdir()
        shutil.copy2(CURRENT_TEMPLATE, self.templates / "app.conf.template")
        shutil.copy2(CURRENT_RUNNER, self.templates / "run-nginx.sh")
        self.include = self.root / "server include.inc"
        self.asset = self.root / "asset $(no-exec).js"
        self.include.write_text("# fixture\n", encoding="utf-8")
        self.asset.write_text("// fixture\n", encoding="utf-8")
        self.log = self.root / "nginx.log"
        self.fake_nginx = self.bin / "nginx"
        self.fake_nginx.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$PRIVATE_ONYX_TEST_NGINX_LOG\"\n"
            "if [ \"${1:-}\" = -V ]; then\n"
            "  printf '%s\\n' 'configure arguments: --with-http_sub_module' >&2\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        self.fake_nginx.chmod(0o755)
        envsubst = self.bin / "envsubst"
        envsubst.write_text("#!/bin/sh\ncat\n", encoding="utf-8")
        envsubst.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_wrapper(
        self, *, module: bool = True, fail_test: bool = False
    ) -> subprocess.CompletedProcess[str]:
        if not module:
            self.fake_nginx.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$PRIVATE_ONYX_TEST_NGINX_LOG\"\n"
                "printf '%s\\n' 'configure arguments:' >&2\n"
                "exit 0\n",
                encoding="utf-8",
            )
            self.fake_nginx.chmod(0o755)
        elif fail_test:
            self.fake_nginx.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$PRIVATE_ONYX_TEST_NGINX_LOG\"\n"
                "if [ \"${1:-}\" = -V ]; then printf '%s\\n' 'configure arguments: --with-http_sub_module' >&2; exit 0; fi\n"
                "if [ \"${1:-}\" = -t ]; then exit 1; fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            self.fake_nginx.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "PRIVATE_ONYX_NGINX_TEMPLATE_DIR": str(self.templates),
            "PRIVATE_ONYX_NGINX_CONF_DIR": str(self.conf),
            "PRIVATE_ONYX_NGINX_SERVER_INCLUDE": str(self.include),
            "PRIVATE_ONYX_NGINX_ASSET": str(self.asset),
            "PRIVATE_ONYX_NGINX_BIN": str(self.fake_nginx),
            "PRIVATE_ONYX_NGINX_VALIDATE_ONLY": "true",
            "PRIVATE_ONYX_TEST_NGINX_LOG": str(self.log),
        }
        return subprocess.run(
            [str(WRAPPER)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def test_current_generated_sources_transform_and_validate_once(self) -> None:
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        transformed = (self.conf / "app.conf.template").read_text(encoding="utf-8")
        rendered = (self.conf / "app.conf").read_text(encoding="utf-8")
        for marker in (
            "PRIVATE_ONYX_WEBUI_RECONNECT_SERVER_INCLUDE",
            "PRIVATE_ONYX_WEBUI_RECONNECT_HTML_ENCODING",
        ):
            self.assertEqual(transformed.count(marker), 1)
            self.assertEqual(rendered.count(marker), 1)
        self.assertEqual(
            transformed.count(
                "proxy_set_header Accept-Encoding $private_onyx_webui_accept_encoding;"
            ),
            1,
        )
        api_location = transformed.split("location ~ ^/(api|openapi.json)", 1)[1].split(
            "    location / {", 1
        )[0]
        self.assertNotIn("PRIVATE_ONYX_WEBUI_RECONNECT", api_location)
        log = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(log.count("-V"), 1)
        self.assertEqual(log.count("-t"), 1)
        self.assertNotIn('-g daemon off;', log)

        second = self.run_wrapper()
        self.assertEqual(second.returncode, 0, second.stderr)
        transformed_again = (self.conf / "app.conf.template").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            transformed_again.count("PRIVATE_ONYX_WEBUI_RECONNECT_SERVER_INCLUDE"),
            1,
        )

    def test_source_shape_drift_fails_before_nginx_start(self) -> None:
        cases = {
            "missing server": lambda text: text.replace(
                "\nserver {\n", "\nserver{\n", 1
            ),
            "two servers": lambda text: text + "\nserver {\n}\n",
            "missing location": lambda text: text.replace(
                "    location / {", "    location /{", 1
            ),
            "two locations": lambda text: text.replace(
                "    location / {", "    location / {\n    location / {", 1
            ),
        }
        original = CURRENT_TEMPLATE.read_text(encoding="utf-8")
        for name, mutate in cases.items():
            with self.subTest(name=name):
                (self.templates / "app.conf.template").write_text(
                    mutate(original), encoding="utf-8"
                )
                self.log.unlink(missing_ok=True)
                result = self.run_wrapper()
                self.assertNotEqual(result.returncode, 0)
                log = self.log.read_text(encoding="utf-8").splitlines()
                self.assertEqual(log, ["-V"])

    def test_runner_start_marker_drift_fails_closed(self) -> None:
        runner = CURRENT_RUNNER.read_text(encoding="utf-8")
        start = runner.splitlines()[-1]
        for name, drifted in (
            ("missing", runner.replace(start, "nginx -g 'daemon off;'")),
            ("duplicate", runner + start + "\n"),
        ):
            with self.subTest(name=name):
                (self.templates / "run-nginx.sh").write_text(
                    drifted, encoding="utf-8"
                )
                self.log.unlink(missing_ok=True)
                result = self.run_wrapper()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("-t", self.log.read_text(encoding="utf-8").splitlines())

    def test_nginx_syntax_failure_propagates_without_start(self) -> None:
        result = self.run_wrapper(fail_test=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.log.read_text(encoding="utf-8").splitlines(), ["-V", "-t"])

    def test_missing_input_or_module_fails_before_start(self) -> None:
        self.asset.unlink()
        missing = self.run_wrapper()
        self.assertNotEqual(missing.returncode, 0)
        self.assertFalse(self.log.exists())

        self.asset.write_text("// fixture\n", encoding="utf-8")
        no_module = self.run_wrapper(module=False)
        self.assertNotEqual(no_module.returncode, 0)
        self.assertEqual(self.log.read_text(encoding="utf-8").splitlines(), ["-V"])


if __name__ == "__main__":
    unittest.main()
