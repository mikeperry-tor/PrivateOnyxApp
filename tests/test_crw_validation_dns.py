from __future__ import annotations

import importlib.util
import struct
import unittest
from pathlib import Path
from types import ModuleType


MODULE_PATH = Path(__file__).resolve().parents[1] / "crw" / "validation_dns.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("crw_validation_dns", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _query(name: str, qtype: int = 1) -> bytes:
    labels = b"".join(
        bytes((len(label),)) + label.encode() for label in name.split(".")
    )
    return (
        struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
        + labels
        + b"\0"
        + struct.pack("!HH", qtype, 1)
    )


def _header(response: bytes) -> tuple[int, int, int, int, int, int]:
    return struct.unpack("!HHHHHH", response[:12])


class CRWValidationDNSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_module()

    def test_public_multilabel_name_gets_synthetic_global_ipv4(self) -> None:
        response = self.module.build_response(_query("www.example.com"))
        _, flags, questions, answers, _, _ = _header(response)
        self.assertEqual(flags & 0xF, 0)
        self.assertEqual((questions, answers), (1, 1))
        self.assertIn(self.module.SYNTHETIC_IPV4.packed, response)

    def test_aaaa_query_does_not_invent_ipv6_connectivity(self) -> None:
        response = self.module.build_response(_query("www.example.com", qtype=28))
        _, flags, questions, answers, _, _ = _header(response)
        self.assertEqual(flags & 0xF, 0)
        self.assertEqual((questions, answers), (1, 0))

    def test_single_label_docker_style_name_is_nxdomain(self) -> None:
        response = self.module.build_response(_query("api_server"))
        _, flags, questions, answers, _, _ = _header(response)
        self.assertEqual(flags & 0xF, 3)
        self.assertEqual((questions, answers), (1, 0))

    def test_docker_internal_suffix_and_subdomain_are_nxdomain(self) -> None:
        for name in (
            "host.docker.internal",
            "x.host.docker.internal",
            "docker.for.mac.host.internal",
            "x.docker.for.mac.host.internal",
        ):
            with self.subTest(name=name):
                response = self.module.build_response(_query(name))
                _, flags, _, answers, _, _ = _header(response)
                self.assertEqual(flags & 0xF, 3)
                self.assertEqual(answers, 0)

    def test_local_naming_suffixes_are_nxdomain(self) -> None:
        for name in ("printer.local", "router.localdomain", "x.home.arpa"):
            with self.subTest(name=name):
                response = self.module.build_response(_query(name))
                self.assertEqual(_header(response)[1] & 0xF, 3)

    def test_malformed_query_returns_formerr(self) -> None:
        response = self.module.build_response(b"\x12\x34")
        _, flags, questions, answers, _, _ = _header(response)
        self.assertEqual(flags & 0xF, 1)
        self.assertEqual((questions, answers), (0, 0))

    def test_edns_data_is_not_echoed_as_question_data(self) -> None:
        query = bytearray(_query("www.example.com"))
        query[10:12] = struct.pack("!H", 1)
        query.extend(b"\0" + struct.pack("!HHIH", 41, 1232, 0, 0))
        response = self.module.build_response(bytes(query))
        _, flags, questions, answers, _, additional = _header(response)
        self.assertEqual(flags & 0xF, 0)
        self.assertEqual((questions, answers, additional), (1, 1, 0))
        self.assertLess(len(response), len(query) + 16)


if __name__ == "__main__":
    unittest.main()
