/* Trusted timing driver: numeric receipts only; no key, ciphertext or seed output.
 * All implementation sources and libraries are pinned by the offline builder.
 * Allocation/initialization are setup; native operation calls include their RNG.
 */
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/resource.h>

#if BENCH_FAMILY == 3
static int active_p, active_q, active_w, active_pk, active_rounded;
#include "ntru_tier_profiles.h"
#include "ntru_tier_kem.c"
static int keygen(unsigned char *pk, unsigned char *sk) { KEM_KeyGen(pk, sk); return 0; }
static int encaps(unsigned char *ct, unsigned char *ss, const unsigned char *pk) { Encap(ct, ss, pk); return 0; }
static int decaps(unsigned char *ss, const unsigned char *ct, const unsigned char *sk) { Decap(ss, ct, sk); return 0; }
#else
#include <oqs/oqs.h>
#if BENCH_FAMILY == 0
static OQS_KEM *kem;
static int keygen(unsigned char *pk, unsigned char *sk) { return OQS_KEM_keypair(kem, pk, sk); }
static int encaps(unsigned char *ct, unsigned char *ss, const unsigned char *pk) { return OQS_KEM_encaps(kem, ct, ss, pk); }
static int decaps(unsigned char *ss, const unsigned char *ct, const unsigned char *sk) { return OQS_KEM_decaps(kem, ss, ct, sk); }
#elif BENCH_FAMILY == 1
#include "api.h"
#define keygen crypto_kem_keypair
#define encaps crypto_kem_enc
#define decaps crypto_kem_dec
#else
#include "api.h"
#define keygen PQCLEAN_MCELIECE348864_CLEAN_crypto_kem_keypair
#define encaps PQCLEAN_MCELIECE348864_CLEAN_crypto_kem_enc
#define decaps PQCLEAN_MCELIECE348864_CLEAN_crypto_kem_dec
#endif
#endif

