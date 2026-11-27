Notes reader examples — local evaluation only

note-reading-demo.json contains three AI-written, explicitly synthetic examples.
They are neither submitted research nor scientific endorsements of any scheme.
They exercise a companion Human Explanation, standalone mathematical Markdown,
source-backed assumption/lemma references, and a precise objection with a finite
enumeration. Important caveats appear in the article, not solely in metadata.

When creating a fresh fixture, save the second note first. Replace the tokens
{{repetition.premise}} and {{repetition.bound}} in the other notes with exact
notebook passage loc links to that saved note's Fresh trials and repetition-bound headings.
Read include_passages=true, then POST the exact source pin and chosen selector
to /api/notebook/locs. Use its returned url in prose and references, and its
reference in scientific dependencies. Never use a latest-
revision link as an exact dependency. Normal publication and access rules apply.

In the existing 8018 synthetic preview, the first two examples were revised
through the regular API, preserving revision 1; the third was added. The ignored
.local/note-reading-demo-receipt.json records their IDs and latest hashes.
The guarded ignored helper .local/upgrade-reading-examples.py checks those
receipts before revising and refuses to overwrite subsequent author edits.
It uses only the isolated local database and synthetic author/reviewer.

Tests validate the templates and shared heading grammar. Scheme argument tests
verify that the same source-backed records resolve through security/correctness
bindings. The examples do not replace real schemes' existing scientific pins.

The v3 templates deliberately have no statements registrations. Resolve their
ordinary saved headings through the derived passages response/contract. The
current preview revisions preserve all earlier examples and old statement URLs.

The current examples also reserve short locs through the normal API. The first
and third notes cite them; the second shows the same P.N beside the target
headings. Subsections are included in the selected range; the following sibling
section is excluded. The loc is deliberately neutral: the title supplies the
mathematical role without claiming approval. This is synthetic local data only.

Current fixtures use explicit ai-written labels. The first explanation is one
Markdown companion with subheadings, rather than a field per paragraph. The
other two are single Markdown articles. Their caveats are written in the article;
ordinary reading no longer repeats claim/conditions in a metadata disclosure.
