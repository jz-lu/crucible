/* Decoder fixtures at the analytically guaranteed boundary for each codec. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "code.h"
#include "parameters.h"
int main(void) {
    const unsigned threshold = PARAM_N2 / 4;
    for (unsigned test = 0; test < 256; ++test) {
        uint64_t message[2] = {0}, decoded[2] = {0};
        uint64_t encoded[VEC_N1N2_SIZE_64] = {0};
        uint8_t *bytes = (uint8_t *)message;
        for (unsigned i = 0; i < 16; ++i) bytes[i] = (uint8_t)(test + 17*i);
        code_encode(encoded, message);
        /* delta maximally damaged RM blocks, one just below its threshold. */
        for (unsigned block = 0; block <= PARAM_DELTA; ++block) {
            unsigned flips = threshold - (block == PARAM_DELTA ? 1 : 0);
            for (unsigned j = 0; j < flips; ++j) {
                unsigned bit = PARAM_N2 * block + j;
                encoded[bit / 64] ^= (uint64_t)1 << (bit % 64);
            }
        }
        code_decode(decoded, encoded);
        if (memcmp(decoded, message, 16)) return 1;
    }
    printf("256 codec fixtures passed at %u-error bound\n", threshold*(PARAM_DELTA+1)-1);
    return 0;
}
