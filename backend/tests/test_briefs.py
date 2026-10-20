"""Team-Lead intake: goal → validated clarifying questions (Stage 8.1).

POST /briefs runs at most two bounded LLM generations outside any
transaction, then persists the validated Brief atomically. Failures store
nothing — no draft row with partial questions.
"""

import asyncio

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.mock import MockProvider
from app.llm.types import LlmConfigurationError, ProviderError
from app.persistence.repositories.concrete import BriefRepository
from app.teamlead.prompts import build_questions_user_prompt, build_summary_user_prompt
from app.teamlead.questions import normalize_questions, validate_questions
from app.teamlead.summary import (
    generate_summary,
    normalize_summary,
    validate_answers,
    validate_summary,
)
from tests.conftest import login_as, seed_operator
from tests.factories import BIZ, BIZ_OTHER, make_business, make_world

_briefs = BriefRepository()

VALID_TEXT = (
    '{"questions": ['
    '{"id": "q1", "text": "Who is the audience?", "options": ["A", "B"]}, '
    '{"id": "q2", "text": "What is the budget?"}'
    "]}"
)


def _brief_url(biz: str = BIZ) -> str:
    return f"/api/v1/businesses/{biz}/briefs"


async def _authed(session: AsyncSession, api_client: httpx.AsyncClient) -> dict:
    """Seed world + owner operator, log in (cookie in jar)."""
    await make_world(session)
    await session.commit()
    await seed_operator(session)
    csrf = await login_as(api_client)
    return {"X-CSRF-Token": csrf}


async def _login_user(
    session: AsyncSession,
    api_client: httpx.AsyncClient,
    email: str,
    password: str,
    grant_biz: str | None,
) -> dict:
    """Create user (optionally without any membership), log in."""
    from sqlalchemy import select

    from app.core.passwords import hash_password
    from app.persistence.models.user import User
    from app.persistence.repositories.concrete import MembershipRepository

    uid = f"u_{email.split('@')[0]}"
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is None:
        session.add(
            User(id=uid, email=email, password_hash=hash_password(password), display_name=email)
        )
        await session.flush()
        if grant_biz is not None:
            await MembershipRepository().grant(session, uid, grant_biz, "owner")
        await session.commit()
    res = await api_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return {"X-CSRF-Token": res.json()["csrf_token"]}


def _mocked(monkeypatch: pytest.MonkeyPatch, provider: MockProvider) -> None:
    monkeypatch.setattr("app.api.v1.briefs.create_provider", lambda settings: provider)


def test_prompt_contains_scope_not_secrets() -> None:
    text = build_questions_user_prompt("Northstar", "Fitness", "Grow fast")
    assert "Northstar" in text and "Grow fast" in text


def test_validate_questions_accepts_valid() -> None:
    assert validate_questions({"questions": [{"id": "q1", "text": "Why?"}]}) != []
    assert (
        validate_questions(
            {"questions": [{"id": "q1", "text": "A?"}, {"id": "q2", "text": "B?", "options": []}]}
        )
        == []
    )


def test_validate_questions_rejects_shapes() -> None:
    assert validate_questions({}) != []
    assert validate_questions({"questions": []}) != []
    assert validate_questions({"questions": [{"id": "only-one", "text": "x"}]}) != []
    assert validate_questions({"questions": [{"id": f"q{i}", "text": "x"} for i in range(5)]}) != []
    assert (
        validate_questions({"questions": [{"id": "  ", "text": "x"}, {"id": "q2", "text": "y"}]})
        != []
    )
    assert (
        validate_questions({"questions": [{"id": "q1", "text": "  "}, {"id": "q2", "text": "y"}]})
        != []
    )
    assert (
        validate_questions({"questions": [{"id": "q1", "text": "x"}, {"id": "q1", "text": "y"}]})
        != []
    )
    many = {
        "questions": [
            {"id": "q1", "text": "x", "options": [f"o{i}" for i in range(7)]},
            {"id": "q2", "text": "y"},
        ]
    }
    assert validate_questions(many) != []
    bad_opts = {
        "questions": [
            {"id": "q1", "text": "x", "options": ["ok", "  "]},
            {"id": "q2", "text": "y"},
        ]
    }
    assert validate_questions(bad_opts) != []


