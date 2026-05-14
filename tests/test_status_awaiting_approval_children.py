"""Tests for the Status view's handling of approved Reports + the
Projects-bucket narrowing (mg-1ef2 revised; mg-b824; mg-4955; mg-2e39;
mg-e68c; mg-f059).

mg-1ef2 originally extended a _PROJECT_TAGS set so a wider range of
lifecycle tags routed Type=idea items into Projects. mg-b824 narrowed
that to only the `in-progress` tag. mg-4955 then split the taxonomy
into _PROJECT_TAGS (any Project-lifecycle tag) and _IN_PROGRESS_TAGS
(`in-progress` plus mayor's `kickoff-done`). mg-2e39 promoted Projects
to a first-class mg `--type=project`. mg-e68c then standardized on the
canonical 5-state lifecycle vocab and dropped the legacy fallback.
mg-f059 re-admitted the legacy mayor-flow tags `kickoff-done` and
`handed-off-to-mayor` as in-flight signals during the ds-a57b lifecycle
migration window — these will collapse back to canonical `in-progress`
only once all in-flight projects retag. Tags that signal "not in
flight" (`scheduled`, `ready-for-kickoff`, `kickoff-pending`, `staged`,
`done`, `cancelled`) continue to hide the Project — those live on the
Product Roadmap. Type=idea items still carrying legacy lifecycle tags
(transition window) remain suppressed from Designs. Part A below
verifies the type-based routing.

The original Part B (awaiting-approval children sub-list) has been
reverted by mg-1ef2-revise — see test_status_children_collapse.py for
the flat-by-type behavior that replaced it. Part B here covers the
replacement: Type=report items tagged `approved` are filtered out of
the Reports bucket because they have moved on to Project form.
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


# -- Part A: Projects bucket reads Type=project + in-progress (mg-2e39) ----

def test_project_with_handed_off_to_mayor_buckets_into_projects(bridget):
    # mg-f059: legacy mayor-flow `handed-off-to-mayor` is in-flight
    # during the ds-a57b migration window, so the Project shows under
    # Projects even alongside a non-flight tag like `staged`.
    items = [{'id': 'mg-fea0', 'type': 'project',
              'tags': ['handed-off-to-mayor', 'staged']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-fea0']


def test_project_with_staged_alone_is_hidden(bridget):
    items = [{'id': 'mg-aaaa', 'type': 'project', 'tags': ['staged']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_project_with_handed_off_alone_buckets_into_projects(bridget):
    # mg-f059: `handed-off-to-mayor` is an in-flight signal.
    items = [{'id': 'mg-bbbb', 'type': 'project',
              'tags': ['handed-off-to-mayor']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-bbbb']


def test_project_with_kickoff_pending_is_hidden(bridget):
    items = [{'id': 'mg-bbc2', 'type': 'project',
              'tags': ['kickoff-pending']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_in_progress_project_buckets_into_projects(bridget):
    items = [{'id': 'mg-79e8', 'type': 'project', 'tags': ['in-progress']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-79e8']
    assert buckets['Designs'] == []


def test_kickoff_done_project_buckets_into_projects(bridget):
    # mg-f059: legacy mayor-flow `kickoff-done` is treated as in-flight
    # during the ds-a57b lifecycle migration window. Will collapse to
    # canonical 'in-progress'-only once all in-flight projects retag.
    items = [{'id': 'mg-79e8', 'type': 'project', 'tags': ['kickoff-done']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-79e8']


def test_mg_fea0_style_handoff_item_renders_under_projects(
        bridget, monkeypatch):
    # mg-f059: a real-world handed-off-to-mayor Project is in flight
    # and renders under Projects in /status output.
    items = [
        {'id': 'mg-fea0', 'type': 'project', 'status': 'claimed',
         'title': 'hand-off-stage item',
         'tags': ['handed-off-to-mayor', 'staged']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '**Projects**' in summary
    assert '[mg-fea0] claimed: hand-off-stage item' in summary


def test_in_progress_project_renders_under_projects(bridget, monkeypatch):
    items = [
        {'id': 'mg-79e8', 'type': 'project', 'status': 'claimed',
         'title': 'live project', 'tags': ['in-progress']},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '**Projects**' in summary
    assert '[mg-79e8] claimed: live project' in summary
    assert '**Designs**' not in summary


def test_ordinary_idea_without_project_tag_stays_in_designs(bridget):
    # Regression: plain Type=idea with no Project-lifecycle tags and no
    # parent-project: tag stays in Designs.
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
