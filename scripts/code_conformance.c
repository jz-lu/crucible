/* Test-only executable; never installed as a challenge worker. */
#define main unused_generation_main
#include "code_worker.c"
#undef main
#include <oqs/rand_nist.h>
#include <string.h>
int main(void){
 uint8_t entropy[48],seed[48],ss[32],recovered[32];
 uint8_t *pk=calloc(PK_BYTES,1),*sk=calloc(SK_BYTES,1),*ct=calloc(CT_BYTES,1);
 if(!pk||!sk||!ct)return 70;
 for(int i=0;i<48;i++)entropy[i]=(uint8_t)i;
 OQS_init();OQS_randombytes_custom_algorithm(OQS_randombytes_nist_kat);
 OQS_randombytes_nist_kat_init_256bit(entropy,NULL);OQS_randombytes(seed,48);
 OQS_randombytes_nist_kat_init_256bit(seed,NULL);
 if(KEYGEN(pk,sk)||ENCAPS(ct,ss,pk)||DECAPS(recovered,ct,sk))return 71;
 if(memcmp(ss,recovered,32))return 72;
 fwrite(pk,1,PK_BYTES,stdout);fwrite(sk,1,SK_BYTES,stdout);fwrite(ct,1,CT_BYTES,stdout);fwrite(ss,1,32,stdout);
 OQS_MEM_secure_free(sk,SK_BYTES);OQS_MEM_cleanse(ss,32);OQS_MEM_cleanse(recovered,32);free(pk);free(ct);OQS_destroy();return 0;
}