def test_normalize_strips_and_drops_empty_options() -> None:
    out = normalize_questions(
        {"questions": [{"id": " q1 ", "text": " A? ", "options": []}, {"id": "q2", "text": "B?"}]}
    )
    assert out == [{"id": "q1", "text": "A?"}, {"id": "q2", "text": "B?"}]


async def test_create_brief_ok(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    provider = MockProvider(texts=[VALID_TEXT])
    _mocked(monkeypatch, provider)

    res = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["goal"] == "Grow fast"
    assert body["status"] == "questions_ready"
    assert body["businessId"] == BIZ
    assert body["contract"] == "v1"
    assert [q["id"] for q in body["questions"]] == ["q1", "q2"]
    assert body["questions"][0]["options"] == ["A", "B"]
    assert body["answers"] == [] and body["summary"] is None and body["projectId"] is None
    assert len(provider.calls) == 1

    row = await _briefs.get(session, BIZ, body["id"])
    assert row is not None and row.status == "questions_ready" and len(row.questions) == 2


@pytest.mark.parametrize("bad_text", ["not json at all {{{", '{"questions": []}'])
async def test_invalid_output_422_without_partial_rows(
    session: AsyncSession,
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    bad_text: str,
) -> None:
    headers = await _authed(session, api_client)
    provider = MockProvider(texts=[bad_text, bad_text])
    _mocked(monkeypatch, provider)

    res = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)

    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "validation_error"
    assert len(provider.calls) == 2  # generation budget exhausted, then stop
    assert await _briefs.count(session, BIZ) == 0


async def test_wrong_count_422(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    one = '{"questions": [{"id": "q1", "text": "Only?"}]}'
    five = (
        '{"questions": [' + ", ".join(f'{{"id": "q{i}", "text": "T{i}?"}}' for i in range(5)) + "]}"
    )
    _mocked(monkeypatch, MockProvider(texts=[one, five]))

    res = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)

    assert res.status_code == 422, res.text
    assert await _briefs.count(session, BIZ) == 0


async def test_second_generation_can_recover(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    provider = MockProvider(texts=["nope {{{", VALID_TEXT])
    _mocked(monkeypatch, provider)

    res = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)

    assert res.status_code == 200, res.text
    assert len(provider.calls) == 2
    assert await _briefs.count(session, BIZ) == 1


async def test_provider_error_500_without_row(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    _mocked(monkeypatch, MockProvider(errors=[ProviderError("boom")]))

    res = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)

    assert res.status_code == 500, res.text
    assert await _briefs.count(session, BIZ) == 0


async def test_unconfigured_provider_500_without_secrets(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)

    def boom(settings):
        raise LlmConfigurationError("llm_api_key is not configured (APP_LLM_API_KEY)")

    monkeypatch.setattr("app.api.v1.briefs.create_provider", boom)
    res = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)

    assert res.status_code == 500, res.text
    assert "APP_LLM_API_KEY" not in res.text
    assert await _briefs.count(session, BIZ) == 0