static void wipe(void *ptr, size_t size) { volatile unsigned char *p=ptr; while(size--) *p++=0; }
static uint64_t now_ns(void) {
  struct timespec t;
  if (clock_gettime(CLOCK_MONOTONIC, &t)) exit(70);
  return (uint64_t)t.tv_sec*1000000000ULL+(uint64_t)t.tv_nsec;
}
static int number(const char *s, long low, long high) {
  char *end; long n=strtol(s, &end, 10);
  if (!*s || *end || n<low || n>high) exit(64);
  return (int)n;
}
static int hex_hash(const char *s) {
  if (strlen(s)!=64) return 0;
  for (int i=0;i<64;i++) if (!((s[i]>='0' && s[i]<='9') || (s[i]>='a' && s[i]<='f'))) return 0;
  return 1;
}
static void limit(int resource, rlim_t value) {
  struct rlimit old;
  if (getrlimit(resource,&old)) exit(70);
  if (old.rlim_max!=RLIM_INFINITY && value>old.rlim_max) value=old.rlim_max;
  old.rlim_cur=value;
  if (setrlimit(resource,&old)) exit(70);
}
static void operation(const char *phase, int index, const char *name, uint64_t ns) {
  printf("{\"event\":\"operation\",\"phase\":\"%s\",\"index\":%d,\"operation\":\"%s\",\"duration_ns\":%llu}\n",phase,index,name,(unsigned long long)ns);
  fflush(stdout);
}
int main(int argc, char **argv) {
  if (argc!=7 || !hex_hash(argv[5]) || !hex_hash(argv[6])) return 64;
  int warmups=number(argv[2],0,10), trials=number(argv[3],1,1000), seconds=number(argv[4],1,120);
  limit(RLIMIT_CORE,0); limit(RLIMIT_CPU,(rlim_t)seconds+2); limit(RLIMIT_STACK,64*1024*1024);
#ifndef __APPLE__
  limit(RLIMIT_AS,2UL*1024*1024*1024);
#endif
  uint64_t setup=now_ns();
  size_t pklen,sklen,ctlen,sslen=32;
#if BENCH_FAMILY == 3
  int tier=number(argv[1],1,PROFILE_COUNT);
  active_p=profiles[tier-1][0]; active_q=profiles[tier-1][1]; active_w=profiles[tier-1][2];
  active_pk=profiles[tier-1][3]; active_rounded=profiles[tier-1][4];
  pklen=PublicKeys_bytes; sklen=SecretKeys_bytes+PublicKeys_bytes+Inputs_bytes+32; ctlen=Ciphertexts_bytes+Confirm_bytes;
#else
  OQS_init();
  if (OQS_randombytes_switch_algorithm(OQS_RAND_alg_system)!=OQS_SUCCESS) return 70;
#if BENCH_FAMILY == 0
  if (strcmp(argv[1],"ML-KEM-512") && strcmp(argv[1],"ML-KEM-768") && strcmp(argv[1],"ML-KEM-1024")) return 64;
  kem=OQS_KEM_new(argv[1]);
  if (!kem) return 70;
  pklen=kem->length_public_key; sklen=kem->length_secret_key; ctlen=kem->length_ciphertext; sslen=kem->length_shared_secret;
#elif BENCH_FAMILY == 1
  if (strcmp(argv[1],"fixed")) return 64;
  pklen=CRYPTO_PUBLICKEYBYTES; sklen=CRYPTO_SECRETKEYBYTES; ctlen=CRYPTO_CIPHERTEXTBYTES;
#else
  if (strcmp(argv[1],"fixed")) return 64;
  pklen=PQCLEAN_MCELIECE348864_CLEAN_CRYPTO_PUBLICKEYBYTES;
  sklen=PQCLEAN_MCELIECE348864_CLEAN_CRYPTO_SECRETKEYBYTES;
  ctlen=PQCLEAN_MCELIECE348864_CLEAN_CRYPTO_CIPHERTEXTBYTES;
#endif
#endif
  if (!pklen || pklen>1400000 || !sklen || sklen>50000 || !ctlen || ctlen>22000 || sslen!=32) return 70;
  unsigned char *pk=calloc(pklen,1), *sk=calloc(sklen,1), *ct=calloc(ctlen,1), ss[32]={0}, recovered[32]={0};
  if (!pk || !sk || !ct) return 70;
  printf("{\"event\":\"setup\",\"duration_ns\":%llu,\"source_sha256\":\"%s\",\"parameters_sha256\":\"%s\"}\n",(unsigned long long)(now_ns()-setup),argv[5],argv[6]);
  fflush(stdout);
  int failed=0;
  for (int phase_index=0;phase_index<2 && !failed;phase_index++) {
    const char *phase=phase_index?"measured":"warmup";
    for (int index=0;index<(phase_index?trials:warmups);index++) {
      printf("{\"event\":\"begin\",\"phase\":\"%s\",\"index\":%d}\n",phase,index); fflush(stdout);
      uint64_t before=now_ns(); int ok=keygen(pk,sk)==0; uint64_t elapsed=now_ns()-before;
      if (ok) operation(phase,index,"keygen",elapsed);
      if (ok) { before=now_ns(); ok=encaps(ct,ss,pk)==0; elapsed=now_ns()-before; if (ok) operation(phase,index,"encapsulate",elapsed); }
      if (ok) { before=now_ns(); ok=decaps(recovered,ct,sk)==0; elapsed=now_ns()-before; if (ok) operation(phase,index,"decapsulate",elapsed); }
      unsigned char different=0;
      for (int j=0;j<32;j++) different|=ss[j]^recovered[j];
      const char *outcome=!ok?"error":different?"agreement_failure":"agreement";
      printf("{\"event\":\"outcome\",\"phase\":\"%s\",\"index\":%d,\"outcome\":\"%s\"}\n",phase,index,outcome); fflush(stdout);
      wipe(sk,sklen); wipe(ss,32); wipe(recovered,32);
      if (!ok || different) { failed=1; break; }
    }
  }
  free(pk); free(sk); free(ct);
#if BENCH_FAMILY == 0
  OQS_KEM_free(kem);
#endif
#if BENCH_FAMILY != 3
  OQS_destroy();
#endif
  return failed?1:0;
}
