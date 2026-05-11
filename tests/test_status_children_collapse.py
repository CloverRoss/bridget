"""Tests for the Status view collapsing Project children under their
parent Project entry (mg-313f).

When mayor kicks off a Project, it files child design/task items
tagged `parent-project:mg-XXXX`. Those children must NOT render as
separate Designs/Tasks/Bugs rows — they collapse under the parent
Project entry as a single count + status breakdown line.
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


# -- categorize_in_flight: parent-project tag handling ---------------------

def test_child_idea_filtered_out_of_designs(bridget):
    items = [
        {'id': 'mg-bbc2', 'type': 'idea', 'tags': ['in-progress'],
         'title': 'parent', 'status': 'claimed'},
        {'id': 'mg-c001', 'type': 'idea', 'tags': ['parent-project:mg-bbc2'],
         'title': 'child design', 'status': 'available'},
    ]
    buckets, children = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-bbc2']
    assert buckets['Designs'] == []
    assert [i['id'] for i in children['mg-bbc2']] == ['mg-c001']


def test_child_task_filtered_out_of_tasks(bridget):
    items = [
        {'id': 'mg-t001', 'type': 'task', 'tags': ['parent-project:mg-bbc2'],
         'title': 'child task', 'status': 'claimed'},
    ]
    buckets, children = bridget.categorize_in_flight(items)
    assert buckets['Tasks'] == []
    assert [i['id'] for i in children['mg-bbc2']] == ['mg-t001']


def test_child_bug_filtered_out_of_bugs(bridget):
    items = [
        {'id': 'mg-b001', 'type': 'bug', 'tags': ['parent-project:mg-bbc2'],
         'title': 'child bug', 'status': 'available'},
    ]
    buckets, children = bridget.categorize_in_flight(items)
    assert buckets['Bugs'] == []
    assert [i['id'] for i in children['mg-bbc2']] == ['mg-b001']


def test_orphan_child_still_filtered_when_parent_absent(bridget):
    # If the parent Project isn't in the in-flight list (e.g., already done),
    # the child is still filtered from Designs/Tasks. The parent reference
    # is authoritative — orphan children just don't render anywhere v1.
    items = [
        {'id': 'mg-c001', 'type': 'idea', 'tags': ['parent-project:mg-ace1'],
         'title': 'orphan', 'status': 'available'},
    ]
    buckets, children = bridget.categorize_in_flight(items)
    assert buckets['Designs'] == []
    assert buckets['Projects'] == []
    assert [i['id'] for i in children['mg-ace1']] == ['mg-c001']


def test_multiple_children_grouped_under_same_parent(bridget):
    items = [
        {'id': 'mg-c001', 'type': 'idea', 'tags': ['parent-project:mg-bbc2'],
         'status': 'available'},
        {'id': 'mg-c002', 'type': 'idea', 'tags': ['parent-project:mg-bbc2'],
         'status': 'available'},
        {'id': 'mg-c003', 'type': 'task', 'tags': ['parent-project:mg-bbc2'],
         'status': 'claimed'},
    ]
    _, children = bridget.categorize_in_flight(items)
    assert [i['id'] for i in children['mg-bbc2']] == [
        'mg-c001', 'mg-c002', 'mg-c003',
    ]


def test_ordinary_design_unaffected(bridget):
    # No parent-project: tag → ordinary Design, appears in Designs as before.
    items = [
        {'id': 'mg-dddd', 'type': 'idea', 'tags': ['bridget'],
         'title': 'ordinary design', 'status': 'available'},
    ]
    buckets, children = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Designs']] == ['mg-dddd']
    assert children == {}


# -- _summarize_children: breakdown formatting -----------------------------

def test_summarize_children_single_status(bridget):
    items = [
        {'id': 'mg-c001', 'type': 'idea', 'status': 'available'},
        {'id': 'mg-c002', 'type': 'idea', 'status': 'available'},
    ]
    assert bridget._summarize_children(items) == '2 available'


def test_summarize_children_mixed_statuses(bridget):
    items = [
        {'id': 'mg-c001', 'type': 'idea', 'status': 'awaiting-approval'},
        {'id': 'mg-c002', 'type': 'idea', 'status': 'awaiting-approval'},
        {'id': 'mg-c003', 'type': 'task', 'status': 'in-progress'},
    ]
    assert bridget._summarize_children(items) == (
        '2 awaiting-approval, 1 in-progress polecat'
    )


def test_summarize_children_marks_tasks_as_polecat(bridget):
    items = [
        {'id': 'mg-t001', 'type': 'task', 'status': 'claimed'},
    ]
    assert bridget._summarize_children(items) == '1 claimed polecat'


def test_summarize_children_handles_missing_status(bridget):
    items = [{'id': 'mg-c001', 'type': 'idea'}]
    assert bridget._summarize_children(items) == '1 ?'


# -- get_status_summary: rendered child-collapse line ----------------------

def test_status_summary_renders_child_collapse_line(bridget, monkeypatch):
    # `available`/`claimed`/`pending` are the in-flight statuses
    # get_status_summary actually surfaces. Children with other statuses
    # are filtered out upstream and never reach the breakdown.
    items = [
        {'id': 'mg-bbc2', 'type': 'idea', 'status': 'claimed',
         'title': 'parent project', 'tags': ['in-progress']},
        {'id': 'mg-c001', 'type': 'idea', 'status': 'pending',
         'title': 'first child design', 'tags': ['parent-project:mg-bbc2']},
        {'id': 'mg-c002', 'type': 'idea', 'status': 'pending',
         'title': 'second child design', 'tags': ['parent-project:mg-bbc2']},
        {'id': 'mg-t001', 'type': 'task', 'status': 'claimed',
         'title': 'child task', 'tags': ['parent-project:mg-bbc2']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '[mg-bbc2] claimed: parent project' in summary
    assert (
        '  └ 3 children: 2 pending, 1 claimed polecat'
        in summary
    )
    # Children must NOT appear in their natural buckets.
    assert 'mg-c001' not in summary
    assert 'mg-c002' not in summary
    assert 'mg-t001' not in summary
    assert '**Designs**' not in summary
    assert '**Tasks**' not in summary


def test_status_summary_no_collapse_line_when_no_children(bridget, monkeypatch):
    items = [
        {'id': 'mg-bbc2', 'type': 'idea', 'status': 'claimed',
         'title': 'lonely project', 'tags': ['in-progress']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '[mg-bbc2] claimed: lonely project' in summary
    assert '  └' not in summary
    assert 'children:' not in summary


def test_status_summary_orphan_child_does_not_appear(bridget, monkeypatch):
    # Child whose parent isn't in in-flight list: filtered from
    # Designs/Tasks per the design (parent reference is authoritative).
    # No collapse line surfaces either — there's no parent Project row to
    # hang it on.
    items = [
        {'id': 'mg-c001', 'type': 'idea', 'status': 'available',
         'title': 'orphan child', 'tags': ['parent-project:mg-ace1']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert 'mg-c001' not in summary
    assert 'mg-ace1' not in summary
    assert summary == 'No work in flight.'


def test_status_summary_ordinary_design_renders_in_designs(
        bridget, monkeypatch):
    # Designs without parent-project: tag are unaffected.
    items = [
        {'id': 'mg-dddd', 'type': 'idea', 'status': 'available',
         'title': 'plain design'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '**Designs**' in summary
    assert '[mg-dddd] available: plain design' in summary
    assert '  └' not in summary


def test_status_summary_collapse_line_only_under_projects_section(
        bridget, monkeypatch):
    # Sanity: the collapse line is rendered immediately below the parent
    # Project entry, inside the Projects section — not under unrelated
    # rows in Designs/Bugs/Tasks.
    items = [
        {'id': 'mg-bbc2', 'type': 'idea', 'status': 'claimed',
         'title': 'parent', 'tags': ['in-progress']},
        {'id': 'mg-dddd', 'type': 'idea', 'status': 'available',
         'title': 'unrelated design'},
        {'id': 'mg-c001', 'type': 'idea', 'status': 'available',
         'title': 'child', 'tags': ['parent-project:mg-bbc2']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    project_idx = summary.index('[mg-bbc2]')
    collapse_idx = summary.index('  └ 1 children')
    designs_idx = summary.index('**Designs**')
    assert project_idx < collapse_idx < designs_idx


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
