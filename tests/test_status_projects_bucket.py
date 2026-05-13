"""Tests for the Status view's dedicated Projects bucket (mg-946e, mg-b824,
mg-4955, mg-2e39, mg-e68c).

Per mg-1d2b Phase 5, Projects are first-class `--type=project` mg items.
Per the mg-e68c tag-taxonomy migration, only the canonical `in-progress`
lifecycle tag promotes a Type=project into the 'Projects' bucket; all
other states (`scheduled`, `ready-for-kickoff`, `done`, `cancelled`, or
no canonical tag) are hidden — they live on the Product Roadmap, not the
status view.

During the transition window, Type=idea items still carrying legacy
Project-lifecycle tags (`scheduled`, `in-progress`, `kickoff-done`, etc.)
are suppressed entirely — they must NOT appear in Designs. Once retyped
to Type=project they bucket normally. Type=idea items with no Project-
lifecycle tags continue to flow into Designs.
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


# -- categorize_in_flight: Type=project routing ----------------------------

def test_project_with_in_progress_buckets_into_projects(bridget):
    items = [{'id': 'mg-aaaa', 'type': 'project', 'tags': ['in-progress']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-aaaa']
    assert buckets['Designs'] == []


def test_project_with_scheduled_is_hidden(bridget):
    items = [{'id': 'mg-fea0', 'type': 'project', 'tags': ['scheduled']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_project_with_ready_for_kickoff_is_hidden(bridget):
    items = [{'id': 'mg-5555', 'type': 'project',
              'tags': ['ready-for-kickoff']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_project_with_done_is_hidden(bridget):
    items = [{'id': 'mg-1111', 'type': 'project', 'tags': ['done']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_project_with_cancelled_is_hidden(bridget):
    items = [{'id': 'mg-2222', 'type': 'project', 'tags': ['cancelled']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_project_with_no_canonical_lifecycle_tag_is_hidden(bridget):
    # Post mg-e68c: legacy `kickoff-done` is no longer a running signal;
    # without canonical `in-progress`, the Project is hidden.
    items = [{'id': 'mg-79e8', 'type': 'project', 'tags': ['kickoff-done']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_project_with_no_tags_is_hidden(bridget):
    # Type=project with no in-progress tag → hidden (lives on Roadmap).
    items = [{'id': 'mg-3333', 'type': 'project'}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_project_with_in_progress_and_other_tags_buckets_into_projects(bridget):
    # Domain/scope tags alongside the canonical lifecycle tag do not
    # affect bucketing — only `in-progress` matters.
    items = [{'id': 'mg-79e8', 'type': 'project',
              'tags': ['bridget', 'infra', 'high-priority', 'in-progress']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-79e8']
    assert buckets['Designs'] == []


# -- categorize_in_flight: legacy Type=idea suppression --------------------

def test_legacy_idea_with_in_progress_is_hidden(bridget):
    # mg-2e39: Type=idea items still carrying legacy Project-lifecycle
    # tags are suppressed during the transition window — they must NOT
    # appear in Designs (regression of mg-4955 suppression).
    items = [{'id': 'mg-4075', 'type': 'idea', 'tags': ['in-progress']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_legacy_idea_with_kickoff_done_is_hidden(bridget):
    items = [{'id': 'mg-79e8', 'type': 'idea', 'tags': ['kickoff-done']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_legacy_idea_with_scheduled_is_hidden(bridget):
    items = [{'id': 'mg-fea0', 'type': 'idea', 'tags': ['scheduled']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_legacy_idea_with_handed_off_to_mayor_is_hidden(bridget):
    items = [{'id': 'mg-fea0', 'type': 'idea',
              'tags': ['handed-off-to-mayor']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_legacy_idea_with_staged_is_hidden(bridget):
    items = [{'id': 'mg-3333', 'type': 'idea', 'tags': ['staged']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


def test_legacy_idea_with_mixed_handoff_state_is_hidden(bridget):
    # Realistic mg-fea0 shape: post-design, pre-kickoff Project carrying
    # multiple lifecycle tags but no in-progress signal.
    items = [{'id': 'mg-fea0', 'type': 'idea',
              'tags': ['handed-off-to-mayor', 'staged', 'roadmap-drafted']}]
    buckets = bridget.categorize_in_flight(items)
    assert all(buckets[s] == [] for s in bridget.STATUS_SECTION_ORDER)


# -- categorize_in_flight: ordinary Type=idea routes to Designs ------------

def test_idea_with_only_parent_project_tag_stays_in_designs(bridget):
    # Children with parent-project: tag flow into their natural bucket
    # per type. Type=idea → Designs. (parent-project: is not in
    # _PROJECT_TAGS so no suppression applies.)
    items = [{'id': 'mg-cccc', 'type': 'idea',
              'tags': ['parent-project:mg-bbc2']}]
    buckets = bridget.categorize_in_flight(items)
    assert buckets['Projects'] == []
    assert [i['id'] for i in buckets['Designs']] == ['mg-cccc']


def test_idea_with_no_project_tags_stays_in_designs(bridget):
    items = [{'id': 'mg-dddd', 'type': 'idea', 'tags': ['bridget']}]
    buckets = bridget.categorize_in_flight(items)
    assert buckets['Projects'] == []
    assert [i['id'] for i in buckets['Designs']] == ['mg-dddd']


def test_idea_with_no_tags_at_all_stays_in_designs(bridget):
    # Regression: tags field omitted entirely.
    items = [{'id': 'mg-eeee', 'type': 'idea'}]
    buckets = bridget.categorize_in_flight(items)
    assert buckets['Projects'] == []
    assert [i['id'] for i in buckets['Designs']] == ['mg-eeee']


# -- categorize_in_flight: non-idea/project types unaffected ---------------

def test_task_with_in_progress_tag_stays_in_tasks(bridget):
    # Suppression check only fires for type=idea/project — task types
    # route by their natural bucket regardless of tags.
    items = [{'id': 'mg-ffff', 'type': 'task', 'tags': ['in-progress']}]
    buckets = bridget.categorize_in_flight(items)
    assert buckets['Projects'] == []
    assert [i['id'] for i in buckets['Tasks']] == ['mg-ffff']


def test_bug_with_in_progress_tag_stays_in_bugs(bridget):
    items = [{'id': 'mg-gggg', 'type': 'bug', 'tags': ['in-progress']}]
    buckets = bridget.categorize_in_flight(items)
    assert buckets['Projects'] == []
    assert [i['id'] for i in buckets['Bugs']] == ['mg-gggg']


def test_task_no_tags_stays_in_tasks(bridget):
    items = [{'id': 'mg-tttt', 'type': 'task'}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Tasks']] == ['mg-tttt']


def test_bug_no_tags_stays_in_bugs(bridget):
    items = [{'id': 'mg-uuuu', 'type': 'bug'}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Bugs']] == ['mg-uuuu']


# -- categorize_in_flight: Type=report routing -----------------------------

def test_report_with_awaiting_approval_buckets_into_reports(bridget):
    items = [{'id': 'dr-a2', 'type': 'report',
              'tags': ['awaiting-approval'], 'status': 'available'}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Reports']] == ['dr-a2']


def test_report_with_approved_is_hidden(bridget):
    # Regression: approved Reports stay filtered out — the Project entry
    # takes over visibility.
    items = [{'id': 'dr-a1', 'type': 'report', 'tags': ['approved'],
              'status': 'claimed'}]
    buckets = bridget.categorize_in_flight(items)
    assert buckets['Reports'] == []


# -- STATUS_SECTION_ORDER --------------------------------------------------

def test_section_order_lists_projects_before_designs(bridget):
    order = bridget.STATUS_SECTION_ORDER
    assert 'Projects' in order
    assert order.index('Projects') < order.index('Designs')


def test_section_order_starts_with_projects(bridget):
    assert bridget.STATUS_SECTION_ORDER[0] == 'Projects'


# -- get_status_summary: rendered output -----------------------------------

def test_status_summary_renders_projects_section_before_designs(
        bridget, monkeypatch):
    items = [
        {'id': 'mg-79e8', 'type': 'project', 'status': 'claimed',
         'title': 'a project', 'tags': ['in-progress']},
        {'id': 'mg-dddd', 'type': 'idea', 'status': 'available',
         'title': 'a design'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '**Projects**' in summary
    assert '**Designs**' in summary
    assert summary.index('**Projects**') < summary.index('**Designs**')
    assert '[mg-79e8] claimed: a project' in summary
    assert '[mg-dddd] available: a design' in summary


def test_status_summary_omits_empty_projects_section(
        bridget, monkeypatch):
    items = [
        {'id': 'mg-dddd', 'type': 'idea', 'status': 'available',
         'title': 'a design'},
    ]
    monkeypatch.setattr(bridget, 'run_mg',
                        lambda args: (0, _ndjson(items), ''))
    summary = bridget.get_status_summary()
    assert '**Projects**' not in summary
    assert '**Designs**' in summary


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
