"""Offline tests for the app's Groq call + QA retry logic (requests is faked)."""
import json
import types

import pytest

import academicsgen_app as app


class FakeResp:
    def __init__(self, status=200, content="", finish="stop", headers=None):
        self.status_code = status
        self._content, self._finish = content, finish
        self.headers = headers or {}
        self.text = ""

    def json(self):
        if self.status_code != 200:
            return {"error": {"message": "fake error"}}
        return {"choices": [{"message": {"content": self._content}, "finish_reason": self._finish}]}


@pytest.fixture
def fake(monkeypatch):
    calls = []
    queue = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return queue.pop(0)

    monkeypatch.setattr(app, "st", types.SimpleNamespace(secrets={"GROQ_API_KEY": "test-key"}))
    monkeypatch.setattr(app.requests, "post", post)
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    return types.SimpleNamespace(calls=calls, queue=queue)


def qa_json(a=10, b=5, c=1):
    return json.dumps({
        "part_a": [{"question": f"Q{i}?", "answer": "A."} for i in range(a)],
        "part_b": [{"question": f"LQ{i}?", "answer": "LA."} for i in range(b)],
        "part_c": [{"question": f"CQ{i}?", "answer": "CA."} for i in range(c)],
    })


ARGS = dict(subject_name="Natural Language Processing", subject_code="AL3501",
            unit_number=2, unit_title="Word Level Analysis", regulation="R-2021")


def test_qa_variant_uses_qa_prompt_and_strict_schema(fake):
    fake.queue.append(FakeResp(content=qa_json()))
    out = app.generate_content(variant="QA", **ARGS)
    assert [len(out[k]) for k in ("part_a", "part_b", "part_c")] == [10, 5, 1]
    body = fake.calls[0]
    assert "Anna University R-2021 lecturer" in body["messages"][0]["content"]
    assert "(13 marks each)" in body["messages"][0]["content"]
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "EXACTLY 10" in body["messages"][0]["content"]
    assert "verified notes" not in body["messages"][0]["content"].lower()   # the old QA bug


def test_qa_wrong_count_is_re_asked(fake):
    fake.queue += [FakeResp(content=qa_json(a=13)), FakeResp(content=qa_json())]
    out = app.generate_content(variant="QA", **ARGS)
    assert len(out["part_a"]) == 10 and len(fake.calls) == 2


def test_qa_gives_up_after_three_bad_answers(fake):
    fake.queue += [FakeResp(content=qa_json(a=13)) for _ in range(3)]
    with pytest.raises(app.GenerationError, match="failed validation 3 times"):
        app.generate_content(variant="QA", **ARGS)


def test_truncated_answer_is_never_returned(fake):
    fake.queue += [FakeResp(content="half an answ", finish="length") for _ in range(3)]
    with pytest.raises(app.GenerationError, match="cut off"):
        app.generate_content(variant="Notes", **ARGS)


def test_bad_key_is_not_retried(fake):
    fake.queue += [FakeResp(status=401)]
    with pytest.raises(app.GenerationError, match="HTTP 401"):
        app.generate_content(variant="Notes", **ARGS)
    assert len(fake.calls) == 1


def test_rate_limit_then_success(fake):
    fake.queue += [FakeResp(status=429, headers={"retry-after": "1"}), FakeResp(content="## Notes\n\ntext")]
    assert app.generate_content(variant="Notes", **ARGS).startswith("## Notes")


def test_notes_prompt_has_markdown_rules_not_qa(fake):
    fake.queue.append(FakeResp(content="x"))
    app.generate_content(variant="VerifiedNotes", **ARGS)
    body = fake.calls[0]
    assert "response_format" not in body
    assert "Do NOT use LaTeX" in body["messages"][0]["content"]


def test_unconfigured_regulation_spends_no_groq_request(fake):
    with pytest.raises(app.UnsupportedRegulationError):
        app.generate_content(variant="QA", **{**ARGS, "regulation": "R-2025"})
    assert fake.calls == []


def test_notes_follow_subject_regulation(fake):
    fake.queue.append(FakeResp(content="x"))
    app.generate_content(variant="Notes", **{**ARGS, "regulation": "R2025"})
    assert "Regulation: R-2025" in fake.calls[0]["messages"][0]["content"]


def test_build_pdf_end_to_end_for_both_kinds():
    qa = json.loads(qa_json())
    for variant, content in (("QA", qa), ("Notes", "## A\n\n|x|y|\n|-|-|\n|1|→|")):
        buf = app.build_pdf("Natural Language Processing", "AL3501", "AI&DS", 2,
                            "Word Level Analysis", variant, content, regulation="R-2021")
        data = buf.read()
        assert data.startswith(b"%PDF") and len(data) > 1000
