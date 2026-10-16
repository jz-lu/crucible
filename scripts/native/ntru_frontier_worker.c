/* Variable-parameter Streamlined NTRU Prime research worker.
 * The build helper imports the pinned 2020 reference core with explicit,
 * audited substitutions. Only one certified public profile per process.
 * Secret inputs/outputs use bounded pipes; no secret argv, files or logging.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>

static int active_p, active_q, active_w, active_pk, active_rounded;
#include "ntru_tier_profiles.h"
#include "ntru_tier_kem.c"

static void wipe(void *ptr, size_t n) {
  volatile unsigned char *v = ptr;
  while (n--) *v++ = 0;
}

static int read_exact(unsigned char *ptr, size_t n) {
  return fread(ptr, 1, n, stdin) == n;
}

static int end_input(void) {
  return fgetc(stdin) == EOF && !ferror(stdin);
}

static int emit(const unsigned char *ptr, size_t n) {
  return fwrite(ptr, 1, n, stdout) == n;
}

int main(int argc, char **argv) {
  struct rlimit zero = {0, 0}, cpu = {20, 20};
  char *end;
  long tier;
  int ok = 0;
  if (setrlimit(RLIMIT_CORE, &zero) || setrlimit(RLIMIT_CPU, &cpu)) return 4;
  if (argc != 3) return 2;
  tier = strtol(argv[1], &end, 10);
  if (*end || tier < 1 || tier > PROFILE_COUNT) return 2;
  active_p = profiles[tier-1][0]; active_q = profiles[tier-1][1];
  active_w = profiles[tier-1][2]; active_pk = profiles[tier-1][3];
  active_rounded = profiles[tier-1][4];
  {
    size_t pklen = PublicKeys_bytes, sklen = SecretKeys_bytes + PublicKeys_bytes + Inputs_bytes + 32;
    size_t ctlen = Ciphertexts_bytes + Confirm_bytes;
    unsigned char pk[pklen], sk[sklen], ct[ctlen], key[32], recovered[32];
    if (!strcmp(argv[2], "generate")) {
      if (!end_input()) return 2;
      KEM_KeyGen(pk, sk); Encap(ct, key, pk); Decap(recovered, ct, sk);
      /* Encaps' target survives a failed independent honest self-check. */
      ok = emit(pk, pklen) && emit(ct, ctlen) && emit(key, 32) && emit(recovered, 32);
    } else if (!strcmp(argv[2], "keygen")) {
      if (!end_input()) return 2;
      KEM_KeyGen(pk, sk); ok = emit(pk, pklen) && emit(sk, sklen);
    } else if (!strcmp(argv[2], "encapsulate")) {
      if (!read_exact(pk, pklen) || !end_input()) return 2;
      Encap(ct, key, pk); ok = emit(ct, ctlen) && emit(key, 32);
    } else if (!strcmp(argv[2], "decapsulate")) {
      if (!read_exact(sk, sklen) || !read_exact(ct, ctlen) || !end_input()) return 2;
      Decap(key, ct, sk); ok = emit(key, 32);
    } else if (!strcmp(argv[2], "sample-short")) {
      /* Explicit test-only sampling observation: independent fresh samples,
       * never the polynomial of a challenge/key generation operation. */
      small sample[p]; unsigned char encoded[Small_bytes];
      if (!end_input()) return 2;
      Short_random(sample); Small_encode(encoded, sample);
      ok = emit(encoded, Small_bytes); wipe(sample, sizeof sample);
    } else return 2;
    wipe(sk, sklen); wipe(key, sizeof key); wipe(recovered, sizeof recovered);
  }
  return ok && fflush(stdout) == 0 ? 0 : 3;
}
