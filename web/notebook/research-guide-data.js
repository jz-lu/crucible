/* Local design fixture: all research, people and verification records are illustrative. */
window.CrucibleResearchGuideData = {
  "directions": {
    "group-actions": {
      "label": "Group-action KEMs",
      "goal": "Find which ingredients could support a useful new construction."
    },
    "experiments": {
      "label": "Reproducible experiments",
      "goal": "Build evidence that another researcher can inspect and repeat."
    }
  },
  "questions": {
    "baseline": {
      "title": "Run the smallest attack benchmark",
      "context": "The benchmark has a documented small instance. A clean run would give later attack experiments a shared point of comparison.",
      "why": "Reproducible evidence gives later experiments a meaningful point of comparison.",
      "progress": [
        "Run the specified instance with the documented parameters and retain the outputs.",
        "Record the environment and commands, or a minimal reproduction of a failure."
      ],
      "notes": [
        "reproduce-note-1",
        "reproduce-note-2",
        "reproduce-note-3"
      ],
      "background": "Python · Reproducible experiments",
      "step": "Start with the smallest input in the run instructions.",
      "summary": "Check the baseline on one small instance and leave a repeatable run log.",
      "direction": "experiments",
      "subject": "Experiment B",
      "author": "Noor",
      "sponsor": "Noor",
      "status": "open",
      "updated": "Today",
      "next": "Add a reproducible baseline to the experiment’s evidence.",
      "openness": "follow",
      "owner": "Noor",
      "taskChecked": true,
      "checkSummary": "The source revisions, scope and expected evidence have been checked in this example. This does not establish the underlying research claim.",
      "approachReason": "The run instructions specify the input, commands and outputs to compare.",
      "verification": {
        "method": "Independent re-run",
        "checks": [
          "Pin the code, inputs, parameters and environment used for the run.",
          "Re-run the submitted commands and compare outputs with the documented baseline.",
          "If the run exposes a failure, reproduce the minimal failing case and identify its scope."
        ],
        "accepts": "A repeatable baseline result or a reproducible discrepancy with a complete run log."
      },
      "points": 100
    },
    "novelty": {
      "title": "Is this a new construction?",
      "context": "The proposed group action resembles an earlier construction. A structure-preserving comparison is supplied as a starting method; its mapping needs adapting to these definitions.",
      "why": "A precise comparison would show which ingredient is worth developing and which earlier results apply.",
      "progress": [
        "Map the objects and operations to a known construction.",
        "Identify a difference that changes what the scheme can do."
      ],
      "notes": [
        "novelty-note-1",
        "novelty-note-2",
        "novelty-note-3"
      ],
      "background": "Group actions · Algebra",
      "step": "Use the comparison checklist in the starting note: match the objects, map the operations, then test whether the mapping preserves the action. Adapt the mapping where the definitions differ.",
      "summary": "Compare the proposed action with the closest earlier construction.",
      "direction": "group-actions",
      "subject": "Candidate A",
      "author": "Mara",
      "sponsor": "Imani",
      "status": "open",
      "updated": "1 day ago",
      "next": "Use the comparison to decide which ingredient deserves further development.",
      "openness": "adapt",
      "owner": "Mara",
      "taskChecked": true,
      "checkSummary": "The source revisions, scope and expected evidence have been checked in this example. This does not establish the underlying research claim.",
      "approachReason": "A comparison method is supplied; you adapt its mapping to the two constructions.",
      "verification": {
        "method": "Check the correspondence",
        "checks": [
          "Pin both definitions and specify the proposed mapping of objects and operations.",
          "Check the argument that the mapping preserves the action, or independently check an explicit counterexample.",
          "Keep the conclusion within the two constructions and parameter cases actually examined."
        ],
        "accepts": "A checked equivalence or a concrete, supported difference between the cited constructions."
      },
      "points": 250
    },
    "assumption": {
      "title": "Make the hardness assumption precise",
      "context": "The proposal names a hard problem, but its input distribution and claimed attack cost need a clearer statement.",
      "why": "A precise assumption gives construction and attack work a shared target.",
      "progress": [
        "Specify the sampled instances, adversary, and success condition.",
        "Find a case where two plausible definitions behave differently."
      ],
      "notes": [
        "assumption-note-1",
        "assumption-note-2",
        "assumption-note-3"
      ],
      "background": "Probability · Security definitions",
      "step": "Write down the attacker’s input, output, and success condition for one parameter choice.",
      "summary": "Pin down the sampled instances and what an attacker must do.",
      "direction": "group-actions",
      "subject": "Candidate A",
      "author": "Leo",
      "sponsor": "Leo",
      "status": "open",
      "updated": "2 days ago",
      "next": "Give construction and attack work the same, testable target.",
      "openness": "approach",
      "owner": "Leo",
      "taskChecked": true,
      "checkSummary": "The source revisions, scope and expected evidence have been checked in this example. This does not establish the underlying research claim.",
      "approachReason": "The required security-game components are known, but their formulation needs mathematical judgment.",
      "verification": {
        "method": "Definition and example review",
        "checks": [
          "Check that the sampler, adversary, allowed resources and success condition are fully specified.",
          "Check that the game matches the construction in the pinned source revision.",
          "Independently check any example used to distinguish competing definitions."
        ],
        "accepts": "A precise, scoped security game with its assumptions explicit and any distinguishing example checked."
      },
      "points": 200
    },
    "attack": {
      "title": "Test a possible shortcut in the attack",
      "context": "The proposal has a baseline attack estimate. A different representation may expose a cheaper route.",
      "why": "An early attack could identify which part of the construction needs to change.",
      "progress": [
        "Demonstrate a shortcut and state its cost and applicable parameters.",
        "Explain a specific obstruction to a plausible attack strategy."
      ],
      "notes": [
        "attack-note-1",
        "attack-note-2",
        "attack-note-3"
      ],
      "background": "Computational algebra · Attack analysis",
      "step": "Try the alternative representation on one small instance. State exactly what the result shows.",
      "summary": "Try the alternative representation on a small, reproducible instance.",
      "direction": "group-actions",
      "subject": "Candidate A",
      "author": "Imani",
      "sponsor": "Imani",
      "status": "open",
      "updated": "Today",
      "next": "Revise the candidate if the shortcut works; record the precise obstacle if it does not.",
      "openness": "approach",
      "owner": "Imani",
      "taskChecked": true,
      "checkSummary": "The source revisions, scope and expected evidence have been checked in this example. This does not establish the underlying research claim.",
      "approachReason": "The target is specified; finding and assessing an attack remains exploratory.",
      "verification": {
        "method": "Re-run and inspect the argument",
        "checks": [
          "Re-run the submitted attack on the declared inputs and parameters.",
          "Compare operation counts against the stated baseline and check the claimed cost argument.",
          "For a negative result, independently check the obstruction to the specific strategy being ruled out."
        ],
        "accepts": "A reproducible shortcut with a scoped cost claim, or a checked obstruction to the stated strategy."
      },
      "points": 400
    },
    "obstacles": {
      "title": "Find the next useful attack question",
      "context": "Earlier attacks depend on several unresolved assumptions. Choose a gap worth investigating and turn it into a concrete research question.",
      "why": "A maintained account of earlier attempts helps researchers find a new opening without repeating the same work.",
      "progress": [
        "Identify an unsupported assumption and cite the notes that depend on it.",
        "Explain why it is worth investigating and define a bounded check that could resolve it."
      ],
      "notes": [
        "synthesis-note-1",
        "synthesis-note-2",
        "synthesis-note-3"
      ],
      "background": "Mathematical reading · Comparing assumptions",
      "step": "Compare the assumptions in the two starting notes.",
      "summary": "Choose an unresolved assumption and turn it into a testable question.",
      "direction": "group-actions",
      "subject": "Research overview",
      "author": "Leo",
      "sponsor": "Leo",
      "status": "open",
      "updated": "Today",
      "next": "Turn one unsupported claim into a concrete evaluation task.",
      "openness": "question",
      "owner": "Leo",
      "taskChecked": true,
      "checkSummary": "The source revisions, scope and expected evidence have been checked in this example. This does not establish the underlying research claim.",
      "approachReason": "You choose which gap deserves attention and formulate the question to investigate.",
      "verification": {
        "method": "Source and test-plan review",
        "checks": [
          "Locate the exact source revisions that rely on the proposed assumption.",
          "Check that the cited material does not already settle the stated gap.",
          "Check that the proposed experiment or mathematical claim distinguishes clear outcomes and states the evidence needed."
        ],
        "accepts": "A source-supported gap and a concrete evaluation plan with observable or mathematically precise outcomes."
      },
      "points": 150
    },
    "variant": {
      "title": "Where else could this action be useful?",
      "context": "Candidate A is one possible use of the underlying group action. Propose a different research goal and substantiate one initial claim about its feasibility.",
      "why": "The same mathematical ingredients may support a useful direction beyond the current candidate.",
      "progress": [
        "State a research objective and identify the documented property that motivates it.",
        "Check one scoped feasibility claim with a reproducible experiment, proof, or explicit counterexample."
      ],
      "notes": [
        "variant-note-1",
        "variant-note-2",
        "variant-note-3"
      ],
      "background": "Group actions · KEM correctness",
      "step": "Choose a possible use, state the smallest claim it depends on, and provide a test or mathematical argument that checks that claim.",
      "summary": "Propose a new research goal and verify one initial claim.",
      "direction": "group-actions",
      "subject": "Candidate A",
      "author": "Noor",
      "sponsor": "Mara",
      "status": "open",
      "updated": "Today",
      "next": "Record a new direction with checked initial evidence and an explicit account of what remains unresolved.",
      "openness": "direction",
      "owner": "Noor",
      "taskChecked": true,
      "checkSummary": "The source revisions, scope and expected evidence have been checked in this example. This does not establish the underlying research claim.",
      "approachReason": "The starting material is supplied; you propose the research goal itself.",
      "verification": {
        "method": "Reproducible test or proof review",
        "checks": [
          "State one precise feasibility claim, including its inputs, assumptions and scope.",
          "Independently re-run the submitted test, or check the proof or explicit counterexample against the pinned definitions.",
          "Check that the evidence supports the stated claim or obstruction and that unresolved assumptions remain explicit."
        ],
        "accepts": "A precise research goal with one independently checked initial claim or obstruction."
      },
      "points": 300
    },
    "reproduce": {
      "title": "Reproduce the reported attack result",
      "context": "An experiment reports a change in attack cost. An independent run would help establish what the result supports.",
      "why": "Reproducible evidence gives later experiments a meaningful point of comparison.",
      "progress": [
        "Re-run the experiment with the recorded inputs and settings.",
        "Locate a discrepancy and explain what changes the result."
      ],
      "notes": [
        "reproduce-note-1",
        "reproduce-note-2",
        "reproduce-note-3"
      ],
      "background": "Python · Reproducible experiments",
      "step": "Run the smallest documented example and compare its output with the reported result.",
      "summary": "An independent run is ready for your assessment.",
      "direction": "experiments",
      "subject": "Experiment B",
      "author": "Leo",
      "sponsor": "You",
      "status": "review",
      "updated": "Today",
      "next": "Check the submitted run before treating it as a reliable baseline.",
      "openness": "follow",
      "owner": "You",
      "taskChecked": true,
      "checkSummary": "The source revisions, scope and expected evidence have been checked in this example. This does not establish the underlying research claim.",
      "approachReason": "The original experiment supplies the procedure; the independent result still needs assessment.",
      "verification": {
        "method": "Independent re-run",
        "checks": [
          "Pin the original result and the submitted code, environment, inputs and parameters.",
          "Re-run the contribution and compare it with the recorded result.",
          "Reproduce any claimed discrepancy and explain which change accounts for it."
        ],
        "accepts": "An independent reproduction or a repeatable discrepancy with an evidence-backed explanation."
      },
      "points": 100
    },
    "synthesis": {
      "title": "Connect the earlier construction attempts",
      "context": "Several notes explore related constructions. Their assumptions, results, and unresolved obstacles need a shared summary.",
      "why": "A maintained account of earlier attempts helps researchers find a new opening without repeating the same work.",
      "progress": [
        "Connect two attempts that address the same underlying obstacle.",
        "Separate an established limitation from an unexplored possibility."
      ],
      "notes": [
        "synthesis-note-1",
        "synthesis-note-2",
        "synthesis-note-3"
      ],
      "background": "Mathematical reading · Comparing assumptions",
      "step": "Read the current summary and two earlier attempts. Identify one connection or unresolved claim.",
      "summary": "A reviewed comparison now gives the direction a clearer starting point.",
      "direction": "group-actions",
      "subject": "Research overview",
      "author": "Mara",
      "sponsor": "Mara",
      "status": "closed",
      "updated": "3 days ago",
      "next": "Use the revised overview to choose the next construction question.",
      "outcome": "The comparison is incorporated into the direction’s overview. Two unresolved assumptions remain explicit; the request for a shared summary is complete.",
      "openness": "approach",
      "owner": "Mara",
      "taskChecked": true,
      "checkSummary": "The source revisions, scope and expected evidence have been checked in this example. This does not establish the underlying research claim.",
      "approachReason": "The sources and comparison goal are fixed; establishing the connections requires a method of your own.",
      "verification": {
        "method": "Source-by-source review",
        "checks": [
          "Pin every source revision used in the comparison.",
          "Check each claimed connection against the definitions and evidence in those sources.",
          "Separate established limitations from remaining assumptions and identify what changed in the overview."
        ],
        "accepts": "A sourced comparison whose connections and stated limitations survive review."
      },
      "points": 150
    }
  },
  "notes": {
    "novelty-note-1": {
      "title": "Proposal outline",
      "summary": "The proposed construction and its claimed new ingredient.",
      "body": "Use this comparison checklist: match the two sets of objects, propose a map between their operations, and test whether the map preserves the action. Adapt the mapping to the candidate's definitions. Separate a change in notation from a change in the mathematical construction.",
      "author": "Mara",
      "revision": 1,
      "direction": "group-actions",
      "background": "Group actions · Algebra"
    },
    "novelty-note-2": {
      "title": "Related construction",
      "summary": "The closest known construction and its assumptions.",
      "body": "Compare the underlying objects, operations, and security assumptions. A useful comparison states exactly where a correspondence holds and where it stops.",
      "author": "Mara",
      "revision": 2,
      "direction": "group-actions",
      "background": "Group actions · Algebra"
    },
    "novelty-note-3": {
      "title": "Comparison in progress",
      "summary": "An unfinished mapping between the two constructions.",
      "body": "The mapping is tentative. A counterexample to one step, or a corrected correspondence, would both help the next researcher continue.",
      "author": "Mara",
      "revision": 3,
      "direction": "group-actions",
      "background": "Group actions · Algebra"
    },
    "assumption-note-1": {
      "title": "Proposed assumption",
      "summary": "The current informal statement of the hard problem.",
      "body": "Identify what an attacker receives, what it must output, and what counts as success. Mark each detail that the current statement leaves open.",
      "author": "Leo",
      "revision": 1,
      "direction": "group-actions",
      "background": "Probability · Security definitions"
    },
    "assumption-note-2": {
      "title": "Parameter choices",
      "summary": "The parameters used by the example implementation.",
      "body": "Check which quantities scale and how instances are sampled. Small examples may clarify a definition without establishing a security claim.",
      "author": "Leo",
      "revision": 2,
      "direction": "group-actions",
      "background": "Probability · Security definitions"
    },
    "assumption-note-3": {
      "title": "Open definitions",
      "summary": "Questions about distributions and exceptional instances.",
      "body": "A carefully chosen example can distinguish competing definitions. Record the assumptions under which that example applies.",
      "author": "Leo",
      "revision": 3,
      "direction": "group-actions",
      "background": "Probability · Security definitions"
    },
    "attack-note-1": {
      "title": "Attack target",
      "summary": "The stated problem and the information available to an attacker.",
      "body": "Pin down the success condition and parameter regime before comparing attacks. Distinguish a weakness in an example from a general attack.",
      "author": "Imani",
      "revision": 1,
      "direction": "group-actions",
      "background": "Computational algebra · Attack analysis"
    },
    "attack-note-2": {
      "title": "Baseline analysis",
      "summary": "The current attack estimate and its assumptions.",
      "body": "Trace the reasoning behind the estimate. Record which steps are proved, measured, or conjectured.",
      "author": "Imani",
      "revision": 2,
      "direction": "group-actions",
      "background": "Computational algebra · Attack analysis"
    },
    "attack-note-3": {
      "title": "Experiment sketch",
      "summary": "A small experiment for testing an alternative representation.",
      "body": "Keep the inputs and procedure reproducible. A failed attempt becomes useful when it identifies a specific, inspectable obstacle.",
      "author": "Imani",
      "revision": 3,
      "direction": "group-actions",
      "background": "Computational algebra · Attack analysis"
    },
    "reproduce-note-1": {
      "title": "Reported result",
      "summary": "The experiment, claimed observation, and stated limitations.",
      "body": "Identify the exact observation to reproduce. Preserve the distinction between a measured trend and a general complexity claim.",
      "author": "Leo",
      "revision": 1,
      "direction": "experiments",
      "background": "Python · Reproducible experiments"
    },
    "reproduce-note-2": {
      "title": "Run instructions",
      "summary": "The implementation, parameters, seeds, and expected output.",
      "body": "Record enough of the environment and procedure for someone else to repeat your run. Note any missing inputs or ambiguous steps.",
      "author": "Leo",
      "revision": 2,
      "direction": "experiments",
      "background": "Python · Reproducible experiments"
    },
    "reproduce-note-3": {
      "title": "Independent check",
      "summary": "A place to compare results and explain discrepancies.",
      "body": "A reproduction can confirm the observation or identify a limitation. Both outcomes can improve the shared record.",
      "author": "Leo",
      "revision": 3,
      "direction": "experiments",
      "background": "Python · Reproducible experiments"
    },
    "synthesis-note-1": {
      "title": "Current research summary",
      "summary": "The current picture of this research direction.",
      "body": "Use this as an entry point. Follow its references when a claim is unclear or when you want to compare the evidence behind two approaches.",
      "author": "Mara",
      "revision": 1,
      "direction": "group-actions",
      "background": "Mathematical reading · Comparing assumptions"
    },
    "synthesis-note-2": {
      "title": "An earlier attempt",
      "summary": "A construction that encountered a specific obstacle.",
      "body": "Identify what was actually ruled out and under which assumptions. Keep the scope of the negative result visible.",
      "author": "Mara",
      "revision": 2,
      "direction": "group-actions",
      "background": "Mathematical reading · Comparing assumptions"
    },
    "synthesis-note-3": {
      "title": "An alternative approach",
      "summary": "A later note that changes one of the assumptions.",
      "body": "Compare the two approaches at the level of their definitions. Explain whether the change avoids the earlier obstacle or simply restates it.",
      "author": "Mara",
      "revision": 3,
      "direction": "group-actions",
      "background": "Mathematical reading · Comparing assumptions"
    },
    "variant-note-1": {
      "title": "The action and its known properties",
      "summary": "The mathematical ingredients used in Candidate A and the properties they establish.",
      "body": "Separate the properties of the underlying action from assumptions introduced by the candidate protocol. A new use must say which established property it relies on and which claim remains untested.",
      "author": "Noor",
      "revision": 1,
      "direction": "group-actions",
      "background": "Group actions · KEM correctness"
    },
    "variant-note-2": {
      "title": "Correctness conditions",
      "summary": "The conditions the protocol needs to produce a shared key.",
      "body": "These conditions explain what the current candidate needs to produce a shared key. A new application may need different conditions; state those explicitly before making a security claim.",
      "author": "Noor",
      "revision": 2,
      "direction": "group-actions",
      "background": "Group actions · KEM correctness"
    },
    "variant-note-3": {
      "title": "Earlier simplification",
      "summary": "A previous attempt and the obstacle it encountered.",
      "body": "Identify the scope of the obstacle. A new research goal should explain whether it changes the premise on which this earlier argument depends.",
      "author": "Noor",
      "revision": 3,
      "direction": "group-actions",
      "background": "Group actions · KEM correctness"
    },
    "your-question": {
      "title": "An alternative sampling rule",
      "summary": "A tentative change to how the example construction samples instances. Its effect on the assumption is unclear.",
      "body": "This example note sets out a candidate sampling rule and asks whether the existing security definition still captures it. A useful next step is to compare the two instance distributions.",
      "author": "You",
      "revision": 1,
      "direction": "group-actions",
      "background": "Probability · Security definitions"
    },
    "your-comparison": {
      "title": "Comparison of two definitions",
      "summary": "A partial mapping, with one step that still needs checking.",
      "body": "This example contribution records the parts of the definitions that match, and isolates a remaining ambiguity. It makes a limited claim rather than a full equivalence result.",
      "author": "You",
      "revision": 2,
      "direction": "group-actions",
      "background": "Group actions · Algebra"
    },
    "your-run": {
      "title": "Independent experiment log",
      "summary": "Recorded inputs, environment and outputs for an independent check.",
      "body": "This example contribution includes a repeatable procedure and distinguishes observed agreement from a general complexity claim. It is still awaiting assessment.",
      "author": "You",
      "revision": 1,
      "direction": "experiments",
      "background": "Python · Reproducible experiments"
    },
    "shared-steps": {
      "title": "A partial correspondence",
      "summary": "Several steps are now mapped; the exceptional case remains open.",
      "body": "Example finding: the comparison narrows the question to one unresolved step. The scope and remaining uncertainty are recorded alongside the argument.",
      "author": "Noor",
      "revision": 2,
      "direction": "group-actions",
      "background": "Group actions · Algebra"
    },
    "independent-run": {
      "title": "Experiment B: independent run",
      "summary": "The submitted log reports agreement within the recorded parameter range.",
      "body": "Example finding: another researcher can now inspect the inputs, outputs and limitations. Agreement outside the recorded range has not been established.",
      "author": "Imani",
      "revision": 1,
      "direction": "experiments",
      "background": "Python · Reproducible experiments"
    },
    "reviewed-summary": {
      "title": "A map of earlier attempts",
      "summary": "A sourced comparison separates established obstacles from two open assumptions.",
      "body": "Example finding: the direction’s overview now links the earlier attempts and their limits. Closing this request does not establish the security of any construction.",
      "author": "Leo",
      "revision": 3,
      "direction": "group-actions",
      "background": "Comparing mathematical arguments"
    }
  },
  "contributions": [
    {
      "id": "partial-1",
      "question": "novelty",
      "note": "shared-steps",
      "kind": "partial",
      "author": "Noor",
      "status": "confirmed",
      "summary": "The shared steps are mapped. The remaining question is whether the exceptional case changes the construction.",
      "reviewer": "Leo",
      "assessment": "Useful partial progress: the comparison narrows the open question. Mara incorporated it into the brief.",
      "approvals": {
        "poster": "acknowledged",
        "pipeline": "not-requested"
      }
    },
    {
      "id": "run-1",
      "question": "reproduce",
      "note": "independent-run",
      "kind": "resolution",
      "author": "Imani",
      "status": "pending",
      "summary": "The recorded run agrees in the tested range. A second person still needs to check the log and its limits.",
      "approvals": {
        "poster": "pending",
        "pipeline": "approved"
      },
      "pipelineReason": "Example check: the recorded inputs, commands and outputs match the requested scope. The poster still needs to assess completion."
    },
    {
      "id": "summary-1",
      "question": "synthesis",
      "note": "reviewed-summary",
      "kind": "resolution",
      "author": "Leo",
      "status": "confirmed",
      "summary": "Earlier attempts are connected in a sourced comparison. The overview now separates known obstacles from open assumptions.",
      "reviewer": "Noor",
      "assessment": "The comparison supports the updated overview. The request is complete; the wider research direction remains open.",
      "approvals": {
        "poster": "approved",
        "pipeline": "approved"
      }
    }
  ],
  "account": {
    "score": 24,
    "credits": 2
  },
  "openness": {
    "follow": {
      "position": 1
    },
    "adapt": {
      "position": 2
    },
    "approach": {
      "position": 3
    },
    "question": {
      "position": 4
    },
    "direction": {
      "position": 5
    }
  }
};
