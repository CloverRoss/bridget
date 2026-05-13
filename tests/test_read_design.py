"""Tests for design-doc helpers + `read`'s remaining surfaces.

Covers:
- read_design_doc / read_design_status frontmatter handling (helpers reused
  by both `read dr-XXXX` and `open mg-XXXX`).
- read_design IO error reporting + iCloud-placeholder materialization
  (mg-b5e5).
- chunk_for_discord boundary splitting + truncation footer.
- handle_command's `read` branch after mg-d3d7 tightening:
  - `read mg-XXXX` short-circuits to a hint pointing at `open`.
  - `read dr-XXXX` continues to surface the design + mail footer.
"""
import importlib.util
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
    mod = _load_bridget(tmp_path)
    designs = tmp_path / 'designs'
    designs.mkdir()
    monkeypatch.setattr(mod, 'DESIGNS_DIR', designs)
    return mod


# -- read_design_doc --------------------------------------------------------

def test_read_design_doc_strips_frontmatter(bridget):
    (bridget.DESIGNS_DIR / 'mg-aaaa.md').write_text(
        '---\nmg_id: mg-aaaa\nstatus: awaiting-approval\n---\n'
        '# Title\n\nBody line.\n'
    )
    body = bridget.read_design_doc('mg-aaaa')
    assert body is not None
    assert 'mg_id' not in body
    assert 'status: awaiting' not in body
    assert body.startswith('# Title')
    assert 'Body line.' in body


def test_read_design_doc_handles_no_frontmatter(bridget):
    (bridget.DESIGNS_DIR / 'mg-bbbb.md').write_text('# Plain\n\nNo frontmatter.\n')
    body = bridget.read_design_doc('mg-bbbb')
    assert body == '# Plain\n\nNo frontmatter.\n'


def test_read_design_doc_returns_none_when_missing(bridget):
    assert bridget.read_design_doc('mg-nope') is None


def test_read_design_status_reads_frontmatter(bridget):
    (bridget.DESIGNS_DIR / 'mg-cccc.md').write_text(
        '---\nmg_id: mg-cccc\nstatus: awaiting-approval\n---\nbody\n'
    )
    assert bridget.read_design_status('mg-cccc') == 'awaiting-approval'


def test_read_design_status_returns_none_without_frontmatter(bridget):
    (bridget.DESIGNS_DIR / 'mg-dddd.md').write_text('# plain\nbody\n')
    assert bridget.read_design_status('mg-dddd') is None


# -- read_design IO + iCloud handling (mg-b5e5) ----------------------------

def test_read_design_happy_path_returns_dict_and_no_log(bridget, capsys):
    (bridget.DESIGNS_DIR / 'mg-1111.md').write_text(
        '---\nstatus: awaiting-approval\ntitle: T\n---\n# Body\n'
    )
    design = bridget.read_design('mg-1111')
    assert design is not None
    assert design['status'] == 'awaiting-approval'
    err = capsys.readouterr().err
    assert 'read_design' not in err


def test_read_design_missing_file_logs_nothing_and_returns_none(bridget, capsys):
    # Missing file: path.exists() short-circuit returns None silently — the
    # diagnostic log is reserved for IO-error surprises, not the common case.
    assert bridget.read_design('mg-nope') is None
    err = capsys.readouterr().err
    assert 'read_design' not in err


def test_read_design_file_not_found_logs_exception_class(bridget, capsys, monkeypatch):
    # path.exists() returns True but read_text raises FileNotFoundError —
    # the iCloud-placeholder symptom we're hunting.
    path = bridget.DESIGNS_DIR / 'mg-2222.md'
    path.write_text('# placeholder shell\n')
    monkeypatch.setattr(
        bridget.Path,
        'read_text',
        lambda self, *a, **kw: (_ for _ in ()).throw(FileNotFoundError('ENOENT')),
    )
    assert bridget.read_design('mg-2222') is None
    err = capsys.readouterr().err
    assert 'read_design IO error for mg-2222' in err
    assert 'FileNotFoundError' in err


def test_read_design_permission_error_logs_exception_class(bridget, capsys, monkeypatch):
    path = bridget.DESIGNS_DIR / 'mg-3333.md'
    path.write_text('# x\n')
    monkeypatch.setattr(
        bridget.Path,
        'read_text',
        lambda self, *a, **kw: (_ for _ in ()).throw(PermissionError('EACCES')),
    )
    assert bridget.read_design('mg-3333') is None
    err = capsys.readouterr().err
    assert 'read_design IO error for mg-3333' in err
    assert 'PermissionError' in err


def test_read_design_unexpected_error_logs_under_unexpected_branch(bridget, capsys, monkeypatch):
    path = bridget.DESIGNS_DIR / 'mg-4444.md'
    path.write_text('# x\n')
    monkeypatch.setattr(
        bridget.Path,
        'read_text',
        lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError('weird')),
    )
    assert bridget.read_design('mg-4444') is None
    err = capsys.readouterr().err
    assert 'read_design unexpected error for mg-4444' in err
    assert 'RuntimeError' in err


