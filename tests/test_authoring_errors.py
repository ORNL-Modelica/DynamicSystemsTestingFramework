"""What the tool says when an action fails: an error a person caused reads as
its plain reason, the location already on the screen beside it; anything
else is a bug and keeps its class name."""

from __future__ import annotations

import pytest

from dstf.authoring.errors import user_text
from dstf.comparison.handles import HandleEditError
from dstf.discovery.patch_apply import PatchError, apply_patch_to_document


def test_a_patch_error_shows_its_reason_only():
    e = PatchError("validator rejected the patched tree: metrics: X", reason="X")
    assert user_text(e) == "X"


def test_a_handle_error_shows_its_reason_only():
    e = HandleEditError(
        "/metrics/children/1: range/max: lower above upper", reason="lower above upper"
    )
    assert user_text(e) == "lower above upper"


def test_an_expected_error_has_no_class_name():
    assert user_text(ValueError("no result for x")) == "no result for x"


def test_a_config_error_has_no_class_name():
    from dstf.config import ConfigError

    assert user_text(ConfigError("no test_spec.json")) == "no test_spec.json"


def test_a_config_error_is_not_logged_and_a_bare_value_error_is(caplog):
    """A ConfigError is a ValueError, so `user_text` alone cannot tell it
    from a bug's; `USER_ERRORS` is what keeps it out of the log."""
    pytest.importorskip("nicegui")
    from dstf.authoring.shell.actions import log_unless_users
    from dstf.config import ConfigError

    for e in (ConfigError("mine"), ValueError("a bug's")):
        try:
            raise e
        except ValueError as raised:
            log_unless_users(raised, "probe")
    assert [r.exc_info[1] for r in caplog.records if r.exc_info] == [e]


def test_a_refused_move_is_a_persons_error_shown_and_not_logged(caplog):
    """A Move to… menu or a drag map gone stale under a staged edit offers
    a move the tree now refuses: its sentence is shown, never logged as a
    bug with a traceback."""
    pytest.importorskip("nicegui")
    from dstf.authoring.errors import USER_ERRORS
    from dstf.authoring.shell.actions import log_unless_users
    from dstf.authoring.tree_move import INTO_ITSELF, MoveRefused

    assert MoveRefused in USER_ERRORS
    try:
        raise MoveRefused(INTO_ITSELF)
    except ValueError as raised:
        log_unless_users(raised, "probe")
        assert user_text(raised) == INTO_ITSELF
    assert not caplog.records


def _users_errors() -> dict:
    """One of each error a person's own act raises, with the sentence it
    is shown as — named HERE, not read from `USER_ERRORS`: a member taken
    out of that tuple leaves its row behind to fail."""
    from dstf.authoring.session import QueueError
    from dstf.authoring.tree_move import INTO_ITSELF, MoveRefused
    from dstf.authoring.unscorable import Refused as EditorRefused
    from dstf.backends.pool import Refused as PoolRefused
    from dstf.config import ConfigError

    return {
        "PatchError": (
            PatchError("add /metrics: no leaf", reason="no leaf"),
            "no leaf",
        ),
        "HandleEditError": (
            HandleEditError("range/max: lower above upper", reason="lower above upper"),
            "lower above upper",
        ),
        "ConfigError": (ConfigError("no test_spec.json"), "no test_spec.json"),
        "QueueError": (QueueError("A is already queued"), "A is already queued"),
        "PoolRefused": (PoolRefused("the pool is closed"), "the pool is closed"),
        "MoveRefused": (MoveRefused(INTO_ITSELF), INTO_ITSELF),
        "EditorRefused": (
            EditorRefused("a tolerance is 0 or more"),
            "a tolerance is 0 or more",
        ),
    }


def test_the_table_of_a_persons_errors_names_every_one():
    from dstf.authoring.errors import USER_ERRORS

    assert {type(e) for e, _ in _users_errors().values()} == set(USER_ERRORS)


@pytest.mark.parametrize(
    "name",
    [
        "PatchError",
        "HandleEditError",
        "ConfigError",
        "QueueError",
        "PoolRefused",
        "MoveRefused",
        "EditorRefused",
    ],
)
def test_a_persons_error_reads_as_its_sentence_and_logs_no_traceback(name, caplog):
    """Each by what it DOES, where the tool shows a failed act: the
    sentence alone — no `Refused: ` before it — and nothing in the log.
    The pool's and the editor's refusals were held by nothing: either
    could leave `USER_ERRORS` with every test green."""
    pytest.importorskip("nicegui")
    from dstf.authoring.shell.actions import log_unless_users

    error, sentence = _users_errors()[name]
    try:
        raise error
    except Exception as raised:
        log_unless_users(raised, "probe")
        assert user_text(raised) == sentence
    assert not caplog.records


def test_an_unexpected_error_keeps_its_class_name():
    assert user_text(KeyError("k")) == "KeyError: 'k'"


def test_a_failing_ops_reason_drops_the_op_and_its_path():
    """``str()`` names the op and the pointer for the CLI; the reason is the
    part a person can act on."""
    data = {"tests": [{"model": "M", "variables": ["x"]}]}
    with pytest.raises(PatchError) as box:
        apply_patch_to_document(
            data, "M", [{"op": "remove", "path": "/comparison/nope"}]
        )
    assert str(box.value).startswith("/comparison/nope: patch_ops[0]")
    assert "patch_ops" not in box.value.reason
    assert "/comparison" not in box.value.reason
    assert user_text(box.value) == box.value.reason


@pytest.fixture
def session(tmp_path):
    from dstf.authoring.session import Session
    from test_session import _project

    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


def _op(*op):
    from dstf.authoring.spec_document import PatchOp
    from test_session import A

    return PatchOp(A, *op)


def _refusal(session, *ops) -> str:
    """What the tool shows when ``ops`` are staged on A and refused."""
    with pytest.raises(PatchError) as box:
        session.stage([_op(*op) for op in ops])
    return user_text(box.value)


def test_a_window_ending_before_it_starts_reads_as_a_sentence(session):
    leaf = {"metric": "nrmse", "variable": "x", "window": {"start": 0.8, "end": 0.2}}
    text = _refusal(session, ("add", "/metrics", leaf))
    assert (
        text == "The window must end after it starts: it starts at 0.8 and ends at 0.2."
    )


def test_a_k_of_n_left_with_fewer_children_than_k_reads_as_a_sentence(session):
    leaf = {"metric": "nrmse", "variable": "x"}
    tree = {
        "combinator": "and",
        "children": [{"combinator": "k-of-n", "k": 2, "children": [leaf, leaf]}],
    }
    session.stage([_op("add", "/metrics", tree)])
    text = _refusal(session, ("remove", "/metrics/children/0/children/1"))
    assert text == "k is 2, but this k-of-n has only 1 criterion."


def test_two_validator_sentences_are_joined_as_sentences(session):
    """Each already ends in a full stop: "check the name.; Every tree"."""
    leaf = {"metric": "nrmse", "variable": "x", "against": "nope"}
    text = _refusal(session, ("add", "/metrics", leaf))
    assert "check the name. Every tree needs" in text, text
    assert ".;" not in text
