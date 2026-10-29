Public TII-254 equivalent-key verification inputs

Source: Markku-Juhani O. Saarinen, tii254-artifact
https://github.com/mjosaarinen/tii254-artifact
Exact commit: 49c3e84a47e47c1ea0722859f1c0ad560c27d2da
Retrieved 2026-10-29. Both data files are copied unchanged from data/.
The author's MIT license is included as LICENSE.

Related primary paper:
Two-Anchor Holdout/Hermite: Solving the TII-254 McEliece Key Recovery Challenge
https://eprint.iacr.org/2026/1986

These are PUBLIC cryptographic challenge inputs and the PUBLISHED recovered
equivalent key, not Crucible participant information or production credentials.
The historical name TII-254 is not a verified 254-bit security claim.

scripts/verify_tii254_key.py is our independent, small verifier. It reconstructs
the binary parity-check matrix by coefficient expansion and compares complete
binary row spaces. The upstream verifier instead builds the Frobenius closure
over GF(256) in Sage. We did not copy or run the upstream Sage verifier.

This check verifies that the published equivalent Goppa key defines the public
code. It does not repeat the expensive structural recovery, certify its reported
runtime, identify the original author's secret key, or validate full-size attack
extrapolation. The complete GPU calculation is not included in this fixture.

Input hashes and the successful local check are recorded in
docs/mceliece-tii254-verification-20261029.json.
