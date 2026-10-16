#include <oqs/oqs.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
static void rng(uint8_t*out,size_t n){for(size_t i=0;i<n;i++)out[i]=(uint8_t)i;}
int main(void){OQS_init();OQS_randombytes_custom_algorithm(rng);OQS_KEM*k=OQS_KEM_new("FrodoKEM-640-SHAKE");if(!k)return 2;
uint8_t*pk=malloc(k->length_public_key),*sk=malloc(k->length_secret_key),*ct=malloc(k->length_ciphertext),ss[16];
if(OQS_KEM_keypair(k,pk,sk)||OQS_KEM_encaps(k,ct,ss,pk))return 3;
fwrite(pk,1,k->length_public_key,stdout);fwrite(sk,1,k->length_secret_key,stdout);fwrite(ct,1,k->length_ciphertext,stdout);fwrite(ss,1,16,stdout);return 0;}
