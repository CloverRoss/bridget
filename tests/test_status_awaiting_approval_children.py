"""Tests for the Status view surfacing awaiting-approval children +
extending _PROJECT_TAGS with handed-off-to-mayor / staged (mg-1ef2).

The mg-313f children-collapse change hid every child of a Project
behind a single aggregate count line. Children carrying the
`awaiting-approval` tag need user attention NOW and were being lost.
This fix renders them as a flagged sub-list under their parent
Project in addition to the aggregate count.

Part A also extends _PROJECT_TAGS with `handed-off-to-mayor` and
`staged` so director/mayor hand-off-stage items bucket as Projects
instead of falling through to Designs.
"""
import importlib.util
import json
import os
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / 'bridget'


def _load_bridget(home: Path):
    os.environ['HOME'] = str(home)
    env_dir = home / '.pogo'
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / 'bridget.env').write_text(
        'DISCORD_BOT_TOKEN=fake-token-for-tests\n'
        'DISCORD_USER_ID=1\n'
        'DISCORD_SERVER_ID=2\n'
    )
    loader = SourceFileLoader('bridget', str(SCRIPT))
    spec = importlib.util.spec_from_loader('bridget', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def bridget(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return _load_bridget(tmp_path)


def _ndjson(items: list[dict]) -> str:
    return '\n'.join(json.dumps(i) for i in items) + '\n'


# -- Part A: _PROJECT_TAGS extension ---------------------------------------

def test_handed_off_to_mayor_buckets_into_projects(bridget):
    items = [{'id': 'mg-fea0', 'type': 'idea',
              'tags': ['handed-off-to-mayor', 'staged']}]
    buckets, _ = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-fea0']
    assert buckets['Designs'] == []


def test_staged_alone_buckets_into_projects(bridget):
    items = [{'id': 'mg-aaaa', 'type': 'idea', 'tags': ['staged']}]
    buckets, _ = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-aaaa']
    assert buckets['Designs'] == []


def test_handed_off_alone_buckets_into_projects(bridget):
    items = [{'id': 'mg-bbbb', 'type': 'idea', 'tags': ['handed-off-to-mayor']}]
    buckets, _ = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-bbbb']
    assert buckets['Designs'] == []


def test_ordinary_kickoff_pending_still_buckets_into_projects(bridget):
    # Regression: existing canonical Project tag still routes correctly.
    items = [{'id': 'mg-bbc2', 'type': 'idea', 'tags': ['kickoff-pending']}]
    buckets, _ = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-bbc2']
    assert buckets['Designs'] == []


def test_new_project_tags_listed_in_constant(bridget):
    # Defensive: lock in that the two new lifecycle tags are declared.
    assert 'handed-off-to-mayor' in bridget._PROJECT_TAGS
    assert 'staged' in bridget._PROJECT_TAGS


# -- Part B: awaiting-approval children sub-list ---------------------------

def test_one_awaiting_approval_child_renders_singular(bridget, monkeypatch):
    items = [
        {'id': 'mg-79e8', 'type': 'idea', 'status': 'claimed',
         'title': 'DO migration', 'tags': ['in-progress']},
        {'id': 'mg-73e0', 'type': 'idea', 'status': 'claimed',
         'title': 'provision LLM host on DO',
         'tags': ['parent-project:mg-79e8', 'awaiting-approval']},
        {'id': 'mg-aaaa', 'type': 'task', 'status': 'claimed',
         'title': 'in-progress polecat',
         'tags': ['parent-project:mg-79e8']},
        {'id': 'mg-bbbb', 'type': 'task', 'status': 'pending',
         'title': 'another child',
         'tags': ['parent-project:mg-79e8']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    # Aggregate count line still present and includes all 3 children.
    assert '  └ 3 children:' in summary
    # Flagged sub-list appears, singular "child".
    assert '  ⚠ 1 child awaiting your approval:' in summary
    assert '    [mg-73e0] awaiting-approval: provision LLM host on DO' in summary


def test_no_awaiting_approval_children_no_flag_line(bridget, monkeypatch):
    items = [
        {'id': 'mg-79e8', 'type': 'idea', 'status': 'claimed',
         'title': 'project', 'tags': ['in-progress']},
        {'id': 'mg-c001', 'type': 'idea', 'status': 'available',
         'title': 'plain child', 'tags': ['parent-project:mg-79e8']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '  └ 1 children:' in summary
    assert '⚠' not in summary
    assert 'awaiting your approval' not in summary


def test_two_awaiting_approval_children_renders_plural(bridget, monkeypatch):
    items = [
        {'id': 'mg-79e8', 'type': 'idea', 'status': 'claimed',
         'title': 'project', 'tags': ['in-progress']},
        {'id': 'mg-c001', 'type': 'idea', 'status': 'claimed',
         'title': 'first awaiting child',
         'tags': ['parent-project:mg-79e8', 'awaiting-approval']},
        {'id': 'mg-c002', 'type': 'idea', 'status': 'claimed',
         'title': 'second awaiting child',
         'tags': ['parent-project:mg-79e8', 'awaiting-approval']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '  ⚠ 2 children awaiting your approval:' in summary
    assert '    [mg-c001] awaiting-approval: first awaiting child' in summary
    assert '    [mg-c002] awaiting-approval: second awaiting child' in summary


def test_awaiting_children_still_in_aggregate_count(bridget, monkeypatch):
    # Comprehensive view: children with awaiting-approval tag stay in the
    # aggregate count, and ALSO appear in the drill-down. They are NOT
    # excluded from the count.
    items = [
        {'id': 'mg-79e8', 'type': 'idea', 'status': 'claimed',
         'title': 'project', 'tags': ['in-progress']},
        {'id': 'mg-c001', 'type': 'idea', 'status': 'claimed',
         'title': 'awaiting child',
         'tags': ['parent-project:mg-79e8', 'awaiting-approval']},
        {'id': 'mg-c002', 'type': 'idea', 'status': 'pending',
         'title': 'plain child', 'tags': ['parent-project:mg-79e8']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '  └ 2 children:' in summary
    assert '  ⚠ 1 child awaiting your approval:' in summary


def test_long_child_title_truncated_in_flag_line(bridget, monkeypatch):
    long_title = 'x' * 200
    items = [
        {'id': 'mg-79e8', 'type': 'idea', 'status': 'claimed',
         'title': 'project', 'tags': ['in-progress']},
        {'id': 'mg-c001', 'type': 'idea', 'status': 'claimed',
         'title': long_title,
         'tags': ['parent-project:mg-79e8', 'awaiting-approval']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    expected = '    [mg-c001] awaiting-approval: ' + 'x' * 80
    assert expected in summary
    assert 'x' * 81 not in summary


def test_mg_fea0_style_handoff_item_buckets_into_projects(bridget, monkeypatch):
    # The motivating case: a Type=idea with handed-off-to-mayor + staged
    # tags (no canonical Project tag, no parent-project) renders under
    # Projects, not Designs.
    items = [
        {'id': 'mg-fea0', 'type': 'idea', 'status': 'claimed',
         'title': 'hand-off-stage item',
         'tags': ['handed-off-to-mayor', 'staged']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '**Projects**' in summary
    assert '**Designs**' not in summary
    assert '[mg-fea0] claimed: hand-off-stage item' in summary


def test_kickoff_pending_idea_still_buckets_into_projects(bridget, monkeypatch):
    # Regression: ordinary Project tag routing unaffected by the new entries.
    items = [
        {'id': 'mg-bbc2', 'type': 'idea', 'status': 'available',
         'title': 'fresh project', 'tags': ['kickoff-pending']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '**Projects**' in summary
    assert '[mg-bbc2] available: fresh project' in summary
    assert '**Designs**' not in summary


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
