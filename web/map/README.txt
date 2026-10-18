Research map integration

Open Research notebook, then Visualizations → Scheme map. Notes is the default.
The map mounts inside the same notebook shell at /#notebook?view=map.
Its shadow root contains styles without changing the surrounding site.
Switching views preserves the notebook filters and map selection/filter state.
Only the network visualization is offered. The old map URL redirects here.
The map route serves only an explicit asset allowlist and uses the existing beta
membership policy. The visualization itself changes no scoring rules or notebook imports.
The notebook research-review workflow has separate migration 0013.

Source data: notes.json (global notebook format) and catalogue.json (bindings,
relationships, selected highlights and a dated scoring snapshot).
After editing source data, run: python web/map/build.py
The builder checks exact note revision hashes before emitting browser payloads.
Do not edit catalogue.js or notes-data.js directly.

Scored and Highlighted each contain the existing eight challenge releases.
All contains thirteen KEMs. Inclusion is not a security endorsement, and a local
challenge implementation is not necessarily the published standard.

The default Notes list includes this same curated collection alongside account
notes, marked Research. Research notes retain exact source IDs, revisions and
content hashes; they are read-only and do not fabricate authors or approvals.
My notes filters to the signed-in author, including their private and published
notes. Other participants see community-approved revisions. Private participant
sharing is retired. The default list orders each reader's latest visible
revisions newest first. Publish requests are reviewed through Review center,
reached from the reviewer's own account; Download my notebook exports owned records.
Live publication-to-map bindings and live eligibility remain future work.
Scheme detail actions use the main site's current registered catalogue, not the
dated scoring snapshot: Open challenge goes directly to the challenge tab and
View parameters opens the scheme overview with graphs and configuration details; both pin that exact
release. Literature-only entries have no fabricated challenge or parameters
link. The catalogue callback is read when an entry opens, without another fetch.
Only explicitly admitted descriptive notes become scheme nodes. Literature
leads, attacks, comparisons and procedural notes remain in the note browser.
The collection has 26 notes, including 11 literature leads and analyses.
Full source notes can now be frozen into server-backed research requests; see
docs/research-notebook-draft.txt for the distinction between assessment and activation.
