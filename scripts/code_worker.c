/* Research worker: one exact compiled parameter profile, no input secrets. */
#include <oqs/oqs.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <unistd.h>
#include "api.h"
#if CODE_FAMILY == 1
#define PK_BYTES CRYPTO_PUBLICKEYBYTES
#define SK_BYTES CRYPTO_SECRETKEYBYTES
#define CT_BYTES CRYPTO_CIPHERTEXTBYTES
#define KEYGEN crypto_kem_keypair
#define ENCAPS crypto_kem_enc
#define DECAPS crypto_kem_dec
#else
#define PK_BYTES PQCLEAN_MCELIECE348864_CLEAN_CRYPTO_PUBLICKEYBYTES
#define SK_BYTES PQCLEAN_MCELIECE348864_CLEAN_CRYPTO_SECRETKEYBYTES
#define CT_BYTES PQCLEAN_MCELIECE348864_CLEAN_CRYPTO_CIPHERTEXTBYTES
#define KEYGEN PQCLEAN_MCELIECE348864_CLEAN_crypto_kem_keypair
#define ENCAPS PQCLEAN_MCELIECE348864_CLEAN_crypto_kem_enc
#define DECAPS PQCLEAN_MCELIECE348864_CLEAN_crypto_kem_dec
#endif
static void limit(int kind,rlim_t value){struct rlimit r;if(getrlimit(kind,&r))_exit(70);if(r.rlim_max!=RLIM_INFINITY&&value>r.rlim_max)value=r.rlim_max;r.rlim_cur=value;if(setrlimit(kind,&r))_exit(70);}
static int u32(uint32_t n){uint8_t v[4]={n>>24,n>>16,n>>8,n};return fwrite(v,1,4,stdout)==4;}
int main(int argc,char **argv){
 (void)argv;if(argc!=2)return 64;
 limit(RLIMIT_CORE,0);limit(RLIMIT_CPU,18);limit(RLIMIT_FSIZE,2000000);limit(RLIMIT_STACK,64*1024*1024);
#ifndef __APPLE__
 limit(RLIMIT_AS,1024UL*1024*1024);
#endif
 alarm(20);OQS_init();if(OQS_randombytes_switch_algorithm(OQS_RAND_alg_system)!=OQS_SUCCESS)return 70;
 uint8_t *pk=calloc(PK_BYTES,1),*sk=calloc(SK_BYTES,1),*ct=calloc(CT_BYTES,1),ss[32]={0},recovered[32]={0};
 if(!pk||!sk||!ct)return 70;
 int success=KEYGEN(pk,sk)==0&&ENCAPS(ct,ss,pk)==0;
 int decap=success?DECAPS(recovered,ct,sk)==0:0;uint8_t difference=0;
 for(int i=0;i<32;i++)difference|=ss[i]^recovered[i];
 uint8_t agrees=decap&&difference==0;OQS_MEM_secure_free(sk,SK_BYTES);OQS_MEM_cleanse(recovered,32);
 if(!success){OQS_MEM_cleanse(ss,32);return 70;}
 int written=fwrite("CPQ1",1,4,stdout)==4&&u32(PK_BYTES)&&u32(CT_BYTES)&&u32(32)&&fwrite(&agrees,1,1,stdout)==1&&fwrite(pk,1,PK_BYTES,stdout)==PK_BYTES&&fwrite(ct,1,CT_BYTES,stdout)==CT_BYTES&&fwrite(ss,1,32,stdout)==32&&fflush(stdout)==0;
 OQS_MEM_cleanse(ss,32);free(pk);free(ct);OQS_destroy();return written?0:74;
}
