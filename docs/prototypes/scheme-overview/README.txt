Scheme overview design draft — 27 November 2026

This is a separate static preview, not a new application route or production
resource publication. It begins with Frodo's 80-stage progression. Move the
pointer across a graph to preview a stage; click to select. Arrow keys preview,
Enter selects and Escape restores the selection. Touch users can tap the graph
or use the slider. Security/Correctness link to the exact saved source sections.

From the repository root:
  python3 -m http.server 8019 --bind 127.0.0.1 --directory docs/prototypes
Open:
  http://127.0.0.1:8019/scheme-overview/

App links default to the original local preview at 127.0.0.1:8018/preview. To use
your normal local application at 8000, open instead:
  http://127.0.0.1:8019/scheme-overview/?app=http://127.0.0.1:8000/
The draft itself needs no account, credentials, database or native runtime.
Following an application link still requires whatever access that app demands.

Data is a saved design/audit extract: exact encoded lengths for 377 default
configurations, historical native timings from October run 40770844682 and
note-backed attack references. Synthetic preview recoveries are not real research
results. Different schemes used different processors; timings are not a fair
cross-scheme ranking. Missing attack estimates stay missing. No API writes,
benchmark launches, note publication or resource activation occur in this draft.

The JSON evidence identifies retained reports; it is not the complete raw receipt
archive. The original extraction used local release snapshots and the retained
benchmark archive described in docs/stage-graph-review-20261127.txt. Rerunning it
requires those exact inputs. Do not activate these samples as current evidence.
Fonts are the same existing bundled Plex/Cardo assets used by the application.
The icon is copied from web/assets/crucible-mark.svg; no remote assets are loaded.

This standalone prototype demonstrates the design. Event/state handling should
be integrated with the application's existing graph selection, authorization,
caching and lifecycle modules if the design is accepted, not added as a second
live data pipeline.
