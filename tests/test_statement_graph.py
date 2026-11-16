"""Exact block references join the existing graph without broadening a pin."""
from crucible import notebook_graph


def test_block_links_index_exact_note_and_preserve_locator_in_source():
    base = '/#notebook?note=test-note&revision=3&sha256=' + 'a' * 64
    for kind in ('statement', 'argument'):
        reference = {'url':base + '&' + kind + '=same-name', 'relation':'challenges', 'scope':'Only this configuration'}
        rows = list(notebook_graph.references({'references':[reference]}))
        assert len(rows) == 1
        assert rows[0]['target_revision'] == 3
        assert rows[0]['target_sha256'] == 'a' * 64
        assert rows[0]['relation'] == 'challenges'
        assert reference['url'].endswith('&' + kind + '=same-name')
    for suffix in ('&statement=a&argument=b', '&statement=a&statement=b', '&statement=constructor',
                   '&statement=Bad', '&argument=' + 'a' * 81):
        assert list(notebook_graph.references({'references':[{'url':base + suffix}]})) == []