def test_read_design_brctl_materializes_icloud_placeholder(bridget, capsys, monkeypatch):
    # path.exists() is False initially, then True after _materialize_icloud
    # runs. Simulates an iCloud placeholder dentry that CloudDocsd downloads
    # on demand.
    designs = bridget.DESIGNS_DIR
    target = designs / 'mg-5555.md'
    state = {'materialized': False}

    real_exists = bridget.Path.exists

    def fake_exists(self):
        if self == target and not state['materialized']:
            return False
        return real_exists(self)

    def fake_materialize(path):
        state['materialized'] = True
        target.write_text('---\nstatus: drafted\n---\n# ok\n')

    monkeypatch.setattr(bridget.Path, 'exists', fake_exists)
    monkeypatch.setattr(bridget, '_materialize_icloud', fake_materialize)

    design = bridget.read_design('mg-5555')
    assert design is not None
    assert design['status'] == 'drafted'


def test_read_design_brctl_giving_up_returns_none_silently(bridget, capsys, monkeypatch):
    # path.exists() stays False even after _materialize_icloud — caller
    # gives up without logging (the missing-file path is the silent one).
    designs = bridget.DESIGNS_DIR
    target = designs / 'mg-6666.md'
    calls = {'n': 0}

    real_exists = bridget.Path.exists

    def fake_exists(self):
        if self == target:
            return False
        return real_exists(self)

    def fake_materialize(path):
        calls['n'] += 1

    monkeypatch.setattr(bridget.Path, 'exists', fake_exists)
    monkeypatch.setattr(bridget, '_materialize_icloud', fake_materialize)

    assert bridget.read_design('mg-6666') is None
    assert calls['n'] == 1
    err = capsys.readouterr().err
    assert 'read_design' not in err


def test_materialize_icloud_no_op_when_brctl_missing(bridget, monkeypatch):
    # On a host without brctl (linux CI, stripped image) the helper is a
    # silent no-op — never spawns subprocess.
    monkeypatch.setattr(bridget.shutil, 'which', lambda _: None)

    def boom(*a, **kw):
        raise AssertionError('subprocess.run must not be called when brctl absent')

    monkeypatch.setattr(bridget.subprocess, 'run', boom)
    bridget._materialize_icloud(bridget.DESIGNS_DIR / 'mg-7777.md')


# -- chunk_for_discord ------------------------------------------------------

def test_chunk_for_discord_short_text_single_chunk(bridget):
    assert bridget.chunk_for_discord('short body') == ['short body']


def test_chunk_for_discord_splits_on_section_boundary(bridget):
    text = (
        '# Title\n\n'
        + ('A' * 1500)
        + '\n## Section 2\n\n'
        + ('B' * 1500)
    )
    chunks = bridget.chunk_for_discord(text, limit=1700, max_chunks=3)
    assert len(chunks) == 2
    assert chunks[1].startswith('## Section 2')
    assert all(len(c) <= 1700 for c in chunks)


def test_chunk_for_discord_truncation_footer_when_too_many_sections(bridget):
    text = '\n'.join(f'## Section {i}\n' + ('X' * 1500) for i in range(6))
    chunks = bridget.chunk_for_discord(text, limit=1700, max_chunks=3)
    assert len(chunks) == 3
    assert 'truncated' in chunks[-1]
    assert 'full doc at' in chunks[-1]


def test_chunk_for_discord_hard_splits_oversize_section(bridget):
    # A single 8000-char block with no section boundaries hard-splits into
    # ceil(8000/1700)=5 chunks; max_chunks=3 truncates with a footer.
    text = 'A' * 8000
    chunks = bridget.chunk_for_discord(text, limit=1700, max_chunks=3)
    assert len(chunks) == 3
    assert 'truncated' in chunks[-1]


# -- handle_command read branch --------------------------------------------
# After mg-d3d7 `read mg-XXXX` no longer surfaces the design — it returns
# a hint redirecting to `open`. The dr- prefix is unchanged.

def test_read_mg_id_returns_hint(bridget, monkeypatch):
    # Even with a design file + a referencing mail present, `read mg-XXXX`
    # must short-circuit to the hint without touching either: that was the
    # mg-93cb confusion this design fixes.
    (bridget.DESIGNS_DIR / 'mg-1234.md').write_text(
        '---\nstatus: awaiting-approval\n---\n# Title\nbody\n'
    )
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _id: [
        (
            Path('/tmp/fake'),
            {'from': 'architect', 'subject': 'approval needed: mg-1234', 'body': 'b'},
            'cur',
        ),
    ])
    reply = bridget.handle_command('read mg-1234')
    assert isinstance(reply, str)
    assert 'mail message-ids' in reply
    assert 'open mg-XXXX' in reply
    # design body not leaked
    assert 'body' not in reply.split('open mg-XXXX')[-1]


def test_read_mg_id_hint_is_case_insensitive(bridget):
    reply = bridget.handle_command('read MG-CAFE')
    assert isinstance(reply, str)
    assert 'open mg-XXXX' in reply


def test_read_dr_prefix_still_surfaces_design(bridget, monkeypatch):
    # Regression: dr- ids are out of scope for the mg-d3d7 tightening.
    # `read dr-XXXX` keeps the old design-doc-plus-mail-footer behavior.
    (bridget.DESIGNS_DIR / 'dr-abcd.md').write_text(
        '---\nstatus: drafted\n---\n# rep\n\nreport text\n'
    )
    monkeypatch.setattr(bridget, 'find_mails_for', lambda _id: [])
    reply = bridget.handle_command('read dr-abcd')
    assert isinstance(reply, list)
    joined = '\n'.join(reply)
    assert 'dr-abcd' in joined
    assert 'report text' in joined


# -- COMMAND_LIST description -----------------------------------------------

def test_command_list_describes_design_first_behavior(bridget):
    cl = bridget.COMMAND_LIST
    # Mentions both new design behavior and legacy fallback.
    assert 'design' in cl.lower()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
