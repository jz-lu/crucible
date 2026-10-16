/* Independent deterministic boundary fixtures for the unchanged native codec. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "code.h"
#include "parameters.h"
int main(void){
 for(unsigned test=0;test<256;test++){
  uint64_t message[2]={0},decoded[2]={0},encoded[VEC_N1N2_SIZE_64]={0};
  uint8_t *bytes=(uint8_t*)message;
  for(int i=0;i<16;i++)bytes[i]=(uint8_t)(test+17*i);
  code_encode(encoded,message);
  /* 15 blocks have96 errors, the16th has95: total1535. */
  for(int block=0;block<16;block++)for(int j=0;j<(block==15?95:96);j++){
   int bit=384*block+j;encoded[bit/64]^=(uint64_t)1<<(bit%64);
  }
  code_decode(decoded,encoded);
  if(memcmp(decoded,message,16))return 1;
 }
 puts("256 codec fixtures passed at1535-error bound");return 0;
}
