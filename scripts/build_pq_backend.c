/* Crucible native KEM worker. No network, input files, or private-key output. */
#include <oqs/oqs.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <unistd.h>

static int allowed(const char *n) {
 const char *names[]={"ML-KEM-512","ML-KEM-768","ML-KEM-1024","FrodoKEM-640-SHAKE","FrodoKEM-976-SHAKE","FrodoKEM-1344-SHAKE","HQC-1","HQC-3","HQC-5","Classic-McEliece-348864","Classic-McEliece-460896","Classic-McEliece-6688128","Classic-McEliece-6960119","Classic-McEliece-8192128"};
 for(size_t i=0;i<sizeof(names)/sizeof(names[0]);i++) if(!strcmp(n,names[i])) return 1;
 return 0;
}
static void limit(int kind, rlim_t value) {
 struct rlimit current;
 if(getrlimit(kind,&current)) _exit(70);
 if(current.rlim_max!=RLIM_INFINITY && value>current.rlim_max) value=current.rlim_max;
 struct rlimit next={value,current.rlim_max};
 if(setrlimit(kind,&next)) _exit(70);
}
static int u32(uint32_t n) {
 uint8_t v[4]={(uint8_t)(n>>24),(uint8_t)(n>>16),(uint8_t)(n>>8),(uint8_t)n};
 return fwrite(v,1,4,stdout)==4;
}
int main(int argc,char **argv) {
 if(argc!=2 || !allowed(argv[1])) return 64;
 limit(RLIMIT_CORE,0); limit(RLIMIT_CPU,18); limit(RLIMIT_FSIZE,2000000);
 limit(RLIMIT_STACK,64*1024*1024);
#ifndef __APPLE__
 limit(RLIMIT_AS,1024UL*1024*1024);
#endif
 alarm(20);
 OQS_init();
 if(OQS_randombytes_switch_algorithm(OQS_RAND_alg_system)!=OQS_SUCCESS) return 70;
 OQS_KEM *k=OQS_KEM_new(argv[1]);
 if(!k || k->length_public_key>1400000 || k->length_ciphertext>22000 || k->length_shared_secret>64 || k->length_secret_key>50000) return 70;
 uint8_t *pk=calloc(k->length_public_key,1), *sk=calloc(k->length_secret_key,1), *ct=calloc(k->length_ciphertext,1), *ss=calloc(k->length_shared_secret,1), *recovered=calloc(k->length_shared_secret,1);
 if(!pk || !sk || !ct || !ss || !recovered) return 70;
 int ok=OQS_KEM_keypair(k,pk,sk)==OQS_SUCCESS && OQS_KEM_encaps(k,ct,ss,pk)==OQS_SUCCESS;
 /* Exactly one honest encapsulation. A mismatch is evidence, never retried. */
 int decap=ok ? OQS_KEM_decaps(k,recovered,ct,sk)==OQS_SUCCESS : 0;
 uint8_t difference=0;
 for(size_t i=0;i<k->length_shared_secret;i++) difference |= ss[i]^recovered[i];
 uint8_t match=(uint8_t)(decap && difference==0);
 OQS_MEM_secure_free(sk,k->length_secret_key);
 OQS_MEM_secure_free(recovered,k->length_shared_secret);
 if(!ok) {OQS_MEM_secure_free(ss,k->length_shared_secret); return 70;}
 int written=fwrite("CPQ1",1,4,stdout)==4 && u32((uint32_t)k->length_public_key) && u32((uint32_t)k->length_ciphertext) && u32((uint32_t)k->length_shared_secret) && fwrite(&match,1,1,stdout)==1 && fwrite(pk,1,k->length_public_key,stdout)==k->length_public_key && fwrite(ct,1,k->length_ciphertext,stdout)==k->length_ciphertext && fwrite(ss,1,k->length_shared_secret,stdout)==k->length_shared_secret && fflush(stdout)==0;
 OQS_MEM_secure_free(ss,k->length_shared_secret); free(pk); free(ct); OQS_KEM_free(k); OQS_destroy();
 return written ? 0 : 74;
}
