(function (root, factory) {
  const seed = factory();
  if (typeof module === 'object' && module.exports) module.exports = seed;
  if (root) root.CrucibleTheorySeed = seed;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  const createdAt = Date.UTC(2026, 9, 16, 12, 0, 0);
  function note(id, title, claim, conditions, body, tags, kind = 'argument') {
    return { id, illustrative: true, revisions: [{ revision: 1, title, claim, conditions, body, tags, kind, createdAt }] };
  }
  const notes = [
    note('alias-lemma', 'A second ciphertext for the same key',
      'If a public efficient transformation turns every honest challenge ciphertext into a distinct accepted ciphertext that decapsulates to the same k-bit key, a one-query classical adversary has real-versus-random distinguishing gap 1 − 2⁻ᵏ.',
      'k ≥ 1. The challenge-query restriction uses exact ciphertext byte-string equality. The transformation receives only the public key and ciphertext; distinctness, admissibility, and key preservation hold with probability 1 over its coins for every honest key pair and encapsulation. Its coins are independent of the comparison key. Decapsulation is deterministic.',
      'Receive the public key, challenge ciphertext c, and supplied key K*. Transform c to c′ and query decapsulation on c′. The query is permitted because c′ differs from c. Output “real” exactly when the response equals K*.\n\nIn the real experiment equality always holds. In the random experiment K* is an independent uniform k-bit string, so equality holds with probability 2⁻ᵏ. The raw gap is therefore 1 − 2⁻ᵏ; with equal experiment priors, guessing success is 1 − 2⁻⁽ᵏ⁺¹⁾. The real shared key need not itself be uniform for this calculation.\n\nThis is a generic conditional lemma with an informal argument. It makes no claim that a live Crucible scheme satisfies the transformation premise.',
      ['IND-CCA', 'ciphertext aliasing', 'conditional result', 'informal proof supplied']),
    note('ignored-tag', 'An ignored tag creates the second ciphertext',
      'For the wrapper that appends a bit 0 during encapsulation and ignores that bit during decapsulation, flipping the bit satisfies the ciphertext-alias lemma.',
      'The base KEM is perfectly correct with k-bit shared keys. The wrapper ciphertext is a uniquely encoded pair (c,b), where b is one bit. Both tag values are accepted and Decaps′(sk,(c,b)) = Decaps(sk,c). No base security assumption is asserted.',
      'Encapsulation returns ((c,0),K) after running the base encapsulation. Transform (c,0) into (c,1). The two byte strings differ, both are admissible, and the wrapped decapsulation returns the same K by perfect correctness of the base. Thus the previous lemma applies.\n\nThis is an illustrative construction family, not an allegation about a deployed KEM or a registered Crucible release. A particular scheme application still needs its exact encoding and decapsulation rules checked.',
      ['ignored-tag wrapper', 'IND-CCA', 'application', 'informal proof supplied']),
    note('rejected-tag', 'Rejecting the tag blocks this particular application',
      'If the wrapper rejects every ciphertext tagged 1, the bit-flipping transformation above fails the key-preservation premise of the alias lemma.',
      'Use the same uniquely encoded pair wrapper, but define Decaps′(sk,(c,1)) = failure. The bit-flipping transformation remains exactly (c,0) ↦ (c,1).',
      'The transformed ciphertext decapsulates to failure, not the encapsulated k-bit key. Therefore this transformation cannot instantiate the lemma.\n\nExcluding this single argument does not establish IND-CCA security. Other ciphertext modifications, weaknesses of the base construction, and encoding issues remain separate questions. This illustrates why an accepted generic note and its scheme applications need separate assessments.',
      ['rejecting-tag wrapper', 'scope limitation', 'non-application'], 'observation'),
    note('linear-search', 'Invert a 32-bit permutation by exhaustive search',
      'Exhaustive search inverts any target of a fixed permutation on 32-bit strings with certainty, using at most 2³² online forward evaluations, no precomputation, and 32 bytes of explicit working storage in the declared toy machine.',
      'N = 2³². A permutation F is fixed and accessible as a forward oracle. After any preprocessing phase, x is uniform and y = F(x) is given. One forward-oracle call costs one online work unit. Oracle internals, code, and input/output channels are excluded from storage for every strategy. The toy machine has direct word operations and an explicitly allocated 32-byte scratch area.',
      'Enumerate every 32-bit candidate z, compute F(z), and compare with y. Because F is a permutation, exactly one candidate succeeds. At most N oracle evaluations are needed; the counters, candidate, target, and result fit in the allocated scratch area.\n\nThis is a worst-case analytic bound, not a measured runtime or an average search cost. The plot uses timeLog2 = 32 and memoryLog2 = 5. No existing KEM or real cryptographic security estimate is modeled.',
      ['permutation inversion', 'toy resource model', 'no preprocessing', 'worst-case bound']),
    note('inverse-table', 'Precompute the complete inverse table',
      'For the same fixed 32-bit permutation, a complete inverse table gives certainty with one online table read, after 2³² preprocessing oracle evaluations and 2³² table writes; storage is 2³⁴ + 32 bytes in the declared toy machine.',
      'Use the same F, target distribution, exclusions, and 32-byte scratch allocation as exhaustive search. A direct-address table stores one 32-bit value T[y] = F⁻¹(y) for each possible y. Each online table read and each forward evaluation count as one work unit; other word operations are omitted for both strategies. The table is built before the target y is revealed and only applies to this same F.',
      'For every z, evaluate F(z) and assign T[F(z)] = z. This costs N forward evaluations and N table writes before the target arrives. Afterwards return T[y] with one table read and no online forward evaluations.\n\nThe table stores N four-byte values, giving 2³⁴ table bytes plus 32 scratch bytes. The point is timeLog2 = 0 and memoryLog2 = log₂(2³⁴ + 32). The displayed online advantage is paid for by preprocessing and storage; the plot does not claim a lower end-to-end cost for one target. A new independent F requires a new table.',
      ['permutation inversion', 'toy resource model', 'preprocessing', 'direct-address table']),
    note('accounting', 'The phase boundary changes the comparison',
      'The complete-table strategy does not reduce the worst-case forward-query budget below the specified exhaustive scan for a single target when preprocessing is included: the scan’s worst-case count and the table’s preprocessing count are both 2³². Its benefit is the online phase, and it can reuse the table only while F stays fixed.',
      'Compare the two specified algorithms on the same 32-bit permutation inversion task and require certainty. Count preprocessing and online forward evaluations separately; table writes and reads remain distinct additional work.',
      'Exhaustive search has no preprocessing and at most N online oracle calls. The table method has N preprocessing oracle calls and zero online oracle calls, plus N preprocessing writes and one online read. For one target the table method therefore offers no reduction in this worst-case oracle-query accounting. For repeated targets under the same F, its preprocessing can be shared.\n\nThis is a statement about these two algorithms, not a lower bound for all inversion algorithms or proof of an optimal time–memory tradeoff. The illustrative view shows two disconnected strategy points. Neither a connecting curve nor a security frontier is justified.',
      ['resource accounting', 'phase boundary', 'scope limitation'], 'observation')
  ];
  return { format: 'crucible-theory-local-v1', illustrative: true,
    notice: 'Local design prototype. Fictional examples and simulated assessments; no review authority, live scheme conclusion, or formal proof checking.',
    notes, views: [] };
});
