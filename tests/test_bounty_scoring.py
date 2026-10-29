"""A completed task reaches profiles/ranking only through its single award."""
from test_bounties import system, opened, boost, submit, published, assess


def test_pending_approval_has_no_points_and_final_award_reaches_profile_and_ranking(system):
    _, owner, reviewer, solver, supporter, *_ = system
    value = boost(supporter, opened(system))
    value = submit(solver, value, published(solver, reviewer))
    submission_id = value['receipt']['submission_id']
    value = assess(owner, value, 'poster', submission_id=submission_id)
    before = solver.client.get('/api/people/solver').json()['person']
    assert before['points'] == before['score_breakdown']['bounty_points'] == 0
    value = assess(reviewer, value, 'pipeline', submission_id=submission_id)
    assert value['bounty']['award']['points'] == 200
    after = solver.client.get('/api/people/solver').json()['person']
    assert after['points'] == after['score_breakdown']['bounty_points'] == 200
    assert after['points'] == sum(after['score_breakdown'].values())
    people = solver.client.get('/api/people').json()['people']
    assert people[0]['handle'] == 'solver' and people[0]['points'] == 200
    assert all(row['points'] == 0 for row in people[1:])
    # Repeated reads derive the same award rather than accruing it on viewing.
    assert solver.client.get('/api/people/solver').json()['person']['points'] == 200
