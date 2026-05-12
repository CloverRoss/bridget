"""Tests for the Status view's handling of Project children after the
mg-1ef2-revise rollback.

mg-44fe (and the original mg-313f attempt) collapsed children carrying
`parent-project:mg-XXXX` under their parent Project entry as an
aggregate count + ⚠ awaiting-approval sub-list. mg-1ef2-revise reverts
that: children now flow into their natural type buckets (Designs /
Tasks / Bugs) like any other item, and the Project entry stays alone
in Projects.

The file name is retained for git history; contents assert the new
flat-by-type behavior.
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


# -- categorize_in_flight: parent-project tag is no longer special ---------

def test_child_idea_routes_to_designs(bridget):
    items = [
        {'id': 'mg-bbc2', 'type': 'idea', 'tags': ['in-progress'],
         'title': 'parent', 'status': 'claimed'},
        {'id': 'mg-c001', 'type': 'idea', 'tags': ['parent-project:mg-bbc2'],
         'title': 'child design', 'status': 'available'},
    ]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-bbc2']
    assert [i['id'] for i in buckets['Designs']] == ['mg-c001']


def test_child_task_routes_to_tasks(bridget):
    items = [
        {'id': 'mg-t001', 'type': 'task', 'tags': ['parent-project:mg-bbc2'],
         'title': 'child task', 'status': 'claimed'},
    ]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Tasks']] == ['mg-t001']


def test_child_bug_routes_to_bugs(bridget):
    items = [
        {'id': 'mg-b001', 'type': 'bug', 'tags': ['parent-project:mg-bbc2'],
         'title': 'child bug', 'status': 'available'},
    ]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Bugs']] == ['mg-b001']


def test_orphan_child_still_routes_by_type(bridget):
    # No special handling: the parent-project: tag is informational only.
    items = [
        {'id': 'mg-c001', 'type': 'idea', 'tags': ['parent-project:mg-ace1'],
         'title': 'orphan', 'status': 'available'},
    ]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Designs']] == ['mg-c001']
    assert buckets['Projects'] == []


def test_ordinary_design_unaffected(bridget):
    items = [
        {'id': 'mg-dddd', 'type': 'idea', 'tags': ['bridget'],
         'title': 'ordinary design', 'status': 'available'},
    ]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Designs']] == ['mg-dddd']


# -- get_status_summary: no collapse line, no ⚠ block ----------------------

def test_status_summary_renders_children_in_natural_buckets(
        bridget, monkeypatch):
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
    # Parent appears in Projects.
    assert '**Projects**' in summary
    assert '[mg-bbc2] claimed: parent project' in summary
    # Children appear in Designs / Tasks per their type.
    assert '**Designs**' in summary
    assert '[mg-c001] pending: first child design' in summary
    assert '[mg-c002] pending: second child design' in summary
    assert '**Tasks**' in summary
    assert '[mg-t001] claimed: child task' in summary
    # No collapse line, no ⚠ block.
    assert '  └' not in summary
    assert '⚠' not in summary
    assert 'awaiting your approval' not in summary
    assert 'children:' not in summary


def test_status_summary_no_artifacts_for_project_with_no_children(
        bridget, monkeypatch):
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


def test_status_summary_orphan_child_renders_in_designs(bridget, monkeypatch):
    # Parent absent from the in-flight list — child still surfaces in
    # its natural bucket (no parent reference is needed).
    items = [
        {'id': 'mg-c001', 'type': 'idea', 'status': 'available',
         'title': 'orphan child', 'tags': ['parent-project:mg-ace1']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '**Designs**' in summary
    assert '[mg-c001] available: orphan child' in summary


def test_status_summary_ordinary_design_renders_in_designs(
        bridget, monkeypatch):
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


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
