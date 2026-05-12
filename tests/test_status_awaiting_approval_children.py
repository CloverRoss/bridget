"""Tests for the Status view's handling of approved Reports + the
_PROJECT_TAGS lifecycle-tag extension (mg-1ef2, revised 2026-05-12).

Part A of mg-1ef2 extended _PROJECT_TAGS with `handed-off-to-mayor`
and `staged` so director/mayor hand-off-stage items bucket as Projects
instead of falling through to Designs. mg-1ef2-revise keeps that
extension intact.

The original Part B (awaiting-approval children sub-list) has been
reverted by mg-1ef2-revise — see test_status_children_collapse.py for
the flat-by-type behavior that replaced it. This file now covers the
replacement Part B: Type=report items tagged `approved` are filtered
out of the Reports bucket because they have moved on to Project form.
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


# -- Part A: _PROJECT_TAGS extension (retained from mg-1ef2) ---------------

def test_handed_off_to_mayor_buckets_into_projects(bridget):
    items = [{'id': 'mg-fea0', 'type': 'idea',
              'tags': ['handed-off-to-mayor', 'staged']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-fea0']
    assert buckets['Designs'] == []


def test_staged_alone_buckets_into_projects(bridget):
    items = [{'id': 'mg-aaaa', 'type': 'idea', 'tags': ['staged']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-aaaa']
    assert buckets['Designs'] == []


def test_handed_off_alone_buckets_into_projects(bridget):
    items = [{'id': 'mg-bbbb', 'type': 'idea', 'tags': ['handed-off-to-mayor']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-bbbb']
    assert buckets['Designs'] == []


def test_ordinary_kickoff_pending_still_buckets_into_projects(bridget):
    # Regression: existing canonical Project tag still routes correctly.
    items = [{'id': 'mg-bbc2', 'type': 'idea', 'tags': ['kickoff-pending']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-bbc2']
    assert buckets['Designs'] == []


def test_new_project_tags_listed_in_constant(bridget):
    assert 'handed-off-to-mayor' in bridget._PROJECT_TAGS
    assert 'staged' in bridget._PROJECT_TAGS


def test_mg_fea0_style_handoff_item_renders_under_projects(
        bridget, monkeypatch):
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


def test_kickoff_pending_idea_renders_under_projects(bridget, monkeypatch):
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


def test_ordinary_idea_without_project_tag_stays_in_designs(bridget):
    # Regression on mg-1ef2-revise rollback: plain Type=idea with no
    # Project-status tags and no parent-project: tag stays in Designs.
    items = [{'id': 'mg-dddd', 'type': 'idea', 'tags': ['bridget']}]
    buckets = bridget.categorize_in_flight(items)
    assert buckets['Projects'] == []
    assert [i['id'] for i in buckets['Designs']] == ['mg-dddd']


# -- Part B (revised): approved Reports filtered from Reports bucket -------

def test_approved_report_filtered_out_of_reports(bridget):
    items = [
        {'id': 'dr-a1', 'type': 'report', 'tags': ['approved'],
         'title': 'scheduled report', 'status': 'claimed'},
    ]
    buckets = bridget.categorize_in_flight(items)
    assert buckets['Reports'] == []
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_awaiting_approval_report_stays_in_reports(bridget):
    items = [
        {'id': 'dr-a2', 'type': 'report', 'tags': ['awaiting-approval'],
         'title': 'pending report', 'status': 'available'},
    ]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Reports']] == ['dr-a2']


def test_untagged_report_stays_in_reports(bridget):
    items = [
        {'id': 'dr-a3', 'type': 'report', 'title': 'fresh report',
         'status': 'available'},
    ]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Reports']] == ['dr-a3']


def test_rejected_report_stays_in_reports(bridget):
    items = [
        {'id': 'dr-a4', 'type': 'report', 'tags': ['rejected'],
         'title': 'rejected report', 'status': 'available'},
    ]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Reports']] == ['dr-a4']


def test_approved_report_omitted_from_status_summary(bridget, monkeypatch):
    items = [
        {'id': 'dr-a1', 'type': 'report', 'status': 'claimed',
         'title': 'scheduled report', 'tags': ['approved']},
        {'id': 'dr-a2', 'type': 'report', 'status': 'available',
         'title': 'pending report', 'tags': ['awaiting-approval']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '**Reports**' in summary
    assert 'dr-a2' in summary
    assert 'dr-a1' not in summary
    assert 'scheduled report' not in summary


def test_approved_tag_on_non_report_does_not_filter(bridget):
    # Approved-filter is scoped to Type=report — an idea/task tagged
    # `approved` for unrelated reasons must still route by its type.
    items = [
        {'id': 'mg-zzzz', 'type': 'idea', 'tags': ['approved'],
         'title': 'approved idea', 'status': 'available'},
    ]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Designs']] == ['mg-zzzz']


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
