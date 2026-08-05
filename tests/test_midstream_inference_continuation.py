from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from enum import Enum
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "onyx" / "patches" / "shared" / "wrapper_env_patches.py"


class RetryableConnectionError(Exception):
    pass


class RetryableTimeout(Exception):
    pass


class RetryableUnavailable(Exception):
    pass


class RetryableInternalError(Exception):
    pass


class Delta:
    def __init__(self, content=None, reasoning_content=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls or []


class Choice:
    def __init__(self, delta=None, finish_reason=None, index=0):
        self.delta = delta or Delta()
        self.finish_reason = finish_reason
        self.index = index


class Packet:
    def __init__(
        self,
        delta=None,
        finish_reason=None,
        packet_id="response-1",
        *,
        id=None,
        created="now",
        choice=None,
        usage=None,
    ):
        self.id = id or packet_id
        self.created = created
        self.choice = choice or Choice(delta, finish_reason)
        self.usage = usage

    def model_copy(self, deep=False):
        return copy.deepcopy(self) if deep else copy.copy(self)


class ToolChoiceOptions(str, Enum):
    AUTO = "auto"
    NONE = "none"


class ReasoningEffort(str, Enum):
    AUTO = "auto"


class Message:
    def __init__(self, content, role):
        self.content = content
        self.role = role
        self.provider_specific_fields = None

    def model_dump(self, exclude_none=False):
        # Mirror Pydantic's declared-field behavior closely enough to prove that
        # the continuation subclass, rather than an undeclared extra, owns the
        # serialized reasoning field.
        field_names = {"role", "content"}
        for cls in type(self).__mro__:
            field_names.update(getattr(cls, "__annotations__", {}))
        dumped = {name: getattr(self, name, None) for name in field_names}
        if exclude_none:
            dumped = {
                key: value for key, value in dumped.items() if value is not None
            }
        return dumped


class AssistantMessage(Message):
    def __init__(self, content=None):
        super().__init__(content, "assistant")


class UserMessage(Message):
    def __init__(self, content):
        super().__init__(content, "user")


class FakeLitellmLLM:
    def __init__(self, streams):
        self.streams = iter(streams)
        self.calls = []

    def stream(
        self,
        prompt,
        tools=None,
        tool_choice=None,
        structured_response_format=None,
        timeout_override=None,
        max_tokens=None,
        reasoning_effort=ReasoningEffort.AUTO,
        user_identity=None,
    ):
        # Strict patch-contract markers from pinned Onyx:
        # retryable_exceptions = (
        # LLM_FIRST_CHUNK_MAX_RETRIES
        # if yielded_any or attempt >= max_attempts - 1:
        # from_litellm_model_response_stream(chunk)
        self.calls.append(
            {
                "prompt": prompt,
                "tools": tools,
                "tool_choice": tool_choice,
                "structured_response_format": structured_response_format,
                "timeout_override": timeout_override,
                "max_tokens": max_tokens,
                "reasoning_effort": reasoning_effort,
                "user_identity": user_identity,
            }
        )
        for item in next(self.streams):
            if isinstance(item, BaseException):
                raise item
            yield item


ORIGINAL_FAKE_STREAM = FakeLitellmLLM.stream


class FakeUpstreamRetryingLitellmLLM:
    """Mirror pinned Onyx's provider-call retry loop for accounting tests."""

    def __init__(self, streams):
        self.streams = iter(streams)
        self.calls = []

    def stream(
        self,
        prompt,
        tools=None,
        tool_choice=None,
        structured_response_format=None,
        timeout_override=None,
        max_tokens=None,
        reasoning_effort=ReasoningEffort.AUTO,
        user_identity=None,
    ):
        retryable_exceptions = (
            RetryableConnectionError,
            RetryableTimeout,
            RetryableUnavailable,
            RetryableInternalError,
        )
        LLM_FIRST_CHUNK_MAX_RETRIES = 1
        max_attempts = 1 + LLM_FIRST_CHUNK_MAX_RETRIES
        yielded_any = False
        for attempt in range(max_attempts):
            self.calls.append(prompt)
            try:
                for chunk in next(self.streams):
                    if isinstance(chunk, BaseException):
                        raise chunk
                    # Strict patch-contract marker from pinned Onyx:
                    # from_litellm_model_response_stream(chunk)
                    yielded_any = True
                    yield chunk
                return
            except retryable_exceptions:
                if yielded_any or attempt >= max_attempts - 1:
                    raise


ORIGINAL_RETRYING_FAKE_STREAM = FakeUpstreamRetryingLitellmLLM.stream


def _fake_modules(llm_cls=FakeLitellmLLM):
    onyx = ModuleType("onyx")
    llm = ModuleType("onyx.llm")
    multi_llm = ModuleType("onyx.llm.multi_llm")
    model_response = ModuleType("onyx.llm.model_response")
    models = ModuleType("onyx.llm.models")
    litellm = ModuleType("litellm")
    litellm_exceptions = ModuleType("litellm.exceptions")
    openai = ModuleType("openai")

    multi_llm.LitellmLLM = llm_cls
    model_response.Delta = Delta
    model_response.ModelResponseStream = Packet
    model_response.StreamingChoice = Choice
    models.AssistantMessage = AssistantMessage
    models.UserMessage = UserMessage
    models.ToolChoiceOptions = ToolChoiceOptions
    models.ReasoningEffort = ReasoningEffort
    litellm_exceptions.APIConnectionError = RetryableConnectionError
    litellm_exceptions.Timeout = RetryableTimeout
    litellm_exceptions.ServiceUnavailableError = RetryableUnavailable
    litellm_exceptions.InternalServerError = RetryableInternalError
    openai.APIConnectionError = RetryableConnectionError
    openai.APITimeoutError = RetryableTimeout
    onyx.llm = llm
    llm.multi_llm = multi_llm

    return {
        "onyx": onyx,
        "onyx.llm": llm,
        "onyx.llm.multi_llm": multi_llm,
        "onyx.llm.model_response": model_response,
        "onyx.llm.models": models,
        "litellm": litellm,
        "litellm.exceptions": litellm_exceptions,
        "openai": openai,
    }


def _load_and_install():
    spec = importlib.util.spec_from_file_location(
        "wrapper_env_patches_midstream_under_test", PATCH_PATH
    )
    assert spec and spec.loader
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    wrapper.apply_midstream_inference_continuation_patch()
    return wrapper


class MidstreamInferenceContinuationTests(unittest.TestCase):
    def setUp(self):
        FakeLitellmLLM.stream = ORIGINAL_FAKE_STREAM
        self.modules = _fake_modules()
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        self.wrapper = _load_and_install()

    def tearDown(self):
        self.module_patch.stop()

    def _content(self, packets):
        return "".join(packet.choice.delta.content or "" for packet in packets)

    def test_continuation_preserves_prefix_reasoning_and_system_message(self):
        system = Message("unchanged system prompt", "system")
        prior = AssistantMessage("prior answer")
        prior.reasoning_content = "prior reasoning"
        prompt = [system, prior, UserMessage("question")]
        llm = FakeLitellmLLM(
            [
                [
                    Packet(Delta(reasoning_content="current reasoning")),
                    Packet(Delta(content="partial answer")),
                    RetryableConnectionError("closed"),
                ],
                [
                    Packet(Delta(reasoning_content="hidden continuation reasoning")),
                    Packet(Delta(content=" completed"), finish_reason="stop"),
                ],
            ]
        )

        packets = list(
            llm.stream(
                prompt,
                tools=[{"type": "function"}],
                tool_choice="auto",
                timeout_override=17,
                max_tokens=123,
                reasoning_effort="high",
                user_identity="user-1",
            )
        )

        self.assertIn("inference stream was interrupted", self._content(packets))
        self.assertTrue(self._content(packets).endswith(" completed"))
        self.assertEqual(system.content, "unchanged system prompt")
        continuation = llm.calls[1]
        self.assertIs(continuation["prompt"][0], system)
        self.assertIs(continuation["prompt"][1], prior)
        partial = continuation["prompt"][-2]
        self.assertEqual(partial.content, "partial answer")
        self.assertEqual(partial.reasoning_content, "current reasoning")
        self.assertEqual(
            partial.model_dump(exclude_none=True)["reasoning_content"],
            "current reasoning",
        )
        self.assertEqual(
            partial.provider_specific_fields["reasoning_content"],
            "current reasoning",
        )
        self.assertEqual(continuation["prompt"][-1].role, "user")
        self.assertEqual(continuation["tools"], [{"type": "function"}])
        self.assertEqual(continuation["tool_choice"], "auto")
        self.assertEqual(continuation["timeout_override"], 17)
        self.assertEqual(continuation["max_tokens"], 123)
        self.assertEqual(continuation["reasoning_effort"], "high")
        self.assertEqual(continuation["user_identity"], "user-1")
        self.assertNotIn("Do not call tools", continuation["prompt"][-1].content)
        emitted_reasoning = "".join(
            packet.choice.delta.reasoning_content or "" for packet in packets
        )
        self.assertEqual(emitted_reasoning, "current reasoning")

    def test_reasoning_only_interruption_continues_with_original_tool_policy(self):
        tools = [{"type": "function", "function": {"name": "search"}}]
        llm = FakeLitellmLLM(
            [
                [
                    Packet(Delta(reasoning_content="first thought")),
                    RetryableConnectionError(),
                ],
                [
                    Packet(Delta(reasoning_content="second thought")),
                    Packet(Delta(tool_calls=[object()]), finish_reason="tool_calls"),
                ],
            ]
        )

        packets = list(
            llm.stream(
                [UserMessage("question")],
                tools=tools,
                tool_choice="required",
            )
        )

        continuation = llm.calls[1]
        self.assertIs(continuation["tools"], tools)
        self.assertEqual(continuation["tool_choice"], "required")
        partial = continuation["prompt"][-2]
        self.assertIsNone(partial.content)
        self.assertEqual(partial.reasoning_content, "first thought")
        emitted_reasoning = "".join(
            packet.choice.delta.reasoning_content or "" for packet in packets
        )
        self.assertIn("reasoning stream was interrupted", emitted_reasoning)
        self.assertTrue(emitted_reasoning.endswith("second thought"))
        self.assertTrue(packets[-1].choice.delta.tool_calls)

    def test_reasoning_only_recovery_failure_becomes_a_saved_partial_answer(self):
        llm = FakeLitellmLLM(
            [
                [
                    Packet(Delta(reasoning_content="unfinished thought")),
                    RetryableConnectionError(),
                ],
                [RetryableConnectionError()],
            ]
        )

        packets = list(llm.stream([UserMessage("question")]))

        self.assertEqual(len(llm.calls), 2)
        self.assertIn("generation above is partial", self._content(packets))
        self.assertEqual(packets[-1].choice.finish_reason, "stop")

    def test_each_progressing_continuation_permits_another_without_a_hard_cap(self):
        llm = FakeLitellmLLM(
            [
                [Packet(Delta(content="A")), RetryableConnectionError()],
                [Packet(Delta(content="B")), RetryableConnectionError()],
                [Packet(Delta(content="C")), RetryableConnectionError()],
                [Packet(Delta(content="D")), RetryableConnectionError()],
                [Packet(Delta(content="E"), finish_reason="stop")],
            ]
        )

        packets = list(llm.stream([UserMessage("question")]))

        self.assertEqual(len(llm.calls), 5)
        self.assertEqual(self._content(packets).count("continues below"), 4)
        self.assertNotIn("Recovery also failed", self._content(packets))
        self.assertEqual(packets[-1].choice.finish_reason, "stop")

    def test_failed_continuation_does_not_retry_back_to_back(self):
        llm = FakeLitellmLLM(
            [
                [Packet(Delta(content="partial")), RetryableConnectionError()],
                [RetryableConnectionError()],
            ]
        )

        packets = list(llm.stream([UserMessage("question")]))

        self.assertEqual(len(llm.calls), 2)
        self.assertIn("Recovery also failed", self._content(packets))

    def test_prechunk_failure_is_delegated_to_upstream_without_wrapper_retry(self):
        wrapped_error = RuntimeError("provider wrapper")
        wrapped_error.__cause__ = RetryableConnectionError("socket closed")
        llm = FakeLitellmLLM([[wrapped_error]])

        with self.assertRaises(RuntimeError):
            list(llm.stream([UserMessage("question")]))
        self.assertEqual(len(llm.calls), 1)

    def test_structured_output_failure_is_not_continued(self):
        llm = FakeLitellmLLM(
            [[Packet(Delta(content='{"partial":')), RetryableConnectionError()]]
        )

        with self.assertRaises(RetryableConnectionError):
            list(
                llm.stream(
                    [UserMessage("question")],
                    structured_response_format={"type": "json_object"},
                )
            )
        self.assertEqual(len(llm.calls), 1)

    def test_non_retryable_failure_is_not_continued(self):
        llm = FakeLitellmLLM(
            [[Packet(Delta(content="partial")), ValueError("malformed response")]]
        )

        with self.assertRaises(ValueError):
            list(llm.stream([UserMessage("question")]))
        self.assertEqual(len(llm.calls), 1)

    def test_clean_eof_without_finish_matches_stock_and_is_not_continued(self):
        llm = FakeLitellmLLM([[Packet(Delta(content="partial clean EOF"))]])

        packets = list(llm.stream([UserMessage("question")]))

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(self._content(packets), "partial clean EOF")
        self.assertFalse(any(packet.choice.finish_reason for packet in packets))

    def test_post_finish_exception_is_preserved_with_visible_warning(self):
        llm = FakeLitellmLLM(
            [
                [
                    Packet(Delta(content="complete answer"), finish_reason="stop"),
                    ValueError("finalizer failed"),
                ]
            ]
        )

        packets = list(llm.stream([UserMessage("question")]))

        self.assertEqual(len(llm.calls), 1)
        self.assertIn("complete answer", self._content(packets))
        self.assertIn("stream finalization then failed", self._content(packets))
        self.assertEqual(packets[-1].choice.finish_reason, "stop")

    def test_textual_tool_payload_with_tools_remains_continuable(self):
        xml = '<function_calls><invoke name="search"><parameter name="q">onyx'
        llm = FakeLitellmLLM(
            [
                [Packet(Delta(content=xml)), RetryableConnectionError()],
                [
                    Packet(
                        Delta(content="</parameter></invoke></function_calls>"),
                        finish_reason="stop",
                    )
                ],
            ]
        )

        packets = list(
            llm.stream(
                [UserMessage("question")],
                tools=[{"type": "function", "function": {"name": "search"}}],
                tool_choice="auto",
            )
        )

        self.assertEqual(len(llm.calls), 2)
        self.assertIn(xml, self._content(packets))
        self.assertTrue(self._content(packets).endswith("</function_calls>"))

    def test_incomplete_tool_call_is_not_continued(self):
        llm = FakeLitellmLLM(
            [
                [
                    Packet(Delta(tool_calls=[object()])),
                    RetryableConnectionError(),
                ]
            ]
        )

        with self.assertRaises(RetryableConnectionError):
            list(llm.stream([UserMessage("question")], tools=[{}]))
        self.assertEqual(len(llm.calls), 1)


class MidstreamProviderAttemptAccountingTests(unittest.TestCase):
    def setUp(self):
        FakeUpstreamRetryingLitellmLLM.stream = ORIGINAL_RETRYING_FAKE_STREAM
        self.modules = _fake_modules(FakeUpstreamRetryingLitellmLLM)
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        self.wrapper = _load_and_install()

    def tearDown(self):
        self.module_patch.stop()

    def test_failed_continuation_uses_upstream_prechunk_retry_twice(self):
        llm = FakeUpstreamRetryingLitellmLLM(
            [
                [Packet(Delta(content="partial")), RetryableConnectionError()],
                [RetryableConnectionError()],
                [RetryableConnectionError()],
            ]
        )

        packets = list(llm.stream([UserMessage("question")]))
        content = "".join(packet.choice.delta.content or "" for packet in packets)

        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(content.count("continues below"), 1)
        self.assertIn("Recovery also failed", content)


class MidstreamContinuationComposeTests(unittest.TestCase):
    def test_compose_uses_upstream_prechunk_retry_and_progress_gating(self):
        compose = (ROOT / "docker-compose.yaml").read_text()
        self.assertIn('LLM_FIRST_CHUNK_MAX_RETRIES: "1"', compose)
        self.assertIn('ONYX_LLM_MIDSTREAM_CONTINUATION_ENABLED: "true"', compose)
        self.assertNotIn("ONYX_LLM_MIDSTREAM_MAX_CONTINUATIONS", compose)
        self.assertIn('ONYX_LLM_NATIVE_TOOL_CALLS_ONLY: "true"', compose)


if __name__ == "__main__":
    unittest.main()
