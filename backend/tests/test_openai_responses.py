"""Prompt 11 Part B: the OpenAI adapter's Responses API path (native tool calling).

The SDK client is replaced by a recorder - no network, no key. Checked: the
request shape (function tools, `store=False`, encrypted reasoning, reasoning
effort, `tool_choice`), parsing of function calls / text / reasoning / usage,
the replay rules (every call answered, reasoning only to the model that made
it), the error boundary, and one assistant turn end to end through it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from app.llm.base import Message, ProviderError, Role, ToolCall, ToolDef
from app.llm.providers.openai import OpenAIProvider
from app.services.assistant.agent import Assistant
from app.services.assistant.eval.world import SyntheticTools

TOOL = ToolDef(name="get_plan", description="the plan", parameters={"type": "object",
                                                                    "properties": {}})


class Recorder:
    def __init__(self, *responses):
        self.requests: list[dict] = []
        self.responses = list(responses)

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        out = self.responses.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _provider(recorder: Recorder, model="gpt-6-luna", effort="low") -> OpenAIProvider:
    p = OpenAIProvider(api_key="sk-test-not-real", model=model, reasoning_effort=effort)
    p._client = NS(responses=recorder)
    return p


def _response(*output, model="gpt-6-luna-2026-09-22", inp=120, out=30, cached=20):
    return NS(output=list(output), model=model, status="completed", incomplete_details=None,
              usage=NS(input_tokens=inp, output_tokens=out,
                       input_tokens_details=NS(cached_tokens=cached)))


def _call(name, args, call_id="call_1"):
    return NS(type="function_call", id="fc_1", call_id=call_id, name=name,
              arguments=json.dumps(args))


REASONING = NS(type="reasoning", id="rs_1", summary=[], encrypted_content="ENCRYPTED")


async def test_tool_calls_go_through_the_responses_api() -> None:
    rec = Recorder(_response(REASONING, _call("get_plan", {})))
    p = _provider(rec)
    out = await p.complete(role=Role.ASSISTANT, messages=[Message("user", "STUDENT: hi")],
                           system="rules", tools=[TOOL], tool_choice="required",
                           max_tokens=500)
    req = rec.requests[0]
    assert req["model"] == "gpt-6-luna" and req["instructions"] == "rules"
    assert req["tools"] == [{"type": "function", "name": "get_plan", "description": "the plan",
                             "parameters": TOOL.parameters, "strict": False}]
    assert req["tool_choice"] == "required" and req["max_output_tokens"] == 500
    assert req["store"] is False and req["include"] == ["reasoning.encrypted_content"]
    assert req["reasoning"] == {"effort": "low"}
    assert req["input"] == [{"role": "user", "content": "STUDENT: hi"}]
    assert out.tool_calls == (ToolCall("call_1", "get_plan", {}, "{}"),)
    assert out.usage.input_tokens == 120 and out.usage.cached_input_tokens == 20
    assert {"type": "reasoning", "id": "rs_1", "summary": [],
            "encrypted_content": "ENCRYPTED"} in out.provider_items


async def test_chat_completions_is_untouched_without_tools() -> None:
    chat = Recorder(NS(choices=[NS(message=NS(content='{"keywords": ["data"]}'),
                                   finish_reason="stop")], model="gpt-6-luna",
                       usage=NS(prompt_tokens=10, completion_tokens=5,
                                prompt_tokens_details=None)))
    p = OpenAIProvider(api_key="sk-test-not-real", model="gpt-6-luna", reasoning_effort="low")
    p._client = NS(chat=NS(completions=chat))
    out = await p.complete(role=Role.ASSISTANT, messages=[Message("user", "tech")],
                           schema={"type": "object"})
    assert out.structured == {"keywords": ["data"]}
    assert chat.requests[0]["reasoning_effort"] == "low"


def test_replay_answers_every_call_and_keeps_reasoning_with_its_model() -> None:
    items = ({"type": "reasoning", "id": "rs_1", "encrypted_content": "X", "summary": []},
             {"type": "function_call", "call_id": "call_1", "name": "get_plan",
              "arguments": "{}"})
    history = [Message("user", "STUDENT: hi"),
               Message("assistant", "", tool_calls=(ToolCall("call_1", "get_plan", {}),),
                       provider_items=items, items_model="gpt-6-luna"),
               Message("tool", '{"ok": true}', tool_call_id="call_1")]
    same = _provider(Recorder())._input_items(history)
    other = _provider(Recorder(), model="gpt-6-sol")._input_items(history)
    assert [i.get("type") for i in same] == [None, "reasoning", "function_call",
                                             "function_call_output"]
    assert [i.get("type") for i in other] == [None, "function_call", "function_call_output"]
    assert same[-1] == {"type": "function_call_output", "call_id": "call_1",
                        "output": '{"ok": true}'}


async def test_vendor_errors_never_cross_the_boundary() -> None:
    p = _provider(Recorder(RuntimeError("401 key sk-test-not-real rejected; body: student")))
    with pytest.raises(ProviderError) as exc:
        await p.complete(role=Role.ASSISTANT, messages=[Message("user", "hi")], tools=[TOOL])
    assert "sk-test" not in str(exc.value) and "student" not in str(exc.value)


async def test_an_assistant_turn_runs_end_to_end_over_responses() -> None:
    reply = {"text": "Your plan's graduation term is Spring 2028."}
    rec = Recorder(_response(REASONING, _call("get_plan", {}, "call_a")),
                   _response(_call("reply", reply, "call_b"), inp=900, out=40))
    tools = SyntheticTools("alex")
    result = await Assistant(tools, default=_provider(rec)).turn("When do I graduate?")
    assert result.reply.text == reply["text"] and result.reply.generated_by == "model"
    assert [c["name"] for c in result.tool_calls] == ["get_plan"]
    second = rec.requests[1]["input"]
    # The function call and its output go back; so does the same model's reasoning.
    outputs = [i for i in second if i.get("type") == "function_call_output"]
    assert outputs and outputs[0]["call_id"] == "call_a"
    assert json.loads(outputs[0]["output"])["name"] == "get_plan"
    assert any(i.get("type") == "reasoning" for i in second)
    assert result.usage.input_tokens == 1020 and tools.plan_revision() == 1


async def test_a_text_answer_without_reply_is_rejected_and_regenerated() -> None:
    rec = Recorder(
        _response(NS(type="message", content=[NS(type="output_text", text="Sure, done!")])),
        _response(_call("get_plan", {}, "call_c")),
        _response(_call("reply", {"text": "Your plan has 3 semesters."}, "call_d")))
    tools = SyntheticTools("alex")
    result = await Assistant(tools, default=_provider(rec)).turn("Tell me about my plan")
    assert result.grounding_failures and "without calling reply" in \
        result.grounding_failures[0][0]
    assert result.reply.text == "Your plan has 3 semesters."
