#!/usr/bin/env python3
"""Offline publication-screening evaluation; never calls a model or reads a key.

Run from repository root:
  python scripts/publication_screening_eval.py prepare --cases docs/publication-screening-cases.json \
      --model MODEL --output /private/tmp/blind-requests.json
  python scripts/publication_screening_eval.py evaluate --cases docs/publication-screening-cases.json \
      --responses /private/tmp/saved-responses.json --output /private/tmp/evaluation.json

Saved responses format: {"format":"crucible-screening-saved-responses-v1",
 "policy_version":..., "prompt_sha256":..., "cases":[
 {"case_id":"case0001", "input_sha256":..., "verdict":{...}}]}.
Instead of "verdict", a record may contain "response" with the complete saved
Responses JSON object; the same production parser then checks completion/usage.
Do not send this evaluator's labels or report to a model. Blind exports contain
only opaque case IDs plus production requests. Files are exclusive mode 0600.
This small synthetic set is a policy regression suite, not a production-accuracy
estimate. Review held-out full-length notes and failure behavior before enabling.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crucible.publication_screening_policy import (
    DEFAULT_MAX_INPUT_TOKENS, INPUT_FORMAT_VERSION, OUTPUT_SCHEMA_SHA256,
    POLICY_VERSION, PROMPT_SHA256, ScreeningCoverageError, ScreeningValidationError,
    canonical_json, prepare_input, strict_json, validate_verdict,
)
from crucible.publication_screening_provider import (
    ScreeningProviderError, parse_response, response_request,
)

MAX_FILE_BYTES = 32 * 1024 * 1024


class EvaluationError(ValueError):
    pass


def read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise EvaluationError('file_size_limit')
    return strict_json(raw, max_bytes=MAX_FILE_BYTES)


def cases_from(document):
    cases = document.get('cases') if type(document) is dict else None
    if type(cases) is not list or not cases or len(cases) > 10000:
        raise EvaluationError('invalid_cases')
    seen = set()
    for case in cases:
        if type(case) is not dict or type(case.get('id')) is not str or case['id'] in seen:
            raise EvaluationError('invalid_case_identity')
        seen.add(case['id'])
        expected = case.get('expected')
        if (type(case.get('input')) is not dict or type(expected) is not dict
                or expected.get('outcome') not in ('no_concern_found', 'human_review_needed')
                or type(expected.get('categories')) is not list
                or not all(type(c) is str for c in expected['categories'])
                or type(expected.get('requires_limitation')) is not bool):
            raise EvaluationError('invalid_case_contract')
    return cases


def metadata(cases):
    return {'policy_version': POLICY_VERSION, 'prompt_sha256': PROMPT_SHA256,
            'input_format_version': INPUT_FORMAT_VERSION, 'output_schema_sha256': OUTPUT_SCHEMA_SHA256,
            'dataset_sha256': hashlib.sha256(canonical_json(cases).encode()).hexdigest()}


def prepare_cases(cases, *, model, max_input_tokens=DEFAULT_MAX_INPUT_TOKENS,
                  max_output_tokens=1500, reasoning_effort=None):
    records = []
    for index, case in enumerate(cases, 1):
        record = {'case_id': f'case{index:04d}'}
        try:
            prepared = prepare_input(case['input'], max_input_tokens=max_input_tokens)
            record.update(input_sha256=prepared.input_sha256,
                          request=response_request(prepared, model=model,
                              max_output_tokens=max_output_tokens, reasoning_effort=reasoning_effort))
        except ScreeningCoverageError as error:
            record['coverage_fallback'] = error.code
        records.append(record)
    return {'format': 'crucible-screening-blind-requests-v1', **metadata(cases),
            'max_input_tokens': max_input_tokens, 'cases': records}


def evaluate_cases(cases, saved, *, max_input_tokens=DEFAULT_MAX_INPUT_TOKENS):
    if (type(saved) is not dict or saved.get('format') != 'crucible-screening-saved-responses-v1'
            or saved.get('policy_version') != POLICY_VERSION or saved.get('prompt_sha256') != PROMPT_SHA256
            or type(saved.get('cases')) is not list):
        raise EvaluationError('response_policy_mismatch')
    by_id = {}
    allowed = {f'case{i:04d}' for i in range(1, len(cases) + 1)}
    for record in saved['cases']:
        if type(record) is not dict or type(record.get('case_id')) is not str:
            raise EvaluationError('invalid_response_identity')
        identifier = record['case_id']
        if identifier not in allowed or identifier in by_id:
            raise EvaluationError('unknown_or_duplicate_response')
        by_id[identifier] = record
    counts = {key: 0 for key in ('expected_clear', 'expected_review', 'valid_clear', 'valid_review',
        'correct_clear', 'correct_review', 'false_positive', 'missed_concern', 'missing_response',
        'invalid_response', 'coverage_fallback', 'evidence_expectation_mismatch')}
    rows = []
    for index, case in enumerate(cases, 1):
        identifier = f'case{index:04d}'
        expected = case['expected']
        expected_clear = expected['outcome'] == 'no_concern_found'
        counts['expected_clear' if expected_clear else 'expected_review'] += 1
        row = {'case_id': identifier}
        try:
            prepared = prepare_input(case['input'], max_input_tokens=max_input_tokens)
        except ScreeningCoverageError as error:
            counts['coverage_fallback'] += 1
            row.update(status='coverage_fallback', code=error.code)
            rows.append(row)
            continue
        record = by_id.get(identifier)
        if record is None:
            counts['missing_response'] += 1; row['status'] = 'missing_response'
        else:
            try:
                if record.get('input_sha256') != prepared.input_sha256:
                    raise EvaluationError('response_input_mismatch')
                choices = set(record) & {'verdict', 'response'}
                if len(choices) != 1 or set(record) != {'case_id', 'input_sha256'} | choices:
                    raise EvaluationError('ambiguous_saved_response')
                if 'response' in record:
                    verdict = parse_response(canonical_json(record['response']), prepared).verdict
                else:
                    verdict = validate_verdict(record['verdict'], prepared)
                clear = verdict['outcome'] == 'no_concern_found'
                counts['valid_clear' if clear else 'valid_review'] += 1
                result = ('correct_clear' if clear else 'false_positive') if expected_clear else (
                    'missed_concern' if clear else 'correct_review')
                counts[result] += 1
                categories = {item['category'] for item in verdict['concerns']}
                matches = set(expected['categories']).issubset(categories) and (
                    not expected['requires_limitation'] or bool(verdict['limitations']))
                if not matches:
                    counts['evidence_expectation_mismatch'] += 1
                row.update(status='valid', result=result, evidence_expectations_match=matches)
            except (ScreeningValidationError, ScreeningProviderError, EvaluationError) as error:
                counts['invalid_response'] += 1
                row.update(status='invalid_response', code=getattr(error, 'code', str(error)))
        rows.append(row)
    return {'format': 'crucible-screening-evaluation-v1', **metadata(cases),
            'counts': counts, 'cases': rows,
            'limitations': 'Synthetic policy regression only. Missing, malformed and uncovered inputs '
                'are human fallbacks, not correct model judgments. Evidence checks are structural; '
                'humans must assess whether the cited passage supports the concern.'}


def write_private(path, document):
    raw = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'evaluate'):
        item = sub.add_parser(name)
        item.add_argument('--cases', required=True)
        item.add_argument('--output', required=True)
        item.add_argument('--max-input-tokens', type=int, default=DEFAULT_MAX_INPUT_TOKENS)
        if name == 'prepare':
            item.add_argument('--model', required=True)
            item.add_argument('--max-output-tokens', type=int, default=1500)
            item.add_argument('--reasoning-effort', choices=('none', 'low'))
        else:
            item.add_argument('--responses', required=True)
    args = parser.parse_args(argv)
    try:
        cases = cases_from(read_json(args.cases))
        if args.command == 'prepare':
            result = prepare_cases(cases, model=args.model, max_input_tokens=args.max_input_tokens,
                                   max_output_tokens=args.max_output_tokens, reasoning_effort=args.reasoning_effort)
            status = {'requests': sum('request' in c for c in result['cases']),
                      'coverage_fallbacks': sum('coverage_fallback' in c for c in result['cases'])}
        else:
            result = evaluate_cases(cases, read_json(args.responses), max_input_tokens=args.max_input_tokens)
            status = result['counts']
        write_private(args.output, result)
        print(json.dumps({'status': 'saved', **status}))
        return 0
    except (EvaluationError, ScreeningValidationError, ScreeningProviderError, OSError, ValueError):
        # Paths, arbitrary keys and provider responses can contain private data.
        print(json.dumps({'status': 'failed', 'reason': 'invalid_input_or_output_unavailable'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