async def test_goal_validation(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    provider = MockProvider(texts=[VALID_TEXT])
    _mocked(monkeypatch, provider)

    assert (
        await api_client.post(_brief_url(), json={"goal": ""}, headers=headers)
    ).status_code == 422
    assert (
        await api_client.post(_brief_url(), json={"goal": "   "}, headers=headers)
    ).status_code == 422
    assert (
        await api_client.post(_brief_url(), json={"goal": "x" * 2001}, headers=headers)
    ).status_code == 422
    assert len(provider.calls) == 0  # rejected before any LLM call
    assert await _briefs.count(session, BIZ) == 0


async def test_anonymous_401(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await make_world(session)
    await session.commit()
    assert (await api_client.post(_brief_url(), json={"goal": "Grow"})).status_code == 401


async def test_csrf_403(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await _authed(session, api_client)
    assert (await api_client.post(_brief_url(), json={"goal": "Grow"})).status_code == 403
    bad = await api_client.post(
        _brief_url(), json={"goal": "Grow"}, headers={"X-CSRF-Token": "nope"}
    )
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "forbidden"


async def test_nonmember_404(session: AsyncSession, api_client: httpx.AsyncClient) -> None:
    await make_world(session)
    await session.commit()
    headers = await _login_user(session, api_client, "stranger@example.com", "pw-stranger-1", None)

    res = await api_client.post(_brief_url(), json={"goal": "Grow"}, headers=headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_foreign_business_404(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_world(session, biz_id=BIZ)
    await make_business(session, biz_id=BIZ_OTHER)
    await session.commit()
    headers_b = await _login_user(session, api_client, "b@example.com", "pw-b-pw-b", BIZ_OTHER)
    provider = MockProvider(texts=[VALID_TEXT])
    _mocked(monkeypatch, provider)

    # B reaches into A's business: uniform 404, no LLM call, no row.
    res = await api_client.post(_brief_url(BIZ), json={"goal": "Grow"}, headers=headers_b)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    assert len(provider.calls) == 0
    assert await _briefs.count(session, BIZ) == 0
    assert await _briefs.count(session, BIZ_OTHER) == 0


async def test_get_resume_same_brief(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    _mocked(monkeypatch, MockProvider(texts=[VALID_TEXT]))
    created = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)
    assert created.status_code == 200, created.text
    brief_id = created.json()["id"]

    res = await api_client.get(f"{_brief_url()}/{brief_id}", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json() == created.json()


async def test_get_unknown_and_foreign_404(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    headers = await _authed(session, api_client)
    assert (await api_client.get(f"{_brief_url()}/nope", headers=headers)).status_code == 404

    await make_business(session, biz_id=BIZ_OTHER)
    await session.commit()
    from tests.test_isolation import _login_as

    headers_b = await _login_as(session, api_client, "b@example.com", "pw-b-pw-b", BIZ_OTHER)
    res = await api_client.get(f"{_brief_url(BIZ)}/nope", headers=headers_b)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_repeated_posts_create_separate_briefs(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    _mocked(monkeypatch, MockProvider(texts=[VALID_TEXT, VALID_TEXT]))

    first = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)
    second = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] != second.json()["id"]
    assert await _briefs.count(session, BIZ) == 2


# ---------------------------------------------------------------------------
# Stage 8.2: answers & structured brief.
# POST .../briefs/{id}/answers validates owner answers against the stored
# questions, generates the brief summary (≤2 generations, outside any txn),
# then flips questions_ready → brief_ready via one conditional UPDATE.
# ---------------------------------------------------------------------------

SUMMARY_TEXT = (
    '{"goal": "Grow fast", "desired_outcome": "Double signup rate", '
    '"context": ["B2B SaaS"], "success_criteria": ["+100% signups"], '
    '"assumptions": ["Budget approved"], "open_questions": ["Launch date?"]}'
)

ANSWERS = [
    {"question_id": "q1", "text": "Solo founders"},
    {"question_id": "q2", "text": "Five thousand"},
]

_STORED_QUESTIONS = [
    {"id": "q1", "text": "Who is the audience?", "options": ["A", "B"]},
    {"id": "q2", "text": "What is the budget?"},
]


async def _make_ready(
    api_client: httpx.AsyncClient,
    headers: dict,
    monkeypatch: pytest.MonkeyPatch,
    summary_texts: list[str] | None = None,
) -> tuple[str, MockProvider]:
    """Create a questions_ready brief; arm the mock for the answers call."""
    _mocked(monkeypatch, MockProvider(texts=[VALID_TEXT]))
    created = await api_client.post(_brief_url(), json={"goal": "Grow fast"}, headers=headers)
    assert created.status_code == 200, created.text
    texts = list(summary_texts if summary_texts is not None else [SUMMARY_TEXT])
    provider = MockProvider(texts=texts)
    _mocked(monkeypatch, provider)
    return created.json()["id"], provider


def test_summary_prompt_contains_qa_not_secrets() -> None:
    text = build_summary_user_prompt("Grow fast", _STORED_QUESTIONS, ANSWERS)
    assert "Grow fast" in text
    assert "Solo founders" in text and "Five thousand" in text
    assert "Who is the audience?" in text


def test_validate_answers_accepts_and_orders() -> None:
    normalized, violations = validate_answers(_STORED_QUESTIONS, list(reversed(ANSWERS)))
    assert violations == []
    assert [a["question_id"] for a in normalized] == ["q1", "q2"]
    assert normalized[0]["text"] == "Solo founders"


def test_validate_answers_rejects() -> None:
    assert validate_answers([], ANSWERS)[1] != []
    assert validate_answers("nope", ANSWERS)[1] != []
    assert validate_answers(_STORED_QUESTIONS, {"a": 1})[1] != []
    unknown = [dict(ANSWERS[0]), {"question_id": "qX", "text": "z"}]
    assert validate_answers(_STORED_QUESTIONS, unknown)[1] != []
    dup = [dict(ANSWERS[0]), dict(ANSWERS[0])]
    assert validate_answers(_STORED_QUESTIONS, dup)[1] != []
    assert validate_answers(_STORED_QUESTIONS, [dict(ANSWERS[0])])[1] != []
    blank = [dict(ANSWERS[0]), {"question_id": "q2", "text": "   "}]
    assert validate_answers(_STORED_QUESTIONS, blank)[1] != []
    assert validate_answers(_STORED_QUESTIONS, [{"question_id": "q1"}])[1] != []
    big = [dict(ANSWERS[0]), {"question_id": "q2", "text": "x" * 2001}]
    assert validate_answers(_STORED_QUESTIONS, big)[1] != []


def test_validate_summary_shapes() -> None:
    import json as _json

    assert validate_summary(_json.loads(SUMMARY_TEXT)) == []
    assert validate_summary({}) != []
    assert validate_summary({"goal": "  ", "desired_outcome": "x"}) != []
    bad_lists = _json.loads(SUMMARY_TEXT)
    bad_lists["context"] = ["ok", "  "]
    assert validate_summary(bad_lists) != []
    many = _json.loads(SUMMARY_TEXT)
    many["assumptions"] = [f"a{i}" for i in range(21)]
    assert validate_summary(many) != []


def test_normalize_summary_strips() -> None:
    out = normalize_summary(
        {
            "goal": " G ",
            "desired_outcome": " D ",
            "context": [" c "],
            "success_criteria": [" s "],
            "assumptions": [],
            "open_questions": [],
        }
    )
    assert out == {
        "goal": "G",
        "desired_outcome": "D",
        "context": ["c"],
        "success_criteria": ["s"],
        "assumptions": [],
        "open_questions": [],
    }


async def test_generate_summary_success_single_call() -> None:
    provider = MockProvider(texts=[SUMMARY_TEXT])
    out = await generate_summary(provider, Settings(), "Grow fast", _STORED_QUESTIONS, ANSWERS)
    assert out["goal"] == "Grow fast" and out["desired_outcome"] == "Double signup rate"
    assert len(provider.calls) == 1


async def test_generate_summary_retries_then_succeeds() -> None:
    provider = MockProvider(texts=['{"bogus": 1}', SUMMARY_TEXT])
    out = await generate_summary(provider, Settings(), "Grow fast", _STORED_QUESTIONS, ANSWERS)
    assert out["goal"] == "Grow fast"
    assert len(provider.calls) == 2


async def test_generate_summary_exhaustion_raises() -> None:
    from app.teamlead.questions import BriefGenerationError

    provider = MockProvider(texts=['{"bogus": 1}', '{"bogus": 2}'])
    with pytest.raises(BriefGenerationError):
        await generate_summary(provider, Settings(), "Grow fast", _STORED_QUESTIONS, ANSWERS)
    assert len(provider.calls) == 2


async def test_generate_summary_propagates_cancel() -> None:
    provider = MockProvider(texts=[SUMMARY_TEXT], latency_s=30.0)
    task = asyncio.ensure_future(
        generate_summary(provider, Settings(), "Grow fast", _STORED_QUESTIONS, ANSWERS)
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_submit_answers_ok(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, provider = await _make_ready(api_client, headers, monkeypatch)

    # Reversed order is accepted; the stored row follows question order.
    res = await api_client.post(
        f"{_brief_url()}/{brief_id}/answers",
        json={"answers": list(reversed(ANSWERS))},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "brief_ready"
    assert [a["question_id"] for a in body["answers"]] == ["q1", "q2"]
    assert body["summary"]["goal"] == "Grow fast"
    assert body["summary"]["desired_outcome"] == "Double signup rate"
    assert len(provider.calls) == 1

    again = await api_client.get(f"{_brief_url()}/{brief_id}", headers=headers)
    assert again.status_code == 200 and again.json() == body


async def test_submit_answers_validation_matrix(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    bad_payloads = [
        {"answers": [{"question_id": "q1", "text": "only one"}]},  # missing q2
        {"answers": [dict(ANSWERS[0]), {"question_id": "qX", "text": "z"}]},  # unknown
        {"answers": [dict(ANSWERS[0]), dict(ANSWERS[0])]},  # duplicate
        {"answers": [dict(ANSWERS[0]), {"question_id": "q2", "text": "  "}]},  # blank
        {"answers": [dict(ANSWERS[0]), {"question_id": "q2", "text": "x" * 2001}]},  # oversize
    ]
    for payload in bad_payloads:
        brief_id, provider = await _make_ready(api_client, headers, monkeypatch)
        res = await api_client.post(
            f"{_brief_url()}/{brief_id}/answers", json=payload, headers=headers
        )
        assert res.status_code == 422, (payload, res.text)
        assert res.json()["error"]["code"] == "validation_error"
        assert len(provider.calls) == 0
        row = await _briefs.get(session, BIZ, brief_id)
        assert row is not None and row.status == "questions_ready" and list(row.answers) == []


async def test_submit_answers_invalid_summary_json(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, provider = await _make_ready(
        api_client, headers, monkeypatch, summary_texts=["not json at all"]
    )
    res = await api_client.post(
        f"{_brief_url()}/{brief_id}/answers", json={"answers": ANSWERS}, headers=headers
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "validation_error"
    row = await _briefs.get(session, BIZ, brief_id)
    assert row is not None and row.status == "questions_ready" and list(row.answers) == []


async def test_submit_answers_exhaustion(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, provider = await _make_ready(
        api_client, headers, monkeypatch, summary_texts=['{"bogus": 1}', '{"bogus": 2}']
    )
    res = await api_client.post(
        f"{_brief_url()}/{brief_id}/answers", json={"answers": ANSWERS}, headers=headers
    )
    assert res.status_code == 422, res.text
    assert len(provider.calls) == 2
    row = await _briefs.get(session, BIZ, brief_id)
    assert row is not None and row.status == "questions_ready" and list(row.answers) == []


async def test_submit_answers_provider_error(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, provider = await _make_ready(api_client, headers, monkeypatch)
    _mocked(monkeypatch, MockProvider(errors=[ProviderError("down")]))
    res = await api_client.post(
        f"{_brief_url()}/{brief_id}/answers", json={"answers": ANSWERS}, headers=headers
    )
    assert res.status_code == 500, res.text
    row = await _briefs.get(session, BIZ, brief_id)
    assert row is not None and row.status == "questions_ready" and list(row.answers) == []


async def test_submit_answers_cancel_no_partial_write(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling mid-generation stores nothing — no answers, no summary."""
    headers = await _authed(session, api_client)
    brief_id, _ = await _make_ready(api_client, headers, monkeypatch)
    provider = MockProvider(texts=[SUMMARY_TEXT], latency_s=30.0)
    _mocked(monkeypatch, provider)

    entered = asyncio.Event()
    real_complete = provider.complete

    async def gated(request):  # type: ignore[no-untyped-def]
        entered.set()
        return await real_complete(request)

    monkeypatch.setattr(provider, "complete", gated)
    task = asyncio.ensure_future(
        api_client.post(
            f"{_brief_url()}/{brief_id}/answers", json={"answers": ANSWERS}, headers=headers
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=5.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    row = await _briefs.get(session, BIZ, brief_id)
    assert row is not None
    assert row.status == "questions_ready"
    assert list(row.answers) == [] and row.summary is None


async def test_submit_answers_repeat_identical_no_extra_llm(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, provider = await _make_ready(api_client, headers, monkeypatch)

    first = await api_client.post(
        f"{_brief_url()}/{brief_id}/answers", json={"answers": ANSWERS}, headers=headers
    )
    assert first.status_code == 200, first.text
    assert len(provider.calls) == 1

    second = await api_client.post(
        f"{_brief_url()}/{brief_id}/answers", json={"answers": ANSWERS}, headers=headers
    )
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert len(provider.calls) == 1


async def test_submit_answers_conflict_different(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, provider = await _make_ready(api_client, headers, monkeypatch)

    first = await api_client.post(
        f"{_brief_url()}/{brief_id}/answers", json={"answers": ANSWERS}, headers=headers
    )
    assert first.status_code == 200, first.text

    other = [dict(ANSWERS[0]), {"question_id": "q2", "text": "Ten thousand"}]
    res = await api_client.post(
        f"{_brief_url()}/{brief_id}/answers", json={"answers": other}, headers=headers
    )
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "conflict"
    row = await _briefs.get(session, BIZ, brief_id)
    assert row is not None and list(row.answers) == first.json()["answers"]


async def test_submit_answers_concurrent_one_winner(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, _ = await _make_ready(
        api_client, headers, monkeypatch, summary_texts=[SUMMARY_TEXT, SUMMARY_TEXT]
    )
    other = [dict(ANSWERS[0]), {"question_id": "q2", "text": "Ten thousand"}]

    results = await asyncio.gather(
        api_client.post(
            f"{_brief_url()}/{brief_id}/answers", json={"answers": ANSWERS}, headers=headers
        ),
        api_client.post(
            f"{_brief_url()}/{brief_id}/answers", json={"answers": other}, headers=headers
        ),
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    row = await _briefs.get(session, BIZ, brief_id)
    assert row is not None and row.status == "brief_ready"
    five = [
        {"question_id": "q1", "text": "Solo founders"},
        {"question_id": "q2", "text": "Five thousand"},
    ]
    ten = [
        {"question_id": "q1", "text": "Solo founders"},
        {"question_id": "q2", "text": "Ten thousand"},
    ]
    assert list(row.answers) in (five, ten)


async def test_submit_answers_anonymous_401(
    session: AsyncSession, api_client: httpx.AsyncClient
) -> None:
    await make_world(session)
    await session.commit()
    url = f"{_brief_url()}/whatever/answers"
    assert (await api_client.post(url, json={"answers": ANSWERS})).status_code == 401


async def test_submit_answers_csrf_403(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, _ = await _make_ready(api_client, headers, monkeypatch)
    url = f"{_brief_url()}/{brief_id}/answers"
    assert (await api_client.post(url, json={"answers": ANSWERS})).status_code == 403
    bad = await api_client.post(url, json={"answers": ANSWERS}, headers={"X-CSRF-Token": "nope"})
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "forbidden"


async def test_submit_answers_scope_404(
    session: AsyncSession, api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _authed(session, api_client)
    brief_id, _ = await _make_ready(api_client, headers, monkeypatch)
    url = f"{_brief_url()}/{brief_id}/answers"

    await make_business(session, biz_id=BIZ_OTHER)
    await session.commit()
    from tests.test_isolation import _login_as

    headers_b = await _login_as(session, api_client, "b@example.com", "pw-b-pw-b", BIZ_OTHER)
    foreign = await api_client.post(url, json={"answers": ANSWERS}, headers=headers_b)
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "not_found"

    # _login_as replaced the jar cookie with B's session — log back in as A.
    headers = {"X-CSRF-Token": await login_as(api_client)}
    missing = await api_client.post(
        f"{_brief_url()}/nope/answers", json={"answers": ANSWERS}, headers=headers
    )
    assert missing.status_code == 404
