"""Read-only integration gate for a manually reviewed, registered adapter.

This checks bindings and declared metadata. It does not run uploaded code,
retrieve URLs, certify security, or activate a scheme. Register reviewed source
in ADAPTERS first; enabling it in CURRENT remains an explicit code change.
"""
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from . import registry, correctness_policy, proposal_retractions
from .protocol_proposals import ProtocolProposal, ProtocolProposalRevision, ProtocolProposalReview
from .protocol_specs import ProtocolSpec, canonical_spec, correctness_review_gaps


CHECKS = ('specification_complete', 'correctness', 'tier_design', 'parameter_selection', 'implementation_conformance',
          'input_resource_safety', 'reproduction')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def check_admission(session, proposal_id, scheme_id, version):
    """Return an auditable binding report; persist no state and run no attacks."""
    record = session.scalar(select(ProtocolProposal).where(ProtocolProposal.id == proposal_id)
        .with_for_update().execution_options(populate_existing=True))
    _require(record is not None, 'Proposal not found.')
    _require(proposal_retractions.get(session, proposal_retractions.SCHEME, record.id) is None,
             'This proposal was retracted by its author; it cannot receive new admission.')
    _require(record.status == 'approved', 'The current proposal revision needs manual approval.')
    revision = session.scalar(select(ProtocolProposalRevision).where(
        ProtocolProposalRevision.proposal_id == record.id,
        ProtocolProposalRevision.revision == record.revision))
    review = session.scalar(select(ProtocolProposalReview).where(
        ProtocolProposalReview.proposal_id == record.id,
        ProtocolProposalReview.revision == record.revision))
    _require(revision is not None and review is not None, 'Missing revision or review evidence.')
    specification = canonical_spec(ProtocolSpec.model_validate(revision.specification))
    digest = _hash(specification)
    _require(digest == revision.content_sha256 == record.content_sha256 == review.content_sha256,
             'The reviewed specification hash does not match the current revision.')
    _require(_hash(record.specification) == digest, 'The current specification differs from its frozen revision.')
    _require(review.decision == 'approved', 'The review did not approve this revision.')
    _require(review.reviewer_id != record.author_id, 'An independent reviewer is required.')
    _require(review.checks.get('test_only_self_review') is not True,
             'Development self-review cannot satisfy independent admission or scheme contribution credit.')
    _require(all(review.checks.get(key) is True for key in CHECKS), 'Required review checks are incomplete.')
    _require(bool(review.evidence and review.evidence.strip()), 'Review evidence is required.')
    _require(specification.get('tier_design') is not None,
             'A configuration and default-progression plan and explicit review of it are required; revise and review historical proposals before new admission.')
    _require(specification['tier_design'].get('parameter_selection') is not None,
             'A parameter-selection plan and explicit review of it are required; revise and review historical proposals before new admission.')
    _require(specification['correctness']['kind'] != 'unknown', 'A reviewed correctness claim is required.')
    gaps = correctness_review_gaps(specification)
    _require(not gaps, ' '.join(gaps))
    implementation = specification.get('implementation') or {}
    source_hash = implementation.get('sha256')
    _require(bool(source_hash) and review.implementation_sha256 == source_hash,
             'The exact implementation artifact has not been reviewed.')
    adapter = registry.ADAPTERS.get((scheme_id, version))
    _require(adapter is not None, 'Register the reviewed adapter explicitly before checking admission.')
    _require((adapter.SCHEME_ID, adapter.SCHEME_VERSION) == (scheme_id, version),
             'The registered adapter identity is inconsistent.')
    _require(specification['version'] == version, 'The proposal release does not match the adapter release.')
    actual_hash = hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest()
    _require(actual_hash == source_hash == adapter.IMPLEMENTATION_SHA256,
             'The registered source differs from the exact reviewed implementation.')
    profiles = adapter.manifests()
    _require(bool(profiles), 'The adapter must declare at least one challenge profile.')
    closure_hashes = {profile.get('implementation', {}).get('artifact_sha256') for profile in profiles}
    if closure_hashes != {None}:
        _require(len(closure_hashes) == 1 and implementation.get('artifact_sha256') in closure_hashes,
                 'The reviewed specification must pin the complete implementation closure, not only its adapter source.')
        provider = getattr(adapter, 'artifact_manifest', None)
        _require(provider is not None, 'The complete implementation closure must be inspectable.')
        verifier = getattr(adapter, 'verify_artifact', provider)
        try:
            closure = verifier()
        except (RuntimeError, OSError, ValueError):
            raise ValueError('The available implementation closure failed its artifact checks.') from None
        expected = closure.get('artifact_sha256')
        actual = hashlib.sha256(json.dumps({key: value for key, value in closure.items() if key != 'artifact_sha256'},
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        _require(actual == expected == implementation.get('artifact_sha256'),
                 'The available implementation closure differs from the reviewed specification.')
    identifiers = [profile.get('id') for profile in profiles]
    _require(all(isinstance(identifier, str) and identifier.strip() == identifier
                 and 1 <= len(identifier) <= 100 for identifier in identifiers)
             and len(set(identifiers)) == len(identifiers)
             and all(profile.get('profile_id') == profile['id'] for profile in profiles),
             'Profiles must have unique nonblank matching id and profile_id values, at most 100 characters.')
    sizes = [profile.get('instance_size', profile.get('parameter_bits')) for profile in profiles]
    _require(all(type(size) is int and size > 0 for size in sizes) and len(set(sizes)) == len(sizes),
             'The adapter must declare distinct positive legacy configuration selectors.')
    tiers = specification['parameters']['supported_tiers']
    if tiers['kind'] == 'list':
        covered = sorted(sizes) == tiers['values']
    else:
        covered = (len(sizes) == (tiers['max'] - tiers['min']) // tiers['step'] + 1
                   and all(tiers['min'] <= size <= tiers['max']
                           and (size - tiers['min']) % tiers['step'] == 0 for size in sizes))
    _require(covered, 'The registered configuration coverage differs from the reviewed specification.')
    for profile in profiles:
        _require((profile.get('scheme_id'), profile.get('version')) == (scheme_id, version),
                 'A profile declares a different scheme or release.')
        _require(profile.get('implementation', {}).get('source_sha256') == source_hash,
                 'A profile points to a different implementation artifact.')
        # Null at the scheme level permits a parameter-dependent output length;
        # the executable challenge still requires an exact bounded byte count
        # for every admitted profile. This checks declarations, not conformity.
        profile_length = profile.get('shared_secret_bytes')
        _require(type(profile_length) is int and 1 <= profile_length <= 4096,
                 'Every profile must declare an integer shared-secret byte length from 1 through 4096.')
        fixed_length = specification['encoding']['shared_secret_bytes']
        if fixed_length is not None:
            _require(profile_length == fixed_length,
                     'The shared-secret length differs from the reviewed specification.')
        # Bind declarations, not mathematical truth. Arbitrary statistical
        # expressions are inert text: exact common-envelope spelling avoids
        # pretending to compare expressions or silently accepting a weaker one.
        correctness = profile.get('correctness')
        _require(isinstance(correctness, dict), 'Every profile must declare its correctness requirement.')
        kind, bound = correctness.get('kind'), correctness.get('failure_bound')
        _require(kind in ('perfect', 'statistical') and isinstance(bound, str) and bool(bound.strip()),
                 'Every profile needs a known correctness kind and a nonblank failure bound.')
        _require(all(isinstance(correctness.get(key), str) and bool(correctness[key].strip())
                     for key in ('argument', 'scope')),
                 'Every profile must state its correctness argument and scope.')
        if kind == 'perfect':
            _require(bound == '0', 'A perfect profile must declare exactly zero disagreement.')
        else:
            _require(specification['correctness']['kind'] == 'statistical',
                     'A statistical profile cannot satisfy a reviewed perfect-correctness envelope.')
            _require(bound == specification['correctness']['failure_bound'],
                     'Every statistical profile must declare the exact reviewed common failure bound; stronger results may be recorded separately as evidence.')
    return {'status': 'binding-check-passed', 'proposal_id': record.id, 'revision': record.revision,
            'correctness_policy_id': specification['correctness'].get('policy_id'),
            'correctness_policy_advisory': False,
            'correctness_claim_target_exponent': correctness_policy.FAILURE_EXPONENT,
            'correctness_evidence_kind': specification['correctness']['justification']['kind'],
            'content_sha256': digest, 'review_id': review.id, 'implementation_sha256': actual_hash,
            'scheme_id': scheme_id, 'version': version, 'profiles_checked': len(profiles),
            'scope': 'Manual research review, configuration declarations and artifact/metadata bindings checked. Correctness guarantees remain claims under their stated dependencies, which may be conjectural. The common claimed failure goal of <=2^-128 or zero is declared; mathematical truth and model adequacy are not established by this binding check. Reviewers assess consistency and any rigorous contradiction; a proof gap alone is not a refutation. No execution, correctness/security certification, or activation performed.'}
