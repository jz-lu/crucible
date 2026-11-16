"""Arithmetic semantics and conservative bit-work budgets, independent of Python timing."""
import pytest
from crucible.attack_models import common as ram


class Bits:
    def __init__(self):
        self.work = 0

    def gate(self, truth, a, b=0):
        self.work += 1
        return (truth >> (2*a+b)) & 1

    def add(self, a, b, width):
        carry, out = 0, 0
        for i in range(width):
            x, y = (a >> i) & 1, (b >> i) & 1
            t = self.gate(6, x, y)
            out |= self.gate(6, t, carry) << i
            carry = self.gate(14, self.gate(8, x, y), self.gate(8, t, carry))
        return out, carry


def test_ripple_adder_executes_every_small_input_within_bound():
    for width in range(1, 6):
        for a in range(1 << width):
            for b in range(1 << width):
                counter = Bits()
                lo, carry = counter.add(a, b, width)
                assert lo + (carry << width) == a + b
                assert counter.work <= ram.add_bits(width)


def test_arithmetic_accounts_cover_their_component_programs():
    for width in range(1, 1025):
        assert ram.mod_add(width) >= 2*ram.add_bits(width+1) + ram.mux_bits(width+1)
        per_round = 2*ram.mod_add(width) + ram.mux_bits(width) + ram.loop_control(width.bit_length())
        assert ram.mul_mod(width) >= width*per_round + 2*width
        assert ram.divmod_bits(width) >= width*(2*ram.add_bits(width+1)+ram.mux_bits(width+1)+ram.loop_control(width.bit_length()))


def test_hash_padding_boundaries_and_squeeze_blocks():
    # An extra byte crossing padding boundaries adds a full compression.
    assert ram.sha256(56)-ram.sha256(55) > 100000
    assert ram.sha256(55)-ram.sha256(54) == 8
    assert ram.sha512(112)-ram.sha512(111) > 200000
    assert ram.shake256(136,32)-ram.shake256(135,32) > 190000
    assert ram.shake128(168,32)-ram.shake128(167,32) > 190000
    assert ram.shake256(0,137)-ram.shake256(0,136) > 190000
    assert ram.sha3_512(72)-ram.sha3_512(71) > 190000


def test_enormous_bounds_remain_finite_without_float_overflow():
    assert ram.log2_int(1 << 100000) == 100000
    assert ram.log2_int((1 << 100000)-1) <= 100000


@pytest.mark.parametrize('bad', [0,-1,True,1.1,'4',None])
def test_widths_reject_unidentified_or_invalid_costs(bad):
    with pytest.raises(ValueError):
        ram.mul_mod(bad)
