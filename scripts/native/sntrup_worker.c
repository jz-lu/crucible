/* Crucible's fixed-size pipe adapter for libntruprime sntrup761.
 * No JSON, filesystem input, plugins, dynamic loader paths or secret logging.
 * The final executable statically links the pinned upstream libraries.
 */
#include <ntruprime.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>

#define PK ntruprime_kem_sntrup761_PUBLICKEYBYTES
#define SK ntruprime_kem_sntrup761_SECRETKEYBYTES
#define CT ntruprime_kem_sntrup761_CIPHERTEXTBYTES
#define SS ntruprime_kem_sntrup761_BYTES

static void wipe(void *p, size_t n) {
  volatile unsigned char *v = p;
  while (n--) *v++ = 0;
}

static int read_exact(unsigned char *p, size_t n) {
  return fread(p, 1, n, stdin) == n;
}

static int end_input(void) {
  return fgetc(stdin) == EOF && !ferror(stdin);
}

static int emit(const unsigned char *p, size_t n) {
  return fwrite(p, 1, n, stdout) == n;
}

int main(int argc, char **argv) {
  unsigned char pk[PK], sk[SK], ct[CT], key[SS], recovered[SS];
  struct rlimit zero = {0, 0}, cpu = {20, 20};
  int ok = 0;
  if (setrlimit(RLIMIT_CORE, &zero) || setrlimit(RLIMIT_CPU, &cpu)) return 4;
  if (argc != 2) return 2;
  if (!strcmp(argv[1], "generate")) {
    if (!end_input()) return 2;
    ntruprime_kem_sntrup761_keypair(pk, sk);
    ntruprime_kem_sntrup761_enc(ct, key, pk);
    ntruprime_kem_sntrup761_dec(recovered, ct, sk);
    /* Preserve Encaps' key even when the independent Decaps self-check fails. */
    ok = emit(pk, PK) && emit(ct, CT) && emit(key, SS) && emit(recovered, SS);
  } else if (!strcmp(argv[1], "keygen")) {
    if (!end_input()) return 2;
    ntruprime_kem_sntrup761_keypair(pk, sk);
    ok = emit(pk, PK) && emit(sk, SK);
  } else if (!strcmp(argv[1], "encapsulate")) {
    if (!read_exact(pk, PK) || !end_input()) return 2;
    ntruprime_kem_sntrup761_enc(ct, key, pk);
    ok = emit(ct, CT) && emit(key, SS);
  } else if (!strcmp(argv[1], "decapsulate")) {
    if (!read_exact(sk, SK) || !read_exact(ct, CT) || !end_input()) return 2;
    ntruprime_kem_sntrup761_dec(key, ct, sk);
    ok = emit(key, SS);
  } else {
    return 2;
  }
  wipe(sk, SK);
  wipe(key, SS);
  wipe(recovered, SS);
  return ok && fflush(stdout) == 0 ? 0 : 3;
}
