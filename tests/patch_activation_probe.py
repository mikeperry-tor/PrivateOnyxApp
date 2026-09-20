"""Offline startup/native-child evidence; never imports or installs a bootstrap."""

from __future__ import annotations

import io
import multiprocessing
import os
from pathlib import Path
import sys
import subprocess
import time
from unittest.mock import patch


def assert_activation() -> dict[str, str]:
    expected = os.environ["PATCH_PROBE_BOOTSTRAP"]
    module = sys.modules.get("sitecustomize")
    assert module is not None, "automatic sitecustomize discovery missing"
    assert str(Path(module.__file__).resolve()) == expected, module.__file__
    names = os.environ["PATCH_PROBE_MODULES"].split(":")
    origins = {"sitecustomize": module.__file__}
    for name in names:
        implementation = sys.modules.get(name)
        assert implementation is not None, f"startup did not load {name}"
        assert str(Path(implementation.__file__).resolve()).startswith(
            "/app/onyx_wrapper_patches/"
        ), implementation.__file__
        assert implementation.__name__ == name
        origins[name] = implementation.__file__
    return origins


def synthetic_pdf() -> bytes:
    """One text page, with correct offsets and no external dependencies."""
    content = b"BT /F1 12 Tf 20 80 Td (onyx synthetic activation fixture) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(data)


def pdfium_child(data: bytes, password: str | None):
    origins = assert_activation()
    from onyx.file_processing.extract_file_text import _extract_pdf_text_pdfium

    return _extract_pdf_text_pdfium(data, password), origins, os.getpid()


def spawned_probe(connection):
    connection.send(assert_activation())
    connection.close()


def validate_native_children() -> None:
    assert_activation()
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    child = context.Process(target=spawned_probe, args=(writer,))
    child.start()
    writer.close()
    try:
        assert reader.poll(120), "spawn startup did not return evidence"
        assert reader.recv()
        child.join(120)
        assert child.exitcode == 0, child.exitcode
    finally:
        if child.is_alive():
            child.terminate()
            child.join()
        reader.close()

    from onyx.file_processing import extract_file_text as pdf
    from onyx.utils.process_isolation import run_in_isolated_process

    results = []

    def observed_runner(function, *args, **kwargs):
        assert function is pdf._extract_pdf_text_pdfium
        assert kwargs == {"timeout": pdf.PDF_TEXT_EXTRACTION_TIMEOUT_SECONDS}
        start = time.monotonic()
        text, origins, pid = run_in_isolated_process(pdfium_child, *args, **kwargs)
        assert pid != os.getpid()
        assert origins
        results.append(time.monotonic() - start)
        return text

    for label in ("cold", "warm"):
        with patch.object(pdf, "run_in_isolated_process", observed_runner):
            text, _, _ = pdf.read_pdf_file(io.BytesIO(synthetic_pdf()))
        assert len(results) == (1 if label == "cold" else 2), "parent fallback is not child evidence"
        assert "onyx synthetic activation fixture" in text
        print(f"PDF_CHILD_{label.upper()} seconds={results[-1]:.3f} timeout={pdf.PDF_TEXT_EXTRACTION_TIMEOUT_SECONDS}")

    # A strict child startup failure still follows the native recovery path.
    # Observe the unmodified launch to prove that stderr is discarded and that
    # successful pypdf recovery is not mistaken for successful PDFium execution.
    from onyx.utils import process_isolation

    original_run = subprocess.run
    failures = []

    def observe_failure(*args, **kwargs):
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["timeout"] == pdf.PDF_TEXT_EXTRACTION_TIMEOUT_SECONDS
        result = original_run(*args, **kwargs)
        assert result.returncode == 78, result.returncode
        assert result.stdout == b"", "failed bootstrap contaminated pickle stdout"
        failures.append(result.returncode)
        return result

    with patch.dict(os.environ, {
        "WRAPPER_PATCH_STRICT": "true",
        "ONYX_CONFIGURED_INFERENCE_HTTP_PROXY_URL": "http://invalid-fixture-proxy:3128",
    }), patch.object(process_isolation.subprocess, "run", observe_failure):
        try:
            run_in_isolated_process(pdfium_child, synthetic_pdf(), None,
                                    timeout=pdf.PDF_TEXT_EXTRACTION_TIMEOUT_SECONDS)
        except process_isolation.IsolatedProcessError as exc:
            assert "code 78" in str(exc), str(exc)
        else:
            raise AssertionError("strict child failure did not reach the native parent error")
        text, _, _ = pdf.read_pdf_file(io.BytesIO(synthetic_pdf()))
        assert "onyx synthetic activation fixture" in text
        assert failures == [78, 78]
    print("NATIVE_STRICT_CHILD_ERROR_AND_PYPDF_RECOVERY_OK")


if __name__ == "__main__":
    # Use the importable module identity for native spawn and isolated pickle.
    from patch_activation_probe import validate_native_children

    validate_native_children()
