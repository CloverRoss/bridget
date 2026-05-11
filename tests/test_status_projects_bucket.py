"""Tests for the Status view's dedicated Projects bucket (mg-946e).

Project-roots are Type=idea items carrying a Project-status tag (e.g.
`kickoff-pending`, `in-progress`). They get their own bucket rendered
before Designs so they don't get drowned by ordinary architect designs.
Children of a Project (tagged only with `parent-project:mg-XXXX`) stay
in Designs/Tasks per their type.
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


# -- categorize_in_flight: Projects bucket ---------------------------------

def test_idea_with_kickoff_pending_buckets_into_projects(bridget):
    items = [{'id': 'mg-bbc2', 'type': 'idea', 'tags': ['kickoff-pending']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-bbc2']
    assert buckets['Designs'] == []


def test_idea_with_in_progress_buckets_into_projects(bridget):
    items = [{'id': 'mg-aaaa', 'type': 'idea', 'tags': ['in-progress']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-aaaa']
    assert buckets['Designs'] == []


def test_idea_with_only_parent_project_tag_stays_in_designs(bridget):
    # Children of a Project carry parent-project:mg-XXXX but no
    # Project-status tag — they're work units, not the Project itself.
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


def test_task_with_project_tag_stays_in_tasks(bridget):
    # Tag check only fires for type=idea — non-idea types are unaffected.
    items = [{'id': 'mg-ffff', 'type': 'task', 'tags': ['in-progress']}]
    buckets = bridget.categorize_in_flight(items)
    assert buckets['Projects'] == []
    assert [i['id'] for i in buckets['Tasks']] == ['mg-ffff']


def test_bug_with_project_tag_stays_in_bugs(bridget):
    items = [{'id': 'mg-gggg', 'type': 'bug', 'tags': ['kickoff-pending']}]
    buckets = bridget.categorize_in_flight(items)
    assert buckets['Projects'] == []
    assert [i['id'] for i in buckets['Bugs']] == ['mg-gggg']


def test_idea_with_mixed_tags_picks_projects(bridget):
    # Project-status tag wins even when other tags are present.
    items = [{'id': 'mg-hhhh', 'type': 'idea',
              'tags': ['bridget', 'scheduled', 'parent-project:mg-bbc2']}]
    buckets = bridget.categorize_in_flight(items)
    assert [i['id'] for i in buckets['Projects']] == ['mg-hhhh']


def test_all_project_status_tags_route_to_projects(bridget):
    # Every tag in _PROJECT_TAGS should bucket a Type=idea into Projects.
    for tag in bridget._PROJECT_TAGS:
        items = [{'id': f'mg-{tag}', 'type': 'idea', 'tags': [tag]}]
        buckets = bridget.categorize_in_flight(items)
        assert [i['id'] for i in buckets['Projects']] == [f'mg-{tag}'], (
            f"tag {tag!r} did not route to Projects"
        )


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
        {'id': 'mg-bbc2', 'type': 'idea', 'status': 'claimed',
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
    assert '[mg-bbc2] claimed: a project' in summary
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
